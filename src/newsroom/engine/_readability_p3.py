"""Readability: metadata, cleaning and parse() (part 3)."""

from __future__ import annotations

import json
import math
import re

from bs4 import Tag

from newsroom.engine import dom as D
from newsroom.engine._readability_p1 import (
    DEFAULT_CHAR_THRESHOLD,
    DEPRECATED_SIZE_ATTRIBUTE_ELEMS,
    DIV_TO_P_ELEMS,
    FLAG_CLEAN_CONDITIONALLY,
    FLAG_WEIGHT_CLASSES,
    HTML_ESCAPE_MAP,
    PHRASING_ELEMS,
    PRESENTATIONAL_ATTRIBUTES,
    REGEXPS,
    js_trim,
    jsre,
    text_of,
)
from newsroom.engine._readability_p2 import ReadabilityGrab

_IMG_FILE = jsre(r"\.(jpg|jpeg|png|webp)", re.I)
_SCHEMA_ORG = jsre(r"^https?\:\/\/schema\.org\/?$")
_PROPERTY_PATTERN = jsre(r"\s*(article|dc|dcterm|og|twitter)\s*:\s*(author|creator|description|published_time|title|site_name)\s*", re.I)
_NAME_PATTERN = jsre(r"^\s*(?:(dc|dcterm|og|twitter|parsely|weibo:(article|webpage))\s*[-\.:]\s*)?(author|creator|pub-date|description|title|site_name)\s*$", re.I)
_CDATA_EDGES = jsre(r"^\s*<!\[CDATA\[|\]\]>\s*$")
_ENTITY_NAMED = re.compile(r"&(quot|amp|apos|lt|gt);")
_ENTITY_NUMERIC = re.compile(r"&#(?:x([0-9a-f]+)|([0-9]+));", re.I)
_LAZY_SRCSET = jsre(r"\.(jpg|jpeg|png|webp)\s+\d")
_LAZY_SRC = jsre(r"^\s*\S+\.(jpg|jpeg|png|webp)\S*\s*$")


class _JsTypeError(Exception):
    """A TypeError JavaScript would throw (reading a property of null, calling .match on a non-string)."""


def js_get(obj, key):
    """obj[key] for what JSON.parse gives."""
    if obj is None:
        raise _JsTypeError(key)
    if isinstance(obj, dict):
        return obj.get(key)
    if isinstance(obj, list):
        if isinstance(key, str) and key.isdigit():
            i = int(key)
            return obj[i] if i < len(obj) else None
        return len(obj) if key == "length" else None
    return None


def js_truthy(v) -> bool:
    return not (v is None or v is False or v == 0 or v == "" or (isinstance(v, float) and math.isnan(v)))


def js_match(v, regex) -> bool:
    """v.match(regex) as a truth value; only a string has .match."""
    if not isinstance(v, str):
        raise _JsTypeError("match")
    return regex.search(v) is not None


def js_parse_int(s: str) -> float:
    m = re.match(r"[ \t\n\v\f\r]*([-+]?\d+)", s)
    return int(m.group(1)) if m else float("nan")


def js_div(a: float, b: float) -> float:
    if b == 0:
        return float("nan") if a == 0 else math.copysign(float("inf"), a)
    return a / b


def _json_parse(text: str):
    def reject(name):
        raise ValueError(name)

    return json.loads(text, parse_constant=reject)


class ReadabilityRest(ReadabilityGrab):
    # -- metadata ---------------------------------------------------------------------------------------------------------------------------

    def _unescapeHtmlEntities(self, s):
        if not s:
            return s

        def numeric(m: re.Match[str]) -> str:
            num = int(m.group(1), 16) if m.group(1) else int(m.group(2), 10)
            if num == 0 or num > 0x10FFFF or 0xD800 <= num <= 0xDFFF:
                num = 0xFFFD
            return chr(num)

        return _ENTITY_NUMERIC.sub(numeric, _ENTITY_NAMED.sub(lambda m: HTML_ESCAPE_MAP[m.group(1)], s))

    def _getJSONLD(self, doc) -> dict:
        """Try to extract metadata from JSON-LD object. For now, only Schema.org objects of type Article or its subtypes are supported."""
        metadata = None
        for element in self._getAllNodesWithTag(doc, ["script"]):
            if metadata is not None or D.get_attribute(element, "type") != "application/ld+json":
                continue
            try:
                # Strip CDATA markers if present
                content = _CDATA_EDGES.sub("", text_of(element))
                parsed = _json_parse(content)
                if isinstance(parsed, list):
                    found = None
                    for it in parsed:
                        t = js_get(it, "@type")
                        if js_truthy(t) and js_match(t, REGEXPS["jsonLdArticleTypes"]):
                            found = it
                            break
                    parsed = found
                    if not js_truthy(parsed):
                        continue
                ctx = js_get(parsed, "@context")
                has_ctx = isinstance(parsed, dict) and "@context" in parsed
                matches = (isinstance(ctx, str) and _SCHEMA_ORG.search(ctx) is not None) or (
                    isinstance(ctx, (dict, list)) or (ctx is None and has_ctx)
                ) and isinstance(js_get(ctx, "@vocab"), str) and _SCHEMA_ORG.search(js_get(ctx, "@vocab")) is not None
                if not matches:
                    continue
                if not js_truthy(js_get(parsed, "@type")) and isinstance(js_get(parsed, "@graph"), list):
                    found = None
                    for it in parsed["@graph"]:
                        t = js_get(it, "@type")
                        if js_match(t if js_truthy(t) else "", REGEXPS["jsonLdArticleTypes"]):
                            found = it
                            break
                    parsed = found
                if not js_truthy(parsed) or not js_truthy(js_get(parsed, "@type")) or not js_match(js_get(parsed, "@type"), REGEXPS["jsonLdArticleTypes"]):
                    continue
                metadata = {}
                name, headline = js_get(parsed, "name"), js_get(parsed, "headline")
                if isinstance(name, str) and isinstance(headline, str) and name != headline:
                    # We have both name and headline element in the JSON-LD. They should both be the same but some websites like aktualne.cz put their own name into "name" and the article title to "headline" which confuses Readability. So we try to check if either "name" or "headline" closely matches the html title, and if so, use that one. If not, then we use "name" by default.
                    title = self._getArticleTitle()
                    name_matches = self._textSimilarity(name, title) > 0.75
                    headline_matches = self._textSimilarity(headline, title) > 0.75
                    metadata["title"] = headline if headline_matches and not name_matches else name
                elif isinstance(name, str):
                    metadata["title"] = js_trim(name)
                elif isinstance(headline, str):
                    metadata["title"] = js_trim(headline)
                author = js_get(parsed, "author")
                if js_truthy(author):
                    if isinstance(js_get(author, "name"), str):
                        metadata["byline"] = js_trim(author["name"])
                    elif isinstance(author, list) and js_truthy(author[0] if author else None) and isinstance(js_get(author[0], "name"), str):
                        metadata["byline"] = ", ".join(js_trim(a["name"]) for a in author if js_truthy(a) and isinstance(js_get(a, "name"), str))
                if isinstance(js_get(parsed, "description"), str):
                    metadata["excerpt"] = js_trim(parsed["description"])
                publisher = js_get(parsed, "publisher")
                if js_truthy(publisher) and isinstance(js_get(publisher, "name"), str):
                    metadata["siteName"] = js_trim(publisher["name"])
                if isinstance(js_get(parsed, "datePublished"), str):
                    metadata["datePublished"] = js_trim(parsed["datePublished"])
            except (_JsTypeError, ValueError, RecursionError):
                pass  # the script names no article: what was read before the error stays
        return metadata if metadata is not None else {}

    def _getArticleMetadata(self, jsonld: dict) -> dict:
        """Attempts to get excerpt and byline metadata for the article."""
        metadata: dict = {}
        values: dict[str, str] = {}
        for element in D.get_elements_by_tag_name(self._doc.soup, "meta"):
            element_name = D.get_attribute(element, "name")
            element_property = D.get_attribute(element, "property")
            content = D.get_attribute(element, "content")
            if not content:
                continue
            matched = None
            # Convert property into a name
            if element_property:
                matched = _PROPERTY_PATTERN.search(element_property)
                if matched:
                    # Convert to lowercase, and remove any whitespace so we can match below.
                    name = jsre(r"\s").sub("", matched.group(0).lower())
                    values[name] = js_trim(content)
            if not matched and element_name and _NAME_PATTERN.search(element_name):
                name = element_name
                if content:
                    # Convert to lowercase, remove any whitespace, and convert dots to colons so we can match below.
                    name = jsre(r"\s").sub("", name.lower()).replace(".", ":")
                    values[name] = js_trim(content)

        def first(*candidates):
            for c in candidates:
                if js_truthy(c):
                    return c
            return candidates[-1]

        v = values.get
        # get title
        metadata["title"] = first(jsonld.get("title"), v("dc:title"), v("dcterm:title"), v("og:title"), v("weibo:article:title"), v("weibo:webpage:title"), v("title"),
                                  v("twitter:title"), v("parsely-title"))  # fmt: skip
        if not js_truthy(metadata["title"]):
            metadata["title"] = self._getArticleTitle()
        article_author = v("article:author") if isinstance(v("article:author"), str) and not self._isUrl(v("article:author")) else None
        # get author
        metadata["byline"] = first(jsonld.get("byline"), v("dc:creator"), v("dcterm:creator"), v("author"), v("parsely-author"), article_author)
        # get description
        metadata["excerpt"] = first(jsonld.get("excerpt"), v("dc:description"), v("dcterm:description"), v("og:description"), v("weibo:article:description"),
                                    v("weibo:webpage:description"), v("description"), v("twitter:description"))  # fmt: skip
        # get site name
        metadata["siteName"] = first(jsonld.get("siteName"), v("og:site_name"))
        # get article published time
        metadata["publishedTime"] = first(jsonld.get("datePublished"), v("article:published_time"), v("parsely-pub-date"), None)
        # in many sites the meta value is escaped with HTML entities, so here we need to unescape it
        for key in ("title", "byline", "excerpt", "siteName", "publishedTime"):
            metadata[key] = self._unescapeHtmlEntities(metadata[key])
        return metadata

    # -- noscript and scripts ---------------------------------------------------------------------------------------------------------------

    def _isSingleImage(self, node) -> bool:
        while node is not None:
            if D.tag_name(node) == "IMG":
                return True
            kids = D.children(node)
            if len(kids) != 1 or js_trim(text_of(node)) != "":
                return False
            node = kids[0]
        return False

    def _unwrapNoscriptImages(self, doc) -> None:
        """Find all <noscript> that are located after <img> nodes, and which contain only one <img> element. Replace the first image with the image from inside the <noscript> tag, and remove the <noscript> tag."""
        # Find img without source or attributes that might contains image, and remove it.
        for img in D.get_elements_by_tag_name(doc.soup, "img"):
            keep = False
            for name, value in D.attributes(img):
                if name in ("src", "srcset", "data-src", "data-srcset") or _IMG_FILE.search(value):
                    keep = True
                    break
            if not keep:
                D.remove(img)
        # Next find noscript and try to extract its image
        for noscript in D.get_elements_by_tag_name(doc.soup, "noscript"):
            if not self._isSingleImage(noscript):
                continue
            tmp = doc.create_element("div")
            D.set_inner_html(tmp, D.inner_html(noscript))
            # If noscript has previous sibling and it only contains image, replace it with noscript content. However we also keep old attributes that might contains image.
            prev = D.previous_element_sibling(noscript)
            if prev is not None and self._isSingleImage(prev):
                prev_img = prev
                if D.tag_name(prev_img) != "IMG":
                    prev_img = D.get_elements_by_tag_name(prev, "img")[0]
                new_img = D.get_elements_by_tag_name(tmp, "img")[0]
                for name, value in D.attributes(prev_img):
                    if value == "":
                        continue
                    if name in ("src", "srcset") or _IMG_FILE.search(value):
                        if D.get_attribute(new_img, name) == value:
                            continue
                        attr_name = name
                        if D.has_attribute(new_img, attr_name):
                            attr_name = "data-old-" + attr_name
                        D.set_attribute(new_img, attr_name, value)
                D.replace_child(noscript.parent, D.first_element_child(tmp), prev)

    def _removeScripts(self, doc) -> None:
        self._removeNodes(self._getAllNodesWithTag(doc, ["script", "noscript"]))

    # -- predicates -------------------------------------------------------------------------------------------------------------------------

    def _hasSingleTagInsideElement(self, element, tag: str) -> bool:
        # There should be exactly 1 element child with given tag
        kids = D.children(element)
        if len(kids) != 1 or D.tag_name(kids[0]) != tag:
            return False
        # And there should be no text nodes with real content
        return not any(D.node_type(n) == D.TEXT_NODE and REGEXPS["hasContent"].search(text_of(n)) for n in D.child_nodes(element))

    def _isElementWithoutContent(self, node) -> bool:
        kids = D.children(node)
        return (
            D.node_type(node) == D.ELEMENT_NODE
            and not len(js_trim(text_of(node)))
            and (not kids or len(kids) == len(D.get_elements_by_tag_name(node, "br")) + len(D.get_elements_by_tag_name(node, "hr")))
        )

    def _hasChildBlockElement(self, element) -> bool:
        return any(D.tag_name(n) in DIV_TO_P_ELEMS or self._hasChildBlockElement(n) for n in D.child_nodes(element))

    def _isPhrasingContent(self, node) -> bool:
        tag = D.tag_name(node)
        return D.node_type(node) == D.TEXT_NODE or tag in PHRASING_ELEMS or (tag in ("A", "DEL", "INS") and all(self._isPhrasingContent(n) for n in D.child_nodes(node)))

    def _isWhitespace(self, node) -> bool:
        return (D.node_type(node) == D.TEXT_NODE and len(js_trim(text_of(node))) == 0) or (D.node_type(node) == D.ELEMENT_NODE and D.tag_name(node) == "BR")

    # -- text and weights -------------------------------------------------------------------------------------------------------------------

    def _getInnerText(self, e, normalize_spaces: bool = True) -> str:
        text = js_trim(text_of(e))
        return REGEXPS["normalize"].sub(" ", text) if normalize_spaces else text

    def _getCharCount(self, e, s: str = ",") -> int:
        return len(self._getInnerText(e).split(s)) - 1

    def _cleanStyles(self, e) -> None:
        """Remove the style attribute on every e and under."""
        if e is None or D.tag_name(e).lower() == "svg":
            return
        for attr in PRESENTATIONAL_ATTRIBUTES:
            D.remove_attribute(e, attr)
        if D.tag_name(e) in DEPRECATED_SIZE_ATTRIBUTE_ELEMS:
            D.remove_attribute(e, "width")
            D.remove_attribute(e, "height")
        cur = D.first_element_child(e)
        while cur is not None:
            self._cleanStyles(cur)
            cur = D.next_element_sibling(cur)

    def _getLinkDensity(self, element) -> float:
        """Get the density of links as a percentage of the content. This is the amount of text that is inside a link divided by the total text in the node."""
        text_length = len(self._getInnerText(element))
        if text_length == 0:
            return 0
        link_length = 0
        for link in D.get_elements_by_tag_name(element, "a"):
            href = D.get_attribute(link, "href")
            coefficient = 0.3 if href and REGEXPS["hashUrl"].search(href) else 1
            link_length += len(self._getInnerText(link)) * coefficient
        return link_length / text_length

    def _getClassWeight(self, e) -> int:
        """Get an elements class/id weight. Uses regular expressions to tell if this element looks good or bad."""
        if not self._flagIsActive(FLAG_WEIGHT_CLASSES):
            return 0
        weight = 0
        # Look for a special classname
        cls = D.class_name(e)
        if cls != "":
            if REGEXPS["negative"].search(cls):
                weight -= 25
            if REGEXPS["positive"].search(cls):
                weight += 25
        # Look for a special ID
        ident = D.element_id(e)
        if ident != "":
            if REGEXPS["negative"].search(ident):
                weight -= 25
            if REGEXPS["positive"].search(ident):
                weight += 25
        return weight

    def _clean(self, e, tag: str) -> None:
        """Clean a node of all elements of type "tag". (Unless it's a youtube/vimeo video. People love movies.)"""
        is_embed = tag in ("object", "embed", "iframe")

        def keep_or_remove(element, i, nodes) -> bool:
            # Allow youtube and vimeo videos through as people usually want to see those.
            if is_embed:
                for _, value in D.attributes(element):
                    if self._allowedVideoRegex.search(value):
                        return False
                # For embed with <object> tag, check inner HTML as well. (tagName is upper case: this never matches, as in the original)
                if D.tag_name(element) == "object" and self._allowedVideoRegex.search(D.inner_html(element)):
                    return False
            return True

        self._removeNodes(self._getAllNodesWithTag(e, [tag]), keep_or_remove)

    def _hasAncestorTag(self, node, tag_name: str, max_depth: int = 0, filter_fn=None) -> bool:
        """Check if a given node has one of its ancestor tag name matching the provided one."""
        max_depth = max_depth or 3
        tag_name = tag_name.upper()
        depth = 0
        while node.parent is not None:
            if max_depth > 0 and depth > max_depth:
                return False
            if D.tag_name(node.parent) == tag_name and (not filter_fn or filter_fn(node.parent)):
                return True
            node = node.parent
            depth += 1
        return False

    def _getRowAndColumnCount(self, table) -> dict:
        """Return an object indicating how many rows and columns this table has."""
        rows = columns = 0
        for tr in D.get_elements_by_tag_name(table, "tr"):
            rowspan = D.get_attribute(tr, "rowspan") or 0
            if rowspan:
                rowspan = js_parse_int(rowspan)
            rows += rowspan if js_truthy(rowspan) and rowspan == rowspan else 1
            columns_in_row = 0
            for cell in D.get_elements_by_tag_name(tr, "td"):
                colspan = D.get_attribute(cell, "colspan") or 0
                if colspan:
                    colspan = js_parse_int(colspan)
                columns_in_row += colspan if js_truthy(colspan) and colspan == colspan else 1
            columns = max(columns, columns_in_row)
        return {"rows": rows, "columns": columns}

    def _markDataTables(self, root) -> None:
        """Look for 'data' (as opposed to 'layout') tables, for which we use similar checks as https://searchfox.org/mozilla-central/rev/f82d5c549f046cb64ce5602bfd894b7ae807c8f8/accessible/generic/TableAccessible.cpp#19"""
        for table in D.get_elements_by_tag_name(root, "table"):
            if D.get_attribute(table, "role") == "presentation":
                table._readabilityDataTable = False
                continue
            if D.get_attribute(table, "datatable") == "0":
                table._readabilityDataTable = False
                continue
            if D.get_attribute(table, "summary"):
                table._readabilityDataTable = True
                continue
            captions = D.get_elements_by_tag_name(table, "caption")
            if captions and len(D.child_nodes(captions[0])):
                table._readabilityDataTable = True
                continue
            # If the table has a descendant with any of these tags, consider a data table:
            if any(D.get_elements_by_tag_name(table, tag) for tag in ("col", "colgroup", "tfoot", "thead", "th")):
                table._readabilityDataTable = True
                continue
            # Nested tables indicate a layout table:
            if D.get_elements_by_tag_name(table, "table"):
                table._readabilityDataTable = False
                continue
            size = self._getRowAndColumnCount(table)
            if size["columns"] == 1 or size["rows"] == 1:
                # single column/row tables are commonly used for page layout purposes.
                table._readabilityDataTable = False
                continue
            if size["rows"] >= 10 or size["columns"] > 4:
                table._readabilityDataTable = True
                continue
            # Now just go by size entirely:
            table._readabilityDataTable = size["rows"] * size["columns"] > 10

    def _fixLazyImages(self, root) -> None:
        """Convert images and figures that have properties like data-src into images that can be loaded without JS."""
        for elem in self._getAllNodesWithTag(root, ["img", "picture", "figure"]):
            is_img = D.tag_name(elem) == "IMG"
            src = (D.get_attribute(elem, "src") or "") if is_img else None
            srcset = (D.get_attribute(elem, "srcset") or "") if is_img else None
            # In some sites (e.g. Kotaku), they put 1px square image as base64 data uri in the src attribute. So, here we check if the data uri is too short, just might as well remove it.
            if src and REGEXPS["b64DataUrl"].search(src):
                parts = REGEXPS["b64DataUrl"].search(src)
                # Make sure it's not SVG, because SVG can have a meaningful image in under 133 bytes.
                if parts.group(1) == "image/svg+xml":
                    continue
                # Make sure this element has other attributes which contains image. If it doesn't, then this src is important and shouldn't be removed.
                src_could_be_removed = False
                for name, value in D.attributes(elem):
                    if name == "src":
                        continue
                    if _IMG_FILE.search(value):
                        src_could_be_removed = True
                        break
                # Here we assume if image is less than 100 bytes (or 133B after encoded to base64) it will be too small, therefore it might be placeholder image.
                if src_could_be_removed:
                    b64_length = len(src) - len(parts.group(0))
                    if b64_length < 133:
                        D.remove_attribute(elem, "src")
                        src = ""
            # `elem.src` is read again: the attribute may be gone
            src_now = (D.get_attribute(elem, "src") or "") if is_img else None
            if (src_now or (srcset and srcset != "null")) and "lazy" not in D.class_name(elem).lower():
                continue
            for name, value in D.attributes(elem):
                if name in ("src", "srcset", "alt"):
                    continue
                copy_to = None
                if _LAZY_SRCSET.search(value):
                    copy_to = "srcset"
                elif _LAZY_SRC.search(value):
                    copy_to = "src"
                if copy_to:
                    if D.tag_name(elem) in ("IMG", "PICTURE"):
                        # if this is an img or picture, set the attribute directly
                        D.set_attribute(elem, copy_to, value)
                    elif D.tag_name(elem) == "FIGURE" and not self._getAllNodesWithTag(elem, ["img", "picture"]):
                        # if the item is a <figure> that does not contain an image or picture, create one and place it inside the figure
                        img = self._doc.create_element("img")
                        D.set_attribute(img, copy_to, value)
                        D.append_child(elem, img)

    def _getTextDensity(self, e, tags: list[str]) -> float:
        """Get the density of text as a percentage of the content: the total text inside the given tags divided by the total text in the node."""
        text_length = len(self._getInnerText(e, True))
        if text_length == 0:
            return 0
        children_length = sum(len(self._getInnerText(child, True)) for child in self._getAllNodesWithTag(e, tags))
        return children_length / text_length

    def _cleanConditionally(self, e, tag: str) -> None:
        """Clean an element of all tags of type "tag" if they look fishy. "Fishy" is an algorithm based on content length, classnames, link density, number of images & embeds, etc."""
        if not self._flagIsActive(FLAG_CLEAN_CONDITIONALLY):
            return

        def is_data_table(t) -> bool:
            return bool(t.__dict__.get("_readabilityDataTable"))

        def should_remove(node, i, nodes) -> bool:
            is_list = tag in ("ul", "ol")
            if not is_list:
                list_length = sum(len(self._getInnerText(lst)) for lst in self._getAllNodesWithTag(node, ["ul", "ol"]))
                is_list = js_div(list_length, len(self._getInnerText(node))) > 0.9
            # First check if this node IS data table, in which case don't remove it.
            if tag == "table" and is_data_table(node):
                return False
            # Next check if we're inside a data table, in which case don't remove it as well.
            if self._hasAncestorTag(node, "table", -1, is_data_table):
                return False
            if self._hasAncestorTag(node, "code"):
                return False
            # keep element if it has a data tables
            if any(is_data_table(t) for t in D.get_elements_by_tag_name(node, "table")):
                return False
            weight = self._getClassWeight(node)
            content_score = 0
            if weight + content_score < 0:
                return True
            if self._getCharCount(node, ",") < 10:
                # If there are not very many commas, and the number of non-paragraph elements is more than paragraphs or other ominous signs, remove the element.
                p = len(D.get_elements_by_tag_name(node, "p"))
                img = len(D.get_elements_by_tag_name(node, "img"))
                li = len(D.get_elements_by_tag_name(node, "li")) - 100
                input_count = len(D.get_elements_by_tag_name(node, "input"))
                heading_density = self._getTextDensity(node, ["h1", "h2", "h3", "h4", "h5", "h6"])
                embed_count = 0
                for embed in self._getAllNodesWithTag(node, ["object", "embed", "iframe"]):
                    # If this embed has attribute that matches video regex, don't delete it.
                    for _, value in D.attributes(embed):
                        if self._allowedVideoRegex.search(value):
                            return False
                    # For embed with <object> tag, check inner HTML as well. (never matches: tagName is upper case)
                    if D.tag_name(embed) == "object" and self._allowedVideoRegex.search(D.inner_html(embed)):
                        return False
                    embed_count += 1
                inner_text = self._getInnerText(node)
                # toss any node whose inner text contains nothing but suspicious words
                if REGEXPS["adWords"].search(inner_text) or REGEXPS["loadingWords"].search(inner_text):
                    return True
                content_length = len(inner_text)
                link_density = self._getLinkDensity(node)
                textish_tags = ["SPAN", "LI", "TD", *DIV_TO_P_ELEMS_ORDER]
                text_density = self._getTextDensity(node, textish_tags)
                is_figure_child = self._hasAncestorTag(node, "figure")

                def have_to_remove() -> bool:
                    errs = 0
                    if not is_figure_child and img > 1 and p / img < 0.5:
                        errs += 1
                    if not is_list and li > p:
                        errs += 1
                    if input_count > p // 3:
                        errs += 1
                    if not is_list and not is_figure_child and heading_density < 0.9 and content_length < 25 and (img == 0 or img > 2) and link_density > 0:
                        errs += 1
                    if not is_list and weight < 25 and link_density > 0.2 + self._linkDensityModifier:
                        errs += 1
                    if weight >= 25 and link_density > 0.5 + self._linkDensityModifier:
                        errs += 1
                    if (embed_count == 1 and content_length < 75) or embed_count > 1:
                        errs += 1
                    if img == 0 and text_density == 0:
                        errs += 1
                    return errs > 0

                remove = have_to_remove()
                # Allow simple lists of images to remain in pages
                if is_list and remove:
                    for child in D.children(node):
                        # Don't filter in lists with li's that contain more than one child
                        if len(D.children(child)) > 1:
                            return remove
                    li_count = len(D.get_elements_by_tag_name(node, "li"))
                    # Only allow the list to remain if every li contains an image
                    if img == li_count:
                        return False
                return remove
            return False

        self._removeNodes(self._getAllNodesWithTag(e, [tag]), should_remove)

    def _cleanMatchedNodes(self, e, filter_fn) -> None:
        """Clean out elements whose id/class combinations match specific string."""
        end_marker = self._getNextNode(e, True)
        nxt = self._getNextNode(e)
        while nxt is not None and nxt is not end_marker:
            if filter_fn(nxt, D.class_name(nxt) + " " + D.element_id(nxt)):
                nxt = self._removeAndGetNext(nxt)
            else:
                nxt = self._getNextNode(nxt)

    def _cleanHeaders(self, e) -> None:
        """Clean out spurious headers from an Element."""
        self._removeNodes(self._getAllNodesWithTag(e, ["h1", "h2"]), lambda node, i, nodes: self._getClassWeight(node) < 0)

    def _headerDuplicatesTitle(self, node) -> bool:
        """Check if this node is an H1 or H2 element whose content is mostly the same as the article title."""
        if D.tag_name(node) not in ("H1", "H2"):
            return False
        heading = self._getInnerText(node, False)
        return self._textSimilarity(self._articleTitle, heading) > 0.75

    def _flagIsActive(self, flag: int) -> bool:
        return (self._flags & flag) > 0

    def _removeFlag(self, flag: int) -> None:
        self._flags = self._flags & ~flag

    def _isProbablyVisible(self, node) -> bool:
        """Determine whether element has any of its ancestors... (here: the element itself) hidden."""
        style = D.style_of(node)
        cls = D.class_name(node)
        return (
            style.get("display", "") != "none"
            and style.get("visibility", "") != "hidden"
            and not D.has_attribute(node, "hidden")
            and (not D.has_attribute(node, "aria-hidden") or D.get_attribute(node, "aria-hidden") != "true" or bool(cls and "fallback-image" in cls))
        )

    # -- entry point ------------------------------------------------------------------------------------------------------------------------

    def parse(self) -> dict | None:
        """Runs readability. Workflow: 1. Prep the document by removing script tags, css, etc. 2. Build readability's DOM tree. 3. Grab the article content from the current dom tree. 4. Replace the current DOM tree with the new one. 5. Read peripheral information from the dom tree. 6. Return the article."""
        # Avoid parsing too large documents, as per configuration option (not used here)
        self._unwrapNoscriptImages(self._doc)
        # Extract JSON-LD metadata before removing scripts
        json_ld = self._getJSONLD(self._doc)
        # Remove script tags from the document.
        self._removeScripts(self._doc)
        self._prepDocument()
        metadata = self._getArticleMetadata(json_ld)
        self._metadata = metadata
        self._articleTitle = metadata["title"]
        article_content = self._grabArticle()
        if article_content is None:
            return None
        self._postProcessContent(article_content)
        # If we haven't found an excerpt in the article's metadata, use the article's first paragraph as the excerpt. This is used for displaying a preview of the article's content.
        if not metadata.get("excerpt"):
            paragraphs = D.get_elements_by_tag_name(article_content, "p")
            if paragraphs:
                metadata["excerpt"] = js_trim(text_of(paragraphs[0]))
        text = text_of(article_content)
        return {
            "title": self._articleTitle,
            "byline": metadata.get("byline") or self._articleByline,
            "dir": self._articleDir,
            "lang": getattr(self, "_articleLang", None),
            "content": D.inner_html(article_content),
            "textContent": text,
            "length": len(text),
            "excerpt": metadata.get("excerpt"),
            "siteName": metadata.get("siteName") or self._articleSiteName,
            "publishedTime": metadata.get("publishedTime"),
        }


DIV_TO_P_ELEMS_ORDER = ["BLOCKQUOTE", "DL", "DIV", "IMG", "OL", "P", "PRE", "TABLE", "UL"]


class Readability(ReadabilityRest):
    pass


__all__ = ["Readability", "DEFAULT_CHAR_THRESHOLD", "Tag"]

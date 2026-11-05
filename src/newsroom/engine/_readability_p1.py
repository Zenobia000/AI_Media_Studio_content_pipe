"""Mozilla Readability 0.6.0, ported method for method. Port of @mozilla/readability, on the DOM of engine/dom.py (linkedom's behaviour)."""

from __future__ import annotations

import re

from newsroom.engine import dom as D
from newsroom.engine.dom import El

# JavaScript's \s: the code points String.prototype.trim and /\s/ know, not Python's.
JS_WS_CHARS = "\t\n\v\f\r    -     　﻿"
_WS_INNER = JS_WS_CHARS
_NOT_WS_INNER = None


def jsre(pattern: str, flags: int = 0) -> re.Pattern[str]:
    """A JavaScript pattern (no `u` flag) as a Python one: \\s and \\S as JavaScript means them, \\w \\d \\b in ASCII, $ at the very end only."""
    out: list[str] = []
    i, in_class = 0, False
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            nxt = pattern[i + 1]
            if nxt == "s":
                out.append(_WS_INNER if in_class else f"[{_WS_INNER}]")
            elif nxt == "S":
                out.append(f"[^{_WS_INNER}]" if not in_class else r"\S")  # \S inside a class is not used by the patterns ported
            else:
                out.append(ch + nxt)
            i += 2
            continue
        if ch == "[" and not in_class:
            in_class = True
        elif ch == "]" and in_class:
            in_class = False
        if ch == "$" and not in_class:
            out.append(r"\Z")
        else:
            out.append(ch)
        i += 1
    return re.compile("".join(out), flags | re.A)


_STRIP = "".join(chr(c) for c in [9, 10, 11, 12, 13, 32, 0xA0, 0x1680, *range(0x2000, 0x200B), 0x2028, 0x2029, 0x202F, 0x205F, 0x3000, 0xFEFF])


def js_trim(s: str) -> str:
    return s.strip(_STRIP)


def js_split_ws(s: str) -> list[str]:
    return jsre(r"\s+").split(s)


FLAG_STRIP_UNLIKELYS = 0x1
FLAG_WEIGHT_CLASSES = 0x2
FLAG_CLEAN_CONDITIONALLY = 0x4

DEFAULT_N_TOP_CANDIDATES = 5
DEFAULT_TAGS_TO_SCORE = ["SECTION", "H2", "H3", "H4", "H5", "H6", "P", "TD", "PRE"]
DEFAULT_CHAR_THRESHOLD = 500

REGEXPS = {
    "unlikelyCandidates": jsre(r"-ad-|ai2html|banner|breadcrumbs|combx|comment|community|cover-wrap|disqus|extra|footer|gdpr|header|legends|menu|related|remark|replies|rss|shoutbox|sidebar|skyscraper|social|sponsor|supplemental|ad-break|agegate|pagination|pager|popup|yom-remote", re.I),
    "okMaybeItsACandidate": jsre(r"and|article|body|column|content|main|shadow", re.I),
    "positive": jsre(r"article|body|content|entry|hentry|h-entry|main|page|pagination|post|text|blog|story", re.I),
    "negative": jsre(r"-ad-|hidden|^hid$| hid$| hid |^hid |banner|combx|comment|com-|contact|footer|gdpr|masthead|media|meta|outbrain|promo|related|scroll|share|shoutbox|sidebar|skyscraper|sponsor|shopping|tags|widget", re.I),
    "extraneous": jsre(r"print|archive|comment|discuss|e[\-]?mail|share|reply|all|login|sign|single|utility", re.I),
    "byline": jsre(r"byline|author|dateline|writtenby|p-author", re.I),
    "normalize": jsre(r"\s{2,}"),
    "videos": jsre(r"\/\/(www\.)?((dailymotion|youtube|youtube-nocookie|player\.vimeo|v\.qq)\.com|(archive|upload\.wikimedia)\.org|player\.twitch\.tv)", re.I),
    "shareElements": jsre(r"(\b|_)(share|sharedaddy)(\b|_)", re.I),
    "nextLink": jsre(r"(next|weiter|continue|>([^\|]|$)|»([^\|]|$))", re.I),
    "prevLink": jsre(r"(prev|earl|old|new|<|«)", re.I),
    "tokenize": jsre(r"\W+"),
    "whitespace": jsre(r"^\s*$"),
    "hasContent": jsre(r"\S$"),
    "hashUrl": jsre(r"^#.+"),
    "srcsetUrl": jsre(r"(\S+)(\s+[\d.]+[xw])?(\s*(?:,|$))"),
    "b64DataUrl": jsre(r"^data:\s*([^\s;,]+)\s*;\s*base64\s*,", re.I),
    "commas": re.compile(",|،|﹐|︐|︑|⹁|⸴|⸲|，"),
    "jsonLdArticleTypes": jsre(r"^Article|AdvertiserContentArticle|NewsArticle|AnalysisNewsArticle|AskPublicNewsArticle|BackgroundNewsArticle|OpinionNewsArticle|ReportageNewsArticle|ReviewNewsArticle|Report|SatiricalArticle|ScholarlyArticle|MedicalScholarlyArticle|SocialMediaPosting|BlogPosting|LiveBlogPosting|DiscussionForumPosting|TechArticle|APIReference$"),
    "adWords": re.compile(r"^(ad(vertising|vertisement)?|pub(licité)?|werb(ung)?|广告|Реклама|Anuncio)\Z", re.I),
    "loadingWords": re.compile(r"^((loading|正在加载|Загрузка|chargement|cargando)(…|\.\.\.)?)\Z", re.I),
}  # fmt: skip

UNLIKELY_ROLES = ["menu", "menubar", "complementary", "navigation", "alert", "alertdialog", "dialog"]
DIV_TO_P_ELEMS = {"BLOCKQUOTE", "DL", "DIV", "IMG", "OL", "P", "PRE", "TABLE", "UL"}
ALTER_TO_DIV_EXCEPTIONS = ["DIV", "ARTICLE", "SECTION", "P", "OL", "UL"]
PRESENTATIONAL_ATTRIBUTES = [
    "align",
    "background",
    "bgcolor",
    "border",
    "cellpadding",
    "cellspacing",
    "frame",
    "hspace",
    "rules",
    "style",
    "valign",
    "vspace",
]
DEPRECATED_SIZE_ATTRIBUTE_ELEMS = ["TABLE", "TH", "TD", "HR", "PRE"]
PHRASING_ELEMS = [
    "ABBR", "AUDIO", "B", "BDO", "BR", "BUTTON", "CITE", "CODE", "DATA", "DATALIST", "DFN", "EM", "EMBED", "I", "IMG", "INPUT", "KBD", "LABEL", "MARK", "MATH",
    "METER", "NOSCRIPT", "OBJECT", "OUTPUT", "PROGRESS", "Q", "RUBY", "SAMP", "SCRIPT", "SELECT", "SMALL", "SPAN", "STRONG", "SUB", "SUP", "TEXTAREA", "TIME",
    "VAR", "WBR",
]  # fmt: skip
CLASSES_TO_PRESERVE = ["page"]
HTML_ESCAPE_MAP = {"lt": "<", "gt": ">", "amp": "&", "quot": '"', "apos": "'"}


def text_of(n) -> str:
    return D.text_content(n)


class ReadabilityBase:
    def __init__(self, doc: D.Document, *, char_threshold: int = 0, keep_classes: bool = False, nb_top_candidates: int = 0) -> None:
        self._doc = doc
        self._articleTitle = None
        self._articleByline = None
        self._articleDir = None
        self._articleSiteName = None
        self._attempts: list[dict] = []
        self._metadata: dict = {}
        self._nbTopCandidates = nb_top_candidates or DEFAULT_N_TOP_CANDIDATES
        self._charThreshold = char_threshold or DEFAULT_CHAR_THRESHOLD
        self._classesToPreserve = CLASSES_TO_PRESERVE
        self._keepClasses = keep_classes
        self._allowedVideoRegex = REGEXPS["videos"]
        self._linkDensityModifier = 0
        self._flags = FLAG_STRIP_UNLIKELYS | FLAG_WEIGHT_CLASSES | FLAG_CLEAN_CONDITIONALLY

    # -- small tree helpers -----------------------------------------------------------------------------------------------------------------

    def _removeNodes(self, nodes, filter_fn=None) -> None:
        for i in range(len(nodes) - 1, -1, -1):
            node = nodes[i]
            parent = node.parent
            if parent is not None and (not filter_fn or filter_fn(node, i, nodes)):
                node.extract()

    def _replaceNodeTags(self, nodes, new_tag: str) -> None:
        for node in nodes:
            self._setNodeTag(node, new_tag)

    def _getAllNodesWithTag(self, node, tag_names: list[str]) -> list[El]:
        # Readability asks the DOM's querySelectorAll, which (unlike getElementsByTagName) does not go into a <template>'s content: a <style> inside a shadow
        # root the page declares stays in the document, and so does the text it adds to its ancestors.
        want = {t.upper() for t in tag_names}
        root = node.soup if isinstance(node, D.Document) else node
        found: list[El] = []
        stack = [iter(D.child_nodes(root))]
        while stack:
            for child in stack[-1]:
                if not D.is_element(child):
                    continue
                if D.tag_name(child) in want:
                    found.append(child)
                if D.tag_name(child) != "TEMPLATE":
                    stack.append(iter(D.child_nodes(child)))
                    break
            else:
                stack.pop()
        return found

    def _postProcessContent(self, article_content) -> None:
        self._fixRelativeUris(article_content)
        self._simplifyNestedElements(article_content)
        if not self._keepClasses:
            self._cleanClasses(article_content)

    def _cleanClasses(self, node) -> None:
        keep = " ".join(c for c in jsre(r"\s+").split(D.get_attribute(node, "class") or "") if c in self._classesToPreserve)
        if keep:
            D.set_attribute(node, "class", keep)
        else:
            D.remove_attribute(node, "class")
        child = D.first_element_child(node)
        while child is not None:
            self._cleanClasses(child)
            child = D.next_element_sibling(child)

    def _isUrl(self, s: str) -> bool:
        try:
            D.new_url_href(s, "")
            return True
        except ValueError:
            return False

    def _fixRelativeUris(self, article_content) -> None:
        base_uri = self._doc.base_uri_value()
        document_uri = None  # linkedom has no documentURI: it is never equal to the base

        def to_absolute(uri: str) -> str:
            if base_uri == document_uri and uri[:1] == "#":
                return uri
            try:
                return D.new_url_href(uri, base_uri or "")
            except ValueError:
                return uri

        for link in self._getAllNodesWithTag(article_content, ["a"]):
            href = D.get_attribute(link, "href")
            if href:
                if href.startswith("javascript:"):
                    kids = D.child_nodes(link)
                    if len(kids) == 1 and D.node_type(kids[0]) == D.TEXT_NODE:
                        text = self._doc.create_text_node(text_of(link))
                        D.replace_child(link.parent, text, link)
                    else:
                        container = self._doc.create_element("span")
                        while D.first_child(link) is not None:
                            D.append_child(container, D.first_child(link))
                        D.replace_child(link.parent, container, link)
                else:
                    D.set_attribute(link, "href", to_absolute(href))

        for media in self._getAllNodesWithTag(article_content, ["img", "picture", "figure", "video", "audio", "source"]):
            src, poster, srcset = D.get_attribute(media, "src"), D.get_attribute(media, "poster"), D.get_attribute(media, "srcset")
            if src:
                D.set_attribute(media, "src", to_absolute(src))
            if poster:
                D.set_attribute(media, "poster", to_absolute(poster))
            if srcset:
                D.set_attribute(
                    media, "srcset", REGEXPS["srcsetUrl"].sub(lambda m: to_absolute(m.group(1)) + (m.group(2) or "") + m.group(3), srcset)
                )

    def _simplifyNestedElements(self, article_content) -> None:
        node = article_content
        while node is not None:
            if (
                node.parent is not None
                and D.tag_name(node) in ("DIV", "SECTION")
                and not ((D.element_id(node)) and D.element_id(node).startswith("readability"))
            ):
                if self._isElementWithoutContent(node):
                    node = self._removeAndGetNext(node)
                    continue
                if self._hasSingleTagInsideElement(node, "DIV") or self._hasSingleTagInsideElement(node, "SECTION"):
                    child = D.children(node)[0]
                    for k, v in D.attributes(node):
                        D.set_attribute_node(child, k, v)
                    D.replace_child(node.parent, child, node)
                    node = child
                    continue
            node = self._getNextNode(node)

    def _getArticleTitle(self) -> str:
        doc = self._doc
        cur_title = orig_title = ""
        try:
            cur_title = orig_title = js_trim(doc.title)
        except Exception:
            pass
        had_hierarchical = False

        def word_count(s: str) -> int:
            return len(js_split_ws(s))

        if re.search(r" [\|\-\\/>»] ", cur_title):
            had_hierarchical = re.search(r" [\\/>»] ", cur_title) is not None
            seps = list(re.finditer(r" [\|\-\\/>»] ", orig_title, re.I))
            cur_title = orig_title[: seps.pop().start()]
            if word_count(cur_title) < 3:
                cur_title = re.sub(r"^[^\|\-\\/>»]*[\|\-\\/>»]", "", orig_title, count=1, flags=re.I)
        elif ": " in cur_title:
            headings = self._getAllNodesWithTag(doc, ["h1", "h2"])
            trimmed = js_trim(cur_title)
            if not any(js_trim(text_of(h)) == trimmed for h in headings):
                cur_title = orig_title[orig_title.rfind(":") + 1 :]
                if word_count(cur_title) < 3:
                    cur_title = orig_title[orig_title.find(":") + 1 :]
                elif word_count(orig_title[: orig_title.find(":")]) > 5:
                    cur_title = orig_title
        elif len(cur_title) > 150 or len(cur_title) < 15:
            h_ones = D.get_elements_by_tag_name(doc.soup, "h1")
            if len(h_ones) == 1:
                cur_title = self._getInnerText(h_ones[0])

        cur_title = REGEXPS["normalize"].sub(" ", js_trim(cur_title))
        count = word_count(cur_title)
        if count <= 4 and (not had_hierarchical or count != word_count(re.sub(r"[\|\-\\/>»]+", "", orig_title)) - 1):
            cur_title = orig_title
        return cur_title

    def _prepDocument(self) -> None:
        doc = self._doc
        self._removeNodes(self._getAllNodesWithTag(doc, ["style"]))
        if doc.body is not None:
            self._replaceBrs(doc.body)
        self._replaceNodeTags(self._getAllNodesWithTag(doc, ["font"]), "SPAN")

    def _nextNode(self, node):
        """Finds the next node, starting from the given one, and ignoring whitespace in between."""
        nxt = node
        while nxt is not None and D.node_type(nxt) != D.ELEMENT_NODE and REGEXPS["whitespace"].search(text_of(nxt)):
            nxt = nxt.next_sibling
        return nxt

    def _replaceBrs(self, elem) -> None:
        for br in self._getAllNodesWithTag(elem, ["br"]):
            nxt = br.next_sibling
            replaced = False
            # Whether 2 or more <br> elements are in a row, ignoring whitespace in between, they become one paragraph.
            while True:
                nxt = self._nextNode(nxt)
                if not (nxt is not None and D.tag_name(nxt) == "BR"):
                    break
                replaced = True
                br_sibling = nxt.next_sibling
                D.remove(nxt)
                nxt = br_sibling
            if replaced:
                p = self._doc.create_element("p")
                D.replace_child(br.parent, p, br)
                nxt = p.next_sibling
                while nxt is not None:
                    # If we've hit another <br><br>, we're done adding children to this <p>.
                    if D.tag_name(nxt) == "BR":
                        next_elem = self._nextNode(nxt.next_sibling)
                        if next_elem is not None and D.tag_name(next_elem) == "BR":
                            break
                    if not self._isPhrasingContent(nxt):
                        break
                    sibling = nxt.next_sibling
                    D.append_child(p, nxt)
                    nxt = sibling
                while D.last_child(p) is not None and self._isWhitespace(D.last_child(p)):
                    D.remove(D.last_child(p))
                if D.tag_name(p.parent) == "P":
                    self._setNodeTag(p.parent, "DIV")

    def _setNodeTag(self, node, tag: str):
        replacement = self._doc.create_element(tag)
        if node.__dict__.get("svg"):
            replacement.svg = False
        while D.first_child(node) is not None:
            D.append_child(replacement, D.first_child(node))
        D.replace_child(node.parent, replacement, node)
        if node.__dict__.get("readability"):
            replacement.readability = node.readability
        for k, v in D.attributes(node):
            D.set_attribute_node(replacement, k, v)
        return replacement

    def _prepArticle(self, article_content) -> None:
        self._cleanStyles(article_content)
        # Check for data tables before we continue, to avoid removing items in those tables, which will often be isolated even though they're visually linked to other content-ful elements (text, images, etc.).
        self._markDataTables(article_content)
        self._fixLazyImages(article_content)
        # Clean out junk from the article content
        self._cleanConditionally(article_content, "form")
        self._cleanConditionally(article_content, "fieldset")
        self._clean(article_content, "object")
        self._clean(article_content, "embed")
        self._clean(article_content, "footer")
        self._clean(article_content, "link")
        self._clean(article_content, "aside")
        # Clean out elements with little content that have "share" in their id/class combinations from final top candidates.
        share_threshold = DEFAULT_CHAR_THRESHOLD
        for top in D.children(article_content):
            self._cleanMatchedNodes(
                top, lambda node, match: REGEXPS["shareElements"].search(match) is not None and len(text_of(node)) < share_threshold
            )
        self._clean(article_content, "iframe")
        self._clean(article_content, "input")
        self._clean(article_content, "textarea")
        self._clean(article_content, "select")
        self._clean(article_content, "button")
        self._cleanHeaders(article_content)
        # Do these last as the previous stuff may have removed junk that will affect these
        self._cleanConditionally(article_content, "table")
        self._cleanConditionally(article_content, "ul")
        self._cleanConditionally(article_content, "div")
        # replace H1 with H2 as H1 should be only title that is displayed separately
        self._replaceNodeTags(self._getAllNodesWithTag(article_content, ["h1"]), "h2")
        # Remove extra paragraphs
        self._removeNodes(
            self._getAllNodesWithTag(article_content, ["p"]),
            lambda paragraph, i, nodes: (
                len(self._getAllNodesWithTag(paragraph, ["img", "embed", "object", "iframe"])) == 0 and not self._getInnerText(paragraph, False)
            ),
        )
        for br in self._getAllNodesWithTag(article_content, ["br"]):
            nxt = self._nextNode(br.next_sibling)
            if nxt is not None and D.tag_name(nxt) == "P":
                D.remove(br)
        # Remove single-cell tables
        for table in self._getAllNodesWithTag(article_content, ["table"]):
            tbody = D.first_element_child(table) if self._hasSingleTagInsideElement(table, "TBODY") else table
            if self._hasSingleTagInsideElement(tbody, "TR"):
                row = D.first_element_child(tbody)
                if self._hasSingleTagInsideElement(row, "TD"):
                    cell = D.first_element_child(row)
                    cell = self._setNodeTag(cell, "P" if all(self._isPhrasingContent(n) for n in D.child_nodes(cell)) else "DIV")
                    D.replace_child(table.parent, cell, table)

    def _initializeNode(self, node) -> None:
        node.readability = {"contentScore": 0}
        score = 0
        tag = D.tag_name(node)
        if tag == "DIV":
            score += 5
        elif tag in ("PRE", "TD", "BLOCKQUOTE"):
            score += 3
        elif tag in ("ADDRESS", "OL", "UL", "DL", "DD", "DT", "LI", "FORM"):
            score -= 3
        elif tag in ("H1", "H2", "H3", "H4", "H5", "H6", "TH"):
            score -= 5
        node.readability["contentScore"] = score + self._getClassWeight(node)

    def _removeAndGetNext(self, node):
        nxt = self._getNextNode(node, True)
        D.remove(node)
        return nxt

    def _getNextNode(self, node, ignore_self_and_kids: bool = False):
        """Traverse the DOM from node to node, starting at the node passed in."""
        if not ignore_self_and_kids and D.first_element_child(node) is not None:
            return D.first_element_child(node)
        if D.next_element_sibling(node) is not None:
            return D.next_element_sibling(node)
        while True:
            node = node.parent
            if not (node is not None and D.next_element_sibling(node) is None):
                break
        return D.next_element_sibling(node) if node is not None else None

    def _textSimilarity(self, text_a: str, text_b: str) -> float:
        tokens_a = [t for t in REGEXPS["tokenize"].split(text_a.lower()) if t]
        tokens_b = [t for t in REGEXPS["tokenize"].split(text_b.lower()) if t]
        if not tokens_a or not tokens_b:
            return 0
        uniq_b = [t for t in tokens_b if t not in tokens_a]
        distance_b = len(" ".join(uniq_b)) / len(" ".join(tokens_b))
        return 1 - distance_b

    def _isValidByline(self, node, match_string: str) -> bool:
        rel = D.get_attribute(node, "rel")
        itemprop = D.get_attribute(node, "itemprop")
        byline_length = len(js_trim(text_of(node)))
        return (
            (rel == "author" or bool(itemprop and "author" in itemprop) or REGEXPS["byline"].search(match_string) is not None)
            and bool(byline_length)
            and byline_length < 100
        )

    def _getNodeAncestors(self, node, max_depth: int = 0) -> list:
        i, ancestors = 0, []
        while node.parent is not None:
            ancestors.append(node.parent)
            i += 1
            if max_depth and i == max_depth:
                break
            node = node.parent
        return ancestors

"""Readability: _grabArticle (part 2)."""

from __future__ import annotations

from newsroom.engine import dom as D
from newsroom.engine._readability_p1 import (
    ALTER_TO_DIV_EXCEPTIONS,
    DEFAULT_TAGS_TO_SCORE,
    FLAG_CLEAN_CONDITIONALLY,
    FLAG_STRIP_UNLIKELYS,
    FLAG_WEIGHT_CLASSES,
    REGEXPS,
    UNLIKELY_ROLES,
    ReadabilityBase,
    js_trim,
    jsre,
    text_of,
)

_SENTENCE_END = jsre(r"\.( |$)")


class ReadabilityGrab(ReadabilityBase):
    def _grabArticle(self, page=None):
        """Using a variety of metrics (content score, classname, element types), find the content that is most likely to be the stuff a user wants to read."""
        doc = self._doc
        is_paging = page is not None
        page = page if page is not None else doc.body
        if page is None:
            return None
        page_cache_html = D.inner_html(page)
        while True:
            strip_unlikely = self._flagIsActive(FLAG_STRIP_UNLIKELYS)
            elements_to_score: list = []
            node = doc.document_element
            should_remove_title_header = True
            while node is not None:
                if D.tag_name(node) == "HTML":
                    self._articleLang = D.get_attribute(node, "lang")
                match_string = D.class_name(node) + " " + D.element_id(node)
                if not self._isProbablyVisible(node):
                    node = self._removeAndGetNext(node)
                    continue
                # User is not able to see elements applied with both "aria-modal = true" and "role = dialog"
                if D.get_attribute(node, "aria-modal") == "true" and D.get_attribute(node, "role") == "dialog":
                    node = self._removeAndGetNext(node)
                    continue
                # If we don't have a byline yet check to see if this node is a byline; if it is store the byline and remove the node.
                if not self._articleByline and not self._metadata.get("byline") and self._isValidByline(node, match_string):
                    # Find child node matching [itemprop="name"] and use that if it exists for a more accurate author name byline
                    end_marker = self._getNextNode(node, True)
                    nxt = self._getNextNode(node)
                    item_prop_name_node = None
                    while nxt is not None and nxt is not end_marker:
                        itemprop = D.get_attribute(nxt, "itemprop")
                        if itemprop and "name" in itemprop:
                            item_prop_name_node = nxt
                            break
                        nxt = self._getNextNode(nxt)
                    self._articleByline = js_trim(text_of(item_prop_name_node if item_prop_name_node is not None else node))
                    node = self._removeAndGetNext(node)
                    continue
                if should_remove_title_header and self._headerDuplicatesTitle(node):
                    should_remove_title_header = False
                    node = self._removeAndGetNext(node)
                    continue
                # Remove unlikely candidates
                if strip_unlikely:
                    if (
                        REGEXPS["unlikelyCandidates"].search(match_string)
                        and not REGEXPS["okMaybeItsACandidate"].search(match_string)
                        and not self._hasAncestorTag(node, "table")
                        and not self._hasAncestorTag(node, "code")
                        and D.tag_name(node) != "BODY"
                        and D.tag_name(node) != "A"
                    ):
                        node = self._removeAndGetNext(node)
                        continue
                    if D.get_attribute(node, "role") in UNLIKELY_ROLES:
                        node = self._removeAndGetNext(node)
                        continue
                # Remove DIV, SECTION, and HEADER nodes without any content(e.g. text, image, video, or iframe).
                if D.tag_name(node) in ("DIV", "SECTION", "HEADER", "H1", "H2", "H3", "H4", "H5", "H6") and self._isElementWithoutContent(node):
                    node = self._removeAndGetNext(node)
                    continue
                if D.tag_name(node) in DEFAULT_TAGS_TO_SCORE:
                    elements_to_score.append(node)
                # Turn all divs that don't have children block level elements into p's
                if D.tag_name(node) == "DIV":
                    # Put phrasing content into paragraphs.
                    p = None
                    child = D.first_child(node)
                    while child is not None:
                        next_sibling = child.next_sibling
                        if self._isPhrasingContent(child):
                            if p is not None:
                                D.append_child(p, child)
                            elif not self._isWhitespace(child):
                                p = doc.create_element("p")
                                D.replace_child(node, p, child)
                                D.append_child(p, child)
                        elif p is not None:
                            while D.last_child(p) is not None and self._isWhitespace(D.last_child(p)):
                                D.remove(D.last_child(p))
                            p = None
                        child = next_sibling
                    # Sites like http://mobile.slate.com encloses each paragraph with a DIV element. DIVs with only a P element inside and no text content can be safely converted into plain P elements to avoid confusing the scoring algorithm with DIVs with are, in practice, paragraphs.
                    if self._hasSingleTagInsideElement(node, "P") and self._getLinkDensity(node) < 0.25:
                        new_node = D.children(node)[0]
                        D.replace_child(node.parent, new_node, node)
                        node = new_node
                        elements_to_score.append(node)
                    elif not self._hasChildBlockElement(node):
                        node = self._setNodeTag(node, "P")
                        elements_to_score.append(node)
                node = self._getNextNode(node)

            # Loop through all paragraphs, and assign a score to them based on how content-y they look. Then add their score to their parent node.
            candidates: list = []
            for element_to_score in elements_to_score:
                parent = element_to_score.parent
                if parent is None or D.tag_name(parent) == "":
                    continue
                # If this paragraph is less than 25 characters, don't even count it.
                inner_text = self._getInnerText(element_to_score)
                if len(inner_text) < 25:
                    continue
                # Exclude nodes with no ancestor.
                ancestors = self._getNodeAncestors(element_to_score, 5)
                if len(ancestors) == 0:
                    continue
                content_score = 0
                content_score += 1  # Add a point for the paragraph itself as a base.
                content_score += len(REGEXPS["commas"].split(inner_text))  # Add points for any commas within this paragraph.
                content_score += min(len(inner_text) // 100, 3)  # For every 100 characters in this paragraph, add another point. Up to 3 points.
                # Initialize and score ancestors.
                for level, ancestor in enumerate(ancestors):
                    if not D.tag_name(ancestor) or ancestor.parent is None or D.tag_name(ancestor.parent) == "":
                        continue
                    if ancestor.__dict__.get("readability") is None:
                        self._initializeNode(ancestor)
                        candidates.append(ancestor)
                    # Node score divider: parent: 1 (no division); grandparent: 2; great grandparent+: ancestor level * 3
                    score_divider = 1 if level == 0 else 2 if level == 1 else level * 3
                    ancestor.readability["contentScore"] += content_score / score_divider

            # After we've calculated scores, loop through all of the possible candidate nodes we found and find the one with the highest score.
            top_candidates: list = []
            for candidate in candidates:
                # Scale the final candidates score based on link density. Good content should have a relatively small link density (5% or less) and be mostly unaffected by this operation.
                candidate_score = candidate.readability["contentScore"] * (1 - self._getLinkDensity(candidate))
                candidate.readability["contentScore"] = candidate_score
                for t in range(self._nbTopCandidates):
                    a_top = top_candidates[t] if t < len(top_candidates) else None
                    if a_top is None or candidate_score > a_top.readability["contentScore"]:
                        top_candidates.insert(t, candidate)
                        if len(top_candidates) > self._nbTopCandidates:
                            top_candidates.pop()
                        break

            top_candidate = top_candidates[0] if top_candidates else None
            needed_to_create_top_candidate = False
            if top_candidate is None or D.tag_name(top_candidate) == "BODY":
                # If we still have no top candidate, just use the body as a last resort. We also have to copy the body node so it is something we can modify.
                top_candidate = doc.create_element("DIV")
                needed_to_create_top_candidate = True
                # Move all of the page's children into topCandidate
                while D.first_child(page) is not None:
                    D.append_child(top_candidate, D.first_child(page))
                D.append_child(page, top_candidate)
                self._initializeNode(top_candidate)
            else:
                # Find a better top candidate node if it contains (at least three) nodes which belong to `topCandidates` array and whose scores are quite closed with current `topCandidate` node.
                alternative_ancestors: list = []
                for i in range(1, len(top_candidates)):
                    if top_candidates[i].readability["contentScore"] / top_candidate.readability["contentScore"] >= 0.75:
                        alternative_ancestors.append(self._getNodeAncestors(top_candidates[i]))
                MINIMUM_TOPCANDIDATES = 3
                if len(alternative_ancestors) >= MINIMUM_TOPCANDIDATES:
                    parent_of_top = top_candidate.parent
                    while D.tag_name(parent_of_top) != "BODY":
                        lists_containing = 0
                        idx = 0
                        while idx < len(alternative_ancestors) and lists_containing < MINIMUM_TOPCANDIDATES:
                            lists_containing += 1 if any(a is parent_of_top for a in alternative_ancestors[idx]) else 0
                            idx += 1
                        if lists_containing >= MINIMUM_TOPCANDIDATES:
                            top_candidate = parent_of_top
                            break
                        parent_of_top = parent_of_top.parent
                if not top_candidate.__dict__.get("readability"):
                    self._initializeNode(top_candidate)

                # Because of our bonus system, parents of candidates might have scores themselves. They get half of the node. There won't be nodes with higher scores than our topCandidate, but if we see the score going *up* in the first few steps up the tree, that's a decent sign that there might be more content lurking in other places that we want to unify in. The sibling stuff below does some of that - but only if we've looked high enough up the DOM tree.
                parent_of_top = top_candidate.parent
                last_score = top_candidate.readability["contentScore"]
                # The scores shouldn't get too low.
                score_threshold = last_score / 3
                while D.tag_name(parent_of_top) != "BODY":
                    if not parent_of_top.__dict__.get("readability"):
                        parent_of_top = parent_of_top.parent
                        continue
                    parent_score = parent_of_top.readability["contentScore"]
                    if parent_score < score_threshold:
                        break
                    if parent_score > last_score:
                        # Alright! We found a better parent to use.
                        top_candidate = parent_of_top
                        break
                    last_score = parent_of_top.readability["contentScore"]
                    parent_of_top = parent_of_top.parent

                # If the top candidate is the only child, use parent instead. This will help sibling joining logic when adjacent content is actually located in parent's sibling node.
                parent_of_top = top_candidate.parent
                while D.tag_name(parent_of_top) != "BODY" and len(D.children(parent_of_top)) == 1:
                    top_candidate = parent_of_top
                    parent_of_top = top_candidate.parent
                if not top_candidate.__dict__.get("readability"):
                    self._initializeNode(top_candidate)

            # Now that we have the top candidate, look through its siblings for content that might also be related. Things like preambles, content split by ads that we removed, etc.
            article_content = doc.create_element("DIV")
            if is_paging:
                D.set_attribute(article_content, "id", "readability-content")
            sibling_score_threshold = max(10, top_candidate.readability["contentScore"] * 0.2)
            # Keep potential top candidate's parent node to try to get text direction of it later.
            parent_of_top = top_candidate.parent
            siblings = D.children(parent_of_top)
            s, sl = 0, len(siblings)
            while s < sl:
                sibling = siblings[s]
                append = False
                if sibling is top_candidate:
                    append = True
                else:
                    content_bonus = 0
                    # Give a bonus if sibling nodes and top candidates have the example same classname
                    if D.class_name(sibling) == D.class_name(top_candidate) and D.class_name(top_candidate) != "":
                        content_bonus += top_candidate.readability["contentScore"] * 0.2
                    if sibling.__dict__.get("readability") and sibling.readability["contentScore"] + content_bonus >= sibling_score_threshold:
                        append = True
                    elif D.node_name(sibling) == "P":
                        link_density = self._getLinkDensity(sibling)
                        node_content = self._getInnerText(sibling)
                        node_length = len(node_content)
                        if node_length > 80 and link_density < 0.25:
                            append = True
                        elif node_length < 80 and node_length > 0 and link_density == 0 and _SENTENCE_END.search(node_content) is not None:
                            append = True
                if append:
                    if D.node_name(sibling) not in ALTER_TO_DIV_EXCEPTIONS:
                        # We have a node that isn't a common block level element, like a form or td tag. Turn it into a div so it doesn't get filtered out later by accident.
                        sibling = self._setNodeTag(sibling, "DIV")
                    D.append_child(article_content, sibling)
                    # Fetch children again to make it compatible with DOM parsers without live collection support.
                    siblings = D.children(parent_of_top)
                    # siblings is a reference to the children array, and sibling is removed from the array when we call appendChild(). As a result, we must revisit this index since the nodes have been shifted.
                    s -= 1
                    sl -= 1
                s += 1

            # So we have all of the content that we need. Now we clean it up for presentation.
            self._prepArticle(article_content)

            if needed_to_create_top_candidate:
                # We already created a fake div thing, and there wouldn't have been any siblings left for the previous loop, so there's no point trying to create a new div, and then move all the children over. Just assign IDs and class names here. No need to append because that already happened anyway.
                D.set_attribute(top_candidate, "id", "readability-page-1")
                D.set_attribute(top_candidate, "class", "page")
            else:
                div = doc.create_element("DIV")
                D.set_attribute(div, "id", "readability-page-1")
                D.set_attribute(div, "class", "page")
                while D.first_child(article_content) is not None:
                    D.append_child(div, D.first_child(article_content))
                D.append_child(article_content, div)

            parse_successful = True
            # Now that we've gone through the full algorithm, check to see if we got any meaningful content. If we didn't, we may need to re-run grabArticle with different flags set.
            text_length = len(self._getInnerText(article_content, True))
            if text_length < self._charThreshold:
                parse_successful = False
                # Restore the page, and try again with the next flag removed.
                D.set_inner_html(page, page_cache_html)
                self._attempts.append({"articleContent": article_content, "textLength": text_length})
                if self._flagIsActive(FLAG_STRIP_UNLIKELYS):
                    self._removeFlag(FLAG_STRIP_UNLIKELYS)
                elif self._flagIsActive(FLAG_WEIGHT_CLASSES):
                    self._removeFlag(FLAG_WEIGHT_CLASSES)
                elif self._flagIsActive(FLAG_CLEAN_CONDITIONALLY):
                    self._removeFlag(FLAG_CLEAN_CONDITIONALLY)
                else:
                    # No luck after removing flags, just return the longest text we found during the different loops
                    self._attempts.sort(key=lambda a: -a["textLength"])  # stable, like Array.prototype.sort
                    # But first check if we actually have something
                    if not self._attempts[0]["textLength"]:
                        return None
                    article_content = self._attempts[0]["articleContent"]
                    parse_successful = True
            if parse_successful:
                # Find out text direction from ancestors of final top candidate.
                for ancestor in [parent_of_top, top_candidate, *self._getNodeAncestors(parent_of_top)]:
                    if not D.tag_name(ancestor):
                        continue
                    article_dir = D.get_attribute(ancestor, "dir")
                    if article_dir:
                        self._articleDir = article_dir
                        break
                return article_content

"""Saved bridge judgements remain evidence after the recall window; an explicit change to the judgement, publication or fact membership can invalidate them without
deleting the link history. Which judgement ties a report to another story is written once here, for making links (consolidate.link_related_stories) and for
reading them (publication/stories.py). Port of events/related.ts."""

from __future__ import annotations

from newsroom.engine.events.relate import TIE_MIN_CONFIDENCE, TIE_RELATIONS

RELATED_MIN_REPORTS = 2


def latest_judgements(scope: str) -> str:
    """Each report's latest grouping judgement, among the decisions `scope` (a condition on `d`) admits. A revision that kept its membership is not one: it neither
    renews nor revokes the ties judged before it."""
    return f"""SELECT DISTINCT ON (d.article_id) d.id, d.article_id, d.verdict, d.candidates FROM grouping_decisions d
    WHERE d.verdict <> 'kept' AND {scope} ORDER BY d.article_id, d.id DESC"""


def firm_tie(d: str, c: str) -> str:
    """Judgement `d` firmly tied its report to candidate `c`, one of `d.candidates`."""
    relations = ", ".join(f"'{r}'" for r in TIE_RELATIONS)
    return f"""{d}.verdict IN ('same-fact', 'same-url', 'new-fact-in-story', 'new-story')
    AND {c}->>'relation' IN ({relations}) AND ({c}->>'confidence')::numeric >= {TIE_MIN_CONFIDENCE}"""

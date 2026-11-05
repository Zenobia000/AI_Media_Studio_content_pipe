"""Public scope at read time: port of publication/scope.ts. Every query that decides whether a report is public now, listed, selected, or
evidence of its fact uses these predicates over `publications p` (and, for evidence, `fact_articles fa`); no other module spells visibility,
the release time or the composite rule in SQL.

Each function returns a SQL fragment that uses the named parameters it lists in its docstring; `scope_params(now)` supplies them.
"""

from __future__ import annotations

from datetime import datetime

from newsroom.engine import pack


def scope_params(now: datetime) -> dict:
    """The parameters the fragments below use: %(now)s and %(browse_floor)s."""
    return {"now": now, "browse_floor": pack.load("selection")["SELECTION"]["browseFloor"], "independent": []}


def foldable_selection_condition() -> str:
    """These reports share their fact's display slot; independent sources keep their own selected card. The site has no sources of its
    own that are shown independently now, so the list (%(independent)s) is empty."""
    return "(NOT p.selected OR p.source_id <> ALL(%(independent)s::text[]))"


def released_condition() -> str:
    return "(NOT p.selected OR p.visible_after <= %(now)s)"


def listed_condition() -> str:
    """Listed on public surfaces at `now`: public, pool eligible, and released by then."""
    return f"p.visibility = 'public' AND p.eligible AND {released_condition()}"


def browsed_condition() -> str:
    """Browsing 全部動態 on every exit (web list, v1 / MCP / Agent, the all RSS): listed and scoring at least the industry's browse floor;
    searches reach every listed item. An unscored item (the model declined to score it) stays, as it does in the pool."""
    return f"{listed_condition()} AND (p.score IS NULL OR p.score >= %(browse_floor)s)"


def story_report_condition() -> str:
    """Story reports include older editorial material outside the pool, but never withdrawn or not yet released content."""
    return f"p.visibility = 'public' AND s.participation_mode = 'editorial' AND {released_condition()}"


def selected_condition() -> str:
    """Selected set as the website shows it (home timeline, reading groups, topics): every selected report."""
    return "p.visibility = 'public' AND p.selected AND p.visible_after <= %(now)s"


def seated_condition() -> str:
    """The selected set as machines receive it (v1, RSS, the sync ledger): the representative of ordinary reports of each fact, plus any
    independently displayed selected articles."""
    return "p.visibility = 'public' AND p.selected AND p.seat AND p.visible_after <= %(now)s"


def composite_condition() -> str:
    """The report was published from a composite (multi-topic) analysis: it only mentions facts."""
    return "EXISTS (SELECT 1 FROM analyses evidence_an WHERE evidence_an.id = p.analysis_id AND evidence_an.output->>'scope' = 'composite')"


def evidence_condition() -> str:
    """`fa` links report `p` to a fact as evidence: a primary or report membership, never a composite."""
    return f"fa.role <> 'mention' AND NOT {composite_condition()}"


def own_fact_evidence_condition() -> str:
    """Report `p` is still evidence of its own fact (`publications.fact_id`, written at publish time)."""
    return f"EXISTS (SELECT 1 FROM fact_articles fa WHERE fa.fact_id = p.fact_id AND fa.article_id = p.article_id AND {evidence_condition()})"


def latest_composite_condition(article: str) -> str:
    """The same rule over the latest analysis of report `article` (an SQL expression), for grouping (recall, story roots, consolidation) and for publish when it
    picks a publication's fact. They decide from the newest analysis, which `p.analysis_id` only points to once the publication is rebuilt from it, and recall
    also meets reports without a publication. Missing scope is unknown, never composite."""
    return f"""(SELECT scope_analysis.output->>'scope' = 'composite' FROM analyses scope_analysis
    WHERE scope_analysis.article_id = {article}
    ORDER BY scope_analysis.input_revision DESC, scope_analysis.id DESC LIMIT 1) IS TRUE"""


def grouping_evidence_condition(article: str) -> str:
    """Evidence for a later identity decision: not a composite and not explicitly withdrawn. A summary-only report or one awaiting publication keeps its existing
    eligibility. Historical memberships and judgements are retained independently of this predicate."""
    return f"""NOT {latest_composite_condition(article)} AND NOT EXISTS (
    SELECT 1 FROM publications grouping_publication
    WHERE grouping_publication.article_id = {article} AND grouping_publication.visibility = 'withdrawn')"""

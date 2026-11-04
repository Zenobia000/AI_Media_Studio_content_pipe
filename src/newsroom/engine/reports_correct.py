"""A classification correction moves the same frozen entry in the reports that carry it; it never composes another issue or pays a writer. The override,
publication, report revisions and audit commit in the caller's transaction. Port of reports/correct.ts (with sectionOf of reports/edition.ts)."""

from __future__ import annotations

import copy
import json

from psycopg.types.json import Jsonb

from newsroom.engine import taxonomy as T
from newsroom.engine.pack import load


def section_order() -> list[str]:
    return list(dict.fromkeys(c["section"] for c in T.categories()))


def section_of(category: str | None) -> str:
    """An item without a category goes into the section of the industry category, else into the last one."""
    by_key = {c["key"]: c["section"] for c in T.categories()}
    order = section_order()
    default = by_key.get("industry") or order[-1]
    return by_key.get(category or "", default)


def is_release(category: str | None, tags: list[str]) -> bool:
    release = load("taxonomy").get("RELEASE")
    return release is not None and category == release["category"] and release["tag"] in tags


def correct_report_classification(conn, article_id: str, reason: str) -> bool:
    """Moves the item to its new section in every issue that carries it; says whether any issue changed."""
    item = conn.execute("SELECT category FROM publications WHERE article_id = %s", (article_id,)).fetchone()
    if not item or not item["category"]:
        return False
    reports = conn.execute(
        """SELECT id, kind, content, revision, generated_at FROM reports
           WHERE content @> %s OR content @> %s ORDER BY id FOR UPDATE""",
        (Jsonb({"sections": [{"items": [{"itemId": article_id}]}]}), Jsonb({"themes": [{"storyRefs": [{"itemId": article_id}]}]})),
    ).fetchall()
    order = section_order()
    changed = False
    for report in reports:
        content = copy.deepcopy(report["content"])
        daily = report["kind"] == "daily"
        groups = (content.get("sections") if daily else content.get("themes")) or []
        label_key = "label" if daily else "heading"
        entries_key = "items" if daily else "storyRefs"
        source = next((g for g in groups if any(e.get("itemId") == article_id for e in g.get(entries_key) or [])), None)
        # Older, hand-written theme headings are not taxonomy columns; do not reinterpret their prose.
        if source is None or (source.get(label_key) or "") not in order:
            continue
        label = section_of(item["category"])
        if source.get(label_key) != label:
            moving = [e for e in source[entries_key] if e.get("itemId") == article_id]
            source[entries_key] = [e for e in source[entries_key] if e.get("itemId") != article_id]
            target = next((g for g in groups if g.get(label_key) == label), None)
            if target is None:
                target = {label_key: label, entries_key: []}
                groups.append(target)
            target[entries_key].extend(moving)
            if not daily:
                source["summary"] = None
                target["summary"] = None
        arranged = sorted((g for g in groups if g.get(entries_key)), key=lambda g: order.index(g[label_key]) if g[label_key] in order else -1)
        if daily:
            content["sections"] = arranged
            # The release figure follows the new classification; without a RELEASE kind there is none.
            if load("taxonomy").get("RELEASE") is not None:
                entries = [e for g in arranged for e in g.get("items") or []]
                rows = conn.execute("SELECT article_id, category, tags FROM publications WHERE article_id = ANY(%s)", ([e["itemId"] for e in entries],)).fetchall()
                by_id = {r["article_id"]: r for r in rows}
                content["metrics"] = {
                    **(content.get("metrics") or {}),
                    "modelsReleased": sum(
                        1 for e in entries
                        if (p := by_id.get(e["itemId"])) and is_release(p["category"], p["tags"]) and not e.get("followUp") and (e.get("firstParty") or e.get("role") in ("官方", "X·官方"))
                    ),
                }  # fmt: skip
        else:
            content["themes"] = arranged
        if json.dumps(content, sort_keys=True) == json.dumps(report["content"], sort_keys=True) and content == report["content"]:
            continue
        conn.execute(
            "INSERT INTO report_revisions (report_id, revision, content, generated_at, reason) VALUES (%s, %s, %s, %s, %s)",
            (report["id"], report["revision"], Jsonb(report["content"]), report["generated_at"], f"classification {article_id}: {reason}"),
        )
        conn.execute("UPDATE reports SET content = %s, revision = revision + 1, updated_at = now() WHERE id = %s", (Jsonb(content), report["id"]))
        changed = True
    return changed

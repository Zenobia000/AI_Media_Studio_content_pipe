"""open-topic (COL-FR-020, COL-FR-021): the candidates a person ticked become one topic with one content per format. Running it again
changes nothing that exists; a format asked for later adds only that content. Several candidates merged into one go into a single topic
whose sources are the sources of all of them."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone

from newsroom.collect.titles import needs_translation  # noqa: F401  (kept next to the other candidate helpers)
from newsroom.core.urls import normalize_url
from newsroom.core.workspace import day_of, inbox_dir, read_annotations, taipei_iso, workspace_dir, write_atomic

PROGRAMS = ("ai-today", "tool-review", "ai-engineering")
FORMATS = ("threads", "carousel", "reel")
MAX_SOURCES = 20
SLUG_MAX = 40


class OpenTopicError(Exception):
    """A bad argument, an unknown candidate or candidates that cannot share a topic: the person's input is wrong (E_INPUT, exit 2)."""

    code = "E_INPUT"


@dataclass
class OpenTopicResult:
    topic_id: str
    created: bool
    candidate_ids: list[str]
    source_ids: list[str]
    contents_created: list[str] = field(default_factory=list)
    contents_existing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def slug_of(title: str) -> str:
    """`YYYY-MM-DD-<slug>`'s slug: ASCII, lower case, joined with "-", at most 40 characters."""
    s = unicodedata.normalize("NFKD", title).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:SLUG_MAX].rstrip("-")
    return s or "topic"


def source_id_of(url: str) -> str:
    """`src_` + the first 12 hex of the SHA-1 of the normalized address: the same address always gives the same id."""
    return "src_" + hashlib.sha1((normalize_url(url) or url).encode("utf-8")).hexdigest()[:12]


def block(name: str, body: str) -> str:
    return f"<!-- newsroom:begin {name} -->\n{body}\n<!-- newsroom:end {name} -->"


def replace_block(text: str, name: str, body: str) -> str:
    begin, end = f"<!-- newsroom:begin {name} -->", f"<!-- newsroom:end {name} -->"
    a, b = text.find(begin), text.find(end)
    if a < 0 or b < a:
        return f"{text.rstrip()}\n\n{block(name, body)}\n"
    return text[:a] + block(name, body) + text[b + len(end) :]


def _status_text(candidates: list[str], sources: list[str]) -> str:
    return f"候選：{'、'.join(candidates)}\n來源：{'、'.join(sources) or '（尚無）'}"


def _yaml_list(items: list[str]) -> str:
    return "[" + ", ".join(items) + "]"


def _one_line(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _load_states(conn, ids: list[str]) -> list[dict]:
    return conn.execute(
        "SELECT candidate_id, status, first_listed_at, snapshot, merged_into FROM candidate_state WHERE candidate_id = ANY(%s)", (ids,)
    ).fetchall()


def _snap(row: dict, key: str, camel: str | None = None):
    snap = row["snapshot"]
    return snap.get(key, snap.get(camel)) if camel else snap.get(key)


def _expand(conn, ids: list[str], note_of, warnings: list[str]) -> list[dict]:
    """The given candidates, the ones merged into them or that they were merged into, and those a note says to merge ("合併到 cand_…")."""
    found: dict[str, dict] = {}
    frontier = list(dict.fromkeys(ids))
    while frontier:
        rows = _load_states(conn, [i for i in frontier if i not in found])
        have = {r["candidate_id"] for r in rows}
        for i in (i for i in frontier if i not in found and i not in have):
            if i in ids:
                raise OpenTopicError(f"找不到候選 {i}；先讓 inbox 產生過一次，或確認編號沒有打錯")
            warnings.append(f"備註指到的候選 {i} 不存在，已略過")
        nxt: set[str] = set()
        for r in rows:
            if r["status"] == "skipped":
                raise OpenTopicError(f"候選 {r['candidate_id']} 已經超過 48 小時被略過")
            found[r["candidate_id"]] = r
            if r["merged_into"]:
                nxt.add(r["merged_into"])
            nxt.update(re.findall(r"合併到\s*(cand_[A-Za-z0-9]+)", note_of(r["candidate_id"])))
        for r in conn.execute("SELECT candidate_id FROM candidate_state WHERE merged_into = ANY(%s)", (list(found),)).fetchall():
            nxt.add(r["candidate_id"])
        frontier = [i for i in nxt if i not in found]
    return list(found.values())


def _article_ids_of(conn, candidate_id: str) -> list[str]:
    """The articles behind a candidate: the article itself, or the articles that reported the story (official sources first)."""
    key = re.sub(r"^cand_", "", candidate_id)
    own = conn.execute("SELECT id FROM articles WHERE id = %s", (key,)).fetchone()
    if own:
        return [own["id"]]
    rows = conn.execute(
        """SELECT DISTINCT ON (a.id) a.id, (s.tier IN ('T1', 'T1_5')) AS official, coalesce(a.published_at, a.discovered_at) AS at
           FROM stories st JOIN story_signals ss ON ss.story_id = st.id JOIN articles a ON a.id = ss.article_id JOIN sources s ON s.id = a.source_id
           WHERE st.public_id::text = %s ORDER BY a.id""",
        (key,),
    ).fetchall()
    rows.sort(key=lambda r: (not r["official"], -r["at"].timestamp()))
    return [r["id"] for r in rows][:MAX_SOURCES]


def _sources_of(conn, candidate_ids: list[str]) -> list[tuple[str, str]]:
    sources: dict[str, str] = {}
    for cid in candidate_ids:
        ids = _article_ids_of(conn, cid)
        if not ids:
            continue
        for a in conn.execute("SELECT url FROM articles WHERE id = ANY(%s)", (ids,)).fetchall():
            sources[source_id_of(a["url"])] = a["url"]
    return list(sources.items())


def _new_topic_id(conn, title: str, created: datetime) -> str:
    base = f"{day_of(created)}-{slug_of(title)}"
    n = 1
    while True:
        tid = base if n == 1 else f"{base}-{n}"
        if not conn.execute("SELECT 1 FROM topics WHERE topic_id = %s", (tid,)).fetchone() and not (workspace_dir() / "topics" / tid).exists():
            return tid
        n += 1


def open_topic(
    conn,
    candidate_ids: list[str],
    program: str | None = None,
    formats: list[str] | None = None,
    angle: str | None = None,
    contents: bool = True,
    now: datetime | None = None,
) -> OpenTopicResult:
    now = now or datetime.now(timezone.utc)
    warnings: list[str] = []
    if not candidate_ids:
        raise OpenTopicError("至少要給一個候選編號")
    if program and program not in PROGRAMS:
        raise OpenTopicError(f"節目只能是 {'、'.join(PROGRAMS)}：{program}")
    for f in formats or []:
        if f not in FORMATS:
            raise OpenTopicError(f"格式只能是 {'、'.join(FORMATS)}：{f}")

    annotations = read_annotations(inbox_dir(), "0000-00-00", "9999-99-99")
    rows = _expand(conn, candidate_ids, lambda i: annotations[i].note if i in annotations else "", warnings)
    all_ids = [*candidate_ids, *(r["candidate_id"] for r in rows if r["candidate_id"] not in candidate_ids)]
    primary = next(r for r in rows if r["candidate_id"] == candidate_ids[0])

    topics = conn.execute("SELECT DISTINCT topic_id FROM topic_candidates WHERE candidate_id = ANY(%s)", (all_ids,)).fetchall()
    if len(topics) > 1:
        raise OpenTopicError(f"這些候選已經分屬不同的 topic：{'、'.join(t['topic_id'] for t in topics)}")

    def from_inbox(pick) -> str:
        return next((v for v in (pick(annotations[i]) if i in annotations else "" for i in all_ids) if v), "")

    chosen_program = program or from_inbox(lambda a: a.program) or _snap(primary, "suggested_program", "suggestedProgram")
    if chosen_program not in PROGRAMS:
        raise OpenTopicError(f"節目只能是 {'、'.join(PROGRAMS)}：{chosen_program}")
    if formats:
        asked = list(formats)
    else:
        asked = list(
            dict.fromkeys(f.strip() for i in all_ids for f in re.split(r"[,，]", annotations[i].formats if i in annotations else "") if f.strip())
        )
    wanted = asked or list(_snap(primary, "suggested_formats", "suggestedFormats"))
    for f in wanted:
        if f not in FORMATS:
            raise OpenTopicError(f"格式只能是 {'、'.join(FORMATS)}：{f}")
    notes = [(i, _one_line(annotations[i].note)) for i in all_ids if i in annotations and _one_line(annotations[i].note)]
    chosen_angle = angle if angle is not None else (notes[0][1][:80] if notes else "")

    found = _sources_of(conn, all_ids)
    existing = topics[0]["topic_id"] if topics else None
    title = _snap(primary, "title")
    topic_id = existing or _new_topic_id(conn, title, now)
    created = existing is None
    first_listed = min(r["first_listed_at"] for r in rows)

    with conn.transaction():
        conn.execute(
            "INSERT INTO topics (topic_id, title, program, tier, created_at) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (topic_id) DO NOTHING",
            (topic_id, title, chosen_program, _snap(primary, "tier"), now),
        )
        for i in all_ids:
            conn.execute("INSERT INTO topic_candidates (candidate_id, topic_id) VALUES (%s, %s) ON CONFLICT (candidate_id) DO NOTHING", (i, topic_id))
        for sid, url in found:
            conn.execute(
                """INSERT INTO topic_sources (topic_id, source_id, url) VALUES (%s, %s, %s)
                            ON CONFLICT (topic_id, source_id) DO UPDATE SET url = COALESCE(topic_sources.url, EXCLUDED.url)""",
                (topic_id, sid, url),
            )

    topic = conn.execute("SELECT title, program, tier, created_at FROM topics WHERE topic_id = %s", (topic_id,)).fetchone()
    all_cands = [
        r["candidate_id"]
        for r in conn.execute("SELECT candidate_id FROM topic_candidates WHERE topic_id = %s ORDER BY candidate_id", (topic_id,)).fetchall()
    ]
    all_sources = [
        r["source_id"] for r in conn.execute("SELECT source_id FROM topic_sources WHERE topic_id = %s ORDER BY source_id", (topic_id,)).fetchall()
    ]

    d = workspace_dir() / "topics" / topic_id
    (d / "contents").mkdir(parents=True, exist_ok=True)
    status_body = _status_text(all_cands, all_sources)
    topic_file = d / "topic.md"
    if topic_file.exists():
        write_atomic(topic_file, replace_block(topic_file.read_text(encoding="utf-8"), "status", status_body))
    else:
        front = "\n".join(
            [
                "---",
                "schema_version: 1",
                f"id: {topic_id}",
                f"title: {json.dumps(topic['title'], ensure_ascii=False)}",
                f"program: {topic['program']}",
                f"tier: {topic['tier'] or 'null'}",
                f"candidate_ids: {_yaml_list(all_cands)}",
                f"source_ids: {_yaml_list(all_sources)}",
                f"first_seen: {taipei_iso(first_listed)}",
                f"created_at: {taipei_iso(topic['created_at'])}",
                "---",
            ]
        )
        write_atomic(topic_file, f"{front}\n\n# {topic['title']}\n\n{block('status', status_body)}\n")

    notes_file = d / "notes.md"
    notes_text = notes_file.read_text(encoding="utf-8") if notes_file.exists() else "# 備註\n"
    for i, text in notes:
        if f"{i}：" not in notes_text:
            notes_text = f"{notes_text.rstrip()}\n\n- {i}：{text}\n"
    write_atomic(notes_file, notes_text)

    created_contents: list[str] = []
    existing_contents: list[str] = []
    present = [p.name for p in (d / "contents").iterdir()] if (d / "contents").exists() else []
    for fmt in [] if contents is False else wanted:
        have = next((n for n in present if n.startswith(f"{fmt}-")), None)
        if have:
            existing_contents.append(have)
            continue
        content_id = f"{fmt}-01"
        front = "\n".join(
            [
                "---",
                "schema_version: 1",
                f"id: {content_id}",
                f"topic_id: {topic_id}",
                f"program: {topic['program']}",
                f"format: {fmt}",
                "selected_revision: null",
                f"angle: {json.dumps(chosen_angle, ensure_ascii=False)}",
                "wave: 1",
                "---",
            ]
        )
        write_atomic(d / "contents" / content_id / "content.md", f"{front}\n\n{block('status', '狀態：new')}\n")
        created_contents.append(content_id)
        present.append(content_id)

    conn.execute("UPDATE candidate_state SET status = 'opened', updated_at = now() WHERE candidate_id = ANY(%s) AND status <> 'skipped'", (all_ids,))
    return OpenTopicResult(topic_id, created, all_cands, all_sources, created_contents, existing_contents, warnings)


def refresh_status(conn, topic_id: str) -> None:
    """Rewrites the status block of topic.md after the sources of a topic changed; the rest of the file is the person's."""
    f = workspace_dir() / "topics" / topic_id / "topic.md"
    if not f.exists():
        return
    cands = [
        r["candidate_id"]
        for r in conn.execute("SELECT candidate_id FROM topic_candidates WHERE topic_id = %s ORDER BY candidate_id", (topic_id,)).fetchall()
    ]
    srcs = [
        r["source_id"] for r in conn.execute("SELECT source_id FROM topic_sources WHERE topic_id = %s ORDER BY source_id", (topic_id,)).fetchall()
    ]
    write_atomic(f, replace_block(f.read_text(encoding="utf-8"), "status", _status_text(cands, srcs)))


def merge_candidates(conn, target: str, other: str) -> str | None:
    """Marks two candidates as one before a topic is opened; if either already has a topic, the other joins it."""
    rows = _load_states(conn, [target, other])
    for i in (target, other):
        if not any(r["candidate_id"] == i for r in rows):
            raise OpenTopicError(f"找不到候選 {i}")
    topics = conn.execute("SELECT DISTINCT topic_id FROM topic_candidates WHERE candidate_id IN (%s, %s)", (target, other)).fetchall()
    if topics:
        return open_topic(conn, [target, other], contents=False).topic_id
    conn.execute("UPDATE candidate_state SET merged_into = %s, updated_at = now() WHERE candidate_id = %s", (target, other))
    return None

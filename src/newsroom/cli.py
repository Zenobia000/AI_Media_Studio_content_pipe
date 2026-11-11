"""The tool surface (IF-020): `newsroom <tool> … [--json]`. With --json every tool prints one JSON object: ok, tool, data, warnings, next on
success; ok false, error_code, message, blocked on failure. Exit codes: 0 success, 1 something failed, 2 bad input, 3 blocked (a person
has to supply something)."""

from __future__ import annotations

import dataclasses
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Optional

import typer

from newsroom import __version__
from newsroom.collect.topics import OpenTopicError
from newsroom.core import db
from newsroom.fetch.ffmpeg import ToolMissing

# The tools print Chinese (and JSON for the agent): always UTF-8, whatever the console's code page is.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

app = typer.Typer(add_completion=False, no_args_is_help=True, help="AI Media Studio：素材收集與補料")


def _version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(version: Annotated[bool, typer.Option("--version", callback=_version, is_eager=True, help="顯示版本")] = False) -> None:
    pass


def _plain(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _plain(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_plain(v) for v in obj]
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


def _emit(obj: dict) -> None:
    typer.echo(json.dumps(_plain(obj), ensure_ascii=False))


def _fail(tool: str, as_json: bool, message: str, code: str = "E_INPUT", exit_code: int = 2, **extra: Any) -> None:
    if as_json:
        _emit({"ok": False, "tool": tool, "error_code": code, "message": message, "blocked": exit_code == 3, **extra})
    else:
        typer.echo(message, err=True)
    raise typer.Exit(exit_code)


def _guarded(tool: str, as_json: bool, fn: Callable[[Any], None]) -> None:
    """Runs a tool on a connection; a bad input (OpenTopicError, missing ffmpeg) is exit 2."""
    try:
        with db.connect() as conn:
            fn(conn)
    except (OpenTopicError, ToolMissing) as e:
        _fail(tool, as_json, str(e), getattr(e, "code", "E_INPUT"))


Json = Annotated[bool, typer.Option("--json", help="輸出 JSON（IF-020）")]


@app.command("migrate")
def migrate() -> None:
    """套用資料表 migration：引擎的（database/migrations）加本專案的（記在 schema_migrations）。空資料庫一個指令建完。"""
    from newsroom.core.migrate import apply_dir  # noqa: PLC0415
    from newsroom.engine.config import REPO_ROOT  # noqa: PLC0415

    # The engine's tables first (database/migrations, 0001-0105), then this project's own (core/migrations, 0106 and up): one command builds a whole database.
    engine = REPO_ROOT / "database" / "migrations"
    with db.connect() as conn:
        applied = [*apply_dir(conn, engine), *apply_dir(conn)] if engine.is_dir() else apply_dir(conn)
    typer.echo(f"套用 {len(applied)} 個 migration" + (f"：{'、'.join(applied)}" if applied else "，資料表已是最新。"))


@app.command("worker")
def worker_cmd() -> None:
    """啟動後臺工作行程：收集信源、抓正文與它們的定時任務（Ctrl+C 停止，會等進行中的工作做完）。"""
    from newsroom.engine.worker import run  # noqa: PLC0415

    run()


@app.command("init")
def init(path: Annotated[Optional[Path], typer.Argument()] = None, as_json: Json = False) -> None:
    """建立工作區（只補缺的，已有的不動）。"""
    from newsroom.core.init import init_workspace  # noqa: PLC0415

    root = (path or Path("newsroom")).resolve()
    try:
        r = init_workspace(root)
    except OSError as e:
        _fail("init", as_json, f"建立工作區失敗：{e}", exit_code=1)
        return
    nxt = [f"設定環境變數 NEWSROOM_HOME={root}", "newsroom migrate（建立資料表）", "編輯 voice.md，寫下口吻", "newsroom doctor 檢查環境"]
    if as_json:
        _emit({"ok": True, "tool": "init", "data": r, "warnings": r.warnings, "next": nxt})
        return
    typer.echo(f"工作區：{root}")
    typer.echo(f"新建 {len(r.created)} 項：{'、'.join(r.created)}" if r.created else "沒有需要新建的，已是完整的工作區。")
    if r.existing and r.created:
        typer.echo(f"原本就有，沒有動：{len(r.existing)} 項")
    for w in r.warnings:
        typer.echo(f"注意：{w}")
    typer.echo("\n下一步：\n" + "\n".join(f"- {n}" for n in nxt))


@app.command("doctor")
def doctor(as_json: Json = False) -> None:
    """檢查機器與設定；有項目失敗時結束碼為 1。"""
    from newsroom.core.doctor import default_deps, exit_code_of, run_checks  # noqa: PLC0415

    checks = run_checks(default_deps())
    code = exit_code_of(checks)
    if as_json:
        if code == 0:
            _emit(
                {
                    "ok": True,
                    "tool": "doctor",
                    "data": {"checks": checks},
                    "warnings": [f"{c.name}：{c.detail}" for c in checks if c.status == "warn"],
                    "next": [],
                }
            )
        else:
            _emit(
                {
                    "ok": False,
                    "tool": "doctor",
                    "error_code": "E_INPUT",
                    "message": f"{sum(c.status == 'fail' for c in checks)} 項檢查失敗",
                    "blocked": False,
                    "data": {"checks": checks},
                }
            )
    else:
        mark = {"ok": "ok  ", "warn": "warn", "fail": "FAIL"}
        for c in checks:
            typer.echo(f"[{mark[c.status]}] {c.name}：{c.detail}")
            if c.fix and c.status != "ok":
                typer.echo(f"       → {c.fix}")
        n = lambda s: sum(c.status == s for c in checks)  # noqa: E731
        typer.echo(f"\nok {n('ok')}、warn {n('warn')}、fail {n('fail')}")
    raise typer.Exit(code)


@app.command("add-source")
def add_source_cmd(url: str, note: str = "", program: Optional[str] = None, formats: Optional[str] = None, title: Optional[str] = None) -> None:
    """手動新增一個連結成為候選。"""
    from newsroom.collect.manual import InvalidUrlError, add_source  # noqa: PLC0415

    try:
        with db.connect() as conn:
            r = add_source(conn, url, note=note, program=program, formats=[f.strip() for f in (formats or "").split(",") if f.strip()], title=title)
    except InvalidUrlError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2) from e
    typer.echo(json.dumps(_plain(r), ensure_ascii=False))
    typer.echo(f"已經加過了，備註已附加；它在 {r.inbox_date} 的 inbox。" if r.already_added else f"已加入候選，會出現在 {r.inbox_date} 的 inbox。")


@app.command("open-topic")
def open_topic_cmd(
    candidates: Annotated[list[str], typer.Argument()],
    program: Optional[str] = None,
    formats: Optional[str] = None,
    angle: Optional[str] = None,
    as_json: Json = False,
) -> None:
    """把勾選的候選建成一個 topic。"""
    from newsroom.collect.topics import open_topic  # noqa: PLC0415

    def run(conn) -> None:
        r = open_topic(conn, candidates, program=program, angle=angle, formats=[f.strip() for f in (formats or "").split(",") if f.strip()] or None)
        if as_json:
            _emit({"ok": True, "tool": "open-topic", "data": r, "warnings": r.warnings, "next": [f"fetch {r.topic_id}"]})
            return
        typer.echo(f"{'已建立' if r.created else '沿用'} topic {r.topic_id}")
        if r.contents_created:
            typer.echo(f"新增 content：{'、'.join(r.contents_created)}")
        if r.contents_existing:
            typer.echo(f"已存在、沒有重建：{'、'.join(r.contents_existing)}")
        for w in r.warnings:
            typer.echo(f"注意：{w}")

    _guarded("open-topic", as_json, run)


@app.command("merge-candidates")
def merge_candidates_cmd(keep: str, merged: str, as_json: Json = False) -> None:
    """合併兩個候選（建題前）。"""
    from newsroom.collect.topics import merge_candidates  # noqa: PLC0415

    def run(conn) -> None:
        topic = merge_candidates(conn, keep, merged)
        if as_json:
            _emit({"ok": True, "tool": "merge", "data": {"topicId": topic}, "warnings": [], "next": [f"open-topic {keep}"]})
        else:
            typer.echo(f"已併入既有 topic {topic}" if topic else "已合併，建題時會歸到同一個 topic。")

    _guarded("merge", as_json, run)


@app.command("fetch-topic")
def fetch_topic_cmd(topic_id: str, force: bool = False, as_json: Json = False) -> None:
    """擷取 topic 每個來源的原文。"""
    from newsroom.fetch.topic import fetch_topic  # noqa: PLC0415

    def run(conn) -> None:
        r = fetch_topic(conn, topic_id, force=force)
        if r.blocked:
            why = "；".join(f"{f['url']}：{f['reason']}" for f in r.failed)
            _fail(
                "fetch",
                as_json,
                f"沒有任何來源抓到原文。{why}",
                "E_MISSING_SOURCE",
                3,
                data=r,
                next=["請把原文貼進 sources/manual-1.md，再執行 fetch"],
            )
        if as_json:
            _emit(
                {
                    "ok": True,
                    "tool": "fetch",
                    "data": r,
                    "warnings": [f"{f['url']}：{f['reason']}" for f in r.failed],
                    "next": [f"assets {r.topic_id}"],
                }
            )
            return
        for f in r.fetched:
            typer.echo(f"{'官方來源 ' if f['official'] else ''}{f['sourceId']}  {f['method']}  {f['wordCount']} 字  {f['url']}")
        if r.kept:
            typer.echo(f"已存在、沒有重抓：{'、'.join(r.kept)}")
        for f in r.failed:
            typer.echo(f"失敗：{f['url']}（{f['reason']}）")

    _guarded("fetch", as_json, run)


@app.command("fetch-media")
def fetch_media_cmd(topic_id: str, layers: str = "1,2,3,4", as_json: Json = False) -> None:
    """下載 topic 的媒體：L1 平臺 API、L2 網頁檔案、L3 下載器、L4 瀏覽器錄影，然後盤點素材。"""
    from newsroom.fetch.assets import inventory  # noqa: PLC0415
    from newsroom.fetch.layers.l1_api import fetch_layer1  # noqa: PLC0415
    from newsroom.fetch.layers.l2_html import fetch_layer2  # noqa: PLC0415
    from newsroom.fetch.layers.l3_dl import fetch_layer3  # noqa: PLC0415
    from newsroom.fetch.layers.l4_record import fetch_layer4  # noqa: PLC0415

    wanted = {x.strip() for x in layers.split(",")}
    for x in wanted:
        if x not in {"1", "2", "3", "4"}:
            _fail("fetch-media", as_json, f"--layers 只能是 1、2、3、4：{x}")

    def run(conn) -> None:
        l1 = fetch_layer1(conn, topic_id) if "1" in wanted else None
        l1_video = {d["sourceId"] for d in l1.downloaded} if l1 else set()
        l2 = fetch_layer2(conn, topic_id) if "2" in wanted else None
        have = l1_video | {d["sourceId"] for d in (l2.downloaded if l2 else []) if d["kind"] == "video"}
        l3 = fetch_layer3(conn, topic_id, have_video=have) if "3" in wanted else None
        have4 = have | {d["sourceId"] for d in (l3.downloaded if l3 else []) if d["kind"] == "video"}
        l4 = fetch_layer4(conn, topic_id, have_video=have4) if "4" in wanted else None
        stock = inventory(conn, topic_id)
        downloaded = [*(l1.downloaded if l1 else []), *(l2.downloaded if l2 else []), *(l3.downloaded if l3 else [])]
        warnings = [
            *(f"{s['url']}：{s['reason']}" for s in (l1.skipped if l1 else []) if s["failed"]),
            *(f"{s['url']}：{s['reason']}" for s in (l2.skipped if l2 else [])),
            *(f"{f['url']}：{f['message']}" for f in (l3.failed if l3 else [])),
            *(f"{s['url']}：{s['reason']}" for s in (l4.skipped if l4 else []) if s["failed"]),
            *stock.warnings,
        ]
        if as_json:
            data = {
                "topicId": topic_id,
                "downloaded": downloaded,
                "recorded": l4.recorded if l4 else [],
                "skipped": l2.skipped if l2 else [],
                "notRecorded": l4.skipped if l4 else [],
                "failed": l3.failed if l3 else [],
                "assets": len(stock.assets),
                "gaps": stock.gaps,
            }
            _emit(
                {
                    "ok": True,
                    "tool": "fetch-media",
                    "data": data,
                    "warnings": warnings,
                    "next": ["gather-assets：補缺口"] if stock.gaps else [f"draft {topic_id}"],
                }
            )
            return
        for d in downloaded:
            typer.echo(f"下載 {d['kind']}  {d['path']}  {d['url']}")
        for r in l4.recorded if l4 else []:
            typer.echo(f"錄影 video  {r['path']}  {r['url']}")
        for w in warnings:
            typer.echo(f"注意：{w}")
        for g in stock.gaps:
            typer.echo(f"缺口 {g.need}：{g.reason}。建議：{g.suggestion}")
        typer.echo(f"素材共 {len(stock.assets)} 個")

    _guarded("fetch-media", as_json, run)


@app.command("preprocess-topic")
def preprocess_topic_cmd(
    topic_id: str,
    video_only: bool = False,
    audio_only: bool = False,
    lufs: Optional[float] = None,
    require_audio: bool = False,
    as_json: Json = False,
) -> None:
    """素材前處理：影片 30fps CFR、旁白 48 kHz WAV 且 −14 LUFS。"""
    from newsroom.fetch.assets import inventory  # noqa: PLC0415
    from newsroom.fetch.preprocess import DEFAULT_LUFS, preprocess  # noqa: PLC0415

    if video_only and audio_only:
        _fail("preprocess", as_json, "--video-only 與 --audio-only 不能同時用")
    if lufs is not None and not (-40 <= lufs < 0):
        _fail("preprocess", as_json, f"--lufs 要在 -40 到 0 之間：{lufs}")

    def run(conn) -> None:
        before = inventory(conn, topic_id)
        if require_audio and not any(a.type == "audio" and a.path.startswith("media/raw/") for a in before.assets):
            _fail(
                "preprocess",
                as_json,
                "找不到旁白錄音。請錄製旁白並放進 media/raw/",
                "E_MISSING_ASSET",
                3,
                next=["把錄音放進 media/raw/，再執行 preprocess"],
            )
        r = preprocess(conn, topic_id, video=not audio_only, audio=not video_only, lufs=lufs if lufs is not None else DEFAULT_LUFS)
        inventory(conn, topic_id)
        if as_json:
            _emit(
                {
                    "ok": True,
                    "tool": "preprocess",
                    "data": r,
                    "warnings": [f"{f['from']}：{f['reason']}" for f in r.failed],
                    "next": [f"assets {topic_id}"],
                }
            )
        else:
            for m in r.made:
                typer.echo(f"{'影片' if m['kind'] == 'video' else '旁白'}  {m['from']} → {m['path']}")
            if r.kept:
                typer.echo(f"已處理過、沒有重做：{len(r.kept)} 個")
            for f in r.failed:
                typer.echo(f"失敗 {f['from']}：{f['reason']}")
            if not (r.made or r.kept or r.failed):
                typer.echo("沒有需要處理的素材。")
        if r.failed and not r.made and not r.kept:
            raise typer.Exit(1)

    _guarded("preprocess", as_json, run)


@app.command("assets-topic")
def assets_topic_cmd(topic_id: str, voiceover: bool = False, as_json: Json = False) -> None:
    """盤點 topic 的素材與缺口（assets.json、media.md）。"""
    from newsroom.fetch.assets import inventory  # noqa: PLC0415

    def run(conn) -> None:
        r = inventory(conn, topic_id, voiceover=voiceover)
        if as_json:
            _emit(
                {
                    "ok": True,
                    "tool": "assets",
                    "data": {**_plain(r), "assets": len(r.assets)},
                    "warnings": r.warnings,
                    "next": ["gather-assets：補缺口"] if r.gaps else [f"draft {r.topic_id}"],
                }
            )
            return
        typer.echo(f"素材 {len(r.assets)} 個（新增 {len(r.added)}、移除 {len(r.removed)}）")
        for g in r.gaps:
            typer.echo(f"缺口 {g.need}：{g.reason}。建議：{g.suggestion}")
        for w in r.warnings:
            typer.echo(f"注意：{w}")

    _guarded("assets", as_json, run)


@app.command("set-title-zh")
def set_title_zh_cmd(
    candidate: Annotated[Optional[str], typer.Argument()] = None,
    title: Annotated[Optional[str], typer.Argument()] = None,
    list_: Annotated[bool, typer.Option("--list")] = False,
    force: bool = False,
) -> None:
    """替候選加上中文標題，或列出還需要中文標題的候選。"""
    from newsroom.collect.titles import EmptyTitleError, pending_titles, set_title_zh  # noqa: PLC0415

    with db.connect() as conn:
        if list_:
            typer.echo(json.dumps(pending_titles(conn), ensure_ascii=False))
            return
        if not candidate or title is None:
            typer.echo("用法：set-title-zh --list，或 set-title-zh <cand_id> <中文標題> [--force]", err=True)
            raise typer.Exit(2)
        try:
            typer.echo("已寫入中文標題。" if set_title_zh(conn, candidate, title, force) else "這則已經有中文標題了（要改請加 --force）。")
        except EmptyTitleError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(2) from e


@app.command("rag-index")
def rag_index_cmd(limit: Optional[int] = None, as_json: Json = False) -> None:
    """把分析過的文章轉成向量存進資料庫（只處理新的或改過的）。"""
    from newsroom.rag.index import EmbedError, index_articles  # noqa: PLC0415

    def run(conn) -> None:
        try:
            data = index_articles(conn, limit=limit)
        except EmbedError as e:
            _fail("rag-index", as_json, str(e), "E_EMBED", 1)
        if as_json:
            _emit({"ok": True, "tool": "rag-index", "data": data, "warnings": [], "next": []})
        else:
            typer.echo(f"可索引 {data['candidates']} 篇，這次轉了 {data['embedded']} 篇，原本就有 {data['already_indexed']} 篇。")

    _guarded("rag-index", as_json, run)


@app.command("rag-search")
def rag_search_cmd(query: str, k: Annotated[int, typer.Option("--k", "-k", help="回傳幾篇")] = 5, as_json: Json = False) -> None:
    """用問題找最相近的文章。"""
    from newsroom.rag.index import EmbedError, search  # noqa: PLC0415

    def run(conn) -> None:
        try:
            hits = search(conn, query, k)
        except EmbedError as e:
            _fail("rag-search", as_json, str(e), "E_EMBED", 1)
        if as_json:
            _emit({"ok": True, "tool": "rag-search", "data": {"query": query, "hits": hits}, "warnings": [], "next": []})
        else:
            for h in hits:
                typer.echo(f"{h['score']:.3f}  {h['title']}\n       {h['url']}")

    _guarded("rag-search", as_json, run)


@app.command("ask")
def ask_cmd(question: str, k: Annotated[int, typer.Option("--k", "-k", help="最多參考幾篇")] = 5, as_json: Json = False) -> None:
    """用資料庫裡的文章回答問題（本機 Qwen），並列出出處；找不到相關文章就說找不到。"""
    from newsroom.rag.ask import ask  # noqa: PLC0415
    from newsroom.rag.index import EmbedError  # noqa: PLC0415

    def run(conn) -> None:
        try:
            data = ask(conn, question, k)
        except EmbedError as e:
            _fail("ask", as_json, str(e), "E_EMBED", 1)
        if as_json:
            _emit({"ok": True, "tool": "ask", "data": data, "warnings": [], "next": []})
            return
        typer.echo(data["answer"])
        for s in data["sources"]:
            shown = f"  ({s['score']:.2f})" if 0 <= s["score"] <= 1 else ""  # a recommendation with no topic carries the article's own 0-100 score, not a similarity
            typer.echo(f"  [{s['n']}] {s['title']}{shown}\n      {s['url']}")

    _guarded("ask", as_json, run)


@app.command("fetch-selftest")
def fetch_selftest_cmd(file: Optional[Path] = None, as_json: Json = False) -> None:
    """每一層補料各試一個固定網址，並檢查下載器版本；有層失敗時結束碼為 1。"""
    from newsroom.fetch.selftest import load_targets, run_selftest  # noqa: PLC0415

    with db.connect() as conn:
        checks = run_selftest(conn, load_targets(file) if file else load_targets())
    failed = [c for c in checks if c.status == "fail"]
    if as_json:
        _emit(
            {
                "ok": False,
                "tool": "fetch-selftest",
                "error_code": "E_EXTERNAL",
                "message": f"{len(failed)} 項失敗",
                "blocked": False,
                "data": {"checks": checks},
            }
            if failed
            else {
                "ok": True,
                "tool": "fetch-selftest",
                "data": {"checks": checks},
                "warnings": [f"{c.name}：{c.detail}" for c in checks if c.status == "warn"],
                "next": [],
            }
        )
    else:
        mark = {"ok": "ok  ", "warn": "warn", "fail": "FAIL"}
        for c in checks:
            typer.echo(f"[{mark[c.status]}] {c.name}{'  ' + c.url if c.url else ''}\n       {c.detail}")
        typer.echo(f"\nok {sum(c.status == 'ok' for c in checks)}、warn {sum(c.status == 'warn' for c in checks)}、fail {len(failed)}")
    raise typer.Exit(1 if failed else 0)


@app.command("fetch-stats")
def fetch_stats_cmd(days: int = 30, as_json: Json = False) -> None:
    """最近幾天各補料層的成功率。"""
    from newsroom.fetch.attempts import fetch_stats  # noqa: PLC0415

    if not 1 <= days <= 366:
        typer.echo(f"--days 要是 1 到 366 的整數：{days}", err=True)
        raise typer.Exit(2)
    with db.connect() as conn:
        stats = fetch_stats(conn, days)
    if as_json:
        _emit({"ok": True, "tool": "fetch-stats", "data": {"days": days, "layers": stats}, "warnings": [], "next": []})
    elif not stats:
        typer.echo(f"最近 {days} 天沒有補料紀錄。")
    else:
        typer.echo(f"最近 {days} 天各層成功率：")
        for s in stats:
            typer.echo(f"  {s.name:<10} {s.ok:>4} / {s.attempts:<4} {s.rate * 100:.0f}%")


@app.command("source-stats")
def source_stats_cmd(days: int = 14, as_json: Json = False) -> None:
    """各信源最近幾天的抓取成功率（T-040），來自 fetch_runs。"""
    if not 1 <= days <= 366:
        typer.echo(f"--days 要是 1 到 366 的整數，收到：{days}", err=True)
        raise typer.Exit(2)
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT s.id, s.name, count(*) FILTER (WHERE r.status = 'ok')::int AS ok, count(*) FILTER (WHERE r.status = 'failed')::int AS failed,
                      count(DISTINCT r.started_at::date)::int AS days_seen,
                      (array_agg(r.error ORDER BY r.started_at DESC) FILTER (WHERE r.status = 'failed'))[1] AS last_error
               FROM sources s LEFT JOIN fetch_runs r ON r.source_id = s.id AND r.started_at > now() - make_interval(days => %s)
               GROUP BY s.id, s.name ORDER BY s.id""",
            (days,),
        ).fetchall()
    stats = [{**r, "rate": r["ok"] / (r["ok"] + r["failed"]) if r["ok"] + r["failed"] else None} for r in rows]
    if as_json:
        _emit({"ok": True, "tool": "source-stats", "data": {"days": days, "sources": stats}, "warnings": [], "next": []})
        return
    typer.echo(f"最近 {days} 天各信源的抓取成功率：")
    for s in stats:
        rate = "無紀錄" if s["rate"] is None else f"{s['rate'] * 100:.0f}%"
        err = f"  最近錯誤：{str(s['last_error'])[:60]}" if s["last_error"] else ""
        typer.echo(f"  {s['id']:<28} {s['ok']:>5} 成功 {s['failed']:>4} 失敗 {rate:>6}  有紀錄 {s['days_seen']} 天{err}")


@app.command("backtest-heat")
def backtest_heat_cmd(labels: Path, as_json: Json = False) -> None:
    """用人工標好的 S／A／B 回測熱度分級（目標：標 S 的有 80% 以上被判為 S 或 A）。"""
    from newsroom.collect.heat_inputs import backtest_heat  # noqa: PLC0415
    from newsroom.collect.score import load_heat_config  # noqa: PLC0415

    with db.connect() as conn:
        r = backtest_heat(conn, json.loads(labels.read_text(encoding="utf-8")), load_heat_config())
    if as_json:
        _emit({"ok": True, "tool": "backtest-heat", "data": r, "warnings": [f"找不到事件 {x.story}" for x in r.rows if not x.found], "next": []})
        return
    for row in r.rows:
        typer.echo(f"{row.label} → {row.tier}（{row.score} 分）  {row.story}" if row.found else f"{row.label} → 找不到  {row.story}")
    typer.echo(
        "\n沒有人工標記的 S 級事件，無法回測。"
        if r.s_rate is None
        else f"\n人工標記的 S 級：{r.s_hits}／{r.s_total} 被判為 S 或 A（{round(r.s_rate * 100)}%，目標 ≥ 80%）"
    )


@app.command("write-inbox")
def write_inbox_cmd() -> None:
    """立刻產生（更新）今天的 inbox。"""
    from newsroom.collect.inbox_writer import write_inbox  # noqa: PLC0415

    with db.connect() as conn:
        r = write_inbox(conn)
    typer.echo(json.dumps(r, ensure_ascii=False) if r else "沒有候選，也沒有要記的來源問題，沒有寫檔。")


@app.command("collect-signals")
def collect_signals_cmd() -> None:
    """記一次 HN／HF／GitHub 的熱度訊號快照。"""
    from newsroom.collect.signals import collect_signals  # noqa: PLC0415
    from newsroom.core.config import credential  # noqa: PLC0415

    with db.connect() as conn:
        typer.echo(json.dumps(collect_signals(conn, github_token=credential("collectors", "GITHUB_TOKEN")), ensure_ascii=False))


@app.command("run-scheduler")
def run_scheduler_cmd(once: bool = False) -> None:
    """常駐執行排程（inbox 每 30 分鐘、訊號每小時、網頁變動每 10 分鐘…）；--once 把到期的跑一輪就結束。"""
    from newsroom.collect.schedule import run  # noqa: PLC0415

    run(once=once)


def entry() -> None:  # pragma: no cover
    sys.exit(app())

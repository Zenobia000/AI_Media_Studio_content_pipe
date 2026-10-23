"""Render architecture diagrams (system, software, data flow, ERD) with local Graphviz."""
from pathlib import Path
import subprocess

OUT = Path(__file__).resolve().parent.parent / 'diagrams'
OUT.mkdir(exist_ok=True)
FONT = 'Microsoft JhengHei'

HEAD = [
    f'graph [bgcolor="white", pad="0.4", nodesep="0.45", ranksep="0.6", fontname="{FONT}", fontsize=20, labelloc=t, compound=true];',
    f'node [shape=box, style="rounded,filled", fillcolor="#eef4fa", color="#42617d", fontname="{FONT}", fontsize=13, margin="0.18,0.1"];',
    f'edge [fontname="{FONT}", fontsize=11, color="#526779", arrowsize=0.75];',
]

# Owner colors keep the three engineers' modules visually distinct.
ALBERT, CHANGXIAO, SHUWEI, SHARED, EXT = '#e3f0e1', '#fdf0dc', '#e8e4f6', '#eef4fa', '#f4f4f4'


def render(name: str, body: list[str], title: str, rankdir: str = 'TB') -> None:
    dot = OUT / f'{name}.dot'
    lines = ['digraph G {', f'graph [rankdir={rankdir}, label="{title}"];', *HEAD, *body, '}']
    dot.write_text('\n'.join(lines), encoding='utf-8')
    for fmt in ('png', 'svg'):
        subprocess.run(['dot', f'-T{fmt}', str(dot), '-o', str(OUT / f'{name}.{fmt}')], check=True)


def legend() -> list[str]:
    return [
        'subgraph cluster_legend { label="負責人"; style="rounded,dashed"; color="#9aa7b3"; fontsize=12;',
        f'lg_a [label="Albert Chiang｜素材收集", fillcolor="{ALBERT}"];',
        f'lg_c [label="周長曉｜文章與影片", fillcolor="{CHANGXIAO}"];',
        f'lg_s [label="楊舒媁｜排程與發布", fillcolor="{SHUWEI}"];',
        'lg_a -> lg_c -> lg_s [style=invis]; }',
    ]


def system() -> None:
    body = [
        'subgraph cluster_ext { label="外部服務"; style="rounded"; color="#9aa7b3";',
        f'node [fillcolor="{EXT}"];',
        'x [label="X API"]; meta [label="Meta API\\nIG／Threads 發布與 Insights"]; feeds [label="RSS／GitHub／HN／HF\\n電子報"];',
        'llm [label="Claude API"]; web [label="網站／YouTube\\n（補料）"]; agg [label="聚合發布服務\\nYouTube／TikTok（R6）"]; r2 [label="物件儲存\\n暫存公開網址（R6）"]; phone [label="手機\\nntfy 推播"]; }',
        'subgraph cluster_host { label="創作者的電腦（Windows 11）"; style="rounded"; color="#42617d";',
        'obs [label="Obsidian\\n開同一個 newsroom/ 資料夾"]; cc [label="Claude Code\\nslash command"];',
        'subgraph cluster_wsl { label="WSL2（Ubuntu，systemd）"; style="rounded"; color="#7a8fa3";',
        'timer [label="systemd timer\\n收集／早報／成效回收"]; cli [label="newsroom CLI\\n（Python 3.11＋poetry）"];',
        'node_r [label="render/\\n（Node＋Remotion 或 HyperFrames）"]; tools [label="ffmpeg／ffprobe\\nyt-dlp／Playwright"];',
        'ml [label="本機模型\\nASR／對齊／斷詞／嵌入"];',
        'ws [label="newsroom/ 工作區\\nMarkdown＋media＋SQLite", shape=cylinder];',
        'subgraph cluster_docker { label="Docker"; style="rounded,dashed"; cd [label="changedetection.io"]; ntfy [label="ntfy（選用自架）"]; } } }',
        'obs -> ws [dir=both, label="人讀寫 Markdown"]; cc -> cli; timer -> cli; cli -> ws [dir=both];',
        'cli -> node_r [label="subprocess＋JSON"]; cli -> tools; cli -> ml; node_r -> ws;',
        'cd -> cli [label="webhook"]; cli -> ntfy -> phone;',
        'cli -> x; cli -> feeds; cli -> web; cli -> llm; cli -> meta; cli -> agg; cli -> r2;',
    ]
    render('system-architecture', body, '系統架構圖｜部署與外部依賴', rankdir='LR')


def software() -> None:
    body = [
        'subgraph cluster_ui { label="入口層"; style="rounded"; color="#9aa7b3";',
        f'node [fillcolor="{SHARED}"]; cli [label="cli（Typer）"]; slash [label=".claude/commands\\n/inbox /produce /reel …"]; }}',
        'subgraph cluster_app { label="流程層"; style="rounded"; color="#9aa7b3";',
        f'orch [label="orchestrator\\nJob／鎖／指紋／重跑", fillcolor="{SHUWEI}"]; sched [label="scheduler／notify／budget", fillcolor="{SHUWEI}"]; }}',
        'subgraph cluster_domain { label="領域模組（各自實作 Step 介面）"; style="rounded"; color="#9aa7b3";',
        f'collect [label="collect\\n收集器、正規化、聚類、熱度、inbox、建題", fillcolor="{ALBERT}"];',
        f'fetch [label="fetch\\n原文擷取、分層補料、素材盤點", fillcolor="{ALBERT}"];',
        f'draft [label="draft\\n事實抽取、寫稿、引用檢查、版本", fillcolor="{CHANGXIAO}"];',
        f'prod [label="production\\nbuild 轉換、字幕、輪播、Reel、規格檢查", fillcolor="{CHANGXIAO}"];',
        f'pub [label="publish\\nreview、approve、發布包、發布", fillcolor="{SHUWEI}"];',
        f'fb [label="feedback\\n成效、週報、建議", fillcolor="{SHUWEI}"]; }}',
        'subgraph cluster_infra { label="基礎層（core）"; style="rounded"; color="#9aa7b3";',
        f'ws [label="workspace\\n路徑、受管區塊、front-matter、原子寫入", fillcolor="{ALBERT}"];',
        f'db [label="db\\nSQLite、migration、repository", fillcolor="{ALBERT}"];',
        f'cfg [label="config／log", fillcolor="{ALBERT}"]; schema [label="schemas\\n07 介面契約", fillcolor="{SHARED}"]; }}',
        'subgraph cluster_adapters { label="Adapter 層（可替換的外部工具）"; style="rounded"; color="#9aa7b3"; node [fillcolor="#f7f7f7"];',
        'a_col [label="Collector\\nrss／github／hn／x／…"]; a_dl [label="Extractor／Downloader\\ntrafilatura／yt-dlp／…"];',
        'a_llm [label="LLM\\nAnthropic"]; a_asr [label="Aligner／ASR"]; a_render [label="Renderer\\nNode CLI"];',
        'a_pub [label="Publisher／Metrics\\nIG／Threads／…"]; a_note [label="Notifier\\nntfy"]; }',
        'node_r [label="render/（Node）\\n模板與元件", fillcolor="#fdf0dc"];',
        'cli -> orch; slash -> cli; sched -> orch;',
        'orch -> collect; orch -> fetch; orch -> draft; orch -> prod; orch -> pub; orch -> fb;',
        'collect -> a_col; fetch -> a_dl; draft -> a_llm; prod -> a_asr; prod -> a_render -> node_r; pub -> a_pub; fb -> a_pub; sched -> a_note;',
        'collect -> ws [lhead=cluster_infra, style=dashed, label="所有領域模組都只依賴 core"];',
        'pub -> db [style=dashed]; draft -> schema [style=dashed];',
        *legend(),
    ]
    render('software-architecture', body, '軟體架構圖｜分層與模組歸屬（依賴方向由上往下）')


def dataflow() -> None:
    body = [
        f'src [label="來源\\nX／RSS／HN／手動網址", fillcolor="{EXT}"];',
        f'col [label="collect", fillcolor="{ALBERT}"]; inbox [label="inbox/YYYY-MM-DD.md\\n（IF-001）", shape=note];',
        'human1 [label="人：勾選、節目、格式、備註", shape=oval, fillcolor="#fff7d6"];',
        f'topic [label="建題\\ntopic.md／content.md\\n（IF-002／003）", fillcolor="{ALBERT}"];',
        f'fetch [label="fetch", fillcolor="{ALBERT}"]; srcmd [label="sources/*.md（IF-004）\\nassets.json（IF-005）", shape=note];',
        f'draft [label="draft", fillcolor="{CHANGXIAO}"]; drafts [label="drafts/rNNN（IF-006）\\nscript／carousel／caption", shape=note];',
        'human2 [label="人：改稿、select、錄旁白", shape=oval, fillcolor="#fff7d6"];',
        f'prod [label="production", fillcolor="{CHANGXIAO}"]; build [label="builds/<id>\\nshots／captions／manifest\\n（IF-010～012）＋out/", shape=note];',
        f'review [label="review／approve", fillcolor="{SHUWEI}"]; human3 [label="人：處理待確認項、確認", shape=oval, fillcolor="#fff7d6"];',
        f'pub [label="發布包／發布", fillcolor="{SHUWEI}"]; publ [label="publications（IF-016）", shape=cylinder];',
        f'fb [label="成效／週報", fillcolor="{SHUWEI}"]; metrics [label="metric_snapshots（IF-017）\\nreports/weekly.md", shape=cylinder];',
        'src -> col -> inbox -> human1 -> topic -> fetch -> srcmd -> draft -> drafts -> human2 -> prod -> build -> review;',
        'human3 -> review [dir=back]; review -> pub -> publ -> fb -> metrics;',
        'metrics -> human1 [style=dashed, label="下週選題參考"];',
        'human2 -> fetch [style=dashed, label="補素材"]; review -> human2 [style=dashed, label="要修改"];',
    ]
    render('data-flow', body, '資料流｜交棒物與人工決策點', rankdir='LR')


def erd() -> None:
    def table(name: str, cols: list[str], color: str) -> str:
        rows = ''.join(f'<tr><td align="left">{c}</td></tr>' for c in cols)
        return (f'{name} [shape=plain, label=<<table border="0" cellborder="1" cellspacing="0" cellpadding="4" bgcolor="{color}">'
                f'<tr><td bgcolor="#42617d"><font color="white"><b>{name}</b></font></td></tr>{rows}</table>>];')

    body = [
        table('sources', ['source_id PK', 'url／canonical_url', 'kind／publisher／author', 'published_at', 'first_seen_at', 'local_path'], ALBERT),
        table('source_signals', ['source_id FK', 'collector', 'signal', 'captured_at'], ALBERT),
        table('candidates', ['candidate_id PK', 'title／title_zh', 'tier／score', 'cluster_key', 'status'], ALBERT),
        table('candidate_sources', ['candidate_id FK', 'source_id FK'], ALBERT),
        table('topics', ['topic_id PK', 'title／program／tier'], ALBERT),
        table('topic_sources', ['topic_id FK', 'source_id FK'], ALBERT),
        table('assets', ['topic_id＋asset_id PK', 'path／type／origin', 'source_id FK', 'fetch_layer／sha256'], ALBERT),
        table('contents', ['topic_id＋content_id PK', 'program／format', 'status', 'selected_revision', 'wave'], SHUWEI),
        table('jobs', ['job_id PK', 'kind／target', 'status／attempt', 'input_fingerprint', 'error_code', 'cost_usd'], SHUWEI),
        table('builds', ['build_id PK', 'content_ref FK', 'manifest_path', 'status／stale'], CHANGXIAO),
        table('approvals', ['approval_id PK', 'content_ref FK', 'build_id FK', 'approved_at／revoked_at'], SHUWEI),
        table('publications', ['publication_id PK', 'content_ref FK', 'build_id FK', 'platform／status', 'url／published_at'], SHUWEI),
        table('metric_snapshots', ['publication_id FK', 'captured_at', 'source／metric_version', 'metrics_json'], SHUWEI),
        'sources -> source_signals [label="1:N"]; candidates -> candidate_sources; sources -> candidate_sources;',
        'topics -> topic_sources; sources -> topic_sources; topics -> assets [label="1:N"]; sources -> assets [style=dashed, label="0..1"];',
        'topics -> contents [label="1:N"]; contents -> builds [label="1:N"]; contents -> jobs [style=dashed, label="target"];',
        'builds -> approvals; contents -> approvals; builds -> publications; contents -> publications [label="1:N"];',
        'publications -> metric_snapshots [label="1:N"]; candidates -> topics [style=dashed, label="picked"];',
    ]
    render('erd', body, 'ERD｜SQLite 主要資料表（顏色代表負責人）', rankdir='LR')


if __name__ == '__main__':
    system()
    software()
    dataflow()
    erd()
    print('ok')

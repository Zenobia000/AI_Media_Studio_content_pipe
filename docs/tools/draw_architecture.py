"""Render the ERD with local Graphviz.

System architecture, software architecture and data flow are authored with archify: diagrams/*.architecture.json, *.dataflow.json.
"""
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
ALBERT, CHANGXIAO, SHUWEI = '#e3f0e1', '#fdf0dc', '#e8e4f6'


def render(name: str, body: list[str], title: str, rankdir: str = 'TB') -> None:
    dot = OUT / f'{name}.dot'
    lines = ['digraph G {', f'graph [rankdir={rankdir}, label="{title}"];', *HEAD, *body, '}']
    dot.write_text('\n'.join(lines), encoding='utf-8')
    for fmt in ('png', 'svg'):
        subprocess.run(['dot', f'-T{fmt}', str(dot), '-o', str(OUT / f'{name}.{fmt}')], check=True)


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
        table('agent_runs', ['run_id PK', 'skill／trigger', 'status／turns', 'started_at／ended_at', 'cost_usd'], SHUWEI),
        table('jobs', ['job_id PK', 'run_id FK', 'kind／target', 'status／attempt', 'input_fingerprint', 'error_code', 'cost_usd'], SHUWEI),
        table('builds', ['build_id PK', 'content_ref FK', 'manifest_path', 'status／stale'], CHANGXIAO),
        table('approvals', ['approval_id PK', 'content_ref FK', 'build_id FK', 'run_id FK／human_quote', 'approved_at／revoked_at'], SHUWEI),
        table('publications', ['publication_id PK', 'content_ref FK', 'build_id FK', 'platform／status', 'url／published_at'], SHUWEI),
        table('metric_snapshots', ['publication_id FK', 'captured_at', 'source／metric_version', 'metrics_json'], SHUWEI),
        'sources -> source_signals [label="1:N"]; candidates -> candidate_sources; sources -> candidate_sources;',
        'topics -> topic_sources; sources -> topic_sources; topics -> assets [label="1:N"]; sources -> assets [style=dashed, label="0..1"];',
        'topics -> contents [label="1:N"]; contents -> builds [label="1:N"]; contents -> jobs [style=dashed, label="target"];',
        'builds -> approvals; contents -> approvals; builds -> publications; contents -> publications [label="1:N"];',
        'publications -> metric_snapshots [label="1:N"]; candidates -> topics [style=dashed, label="picked"];',
        'agent_runs -> jobs [label="1:N"]; agent_runs -> approvals [style=dashed, label="0..1"];',
    ]
    render('erd', body, 'ERD｜SQLite 主要資料表（顏色代表負責人）', rankdir='LR')


if __name__ == '__main__':
    erd()
    print('ok')

"""Render the creator's end-to-end user flow with local Graphviz."""
from pathlib import Path
import subprocess

OUT = Path(__file__).resolve().parent.parent / 'diagrams'
OUT.mkdir(exist_ok=True)
FONT = 'Microsoft JhengHei'

# Shape/colour per actor: human steps stand out from automated ones.
HUMAN = 'shape=box, style="rounded,filled,bold", fillcolor="#fde3c8", color="#c0702a"'
SYSTEM = 'shape=box, style="rounded,filled", fillcolor="#e4eef8", color="#42617d"'
DECIDE = 'shape=diamond, style="filled", fillcolor="#fff6d6", color="#b08a1e", margin="0.04,0.04"'
EXTERNAL = 'shape=box, style="rounded,filled,dashed", fillcolor="#f4f4f4", color="#7a7a7a"'
FILE = 'shape=note, style="filled", fillcolor="#ffffff", color="#9aa7b3", fontsize=11'


def node(name: str, label: str, style: str) -> str:
    return f'{name} [label="{label}", {style}];'


def phase(key: str, title: str, nodes: list[str]) -> list[str]:
    return [f'subgraph cluster_{key} {{ label="{title}"; style="rounded"; color="#9aa7b3"; fontsize=15;', *nodes, '}']


def body() -> list[str]:
    lines: list[str] = []
    lines += phase('collect', '① 收集與選題（每天）', [
        node('timer', '定時收集\\nRSS／GitHub／HN／電子報／X\\n去重、聚類、熱度分級', SYSTEM),
        node('notify', 'S 級事件推播／早報\\n（ntfy）', SYSTEM),
        node('inbox_md', 'inbox.md', FILE),
        node('read_inbox', '在 Obsidian 看 inbox\\n勾選要做的題目', HUMAN),
        node('add_src', '看到好題目\\nnewsroom add-source <url>', HUMAN),
    ])
    lines += phase('produce', '② 產出草稿', [
        node('produce', '執行 /produce\\n（newsroom produce）', HUMAN),
        node('pipeline', '建題 → 原文擷取 → 分層補料\\n→ 素材盤點 → 事實抽取\\n→ 寫稿 → 引用檢查', SYSTEM),
        node('draft_md', 'script.md／carousel.md\\ncaption.md／media.md', FILE),
        node('edit', '審稿與修改\\n處理「待確認」的引用', HUMAN),
        node('ok_draft', '稿件\\n可以用？', DECIDE),
        node('redraft', 'newsroom redraft／select\\n重生或換版本', HUMAN),
        node('gap', '素材缺口（L5）\\n人工截圖、錄影補進 media/raw', HUMAN),
    ])
    lines += phase('make', '③ 製作成品（依格式）', [
        node('fmt', '格式？', DECIDE),
        node('carousel', '產生輪播圖卡\\nnewsroom carousel', SYSTEM),
        node('record', '錄旁白\\n放進 media/raw/', HUMAN),
        node('subtitle', '字幕：講稿對齊錄音\\n斷詞、分行\\nnewsroom subtitle', SYSTEM),
        node('reel', '渲染 Reel 9:16\\nnewsroom reel', SYSTEM),
        node('spec', '規格檢查\\n尺寸、長度、字數', SYSTEM),
    ])
    lines += phase('publish', '④ 確認與發布', [
        node('review', '預覽成品與待確認項\\nnewsroom review', HUMAN),
        node('ok_final', '確認\\n發布？', DECIDE),
        node('approve', 'newsroom approve', HUMAN),
        node('route', '發布方式？', DECIDE),
        node('native', '到 IG／Threads 設原生排程\\nnewsroom schedule（紀錄）', HUMAN),
        node('auto', '自動發布\\n（R6，官方 API 或聚合服務）', SYSTEM),
        node('platform', 'Instagram／Threads', EXTERNAL),
        node('mark', '填回貼文網址\\nnewsroom mark-published', HUMAN),
    ])
    lines += phase('feedback', '⑤ 回收成效（每週）', [
        node('metrics', '回收成效\\nInsights 自動（R5）\\n或 newsroom metrics add 手填', SYSTEM),
        node('retro', '產生週報與建議\\nnewsroom retro', SYSTEM),
        node('read_retro', '讀週報\\n調整選題與節目比例', HUMAN),
    ])
    lines += [
        'subgraph cluster_fail { label="任何一步失敗"; style="rounded,dashed"; color="#c25450"; fontsize=13;',
        node('fail', 'Job 失敗或被擋\\n推播通知', SYSTEM),
        node('fix', 'newsroom status 看原因\\n修正後 newsroom retry', HUMAN),
        '}',
    ]
    lines += [
        'timer -> inbox_md; timer -> notify [style=dashed]; notify -> read_inbox [style=dashed];',
        'inbox_md -> read_inbox; add_src -> inbox_md [style=dashed];',
        'read_inbox -> produce; produce -> pipeline -> draft_md -> edit -> ok_draft;',
        'ok_draft -> redraft [label="否"]; redraft -> pipeline [style=dashed];',
        'pipeline -> gap [label="缺素材", style=dashed, color="#c0702a"]; gap -> pipeline [style=dashed];',
        'ok_draft -> fmt [label="是"];',
        'fmt -> review [label="Threads 文字"];',
        'fmt -> carousel [label="輪播"]; carousel -> spec;',
        'fmt -> record [label="Reel"]; record -> subtitle -> reel -> spec;',
        'spec -> review;',
        'review -> ok_final; ok_final -> edit [label="否：改稿\\n成品自動失效", style=dashed, color="#c0702a", constraint=false];',
        'ok_final -> approve [label="是"]; approve -> route;',
        'route -> native [label="R1–R5"]; route -> auto [label="R6"];',
        'native -> platform; auto -> platform; platform -> mark [label="原生排程時"];',
        'mark -> metrics; auto -> metrics; metrics -> retro -> read_retro;',
        'read_retro -> read_inbox [label="下一輪", style=dashed, constraint=false];',
        'fail -> fix;',
        'subgraph cluster_legend { label="圖例"; style="rounded,dashed"; color="#9aa7b3"; fontsize=12;',
        node('lg_h', '人工操作', HUMAN), node('lg_s', '系統自動', SYSTEM),
        node('lg_d', '判斷', DECIDE), node('lg_f', '產出檔案', FILE), node('lg_e', '外部平台', EXTERNAL),
        'lg_h -> lg_s -> lg_d -> lg_f -> lg_e [style=invis]; }',
    ]
    return lines


def render() -> None:
    dot = OUT / 'user-flow.dot'
    lines = [
        'digraph G {',
        f'graph [rankdir=TB, label="User Flow｜創作者從選題到回收成效", labelloc=t, fontsize=20, '
        f'fontname="{FONT}", bgcolor="white", pad="0.4", nodesep="0.4", ranksep="0.5", compound=true];',
        f'node [fontname="{FONT}", fontsize=13, margin="0.16,0.08"];',
        f'edge [fontname="{FONT}", fontsize=11, color="#526779", arrowsize=0.75];',
        *body(),
        '}',
    ]
    dot.write_text('\n'.join(lines), encoding='utf-8')
    for fmt in ('png', 'svg'):
        subprocess.run(['dot', f'-T{fmt}', str(dot), '-o', str(OUT / f'user-flow.{fmt}')], check=True)


if __name__ == '__main__':
    render()
    print('ok')

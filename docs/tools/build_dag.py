"""Build the ticket DAG from ticket front-matter, validate it, and render outputs.

Single source of truth: docs/tickets/T-*.md front-matter.
Outputs: diagrams/dag.{dot,png,svg}, tickets/README.md, epic ticket tables in tickets/E*.md.
Exit code 1 when validation fails (unknown deps, cycles, sprint order, uncovered FRs).
"""
from __future__ import annotations

import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TICKETS = ROOT / 'tickets'
SRS = ROOT / 'spec'
DIAGRAMS = ROOT / 'diagrams'
FONT = 'Microsoft JhengHei'
NL = chr(10)
CAPACITY = 10  # points (person-days) per person per two-week sprint
ALL = '全體'
PEOPLE = ['Albert Chiang', '周長曉', '楊舒媁']
COLORS = {'Albert Chiang': '#e3f0e1', '周長曉': '#fdf0dc', '楊舒媁': '#e8e4f6', ALL: '#ffffff'}
TYPE_STYLE = {'spike': 'rounded,filled,dashed', 'integration': 'rounded,filled,bold'}


def parse_front_matter(path: Path) -> dict:
    text = path.read_text(encoding='utf-8')
    m = re.match(r'---\n(.*?)\n---\n', text, re.S)
    if not m:
        raise ValueError(f'{path.name}: missing front-matter')
    data = {}
    for line in m.group(1).splitlines():
        key, _, value = line.partition(':')
        value = value.strip()
        if value.startswith('[') and value.endswith(']'):
            data[key.strip()] = [v.strip() for v in value[1:-1].split(',') if v.strip()]
        else:
            data[key.strip()] = value
    data['points'] = int(data['points'])
    return data


def sprint_no(s: str) -> int:
    return int(s.lstrip('S'))


def load() -> dict[str, dict]:
    return {t['id']: t for t in (parse_front_matter(p) for p in sorted(TICKETS.glob('T-*.md')))}


def srs_requirements() -> set[str]:
    ids = set()
    for p in SRS.glob('*.md'):
        ids |= set(re.findall(r'\*\*([A-Z]+-FR-\d{3})｜', p.read_text(encoding='utf-8')))
    ids.discard('XXX-FR-001')  # format example in SRS 00, not a requirement
    return ids


def validate(t: dict[str, dict]) -> list[str]:
    errors = []
    for tid, x in t.items():
        for d in x['depends_on']:
            if d not in t:
                errors.append(f'{tid}: unknown dependency {d}')
            elif sprint_no(t[d]['sprint']) > sprint_no(x['sprint']):
                errors.append(f"{tid} ({x['sprint']}) depends on {d} scheduled later ({t[d]['sprint']})")
    # Cycle detection via DFS colouring.
    state: dict[str, int] = {}

    def visit(n: str, stack: list[str]) -> None:
        state[n] = 1
        for d in t[n]['depends_on']:
            if d not in t:
                continue
            if state.get(d) == 1:
                errors.append('cycle: ' + ' -> '.join(stack + [n, d]))
            elif d not in state:
                visit(d, stack + [n])
        state[n] = 2

    for n in t:
        if n not in state:
            visit(n, [])
    implemented = {r for x in t.values() for r in x['implements']}
    for r in sorted(srs_requirements() - implemented):
        errors.append(f'requirement not implemented by any ticket: {r}')
    return errors


def loads(t: dict[str, dict]) -> dict[tuple[str, str], int]:
    out: dict[tuple[str, str], int] = defaultdict(int)
    for x in t.values():
        owners = PEOPLE if x['owner'] == ALL else [x['owner']]
        for o in owners:
            out[(o, x['sprint'])] += x['points']
    return out


def critical_path(t: dict[str, dict]) -> tuple[int, list[str]]:
    memo: dict[str, tuple[int, list[str]]] = {}

    def longest(n: str) -> tuple[int, list[str]]:
        if n not in memo:
            best = (0, [])
            for d in t[n]['depends_on']:
                cand = longest(d)
                if cand[0] > best[0]:
                    best = cand
            memo[n] = (best[0] + t[n]['points'], best[1] + [n])
        return memo[n]

    return max((longest(n) for n in t), key=lambda x: x[0])


def downstream(t: dict[str, dict]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for tid, x in t.items():
        for d in x['depends_on']:
            out[d].append(tid)
    return out


def handoffs(t: dict[str, dict]) -> list[tuple[str, str]]:
    """Edges where the baton passes between two different people."""
    edges = []
    for tid, x in t.items():
        for d in x['depends_on']:
            a, b = t[d]['owner'], x['owner']
            if a != b and ALL not in (a, b):
                edges.append((d, tid))
    return edges


def node_line(tid: str, x: dict, crit: set[str]) -> str:
    title = x['title'] if len(x['title']) <= 20 else x['title'][:19] + '…'
    style = TYPE_STYLE.get(x['type'], 'rounded,filled')
    pen = ', penwidth=2.5, color="#c0392b"' if tid in crit else ''
    return (f'"{tid}" [label="{tid}\n{title}\n{x["points"]} 點", '
            f'fillcolor="{COLORS[x["owner"]]}", style="{style}"{pen}];')


def edge_line(d: str, tid: str, t: dict, crit_edges: set, hand: set) -> str:
    if (d, tid) in crit_edges:
        return f'"{d}" -> "{tid}" [color="#c0392b", penwidth=2.5];'
    if (d, tid) in hand:
        return f'"{d}" -> "{tid}" [color="#e67e22", penwidth=1.6];'
    return f'"{d}" -> "{tid}";'


HEADER = [f'node [shape=box, style="rounded,filled", fontname="{FONT}", fontsize=12, color="#42617d", margin="0.14,0.07"];',
          'edge [color="#9aa7b3", arrowsize=0.7];']
LEGEND = '顏色＝負責人；虛線框＝Spike；粗框＝整合驗收；紅＝關鍵路徑；橘線＝跨人交棒；灰節點＝前面 Sprint 交過來的票'


def render_sprint(t: dict[str, dict], s: str, crit: list[str]) -> None:
    """One swimlane diagram per sprint: lanes are people, ghost nodes are upstream tickets from earlier sprints."""
    crit_set, crit_edges, hand = set(crit), set(zip(crit, crit[1:])), set(handoffs(t))
    members = {k: v for k, v in t.items() if v['sprint'] == s}
    lines = ['digraph S {', f'graph [rankdir=LR, bgcolor="white", fontname="{FONT}", fontsize=20, labelloc=t, '
             f'nodesep=0.3, ranksep=0.7, label="{s}｜接力圖\n{LEGEND}"];', *HEADER]
    for owner in PEOPLE + [ALL]:
        lane = sorted(k for k, v in members.items() if v['owner'] == owner)
        if not lane:
            continue
        pts = sum(members[k]['points'] for k in lane)
        lines.append(f'subgraph "cluster_{owner}" {{ label="{owner}（{pts} 點）"; style="rounded,filled"; '
                     f'fillcolor="{COLORS[owner]}40"; color="#c7d0d9"; fontsize=15;')
        lines += [node_line(k, members[k], crit_set) for k in lane]
        lines.append('}')
    ghosts = sorted({d for v in members.values() for d in v['depends_on'] if t[d]['sprint'] != s})
    for g in ghosts:
        lines.append(f'"{g}" [label="{g}\n{t[g]["owner"]}｜{t[g]["sprint"]}", fillcolor="#f2f2f2", color="#b0b0b0", fontcolor="#777777", fontsize=10];')
    for k, v in members.items():
        lines += [edge_line(d, k, t, crit_edges, hand) for d in v['depends_on']]
    lines.append('}')
    write_graph(f'dag-{s}', lines)


def render_full(t: dict[str, dict], crit: list[str]) -> None:
    """Whole-project DAG without sprint clusters; meant to be zoomed in the SVG."""
    crit_set, crit_edges, hand = set(crit), set(zip(crit, crit[1:])), set(handoffs(t))
    lines = ['digraph DAG {', f'graph [rankdir=LR, bgcolor="white", fontname="{FONT}", fontsize=22, labelloc=t, '
             f'nodesep=0.2, ranksep=0.6, label="全專案 Ticket DAG（請開 SVG 放大）\n{LEGEND}"];', *HEADER]
    lines += [node_line(k, v, crit_set) for k, v in sorted(t.items())]
    lines += [edge_line(d, k, t, crit_edges, hand) for k, v in t.items() for d in v['depends_on']]
    lines.append('}')
    write_graph('dag', lines)


def write_graph(name: str, lines: list[str]) -> None:
    dot = DIAGRAMS / f'{name}.dot'
    dot.write_text(NL.join(lines), encoding='utf-8')
    for fmt in ('png', 'svg'):
        subprocess.run(['dot', f'-T{fmt}', str(dot), '-o', str(DIAGRAMS / f'{name}.{fmt}')], check=True)


def render_readme(t: dict[str, dict], load: dict, crit_len: int, crit: list[str]) -> None:
    down = downstream(t)
    sprints = sorted({x['sprint'] for x in t.values()}, key=sprint_no)
    out = ['# Tickets 總表', '',
           '> 這份檔案由 `python docs/tools/build_dag.py` 產生，請勿手動修改。要改票，請改 `T-*.md` 的 front-matter 後重新執行。', '',
           f'共 {len(t)} 張票、{sum(x["points"] for x in t.values())} 點（1 點＝1 人天；`全體` 的票每人各計一次）。', '',
           '## 每人每 Sprint 負載', '',
           '| 負責人 | ' + ' | '.join(sprints) + ' | 合計 |', '|---|' + '---|' * (len(sprints) + 1)]
    for p in PEOPLE:
        cells = []
        for s in sprints:
            v = load.get((p, s), 0)
            cells.append(f'**{v}** ⚠' if v > CAPACITY else str(v))
        out.append(f'| {p} | ' + ' | '.join(cells) + f' | {sum(load.get((p, s), 0) for s in sprints)} |')
    out += ['', f'容量假設：每人每 Sprint {CAPACITY} 點；超過時標 ⚠。', '',
            '## 關鍵路徑', '', f'總長 {crit_len} 點：' + ' → '.join(f'[{c}]({c}.md)' for c in crit), '',
            '關鍵路徑上的票延誤，整個專案就會延誤；每日站會優先檢查這條路徑。', '',
            '## 全部票', '']
    for s in sprints:
        out += [f'### {s}', '', '| 票 | 標題 | 負責人 | 點 | 類型 | 前置票 | 交棒給 | 狀態 |', '|---|---|---|---|---|---|---|---|']
        for tid in sorted((k for k, v in t.items() if v['sprint'] == s)):
            x = t[tid]
            deps = '、'.join(x['depends_on']) or '—'
            nxt = '、'.join(sorted(down.get(tid, []))) or '—'
            out.append(f"| [{tid}]({tid}.md) | {x['title']} | {x['owner']} | {x['points']} | {x['type']} | {deps} | {nxt} | {x['status']} |")
        out.append('')
    out += ['## Epic', ''] + [f'- [{p.stem}]({p.name})' for p in sorted(TICKETS.glob('E*.md'))] + ['']
    (TICKETS / 'README.md').write_text('\n'.join(out), encoding='utf-8')


def render_epics(t: dict[str, dict]) -> None:
    """Rewrite each epic file's ticket table from ticket front-matter."""
    for path in sorted(TICKETS.glob('E*.md')):
        text = path.read_text(encoding='utf-8')
        head, sep, _ = text.partition('| 票 | 標題 |')
        if not sep:
            continue
        rows = sorted((v for v in t.values() if v['epic'] == path.stem), key=lambda v: (sprint_no(v['sprint']), v['id']))
        table = ['| 票 | 標題 | 負責人 | Sprint | 估點 |', '|---|---|---|---|---|']
        table += [f"| [{v['id']}]({v['id']}.md) | {v['title']} | {v['owner']} | {v['sprint']} | {v['points']} |" for v in rows]
        table += ['', f"共 {len(rows)} 張、{sum(v['points'] for v in rows)} 點。", '']
        path.write_text(head + NL.join(table), encoding='utf-8')


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    t = load()
    errors = validate(t)
    load_map = loads(t)
    crit_len, crit = critical_path(t)
    if not errors:
        render_full(t, crit)
        for s in sorted({x['sprint'] for x in t.values()}, key=sprint_no):
            render_sprint(t, s, crit)
        render_readme(t, load_map, crit_len, crit)
        render_epics(t)
    print(f'tickets: {len(t)}  points: {sum(x["points"] for x in t.values())}')
    sprints = sorted({x['sprint'] for x in t.values()}, key=sprint_no)
    for p in PEOPLE:
        row = '  '.join(f'{s}={load_map.get((p, s), 0):>2}' for s in sprints)
        print(f'{p:<14} {row}')
    print(f'critical path ({crit_len} pts): ' + ' -> '.join(crit))
    print(f'cross-person handoffs: {len(handoffs(t))}')
    over = [(p, s, v) for (p, s), v in load_map.items() if v > CAPACITY]
    for p, s, v in over:
        print(f'WARN over capacity: {p} {s} = {v}')
    for e in errors:
        print('ERROR', e)
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())

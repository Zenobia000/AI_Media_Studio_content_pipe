"""Render UML state diagrams with local Graphviz; export PlantUML source too."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent / 'diagrams'
ROOT.mkdir(exist_ok=True)

def draw(name, title, states, transitions, terminal=None):
    lines = ['digraph G {', 'graph [rankdir=TB, bgcolor="white", pad="0.35", nodesep="0.7", ranksep="0.65", fontname="Microsoft JhengHei", fontsize=22, labelloc=t, label="'+title+'"];', 'node [shape=box, style="rounded,filled", fillcolor="#eef4fa", color="#42617d", fontname="Microsoft JhengHei", fontsize=15, margin="0.2,0.12"];', 'edge [fontname="Microsoft JhengHei", fontsize=12, color="#526779", arrowsize=0.8];', 'initial [shape=circle, label="", width=0.16, height=0.16, fillcolor="#182c3b", color="#182c3b"];']
    puml = ['@startuml', 'title '+title, 'skinparam defaultFontName Microsoft JhengHei', 'hide empty description']
    for key, label in states.items():
        lines.append(f'{key} [label="{label}"];')
        puml.append(f'state "{label}" as {key}')
    if terminal:
        lines.append('final [shape=doublecircle, label="", width=0.18, height=0.18, fillcolor="#182c3b", color="#182c3b"];')
    for src, dst, event in transitions:
        escaped = event.replace('"', '\\"')
        lines.append(f'{src} -> {dst} [label="{escaped}"];')
        puml.append(f'{"[*]" if src == "initial" else src} --> {"[*]" if dst == "final" else dst}' + (f' : {event}' if event else ''))
    lines.append('}')
    puml.append('@enduml')
    dot = ROOT / f'{name}.dot'
    dot.write_text('\n'.join(lines), encoding='utf-8')
    (ROOT / f'{name}.puml').write_text('\n'.join(puml), encoding='utf-8')
    for fmt in ('png', 'svg'):
        subprocess.run(['dot', f'-T{fmt}', str(dot), '-o', str(ROOT / f'{name}.{fmt}')], check=True)

draw('content-state', 'Content｜單則內容狀態', {
    'drafting':'草稿中 / drafting', 'review':'待改稿 / review',
    'producing':'製作中 / producing', 'ready':'待成品確認 / ready',
    'approved':'已確認 / approved', 'published':'已發布 / published'}, [
    ('initial','drafting','選題並執行 produce / 建立內容'),
    ('drafting','review','草稿生成成功 / 提供來源與預覽'),
    ('review','drafting','要求重生草稿 / 保留人工稿'),
    ('review','producing','啟動製作 [稿件與必要素材已備妥]'),
    ('producing','ready','輸出成功 / 保存成品版本'),
    ('ready','review','要求修改 / 標記成品需重製'),
    ('ready','approved','人確認 [待確認項已處理] / 綁定版本'),
    ('approved','review','修改內容 / 撤銷本版本確認'),
    ('approved','published','記錄實際發布 [版本已確認]'),
    ('published','published','另一平台發布／回填成效 / 更新對應紀錄')])

draw('job-state', 'Job｜單一步驟執行狀態', {
    'queued':'排隊中 / queued', 'running':'執行中 / running',
    'blocked':'待補資料 / blocked', 'failed':'執行失敗 / failed',
    'succeeded':'執行成功 / succeeded'}, [
    ('initial','queued','建立工作'),
    ('queued','running','取得執行鎖 / 記錄 attempt'),
    ('running','blocked','必要輸入不足 / 列出缺口'),
    ('blocked','queued','人補齊資料並要求重跑'),
    ('running','failed','工具錯誤／程序中斷 / 保存錯誤'),
    ('failed','queued','要求重試 [問題已處理]'),
    ('running','succeeded','執行完成 / 保存輸出路徑'),
    ('succeeded','final','')], terminal=True)

draw('publication-state', 'Publication｜單一平台發布狀態', {
    'pending':'待人工發布 / pending', 'scheduled':'平台已排程 / scheduled',
    'published':'已實際發布 / published'}, [
    ('initial','pending','建立發布紀錄 [成品版本已確認]'),
    ('pending','scheduled','人完成平台原生排程 / 記錄預定時間'),
    ('scheduled','pending','取消排程／尚未發布 / 更新紀錄'),
    ('pending','published','人工發布並填回網址、時間'),
    ('scheduled','published','確認已實際發布 / 填回網址、時間'),
    ('published','published','回填成效 / 新增 MetricSnapshot')])
print('Rendered 3 UML state diagrams: PNG, SVG, PlantUML, DOT.')

"""Makes the three design directions for the front page (a-editorial.html, b-studio.html, c-magazine.html) and index.html, from real items of the dev database
(only titles without Simplified-only characters, so the sample reads as the Traditional site would).

    python docs/design/frontend-directions/build.py

These are static pictures of a page: nothing here is connected to the backend, and nothing in AIHOT/apps/web is changed."""

from __future__ import annotations

import html
import json
from pathlib import Path

import psycopg

OUT = Path(__file__).resolve().parent
SIMPLIFIED = set(
    "发为这个们来时从对说会与产开关机进过还种后点应经动现间问专业东两严丰临义乐习书买乱争于云亚亲仅仓价众优伙伟传伤华协单卖卫历压厂县参双变叙号叹启员响团园图国圆场坏块坚墙备复头夹奋奖妇妈学宁宝实审导将尔尝层属岁岛币师带帮广庆库异弃张弹强归当录彻忆忧怀态总恶惊愿户扩护报担拥择挂损换据摇数断旧显晓暂术杀杂权条杨极构枪标样桥检楼欢歼气汇汉沟没泪泽洁济浅测满滚灭灯灵炼烁热爱牵独狭猎猪献环琐电疗痒盘证评识诉词译试诗话诚误说请诸读课调谁谈谊谋谓谢豪贝贞负贡财责贤败账货质贩贪贫购贯贵贷贸费贺资赏赛赞赢赵趋跃踪车轨转轮软轰轻载较辅辆辈输辑辩边达迁迈运远违连迟适选逊递逻遗邓邮邻郑酱释针钉钓钟钢钥钱钻铁铃铜铭银铺链销锁锅锋错键锡锦镇镜长门闪闭闯闲闷闹阀队阳阴阵阶际陆陈险随隐难雏雾静页顶项顺须顾顿颁颂预领颇频颗题颜额风飞饭饮饱饼馆馈马驱驶驻驾验骑骗骤鱼鲜鸟鸡麦黄齐齿龄龙"
)
CAT = {"ai-models": "模型", "ai-products": "產品", "industry": "產業", "paper": "論文", "opinion": "觀點", "tip": "教學"}


def load() -> dict:
    conn = psycopg.connect("postgres://postgres:ci@127.0.0.1:55432/dev")
    rows = conn.execute(
        """select p.title, left(coalesce(p.summary, ''), 130), coalesce(p.reason, ''), p.category, p.score, s.name, p.timeline_at, p.selected
           from publications p join sources s on s.id = p.source_id
           where p.visibility = 'public' and p.title ~ '[一-鿿]' and p.category is not null and p.score is not null
           order by p.selected desc, p.score desc, p.timeline_at desc limit 120"""
    ).fetchall()
    items = []
    for r in rows:
        text = r[0] + r[1]
        if any(c in SIMPLIFIED for c in text):
            continue
        items.append(
            {
                "title": r[0],
                "summary": r[1],
                "reason": r[2],
                "cat": CAT.get(r[3], r[3]),
                "score": float(r[4]),
                "source": r[5],
                "time": r[6].strftime("%m/%d %H:%M"),
                "selected": r[7],
            }
        )
    stories = conn.execute(
        """select s.title, (select count(*) from facts f join fact_articles fa on fa.fact_id = f.id where f.story_id = s.id) n
           from stories s order by n desc, s.updated_at desc limit 40"""
    ).fetchall()
    stories = [{"title": s[0], "n": s[1]} for s in stories if not any(c in SIMPLIFIED for c in s[0])][:6]
    return {"items": items[:12], "stories": stories}


def e(s: str) -> str:
    return html.escape(s)


def tier(score: float) -> str:
    return "S" if score >= 80 else "A" if score >= 60 else "B"


# ---------------------------------------------------------------- A: editorial
def page_a(d: dict) -> str:
    lead, rest = d["items"][0], d["items"][1:]
    stories = "".join(f'<li><span class="n">{i + 1}</span>{e(s["title"])}<em>{s["n"]} 篇報導</em></li>' for i, s in enumerate(d["stories"]))
    rows = "".join(
        f'<article><div class="meta"><b>{e(x["cat"])}</b> · {e(x["source"])} · {x["time"]}</div><h3>{e(x["title"])}</h3><p>{e(x["summary"])}</p></article>'
        for x in rest[:7]
    )
    return f"""<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>方向 A · 編輯部</title>
<style>
:root{{--paper:#faf7f2;--ink:#1c1a17;--ink2:#5b554c;--line:#ded6c8;--accent:#b3261e}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:"Noto Serif TC","Source Han Serif TC","PMingLiU",Georgia,serif;line-height:1.75}}
header{{border-bottom:3px double var(--ink);padding:22px 0 10px;text-align:center}}header h1{{margin:0;font-size:38px;letter-spacing:.25em}}header p{{margin:4px 0 0;color:var(--ink2);font-size:13px;letter-spacing:.1em;font-family:system-ui,"Microsoft JhengHei",sans-serif}}
nav{{display:flex;justify-content:center;gap:28px;padding:10px;border-bottom:1px solid var(--line);font:14px system-ui,"Microsoft JhengHei",sans-serif}}nav a{{color:var(--ink2);text-decoration:none}}nav a:first-child{{color:var(--accent);font-weight:700}}
main{{max-width:1080px;margin:0 auto;padding:28px 24px;display:grid;grid-template-columns:1fr 300px;gap:44px}}
.lead{{border-bottom:1px solid var(--line);padding-bottom:22px;margin-bottom:8px}}.lead .meta{{color:var(--accent)}}.lead h2{{font-size:34px;line-height:1.35;margin:6px 0 10px}}.lead p{{font-size:17px;color:var(--ink2);margin:0}}
article{{padding:16px 0;border-bottom:1px solid var(--line)}}article h3{{margin:2px 0 4px;font-size:20px;line-height:1.45}}article p{{margin:0;color:var(--ink2);font-size:15px}}
.meta{{font:12px system-ui,"Microsoft JhengHei",sans-serif;color:var(--ink2);letter-spacing:.05em}}.meta b{{color:var(--accent)}}
aside h4{{margin:0 0 6px;font:700 13px system-ui,"Microsoft JhengHei",sans-serif;letter-spacing:.2em;border-bottom:2px solid var(--ink);padding-bottom:6px}}
aside ol{{list-style:none;padding:0;margin:0 0 30px}}aside li{{padding:10px 0;border-bottom:1px solid var(--line);font-size:15px;line-height:1.5}}aside .n{{display:inline-block;width:26px;color:var(--accent);font-weight:700}}aside em{{display:block;margin-left:26px;font:12px system-ui,"Microsoft JhengHei",sans-serif;color:var(--ink2)}}
footer{{text-align:center;color:var(--ink2);font:12px system-ui,"Microsoft JhengHei",sans-serif;padding:30px}}
@media(max-width:820px){{main{{grid-template-columns:1fr}}}}
</style>
<header><h1>MediaHOT</h1><p>AI 產業每日要聞　·　由編輯部與模型共同整理</p></header>
<nav><a>今日精選</a><a>模型</a><a>產品</a><a>產業</a><a>論文</a><a>教學</a><a>觀點</a></nav>
<main><section>
<div class="lead"><div class="meta"><b>頭條 · {e(lead["cat"])}</b> · {e(lead["source"])} · {lead["time"]}</div><h2>{e(lead["title"])}</h2><p>{e(lead["summary"])}</p></div>
{rows}</section>
<aside><h4>當前熱點</h4><ol>{stories}</ol><h4>本日數字</h4><ol><li>收錄 <b>{len(d["items"]) * 30}</b> 篇<em>示意數字</em></li><li>精選 <b>{sum(1 for x in d["items"] if x["selected"])}</b> 篇<em>示意數字</em></li></ol></aside></main>
<footer>方向 A：報紙式排版。重點是文字層次，幾乎不靠卡片與色塊。</footer></html>"""


# ---------------------------------------------------------------- B: studio
def page_b(d: dict) -> str:
    rows = "".join(
        f'<div class="row{" on" if i == 0 else ""}"><span class="tier t{tier(x["score"])}">{tier(x["score"])}</span><div><div class="t">{e(x["title"])}</div>'
        f'<div class="m">{e(x["cat"])} · {e(x["source"])} · {x["time"]}</div></div><span class="sc">{x["score"]:.0f}</span><input type="checkbox"></div>'
        for i, x in enumerate(d["items"])
    )
    pick = d["items"][0]
    ev = "".join(f'<div class="ev"><b>{s["n"]}</b>{e(s["title"])}</div>' for s in d["stories"][:4])
    return f"""<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>方向 B · 選題工作台</title>
<style>
:root{{--bg:#0f1318;--p:#171d25;--p2:#1e2630;--line:#2a3441;--ink:#e8edf3;--ink2:#93a1b3;--S:#ff5d5d;--A:#ffb020;--B:#5aa7ff;--ok:#3ddc97}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,"Microsoft JhengHei","PingFang TC",sans-serif;height:100vh;display:grid;grid-template-rows:48px 1fr}}
.top{{display:flex;align-items:center;gap:18px;padding:0 16px;background:var(--p);border-bottom:1px solid var(--line)}}.top b{{font-size:16px}}.top span{{color:var(--ink2)}}.top .r{{margin-left:auto;color:var(--ok)}}
.app{{display:grid;grid-template-columns:190px 1fr 360px;min-height:0}}
.side{{background:var(--p);border-right:1px solid var(--line);padding:14px 10px}}.side h5{{margin:14px 8px 6px;color:var(--ink2);font-size:11px;letter-spacing:.15em}}.side a{{display:flex;justify-content:space-between;padding:7px 10px;border-radius:6px;color:var(--ink2)}}.side a.on{{background:var(--p2);color:var(--ink)}}
.list{{overflow:auto;padding:12px 16px}}.bar{{display:flex;gap:10px;margin-bottom:10px;color:var(--ink2)}}.bar .chip{{padding:3px 10px;border:1px solid var(--line);border-radius:99px}}.bar .chip.on{{background:var(--p2);color:var(--ink)}}
.row{{display:grid;grid-template-columns:34px 1fr 40px 22px;gap:12px;align-items:center;padding:10px 12px;border:1px solid var(--line);border-radius:8px;margin-bottom:8px;background:var(--p)}}.row.on{{border-color:#4b6b92;background:var(--p2)}}
.tier{{width:30px;height:30px;border-radius:8px;display:grid;place-items:center;font-weight:800;color:#0f1318}}.tS{{background:var(--S)}}.tA{{background:var(--A)}}.tB{{background:var(--B)}}.t{{font-weight:600}}.m{{color:var(--ink2);font-size:12px}}.sc{{color:var(--ink2);text-align:right}}
.detail{{background:var(--p);border-left:1px solid var(--line);padding:16px;overflow:auto}}.detail h2{{font-size:18px;line-height:1.4;margin:6px 0 8px}}.detail p{{color:var(--ink2)}}.why{{background:var(--p2);border-left:3px solid var(--ok);padding:8px 12px;border-radius:4px;margin:10px 0}}
.btns{{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:14px 0}}.btn{{padding:9px;text-align:center;border-radius:8px;border:1px solid var(--line);background:var(--p2)}}.btn.main{{background:var(--ok);color:#0b2a1e;font-weight:700;border:none;grid-column:span 2}}
.ev{{padding:8px 0;border-bottom:1px solid var(--line);color:var(--ink2)}}.ev b{{display:inline-block;width:26px;color:var(--A)}}h6{{margin:18px 0 4px;color:var(--ink2);font-size:11px;letter-spacing:.15em}}
@media(max-width:980px){{.app{{grid-template-columns:1fr}}.side,.detail{{display:none}}}}
</style>
<div class="top"><b>MediaHOT 選題台</b><span>今天 11/05 · 共 {len(d["items"]) * 30} 則候選</span><span class="r">● 收集運作中</span></div>
<div class="app"><div class="side"><h5>今日清單</h5><a class="on">全部候選<span>{len(d["items"]) * 30}</span></a><a>S 級（超熱）<span>{sum(1 for x in d["items"] if tier(x["score"]) == "S")}</span></a><a>A 級<span>{sum(1 for x in d["items"] if tier(x["score"]) == "A")}</span></a><a>昨日未選<span>7</span></a>
<h5>分類</h5><a>模型</a><a>產品</a><a>產業</a><a>論文</a><a>教學</a><a>觀點</a><h5>製作中</h5><a>題目資料夾<span>3</span></a><a>待寫稿<span>2</span></a></div>
<div class="list"><div class="bar"><span class="chip on">依熱度</span><span class="chip">依時間</span><span class="chip">只看未處理</span><span style="margin-left:auto">已勾選 1 則</span></div>{rows}</div>
<div class="detail"><div class="m">{e(pick["cat"])} · {e(pick["source"])} · {pick["time"]}</div><h2>{e(pick["title"])}</h2><p>{e(pick["summary"])}</p>
<div class="why"><b>為什麼值得做</b><br>{e(pick["reason"] if any("一" <= c <= "鿿" for c in pick["reason"]) else "熱度高，多個來源同時討論。")}</div>
<div class="btns"><div class="btn main">建立題目資料夾</div><div class="btn">做成貼文</div><div class="btn">做成影片</div><div class="btn">合併到其他題目</div><div class="btn">稍後再看</div></div>
<h6>同一件事的其他報導</h6>{ev}</div></div>
<!-- 方向 B：給創作者每天挑題目用的工作台。重點是熱度分級、一鍵建立題目，而不是閱讀文章。 --></html>"""


# ---------------------------------------------------------------- C: magazine
def page_c(d: dict) -> str:
    colors = {"模型": "#6d5dfc", "產品": "#ff7a45", "產業": "#12b886", "論文": "#339af0", "教學": "#f59f00", "觀點": "#e64980"}
    cards = "".join(
        f'<a class="card"><span class="chip" style="background:{colors.get(x["cat"], "#888")}">{e(x["cat"])}</span><h3>{e(x["title"])}</h3><p>{e(x["summary"])}</p><div class="m"><span>{e(x["source"])}</span><span>{x["time"]}</span>'
        f"{'<span class=pick>精選</span>' if x['selected'] else ''}</div></a>"
        for x in d["items"][1:10]
    )
    hot = "".join(
        f'<div class="h"><b>{i + 1}</b><div>{e(s["title"])}<br><small>{s["n"]} 篇報導</small></div></div>' for i, s in enumerate(d["stories"][:4])
    )
    hero = d["items"][0]
    return f"""<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>方向 C · 雜誌牆</title>
<style>
:root{{--bg:#f4f5f9;--card:#fff;--ink:#14151a;--ink2:#6b7080;--line:#e4e6ee}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 system-ui,"Microsoft JhengHei","PingFang TC",sans-serif}}
header{{display:flex;align-items:center;gap:24px;padding:16px 32px;background:#fff;border-bottom:1px solid var(--line);position:sticky;top:0}}header b{{font-size:22px;background:linear-gradient(90deg,#6d5dfc,#ff7a45);-webkit-background-clip:text;color:transparent}}header a{{color:var(--ink2)}}header .s{{margin-left:auto;background:var(--bg);border-radius:99px;padding:7px 16px;color:var(--ink2)}}
.wrap{{max-width:1180px;margin:0 auto;padding:28px 24px}}
.hero{{display:grid;grid-template-columns:1.6fr 1fr;gap:20px;margin-bottom:28px}}.big{{background:linear-gradient(135deg,#251f5e,#6d5dfc 60%,#ff7a45);color:#fff;border-radius:20px;padding:34px;min-height:280px;display:flex;flex-direction:column;justify-content:flex-end}}.big .chip{{align-self:flex-start;background:rgba(255,255,255,.2);padding:3px 12px;border-radius:99px;font-size:13px}}.big h2{{font-size:30px;line-height:1.35;margin:14px 0 8px}}.big p{{margin:0;opacity:.85}}
.hot{{background:var(--card);border-radius:20px;padding:22px;border:1px solid var(--line)}}.hot h4{{margin:0 0 10px}}.h{{display:flex;gap:12px;padding:10px 0;border-bottom:1px solid var(--line)}}.h b{{width:26px;height:26px;border-radius:8px;background:var(--bg);display:grid;place-items:center;color:#ff7a45}}.h small{{color:var(--ink2)}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}}.card{{background:var(--card);border-radius:16px;padding:20px;border:1px solid var(--line);display:flex;flex-direction:column;gap:6px;transition:.15s}}.card:hover{{transform:translateY(-3px);box-shadow:0 10px 30px #1a1d3a14}}
.chip{{align-self:flex-start;color:#fff;font-size:12px;padding:2px 10px;border-radius:99px}}.card h3{{margin:6px 0 0;font-size:17px;line-height:1.45}}.card p{{margin:0;color:var(--ink2);font-size:14px}}.m{{margin-top:auto;padding-top:10px;display:flex;gap:12px;color:var(--ink2);font-size:12px}}.pick{{margin-left:auto;color:#d9480f;font-weight:700}}
@media(max-width:900px){{.hero,.grid{{grid-template-columns:1fr}}}}
</style>
<header><b>MediaHOT</b><a>精選</a><a>全部</a><a>熱點事件</a><a>日報週報</a><span class="s">搜尋標題、摘要…</span></header>
<div class="wrap"><div class="hero"><div class="big"><span class="chip">{e(hero["cat"])} · 今日最受關注</span><h2>{e(hero["title"])}</h2><p>{e(hero["summary"])}</p></div><div class="hot"><h4>🔥 當前熱點</h4>{hot}</div></div>
<div class="grid">{cards}</div></div>
<!-- 方向 C：卡片牆。色彩分類、大標題主視覺，偏向一般讀者的資訊站。 --></html>"""


def index(d: dict) -> str:
    return """<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><title>前端設計方向</title><style>body{font:16px/1.7 system-ui,"Microsoft JhengHei",sans-serif;max-width:760px;margin:40px auto;padding:0 20px}
a{display:block;padding:16px 20px;margin:12px 0;border:1px solid #ddd;border-radius:12px;color:inherit;text-decoration:none}a:hover{background:#f6f6f6}small{color:#666}</style>
<h1>MediaHOT 前端：三個設計方向</h1><p>靜態示意，內容是你資料庫裡真實的文章（只取繁體標題）。沒有接後端，也沒有動現有的網站。</p>
<a href="a-editorial.html"><b>A　編輯部</b><br><small>報紙式排版，重文字層次。偏讀者閱讀。</small></a>
<a href="b-studio.html"><b>B　選題工作台</b><br><small>三欄：篩選／清單（S、A、B 熱度）／詳情與一鍵建立題目。偏創作者每天挑題目。</small></a>
<a href="c-magazine.html"><b>C　雜誌牆</b><br><small>卡片牆、色彩分類、大標題主視覺。偏一般讀者的資訊站。</small></a></html>"""


def main() -> None:
    d = load()
    (OUT / "sample-data.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    for name, fn in (("a-editorial.html", page_a), ("b-studio.html", page_b), ("c-magazine.html", page_c), ("index.html", index)):
        (OUT / name).write_text(fn(d), encoding="utf-8")
    print(f"{len(d['items'])} items, {len(d['stories'])} stories -> {OUT}")


if __name__ == "__main__":
    main()

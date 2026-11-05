"""Usage: python -I tests/tools/compare_real_pages.py <repository root> <pages.json from collect_real_pages.py> <a work folder>
Needs Node and AIHOT/node_modules.

Reads the collected pages with the TypeScript engine's readable() and with the Python page reader, and compares everything: html, text, images, via, dates."""

import json
import os
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
pages = json.load(open(sys.argv[2], encoding="utf8"))
work = Path(sys.argv[3])
work.mkdir(exist_ok=True)
(work / "pages.json").write_text(json.dumps([{"html": p["html"], "url": p["url"]} for p in pages]), encoding="utf-8")
env = {**os.environ, "TZ": "UTC", "DATABASE_URL": "postgres://x"}
out = subprocess.run(
    ["node", "scripts/extract-run.ts", str(work / "pages.json")], cwd=root / "AIHOT", env=env, check=True, capture_output=True, timeout=600
)
node = json.loads(out.stdout.decode().strip().splitlines()[-1])

sys.path.insert(0, str(root / "src"))
from newsroom.engine.page_reader import PageRules, readable  # noqa: E402

same = diff = nothing = 0
rows = []
for p, n in zip(pages, node, strict=True):
    try:
        got = readable(p["html"], p["url"], PageRules(None, [], None))
    except Exception as e:  # noqa: BLE001
        got = {"error": str(e)[:200]}
    if n is None or got is None:
        state = (
            "nothing/nothing"
            if (n is None and got is None)
            else f"MISMATCH nothing-vs-article node={'nothing' if n is None else 'article'} py={'nothing' if got is None else 'article'}"
        )
    elif "error" in n or isinstance(got, dict):
        state = "error/error" if ("error" in n) == isinstance(got, dict) else f"MISMATCH error node={n} py={got}"
    else:
        py = {
            "html": got.html, "text": got.text, "images": got.images, "via": got.via,
            "publishedAt": got.published_at.isoformat(timespec="milliseconds").replace("+00:00", "Z") if got.published_at else None,
            "publicationTime": got.publication_time,
        }  # fmt: skip
        bad = [k for k, v in py.items() if n.get(k) != v]
        state = "identical" if not bad else "DIFF " + ",".join(bad)
        if bad:
            k = bad[0]
            rows.append((p["name"], k, str(py[k])[:200], str(n.get(k))[:200]))
    if state.startswith(("identical", "nothing", "error")):
        same += 1
        nothing += state.startswith(("nothing", "error"))
    else:
        diff += 1
    print(f"{p['name']:<22} {len(p['html']):>8}  {state}")
print(f"\nidentical: {same} (of which no article/error on both sides: {nothing}); different: {diff}; total {len(pages)}")
for r in rows:
    print("\n", r[0], r[1], "\n  py  ", r[2], "\n  node", r[3])

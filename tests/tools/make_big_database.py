"""Usage: python tests/tools/make_big_database.py <postgres url of an empty copy, e.g. postgres://postgres:ci@127.0.0.1:55432/big> [copies]

Makes a database big enough for a load check: the 40 articles of the seeded database, with what belongs to them (analyses, publications, search text, revisions,
discoveries), copied `copies` times (default 1250, so 50,000 articles). Every copy has its own id, address and identity key, and its times are earlier by one minute
per copy, so that lists and pages have an order and the search finds many rows. The target must be a copy of the seeded template (create database big template
seeded_tpl); the load check is tests/tools/load_check.py."""

from __future__ import annotations

import sys
import time

import psycopg

url = sys.argv[1]
copies = int(sys.argv[2]) if len(sys.argv) > 2 else 1250
NEW_ID = "substr(md5(a.{col} || k::text), 1, 25)"
SHIFT = "(k::text || ' minutes')::interval"
# table -> (the column holding the article's id, columns that get a new value, columns shifted back in time)
TABLES = {
    "articles": (
        "id",
        {"identity_key": "a.identity_key || '#' || k::text", "url": "a.url || '?c=' || k::text"},
        ["timeline_at", "discovered_at", "published_at"],
    ),
    "analyses": ("article_id", {}, []),
    "publications": ("article_id", {}, ["timeline_at", "sort_at", "published_at"]),
    "pool_search": ("article_id", {}, []),
    "article_revisions": ("article_id", {}, []),
    "article_discoveries": ("article_id", {}, []),
}

with psycopg.connect(url, autocommit=True) as conn:
    started = time.time()
    for table, (id_col, replaced, shifted) in TABLES.items():
        columns = [
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = %s ORDER BY ordinal_position", (table,)
            ).fetchall()
        ]
        if table == "analyses":
            columns = [c for c in columns if c != "id"]  # a new number from the sequence
        exprs = []
        for c in columns:
            if c == id_col:
                exprs.append(NEW_ID.format(col=id_col))
            elif c in replaced:
                exprs.append(replaced[c])
            elif c in shifted:
                exprs.append(f"a.{c} - {SHIFT}")
            else:
                exprs.append(f"a.{c}")
        sql = f"INSERT INTO {table} ({', '.join(columns)}) SELECT {', '.join(exprs)} FROM {table} a CROSS JOIN generate_series(1, %s) k WHERE true"
        # the originals are read first, so a table is not read again while it grows
        sql = sql.replace(f"FROM {table} a CROSS", f"FROM (SELECT * FROM {table}) a CROSS")
        conn.execute(sql, (copies,))
        print(f"{table}: {conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]} rows ({time.time() - started:.0f}s)", flush=True)
    for table in TABLES:
        conn.execute(f"ANALYZE {table}")
    print("done", f"{time.time() - started:.0f}s")

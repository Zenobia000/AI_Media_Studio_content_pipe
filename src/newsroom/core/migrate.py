"""Applies the SQL files of core/migrations by name, recorded in the same schema_migrations table AIHOT's own migrate script uses, so
the two never apply a file twice. AIHOT's engine migrations (database/migrations) are applied by AIHOT; `apply_dir` also serves
tests, which build a whole database from both."""

from __future__ import annotations

from pathlib import Path

MIGRATIONS = Path(__file__).with_name("migrations")


def apply_dir(conn, directory: Path | str = MIGRATIONS) -> list[str]:
    """Applies the .sql files of a folder, in file-name order, that schema_migrations does not list yet. Returns the names applied."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
    done = {r["name"] for r in conn.execute("SELECT name FROM schema_migrations").fetchall()}
    applied: list[str] = []
    for file in sorted(Path(directory).glob("*.sql"), key=lambda p: p.name):
        if file.name in done:
            continue
        text = file.read_text(encoding="utf-8")
        if "CONCURRENTLY" in text.upper():
            # A concurrent index build cannot run inside a transaction block (AIHOT's migrate script does the same).
            conn.execute(text)
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (file.name,))
        else:
            with conn.transaction():
                conn.execute(text)
                conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (file.name,))
        applied.append(file.name)
    return applied

"""SQLite FTS5 index for who_mentions queries."""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path
from typing import Any

from . import ris_client as rc
from .content import html_to_markdown

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "ris.db"


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _conn() as conn:
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(
                document_id UNINDEXED,
                short_title UNINDEXED,
                paragraph UNINDEXED,
                doc_url UNINDEXED,
                in_force_from UNINDEXED,
                body,
                tokenize='unicode61'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS crawl_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)


def _upsert(conn: sqlite3.Connection, doc_id: str, short_title: str,
            paragraph: str, doc_url: str, in_force_from: str, body: str) -> None:
    conn.execute("DELETE FROM docs WHERE document_id = ?", (doc_id,))
    conn.execute(
        "INSERT INTO docs(document_id, short_title, paragraph, doc_url, in_force_from, body) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (doc_id, short_title, paragraph, doc_url, in_force_from, body),
    )


def search_fts(query: str, limit: int = 20) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT document_id, short_title, paragraph, doc_url, in_force_from "
            "FROM docs WHERE body MATCH ? ORDER BY rank LIMIT ?",
            (query, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def doc_count() -> int:
    try:
        with _conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    except sqlite3.OperationalError:
        return 0


async def crawl(pages: int = 0, delay: float = 0.5) -> int:
    """Crawl BrKons and index into FTS. pages=0 means all."""
    init_db()
    indexed = 0
    page = 1
    total = None

    while True:
        refs, hits = await rc.search_bundesrecht(
            pro_seite="OneHundred",
            seite=page,
        )
        if total is None:
            total = hits
            logger.info("Crawl started: %d total docs", total)

        if not refs:
            break

        htmls = await asyncio.gather(*[rc.fetch_document_html(r) for r in refs])

        with _conn() as conn:
            for ref, html in zip(refs, htmls):
                meta = rc._meta_from_ref(ref)
                body = html_to_markdown(html)
                _upsert(conn, meta["document_id"], meta["short_title"],
                        meta["paragraph"], meta["doc_url"],
                        meta["in_force_from"], body)
            conn.execute(
                "INSERT OR REPLACE INTO crawl_meta VALUES ('last_page', ?)",
                (str(page),),
            )

        indexed += len(refs)
        logger.info("Page %d: indexed %d docs (total so far: %d)", page, len(refs), indexed)

        if pages and page >= pages:
            break
        if indexed >= hits:
            break

        page += 1
        await asyncio.sleep(delay)

    return indexed


if __name__ == "__main__":
    import sys
    pages = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    logging.basicConfig(level=logging.INFO)
    n = asyncio.run(crawl(pages=pages))
    print(f"Indexed {n} documents")

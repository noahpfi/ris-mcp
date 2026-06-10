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


def _existing_ids() -> set[str]:
    try:
        with _conn() as conn:
            rows = conn.execute("SELECT document_id FROM docs").fetchall()
            return {r[0] for r in rows}
    except sqlite3.OperationalError:
        return set()


def _resume_page() -> int:
    try:
        with _conn() as conn:
            row = conn.execute(
                "SELECT value FROM crawl_meta WHERE key = 'last_page'"
            ).fetchone()
            return int(row[0]) if row else 1
    except sqlite3.OperationalError:
        return 1


async def crawl(pages: int = 0, base_delay: float = 0.5, resume: bool = True) -> int:
    """Crawl BrKons and index into FTS. pages=0 means all."""
    init_db()
    indexed = 0
    page = _resume_page() if resume else 1
    total = None
    batch_delay = base_delay

    if page > 1:
        logger.info("Resuming from page %d", page)

    while True:
        refs, hits = await rc.search_bundesrecht(
            pro_seite="OneHundred",
            seite=page,
        )
        if total is None:
            total = hits
            logger.info("Crawl started: %d total docs, starting at page %d", total, page)

        if not refs:
            break

        results = []
        for i in range(0, len(refs), 10):
            batch = refs[i:i + 10]
            batch_results = await asyncio.gather(
                *[rc.fetch_document_html(r) for r in batch],
                return_exceptions=True,
            )
            results.extend(batch_results)

            errors = sum(1 for r in batch_results if isinstance(r, Exception) or not r)
            if errors >= 2:
                batch_delay = min(batch_delay * 2, 30.0)
                logger.warning("Rate errors (%d/batch) — slowing to %.1fs", errors, batch_delay)
            elif errors == 0 and batch_delay > base_delay:
                batch_delay = max(batch_delay * 0.85, base_delay)

            await asyncio.sleep(batch_delay)

        with _conn() as conn:
            for ref, html in zip(refs, results):
                if isinstance(html, Exception) or not html:
                    logger.warning("Skipping %s", rc._meta_from_ref(ref)["document_id"])
                    continue
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
        logger.info("Page %d/%d: +%d docs (%d total indexed) [delay=%.1fs]",
                    page, (total // 100) + 1, len(refs), indexed, batch_delay)

        if pages and page >= pages:
            break

        page += 1

    return indexed


async def fill_gaps(batch_size: int = 20, base_delay: float = 1.0) -> int:
    """Fetch and index documents present in the API but missing from the local index."""
    init_db()
    known = _existing_ids()
    logger.info("Index has %d docs. Scanning API for gaps...", len(known))

    missing: list[dict] = []
    page = 1
    total = None

    while True:
        refs, hits = await rc.search_bundesrecht(pro_seite="OneHundred", seite=page)
        if total is None:
            total = hits
            logger.info("API reports %d total docs", total)
        if not refs:
            break
        for ref in refs:
            if rc._meta_from_ref(ref)["document_id"] not in known:
                missing.append(ref)
        logger.info("Scanned page %d/%d — %d gaps so far", page, (total // 100) + 1, len(missing))
        page += 1

    logger.info("Found %d missing docs. Fetching...", len(missing))
    indexed = 0
    batch_delay = base_delay

    for i in range(0, len(missing), batch_size):
        batch = missing[i:i + batch_size]
        results = await asyncio.gather(
            *[rc.fetch_document_html(r) for r in batch],
            return_exceptions=True,
        )

        errors = sum(1 for r in results if isinstance(r, Exception) or not r)
        if errors >= 2:
            batch_delay = min(batch_delay * 2, 30.0)
            logger.warning("Rate errors (%d/batch) — slowing to %.1fs", errors, batch_delay)
        elif errors == 0 and batch_delay > base_delay:
            batch_delay = max(batch_delay * 0.85, base_delay)

        with _conn() as conn:
            for ref, html in zip(batch, results):
                if isinstance(html, Exception) or not html:
                    logger.warning("Skipping %s", rc._meta_from_ref(ref)["document_id"])
                    continue
                meta = rc._meta_from_ref(ref)
                body = html_to_markdown(html)
                _upsert(conn, meta["document_id"], meta["short_title"],
                        meta["paragraph"], meta["doc_url"],
                        meta["in_force_from"], body)
                indexed += 1
        logger.info("Gap fill: %d/%d indexed [delay=%.1fs]", indexed, len(missing), batch_delay)
        await asyncio.sleep(batch_delay)

    return indexed


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) > 1 and sys.argv[1] == "--fill-gaps":
        n = asyncio.run(fill_gaps())
        print(f"Gap-filled {n} documents")
    else:
        pages = int(sys.argv[1]) if len(sys.argv) > 1 else 0
        n = asyncio.run(crawl(pages=pages))
        print(f"Indexed {n} documents")

"""SQLite FTS5 index for who_mentions queries."""
from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from cachetools import TTLCache

from . import ris_client as rc
from .config import DB_PATH
from .content import html_to_markdown

logger = logging.getLogger(__name__)

BUSY_TIMEOUT_MS = 5000
# doc_count() is a full FTS5 scan and is hit by every who_mentions call and
# every container healthcheck. The number moves only when the crawler runs.
_COUNT_TTL = 300
_count_cache: TTLCache = TTLCache(maxsize=2, ttl=_COUNT_TTL)


@contextmanager
def _connect(*, readonly: bool = False) -> Iterator[sqlite3.Connection]:
    """Open a connection, commit or roll back, and always close it.

    `with sqlite3.connect(...)` is a transaction context manager, not a closing
    one — using it directly leaks a file descriptor per call, which only shows
    up once the process is long-lived (HTTP) rather than one-shot (stdio).

    Readers open the file read-write and rely on `query_only` rather than
    `mode=ro`: the index runs in WAL, and a read-only handle cannot create the
    -shm sidecar it needs when no writer holds the database open.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    if readonly:
        conn.execute("PRAGMA query_only = 1")
    try:
        yield conn
        if not readonly:
            conn.commit()
    except BaseException:
        if not readonly:
            conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with _connect() as conn:
        # WAL lets the server keep serving reads while the crawler writes.
        conn.execute("PRAGMA journal_mode = WAL")
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


# unicode61 splits on everything that is not a letter or digit, so mirroring
# that here keeps the terms we send identical to the ones the index holds.
_FTS_TERM = re.compile(r"[^\W_]+", re.UNICODE)


def fts_query(raw: str) -> str:
    """Turn a user string into a safe FTS5 query: each term quoted, ANDed.

    MATCH takes a query language, not a literal. An unbalanced quote, a bare
    `*`, or a word like `NOT` in the caller's citation would raise
    OperationalError or silently change the meaning — on a public endpoint that
    is a crash per request. Quoting each term preserves the previous
    implicit-AND semantics without exposing the operators.

    Returns "" when the input holds no indexable term.
    """
    return " ".join(f'"{term}"' for term in _FTS_TERM.findall(raw))


def search_fts(query: str, limit: int = 20) -> list[dict[str, Any]]:
    match = fts_query(query)
    if not match:
        return []
    with _connect(readonly=True) as conn:
        rows = conn.execute(
            "SELECT document_id, short_title, paragraph, doc_url, in_force_from "
            "FROM docs WHERE body MATCH ? ORDER BY rank LIMIT ?",
            (match, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def doc_count() -> int:
    cached = _count_cache.get("docs")
    if cached is not None:
        return cached
    try:
        with _connect(readonly=True) as conn:
            count = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    except sqlite3.OperationalError:
        return 0
    _count_cache["docs"] = count
    return count


def last_crawl() -> str:
    """ISO timestamp of the last completed crawl, or "" if never."""
    cached = _count_cache.get("last_crawl")
    if cached is not None:
        return cached
    try:
        with _connect(readonly=True) as conn:
            row = conn.execute(
                "SELECT value FROM crawl_meta WHERE key = 'last_crawl'"
            ).fetchone()
    except sqlite3.OperationalError:
        return ""
    value = row[0] if row else ""
    _count_cache["last_crawl"] = value
    return value


def _mark_crawled(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO crawl_meta VALUES ('last_crawl', ?)",
        (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),),
    )


def _existing_ids() -> set[str]:
    try:
        with _connect(readonly=True) as conn:
            rows = conn.execute("SELECT document_id FROM docs").fetchall()
            return {r[0] for r in rows}
    except sqlite3.OperationalError:
        return set()


def _resume_page() -> int:
    try:
        with _connect(readonly=True) as conn:
            row = conn.execute(
                "SELECT value FROM crawl_meta WHERE key = 'last_page'"
            ).fetchone()
            return int(row[0]) if row else 1
    except sqlite3.OperationalError:
        return 1


async def _fetch_adaptive(
    refs: list[dict[str, Any]], delay: float, base_delay: float
) -> tuple[list[Any], float]:
    """Fetch one batch concurrently and adapt the inter-batch delay to errors.

    Returns the results positionally aligned with `refs` (exceptions included)
    and the delay the next batch should use.
    """
    results = await asyncio.gather(
        *[rc.fetch_document_html(ref) for ref in refs],
        return_exceptions=True,
    )
    errors = sum(1 for r in results if isinstance(r, Exception) or not r)
    if errors >= 2:
        delay = min(delay * 2, 30.0)
        logger.warning("Rate errors (%d/batch) — slowing to %.1fs", errors, delay)
    elif errors == 0 and delay > base_delay:
        delay = max(delay * 0.85, base_delay)
    return results, delay


def _index_results(
    conn: sqlite3.Connection, refs: list[dict[str, Any]], results: list[Any]
) -> int:
    """Write the successful fetches of one batch. Returns how many landed."""
    indexed = 0
    for ref, html in zip(refs, results):
        meta = rc._meta_from_ref(ref)
        if isinstance(html, Exception) or not html:
            logger.warning("Skipping %s", meta["document_id"])
            continue
        _upsert(conn, meta["document_id"], meta["short_title"], meta["paragraph"],
                meta["doc_url"], meta["in_force_from"], html_to_markdown(html))
        indexed += 1
    return indexed


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

        results: list[Any] = []
        for i in range(0, len(refs), 10):
            batch_results, batch_delay = await _fetch_adaptive(
                refs[i:i + 10], batch_delay, base_delay
            )
            results.extend(batch_results)
            await asyncio.sleep(batch_delay)

        with _connect() as conn:
            _index_results(conn, refs, results)
            conn.execute(
                "INSERT OR REPLACE INTO crawl_meta VALUES ('last_page', ?)",
                (str(page),),
            )
            _mark_crawled(conn)

        indexed += len(refs)
        logger.info("Page %d/%d: +%d docs (%d total indexed) [delay=%.1fs]",
                    page, (total // 100) + 1, len(refs), indexed, batch_delay)

        if pages and page >= pages:
            break

        page += 1

    return indexed


async def fill_gaps(batch_size: int = 20, base_delay: float = 1.0) -> int:
    """Fetch and index documents present in the API but missing from the index.

    Scanning and fetching interleave on purpose. Collecting every missing
    reference before fetching held ~20KB per reference: against a 280k-document
    gap that is ~5.7GB resident, which the crawler container does not survive.
    Never more than one page plus one batch is live at a time.
    """
    init_db()
    known = _existing_ids()
    logger.info("Index has %d docs. Scanning API for gaps...", len(known))

    indexed = 0
    scanned = 0
    page = 1
    total = None
    batch_delay = base_delay
    pending: list[dict[str, Any]] = []

    async def flush(batch: list[dict[str, Any]]) -> int:
        nonlocal batch_delay
        results, batch_delay = await _fetch_adaptive(batch, batch_delay, base_delay)
        with _connect() as conn:
            landed = _index_results(conn, batch, results)
            _mark_crawled(conn)
        known.update(rc._meta_from_ref(ref)["document_id"] for ref in batch)
        await asyncio.sleep(batch_delay)
        return landed

    while True:
        refs, hits = await rc.search_bundesrecht(pro_seite="OneHundred", seite=page)
        if total is None:
            total = hits
            logger.info("API reports %d total docs", total)
        if not refs:
            break

        scanned += len(refs)
        pending.extend(
            ref for ref in refs if rc._meta_from_ref(ref)["document_id"] not in known
        )

        while len(pending) >= batch_size:
            indexed += await flush(pending[:batch_size])
            del pending[:batch_size]

        if page % 25 == 0:
            logger.info("Scanned %d/%d docs — %d indexed, %d queued [delay=%.1fs]",
                        scanned, total, indexed, len(pending), batch_delay)
        page += 1

    if pending:
        indexed += await flush(pending)

    # A run that found no gaps is still a completed pass over the API, and the
    # health endpoint reports staleness off this timestamp.
    with _connect() as conn:
        _mark_crawled(conn)

    logger.info("Gap fill complete: %d indexed of %d scanned", indexed, scanned)
    return indexed


async def _main(argv: list[str]) -> str:
    try:
        if argv and argv[0] == "--fill-gaps":
            return f"Gap-filled {await fill_gaps()} documents"
        pages = int(argv[0]) if argv else 0
        return f"Indexed {await crawl(pages=pages)} documents"
    finally:
        await rc.close()


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    print(asyncio.run(_main(sys.argv[1:])))

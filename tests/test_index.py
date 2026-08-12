"""Index layer: connection hygiene, FTS query construction, metadata."""
from __future__ import annotations

import os
import sqlite3

import pytest

from src import index


@pytest.fixture(autouse=True)
def fresh_db():
    if index.DB_PATH.exists():
        index.DB_PATH.unlink()
    for suffix in ("-wal", "-shm"):
        sidecar = index.DB_PATH.with_name(index.DB_PATH.name + suffix)
        if sidecar.exists():
            sidecar.unlink()
    index._count_cache.clear()
    index.init_db()
    yield
    index._count_cache.clear()


def _seed(*docs: tuple[str, str, str]) -> None:
    with index._connect() as conn:
        for doc_id, short_title, body in docs:
            index._upsert(conn, doc_id, short_title, "§ 1", "https://x/1", "1900-01-01", body)


def _open_fds() -> int:
    """Descriptor count for this process. /dev/fd exists on macOS and Linux."""
    return len(os.listdir("/dev/fd"))


@pytest.mark.skipif(not os.path.isdir("/dev/fd"), reason="no /dev/fd on this platform")
def test_connect_closes_the_connection():
    """`with sqlite3.connect(...)` commits but never closes — regression guard."""
    _seed(("a", "ABGB", "verweist auf § 1295 ABGB"))

    index._count_cache.clear()
    index.doc_count()
    index.search_fts("1295")  # warm anything that opens a lasting descriptor
    before = _open_fds()

    for _ in range(200):
        index._count_cache.clear()
        assert index.doc_count() == 1
        assert index.search_fts("1295")

    # The old pattern leaked one connection (up to three descriptors with WAL)
    # per call; 400 calls would be unmistakable.
    assert _open_fds() - before < 10


def test_fts_query_quotes_every_term():
    assert index.fts_query("§ 1295 ABGB") == '"1295" "ABGB"'
    assert index.fts_query("Art. 7 B-VG") == '"Art" "7" "B" "VG"'


def test_fts_query_neutralises_operators():
    """Raw MATCH input is a query language; these used to raise or change meaning."""
    for hostile in ('foo" OR body:*', "NEAR(a b)", "a*", 'unbalanced "', "AND", "^x"):
        built = index.fts_query(hostile)
        if not built:
            continue
        with index._connect(readonly=True) as conn:
            conn.execute("SELECT document_id FROM docs WHERE body MATCH ?", (built,)).fetchall()


def test_fts_query_empty_for_punctuation_only():
    assert index.fts_query("§§ —") == ""
    assert index.search_fts("§§ —") == []


def test_search_matches_all_terms_not_any():
    _seed(
        ("a", "ABGB", "Schadenersatz nach § 1295 ABGB"),
        ("b", "StGB", "§ 1295 steht hier ohne das andere Kürzel"),
    )
    hits = index.search_fts("1295 ABGB")
    assert [h["document_id"] for h in hits] == ["a"]


def test_search_respects_limit():
    _seed(*[(f"d{i}", "ABGB", "wiederholt Wort") for i in range(10)])
    assert len(index.search_fts("Wort", limit=3)) == 3


def test_doc_count_is_cached_until_cleared():
    _seed(("a", "ABGB", "eins"))
    assert index.doc_count() == 1
    _seed(("b", "ABGB", "zwei"))
    assert index.doc_count() == 1, "cached value expected"
    index._count_cache.clear()
    assert index.doc_count() == 2


def test_missing_database_reports_empty_not_crash():
    index.DB_PATH.unlink()
    index._count_cache.clear()
    assert index.doc_count() == 0
    assert index.last_crawl() == ""


def test_journal_mode_is_wal():
    with index._connect(readonly=True) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_readonly_connection_rejects_writes():
    with pytest.raises(sqlite3.OperationalError):
        with index._connect(readonly=True) as conn:
            conn.execute("DELETE FROM docs")


def test_mark_crawled_sets_timestamp():
    assert index.last_crawl() == ""
    with index._connect() as conn:
        index._mark_crawled(conn)
    index._count_cache.clear()
    stamp = index.last_crawl()
    assert stamp.endswith("Z") and stamp[4] == "-"


def test_rollback_on_error_leaves_index_untouched():
    _seed(("a", "ABGB", "eins"))
    index._count_cache.clear()
    with pytest.raises(RuntimeError):
        with index._connect() as conn:
            index._upsert(conn, "b", "ABGB", "§ 2", "u", "d", "zwei")
            raise RuntimeError("boom")
    index._count_cache.clear()
    assert index.doc_count() == 1

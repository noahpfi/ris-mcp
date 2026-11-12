"""Crawling RIS into the local index. Page arithmetic runs offline; crawls run against the real RIS API with
`pytest -m live`, a page or two at a time."""
from __future__ import annotations

import pytest

from src import index
from src import ris_client as rc


@pytest.fixture(autouse=True)
def fresh_db():
    for suffix in ("", "-wal", "-shm"):
        path = index.DB_PATH.with_name(index.DB_PATH.name + suffix)
        if path.exists():
            path.unlink()
    index._count_cache.clear()
    index.init_db()
    yield
    index._count_cache.clear()


@pytest.mark.parametrize("total, pages, last", [(2566, 0, 26), (2600, 0, 26), (2566, 5, 5), (2566, 40, 26), (0, 0, 0)])
def test_last_page_never_passes_the_end(total, pages, last):
    """RIS answers a page past the end with an error, so the crawl must stop on the last page itself."""
    assert index._last_page(total, pages) == last


@pytest.fixture
def recorded(monkeypatch):
    """Passes every call through to RIS and records the order of page scans and document fetches."""
    events: list[str] = []
    search, fetch = rc.search_bundesrecht, rc.fetch_document_html

    async def scan(**kwargs):
        events.append(f"scan:{kwargs.get('seite', 1)}")
        return await search(**kwargs)

    async def read(ref):
        events.append(f"fetch:{rc._meta_from_ref(ref)['document_id']}")
        return await fetch(ref)

    monkeypatch.setattr(rc, "search_bundesrecht", scan)
    monkeypatch.setattr(rc, "fetch_document_html", read)
    return events


def _count() -> int:
    index._count_cache.clear()
    return index.doc_count()


@pytest.mark.live
async def test_crawl_indexes_each_page_and_records_where_to_resume(recorded):
    indexed = await index.crawl(pages=1, base_delay=0, resume=False)
    assert indexed == index.PAGE_SIZE == _count()
    assert recorded.count("scan:1") == 1 and not [e for e in recorded if e.startswith("scan:2")]
    assert index._resume_page() == 1


@pytest.mark.live
async def test_fill_gaps_streams_and_skips_what_it_already_holds(recorded):
    """Fetching interleaves with scanning, since collecting every missing reference first held about 20 KB per
    reference, 5.7 GB at a gap of 280k."""
    indexed = await index.fill_gaps(batch_size=20, base_delay=0, pages=2)
    assert indexed == 2 * index.PAGE_SIZE == _count()
    assert recorded.index("scan:2") > next(i for i, e in enumerate(recorded) if e.startswith("fetch:"))
    first_pass = index.last_crawl()

    recorded.clear()
    assert await index.fill_gaps(batch_size=20, base_delay=0, pages=2) == 0
    assert not [e for e in recorded if e.startswith("fetch:")]
    assert index.last_crawl() >= first_pass


@pytest.mark.live
async def test_fill_gaps_skips_a_document_that_fails_and_indexes_the_rest(recorded, monkeypatch):
    fetch = rc.fetch_document_html
    failed: list[str] = []

    async def first_fails(ref):
        if not failed:
            failed.append(rc._meta_from_ref(ref)["document_id"])
            raise rc.RisApiError("RIS answered 503 Service Unavailable 4 times")
        return await fetch(ref)

    monkeypatch.setattr(rc, "fetch_document_html", first_fails)
    indexed = await index.fill_gaps(batch_size=20, base_delay=0, pages=1)
    assert indexed == index.PAGE_SIZE - 1 == _count()
    assert failed[0] not in index._existing_ids()

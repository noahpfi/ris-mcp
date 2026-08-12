"""Crawler behaviour. No network: the RIS client is stubbed."""
from __future__ import annotations

import pytest

from src import index
from src import ris_client as rc

PAGES = 12
PER_PAGE = 100


def _ref(doc_id: str) -> dict:
    return {"Data": {
        "Metadaten": {
            "Technisch": {"ID": doc_id},
            "Allgemein": {"DokumentUrl": f"https://ris/{doc_id}"},
            "Bundesrecht": {"Kurztitel": "ABGB", "BrKons": {
                "ArtikelParagraphAnlage": f"§ {doc_id}",
                "Inkrafttretensdatum": "1900-01-01",
            }},
        },
        "Dokumentliste": {"ContentReference": {"Urls": {"ContentUrl": [
            {"DataType": "Html", "Url": f"https://ris/{doc_id}.html"},
        ]}}},
    }}


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


@pytest.fixture
def stub_api(monkeypatch):
    """Serve PAGES synthetic pages and record the order of API interactions."""
    events: list[str] = []

    async def search_bundesrecht(*, seite=1, **_kwargs):
        events.append(f"scan:{seite}")
        if seite > PAGES:
            return [], PAGES * PER_PAGE
        start = (seite - 1) * PER_PAGE
        return [_ref(f"d{start + i}") for i in range(PER_PAGE)], PAGES * PER_PAGE

    async def fetch_document_html(ref):
        doc_id = rc._meta_from_ref(ref)["document_id"]
        events.append(f"fetch:{doc_id}")
        return f"<html><body><p>body of {doc_id}</p></body></html>"

    monkeypatch.setattr(rc, "search_bundesrecht", search_bundesrecht)
    monkeypatch.setattr(rc, "fetch_document_html", fetch_document_html)
    return events


async def test_fill_gaps_indexes_every_missing_document(stub_api):
    indexed = await index.fill_gaps(batch_size=20, base_delay=0)
    assert indexed == PAGES * PER_PAGE
    index._count_cache.clear()
    assert index.doc_count() == PAGES * PER_PAGE


async def test_fill_gaps_streams_instead_of_collecting(stub_api):
    """Fetching must interleave with scanning.

    Collecting every missing reference first held ~20KB each — ~5.7GB against a
    280k gap, which the crawler container does not survive. If fetching only
    starts after the final page is scanned, that regression is back.
    """
    await index.fill_gaps(batch_size=20, base_delay=0)

    last_scan = max(i for i, e in enumerate(stub_api) if e.startswith("scan:"))
    first_fetch = min(i for i, e in enumerate(stub_api) if e.startswith("fetch:"))
    assert first_fetch < last_scan, "all scanning completed before any fetching"


async def test_fill_gaps_skips_documents_already_indexed(stub_api):
    await index.fill_gaps(batch_size=20, base_delay=0)
    stub_api.clear()

    indexed = await index.fill_gaps(batch_size=20, base_delay=0)
    assert indexed == 0
    assert not [e for e in stub_api if e.startswith("fetch:")], "refetched known documents"


async def test_fill_gaps_marks_the_run_even_with_no_gaps(stub_api):
    await index.fill_gaps(batch_size=20, base_delay=0)
    index._count_cache.clear()
    first = index.last_crawl()
    assert first

    await index.fill_gaps(batch_size=20, base_delay=0)
    index._count_cache.clear()
    assert index.last_crawl() >= first


async def test_fill_gaps_survives_a_failing_document(stub_api, monkeypatch):
    async def flaky(ref):
        doc_id = rc._meta_from_ref(ref)["document_id"]
        if doc_id == "d5":
            raise RuntimeError("upstream refused")
        if doc_id == "d6":
            return ""
        return f"<html><body><p>{doc_id}</p></body></html>"

    monkeypatch.setattr(rc, "fetch_document_html", flaky)
    indexed = await index.fill_gaps(batch_size=20, base_delay=0)
    assert indexed == PAGES * PER_PAGE - 2
    index._count_cache.clear()
    assert index.doc_count() == PAGES * PER_PAGE - 2


async def test_crawl_writes_pages_and_resume_marker(stub_api):
    indexed = await index.crawl(pages=2, base_delay=0, resume=False)
    assert indexed == 2 * PER_PAGE
    index._count_cache.clear()
    assert index.doc_count() == 2 * PER_PAGE
    with index._connect(readonly=True) as conn:
        row = conn.execute("SELECT value FROM crawl_meta WHERE key='last_page'").fetchone()
    assert row[0] == "2"

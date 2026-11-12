import pytest

import manga_dl
from conftest import PAGE_BYTES, FakeResponse

PAGE_URL = f"{manga_dl.STATIC_BASE_URL}/static/page.webp"


def fetch_page(workspace, monkeypatch, response):
    downloader = manga_dl.MangaDownloader()
    monkeypatch.setattr(manga_dl.requests, "get", lambda *args, **kwargs: response)
    save_path = workspace / "000.webp"
    return downloader.download_image(PAGE_URL, save_path), save_path


@pytest.mark.parametrize("response", [
    FakeResponse(body=b"<html>Just a moment...</html>", headers={"Content-Type": "text/html; charset=utf-8"}),
    FakeResponse(body=b'{"error": "rate limited"}', headers={"Content-Type": "application/json"}),
    FakeResponse(body=b"", headers={"Content-Type": "image/webp"}),
    FakeResponse(body=b""),
])
def test_a_page_that_is_not_an_image_is_not_saved(workspace, monkeypatch, response):
    saved, save_path = fetch_page(workspace, monkeypatch, response)
    assert saved is False
    assert list(workspace.glob("000.webp*")) == []


@pytest.mark.parametrize("headers", [
    {"Content-Type": "image/webp"},
    {"content-type": "IMAGE/JPEG"},
    {"Content-Type": "application/octet-stream"},
    {},
])
def test_an_image_body_is_saved(workspace, monkeypatch, headers):
    saved, save_path = fetch_page(workspace, monkeypatch, FakeResponse(body=PAGE_BYTES, headers=headers))
    assert saved is True
    assert save_path.read_bytes() == PAGE_BYTES


def test_a_chapter_with_an_html_page_is_marked_incomplete(site):
    site.add_manga("m1", "Series", [{"id": "c1", "number": 1, "scanId": "g"}], pages_per_chapter=2)
    site.page_overrides[f"{manga_dl.STATIC_BASE_URL}/static/m1/c1/1.webp"] = FakeResponse(
        body=b"<html>blocked</html>", headers={"Content-Type": "text/html"}
    )
    downloader = manga_dl.MangaDownloader()
    chapter = site.manga["m1"]["info"]["chapters"][0]
    assert downloader.process_chapter("m1", site.manga["m1"]["info"], chapter) == "incomplete"

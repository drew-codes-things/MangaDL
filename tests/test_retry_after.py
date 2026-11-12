import manga_dl
from conftest import FakeResponse


def test_a_huge_retry_after_is_capped(site, monkeypatch):
    replies = iter([FakeResponse(429, headers={"Retry-After": "86400"}), FakeResponse(payload={"ok": True})])
    monkeypatch.setattr(manga_dl.requests, "get", lambda *args, **kwargs: next(replies))
    sleeps = []
    monkeypatch.setattr(manga_dl.time, "sleep", sleeps.append)
    downloader = manga_dl.MangaDownloader()
    downloader.config["retry_attempts"] = 2
    assert downloader.fetch_json("/api/manga/info") == {"ok": True}
    assert sleeps == [manga_dl.MAX_RETRY_AFTER_SECONDS]


def test_a_small_retry_after_is_obeyed(site, monkeypatch):
    replies = iter([FakeResponse(429, headers={"Retry-After": "7"}), FakeResponse(payload={"ok": True})])
    monkeypatch.setattr(manga_dl.requests, "get", lambda *args, **kwargs: next(replies))
    sleeps = []
    monkeypatch.setattr(manga_dl.time, "sleep", sleeps.append)
    downloader = manga_dl.MangaDownloader()
    downloader.config["retry_attempts"] = 2
    assert downloader.fetch_json("/api/manga/info") == {"ok": True}
    assert sleeps == [7]

import pytest

import manga_dl
from conftest import FakeResponse

GOOD_CHAPTERS = [{"id": "g1", "number": 1, "scanId": "g"}]
LONG_TITLE = "L" * 300


def good_cbz(downloader):
    return downloader.download_path / "Manga" / "Good" / "Chapter 001.cbz"


@pytest.fixture
def good_manga(site):
    site.add_manga("good", "Good", list(GOOD_CHAPTERS))
    return site


def test_a_non_json_response_does_not_stop_the_batch(good_manga):
    good_manga.info_overrides["broken"] = FakeResponse(body=b"<html>oops</html>")
    downloader = manga_dl.MangaDownloader()
    downloader.run_batch(["broken", "good"])
    assert good_cbz(downloader).is_file()


def test_an_unexpected_json_shape_does_not_stop_the_batch(good_manga):
    good_manga.info_overrides["odd"] = FakeResponse(payload=["not", "a", "dict"])
    downloader = manga_dl.MangaDownloader()
    downloader.run_batch(["odd", "good"])
    assert good_cbz(downloader).is_file()


def test_a_title_too_long_for_the_disk_does_not_stop_the_batch(good_manga):
    good_manga.add_manga("long", LONG_TITLE, [{"id": "l1", "number": 1, "scanId": "g"}])
    downloader = manga_dl.MangaDownloader()
    downloader.run_batch(["long", "good"])
    assert good_cbz(downloader).is_file()


def test_fetch_json_returns_none_for_a_non_json_body(site):
    site.info_overrides["broken"] = FakeResponse(body=b"<html>oops</html>")
    downloader = manga_dl.MangaDownloader()
    assert downloader.get_manga_info("broken") is None


def test_watch_survives_an_unexpected_response(good_manga, monkeypatch):
    good_manga.info_overrides["odd"] = FakeResponse(payload=["not", "a", "dict"])

    def stop_after_first_cycle(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(manga_dl.time, "sleep", stop_after_first_cycle)
    downloader = manga_dl.MangaDownloader()
    downloader.watch(60, ids=["odd", "good"])
    assert good_cbz(downloader).is_file()

import json
import zipfile

import pytest

import manga_dl

CHAPTER = {"id": "ch-1", "number": 1, "title": "Start"}
INFO = {"title": "Test Series", "type": "Manga"}
PAGES = [{"image": f"/p{i}.webp"} for i in range(4)]


class FakeResponse:
    status_code = 200

    def __init__(self, body):
        self.body = body

    def iter_content(self, size):
        yield self.body


@pytest.fixture
def downloader(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({"retry_attempts": 1, "retry_delay_seconds": 0}))
    instance = manga_dl.MangaDownloader()
    instance.requested = []
    instance.unavailable = set()
    monkeypatch.setattr(instance, "get_chapter_pages", lambda manga_id, chapter_id: PAGES)

    def fake_get(url, params=None, stream=False, allow_404=False):
        instance.requested.append(url)
        if url in instance.unavailable:
            return None
        return FakeResponse(url.encode())

    monkeypatch.setattr(instance, "request_get", fake_get)
    return instance


def cbz_files(downloader):
    return sorted(path.name for path in downloader.download_path.rglob("*.cbz"))


def test_incomplete_chapter_keeps_its_pages_and_resumes_only_missing_ones(downloader):
    downloader.unavailable = {f"{manga_dl.STATIC_BASE_URL}/p2.webp"}
    assert downloader.process_chapter("m1", INFO, CHAPTER) == "incomplete"
    assert cbz_files(downloader) == ["Chapter 001_INCOMPLETE.cbz"]
    temp_dir = downloader.temp_path / "ch-1"
    assert sorted(path.name for path in temp_dir.glob("*.webp")) == ["000.webp", "001.webp", "003.webp"]

    downloader.unavailable = set()
    downloader.requested = []
    assert downloader.process_chapter("m1", INFO, CHAPTER) == "ok"
    assert downloader.requested == [f"{manga_dl.STATIC_BASE_URL}/p2.webp"]
    assert cbz_files(downloader) == ["Chapter 001.cbz"]
    assert not temp_dir.exists()


def test_leftover_partial_pages_are_redownloaded(downloader):
    temp_dir = downloader.temp_path / "ch-1"
    temp_dir.mkdir()
    (temp_dir / "000.webp").write_bytes(b"done")
    (temp_dir / "001.webp.part").write_bytes(b"half")
    (temp_dir / "002.webp").write_bytes(b"")
    downloader.process_chapter("m1", INFO, CHAPTER)
    assert [url.rsplit("/", 1)[1] for url in sorted(downloader.requested)] == ["p1.webp", "p2.webp", "p3.webp"]
    cbz = next(downloader.download_path.rglob("*.cbz"))
    with zipfile.ZipFile(cbz) as zf:
        names = zf.namelist()
        assert zf.read("000.webp") == b"done"
    assert not any(name.endswith(".part") for name in names)


def test_failed_stream_leaves_no_page_file(downloader, tmp_path):
    class BrokenResponse:
        status_code = 200

        def iter_content(self, size):
            yield b"start"
            raise ConnectionError("dropped")

    downloader.request_get = lambda *args, **kwargs: BrokenResponse()
    save_path = tmp_path / "005.webp"
    with pytest.raises(ConnectionError):
        downloader.download_image("https://example.invalid/5.webp", save_path)
    assert not save_path.exists()


def test_resume_check_keeps_downloaded_pages(downloader, monkeypatch):
    temp_dir = downloader.temp_path / "ch-9"
    temp_dir.mkdir()
    (temp_dir / "000.webp").write_bytes(b"a")
    (temp_dir / "002.webp").write_bytes(b"c")
    (temp_dir / "001.webp.part").write_bytes(b"b")
    empty_dir = downloader.temp_path / "ch-empty"
    empty_dir.mkdir()
    downloader.downloaded = {"m9": {"title": "Nine", "chapters": {"ch-9": {"number": 9}}}}
    monkeypatch.setattr("builtins.input", lambda prompt: "y")

    assert manga_dl.check_and_offer_resume(downloader) == ["m9"]
    assert sorted(path.name for path in temp_dir.iterdir()) == ["000.webp", "002.webp"]
    assert not empty_dir.exists()
    assert downloader.downloaded["m9"]["chapters"] == {}

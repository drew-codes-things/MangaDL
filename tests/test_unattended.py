import io
import sys

import pytest

import manga_dl


@pytest.fixture
def no_terminal(monkeypatch):
    def closed_stdin(prompt=""):
        raise EOFError("EOF when reading a line")

    monkeypatch.setattr("builtins.input", closed_stdin)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    monkeypatch.setattr(manga_dl, "attach_log_file", lambda: None)


def leave_incomplete_chapter(workspace):
    series = workspace / "downloads" / "Manga" / "Series"
    series.mkdir(parents=True)
    (series / "Chapter 001_INCOMPLETE.cbz").write_bytes(b"partial")


def run_main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["manga_dl", *argv])
    manga_dl.main()


def test_batch_without_a_terminal_does_not_ask_about_resume(site, workspace, no_terminal, monkeypatch):
    leave_incomplete_chapter(workspace)
    (workspace / "manga_ids.txt").write_text("")
    run_main(monkeypatch, "--batch")


def test_ids_without_a_terminal_does_not_ask_about_resume(site, workspace, no_terminal, monkeypatch):
    leave_incomplete_chapter(workspace)
    site.add_manga("m1", "Other", [{"id": "c1", "number": 1, "scanId": "g"}])
    run_main(monkeypatch, "--ids", "m1")
    assert (workspace / "downloads" / "Manga" / "Other" / "Chapter 001.cbz").is_file()


def test_check_resume_with_closed_stdin_answers_no(site, workspace, no_terminal):
    leave_incomplete_chapter(workspace)
    downloader = manga_dl.MangaDownloader()
    assert manga_dl.check_and_offer_resume(downloader) == []
    assert (workspace / "downloads" / "Manga" / "Series" / "Chapter 001_INCOMPLETE.cbz").exists()


def test_a_chapter_without_pages_leaves_no_temp_folder(site):
    site.add_manga("m1", "Series", [{"id": "c1", "number": 1, "scanId": "g"}])
    site.manga["m1"]["pages"] = {}
    downloader = manga_dl.MangaDownloader()
    chapter = site.manga["m1"]["info"]["chapters"][0]
    assert downloader.process_chapter("m1", site.manga["m1"]["info"], chapter) == "failed"
    assert list(downloader.temp_path.iterdir()) == []


def test_an_incomplete_cbz_survives_when_the_page_list_cannot_be_fetched(site, workspace):
    leave_incomplete_chapter(workspace)
    site.add_manga("m1", "Series", [{"id": "c1", "number": 1, "scanId": "g"}])
    site.manga["m1"]["pages"] = {}
    downloader = manga_dl.MangaDownloader()
    chapter = site.manga["m1"]["info"]["chapters"][0]
    assert downloader.process_chapter("m1", site.manga["m1"]["info"], chapter) == "failed"
    assert (workspace / "downloads" / "Manga" / "Series" / "Chapter 001_INCOMPLETE.cbz").exists()

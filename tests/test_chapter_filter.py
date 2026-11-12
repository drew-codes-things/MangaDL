import sys

import pytest

import manga_dl

BAD_FILTERS = ["1..10", "10-", "abc", "1–10", "nan", "inf", "-5", ",", "1-10,abc"]


@pytest.mark.parametrize("raw", BAD_FILTERS)
def test_an_unreadable_chapter_list_is_refused(workspace, raw):
    with pytest.raises(manga_dl.ChapterFilterError):
        manga_dl.MangaDownloader(chapter_filter=raw)


def test_a_valid_chapter_list_still_parses(workspace):
    downloader = manga_dl.MangaDownloader(chapter_filter="1-10, 42.5")
    assert downloader._chapter_matches(3)
    assert downloader._chapter_matches(42.5)
    assert not downloader._chapter_matches(11)


def test_the_cli_exits_with_an_error_on_a_bad_chapter_list(site, monkeypatch, capsys):
    monkeypatch.setattr(manga_dl, "attach_log_file", lambda: None)
    monkeypatch.setattr(sys, "argv", ["manga_dl", "--ids", "m1", "--chapters", "1..10"])
    with pytest.raises(SystemExit) as stopped:
        manga_dl.main()
    assert stopped.value.code == 2
    assert "1..10" in capsys.readouterr().err
    assert site.calls == []


def test_the_menu_refuses_a_bad_chapter_list_and_resets_its_options(site, monkeypatch):
    answers = iter(["m1", "y", "y", "abc"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    downloader = manga_dl.MangaDownloader()
    downloader._download_single(normal=False)
    assert site.calls == []
    assert (downloader.dry_run, downloader.force, downloader.chapter_filter) == (False, False, None)

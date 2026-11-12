import logging
import sys

import pytest
import requests

import manga_dl
from conftest import FakeResponse


@pytest.fixture
def run_main(site, monkeypatch):
    monkeypatch.setattr(manga_dl, "attach_log_file", lambda: None)

    def run(*argv):
        monkeypatch.setattr(sys, "argv", ["manga_dl", *argv])
        try:
            manga_dl.main()
        except SystemExit as stopped:
            return stopped.code
        return 0

    site.add_manga("good", "Good", [{"id": "g1", "number": 1, "scanId": "g"}])
    return run


def test_a_successful_run_exits_zero(run_main):
    assert run_main("--ids", "good") == 0


def test_an_unknown_id_exits_one(run_main):
    assert run_main("--ids", "missing") == 1


def test_only_invalid_ids_exits_one(run_main):
    assert run_main("--ids", "https://example.com/not-a-manga") == 1


def test_a_batch_with_one_failure_exits_one(run_main, site, workspace):
    site.info_overrides["broken"] = FakeResponse(body=b"<html>oops</html>")
    (workspace / "manga_ids.txt").write_text("good\nbroken\n")
    assert run_main("--batch") == 1


def test_a_clean_batch_exits_zero(run_main, workspace):
    (workspace / "manga_ids.txt").write_text("good\n")
    assert run_main("--batch") == 0


def test_an_adult_title_skipped_by_config_is_not_a_failure(run_main, site):
    site.add_manga("adult", "Adult", [{"id": "a1", "number": 1, "scanId": "g"}])
    site.manga["adult"]["info"]["isAdult"] = True
    assert run_main("--ids", "adult") == 0


def test_list_updates_with_a_fetch_error_exits_one(run_main):
    assert run_main("--list-updates", "--ids", "missing") == 1


def test_a_dry_run_of_an_unknown_id_exits_one(run_main):
    assert run_main("--dry-run", "--ids", "missing") == 1


def test_a_network_failure_is_not_reported_as_not_found(site, monkeypatch, caplog):
    def offline(*args, **kwargs):
        raise requests.exceptions.ConnectionError("network is unreachable")

    monkeypatch.setattr(manga_dl.requests, "get", offline)
    downloader = manga_dl.MangaDownloader()
    with caplog.at_level(logging.INFO, logger="manga_dl"):
        assert downloader.process_manga("m1") is False
    assert "not found on atsu.moe" not in caplog.text
    assert "Could not fetch manga info for m1" in caplog.text

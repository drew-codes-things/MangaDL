import manga_dl

TWO_GROUPS = [
    {"id": "a1", "number": 1, "scanId": "alpha"},
    {"id": "a2", "number": 2, "scanId": "alpha"},
    {"id": "b1", "number": 1, "scanId": "beta"},
]


def fully_downloaded(site):
    site.add_manga("m1", "Beta", list(TWO_GROUPS))
    downloader = manga_dl.MangaDownloader()
    assert downloader.process_manga("m1")
    return downloader


def test_a_multi_group_manga_is_up_to_date_after_a_full_download(site):
    downloader = fully_downloaded(site)
    assert downloader.count_new_chapters("m1") == ("Beta", 0)
    assert downloader.has_new_chapters("m1") is False


def test_a_new_chapter_is_still_reported(site):
    downloader = fully_downloaded(site)
    site.manga["m1"]["info"]["chapters"].append({"id": "b3", "number": 3, "scanId": "beta"})
    assert downloader.count_new_chapters("m1") == ("Beta", 1)
    assert downloader.has_new_chapters("m1") is True


def test_update_mode_skips_an_up_to_date_multi_group_manga(site):
    downloader = fully_downloaded(site)
    downloader.update_only = True
    site.calls.clear()
    assert downloader.process_manga("m1")
    assert site.requested_manga_ids() == ["m1"]

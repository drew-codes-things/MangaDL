import manga_dl


def test_the_same_manga_given_twice_is_processed_once(site):
    site.add_manga("m1", "Series", [{"id": "c1", "number": 1, "scanId": "g"}])
    downloader = manga_dl.MangaDownloader()
    assert downloader.run(cli_ids=["m1", "m1", "https://atsu.moe/manga/m1"]) == 0
    assert site.requested_manga_ids() == ["m1"]

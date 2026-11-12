import json

import manga_dl


def add_colliding_titles(site):
    site.add_manga("rz1", "Re:Zero", [{"id": "a1", "number": 1, "scanId": "g"}])
    site.add_manga("rz2", "ReZero", [{"id": "b1", "number": 1, "scanId": "g"}])


def cbz_paths(downloader):
    return sorted(str(path.relative_to(downloader.download_path)) for path in downloader.download_path.rglob("*.cbz"))


def test_titles_that_sanitise_alike_get_separate_folders(site):
    add_colliding_titles(site)
    downloader = manga_dl.MangaDownloader()
    assert downloader.process_manga("rz1")
    assert downloader.process_manga("rz2")
    assert cbz_paths(downloader) == ["Manga/ReZero [rz2]/Chapter 001.cbz", "Manga/ReZero/Chapter 001.cbz"]


def test_parallel_downloads_of_colliding_titles_do_not_share_a_folder(site):
    add_colliding_titles(site)
    downloader = manga_dl.MangaDownloader()
    downloader.run(cli_ids=["rz1", "rz2"])
    paths = cbz_paths(downloader)
    assert len(paths) == 2
    assert len({path.rsplit("/", 1)[0] for path in paths}) == 2


def test_an_existing_single_title_folder_keeps_its_name(site, workspace):
    site.add_manga("m1", "Series", [{"id": "c1", "number": 1, "scanId": "g"}, {"id": "c2", "number": 2, "scanId": "g"}])
    (workspace / "downloaded.json").write_text(json.dumps(
        {"m1": {"title": "Series", "chapters": {"c1": {"number": 1}}}}
    ))
    downloader = manga_dl.MangaDownloader()
    assert downloader.process_manga("m1")
    assert cbz_paths(downloader) == ["Manga/Series/Chapter 002.cbz"]


def test_the_first_recorded_owner_keeps_the_plain_folder(site, workspace):
    add_colliding_titles(site)
    (workspace / "downloaded.json").write_text(json.dumps(
        {"rz1": {"title": "Re:Zero", "chapters": {}}}
    ))
    downloader = manga_dl.MangaDownloader()
    assert downloader.process_manga("rz2")
    assert downloader.process_manga("rz1")
    assert cbz_paths(downloader) == ["Manga/ReZero [rz2]/Chapter 001.cbz", "Manga/ReZero/Chapter 001.cbz"]

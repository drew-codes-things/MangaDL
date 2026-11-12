import errno
import zipfile

import manga_dl

CHAPTER = {"id": "ch-1", "number": 1, "title": "Start", "scanId": "g1"}
MANGA_ID = "m1"
PAGE_COUNT = 3


def make_downloader(site):
    site.add_manga(MANGA_ID, "Series", [CHAPTER], pages_per_chapter=PAGE_COUNT)
    return manga_dl.MangaDownloader()


def series_dir(downloader):
    return downloader.download_path / "Manga" / "Series"


def manga_info(site):
    return site.manga[MANGA_ID]["info"]


def fail_on_second_entry(monkeypatch):
    real_write = zipfile.ZipFile.write
    disk = {"writes": 0, "full": True}

    def write(self, filename, arcname=None, *args, **kwargs):
        disk["writes"] += 1
        if disk["full"] and disk["writes"] == 2:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_write(self, filename, arcname, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "write", write)
    return disk


def test_a_cbz_that_fails_mid_write_is_not_kept_or_recorded(site, monkeypatch):
    downloader = make_downloader(site)
    disk = fail_on_second_entry(monkeypatch)

    assert downloader.process_chapter(MANGA_ID, manga_info(site), CHAPTER) == "failed"

    assert list(series_dir(downloader).iterdir()) == []
    assert CHAPTER["id"] not in downloader.downloaded.get(MANGA_ID, {}).get("chapters", {})

    disk["full"] = False
    assert downloader.process_chapter(MANGA_ID, manga_info(site), CHAPTER) == "ok"
    with zipfile.ZipFile(series_dir(downloader) / "Chapter 001.cbz") as archive:
        assert sorted(archive.namelist()) == ["000.webp", "001.webp", "002.webp", "ComicInfo.xml"]
    assert CHAPTER["id"] in downloader.downloaded[MANGA_ID]["chapters"]


def test_a_zero_byte_cbz_is_downloaded_again(site):
    downloader = make_downloader(site)
    series_dir(downloader).mkdir(parents=True)
    (series_dir(downloader) / "Chapter 001.cbz").write_bytes(b"")

    assert downloader.process_chapter(MANGA_ID, manga_info(site), CHAPTER) == "ok"
    assert zipfile.is_zipfile(series_dir(downloader) / "Chapter 001.cbz")


def test_a_cbz_cut_short_by_an_older_run_is_downloaded_again(site, workspace):
    downloader = make_downloader(site)
    series_dir(downloader).mkdir(parents=True)
    page = workspace / "000.webp"
    page.write_bytes(b"page")
    with zipfile.ZipFile(series_dir(downloader) / "Chapter 001.cbz", "w") as archive:
        archive.write(page, page.name)

    assert downloader.process_chapter(MANGA_ID, manga_info(site), CHAPTER) == "ok"


def test_a_complete_cbz_is_skipped_without_fetching(site, workspace):
    downloader = make_downloader(site)
    series_dir(downloader).mkdir(parents=True)
    page = workspace / "000.webp"
    page.write_bytes(b"page")
    info = workspace / "ComicInfo.xml"
    info.write_text("<ComicInfo/>")
    with zipfile.ZipFile(series_dir(downloader) / "Chapter 001.cbz", "w") as archive:
        archive.write(page, page.name)
        archive.write(info, info.name)
    site.calls.clear()

    assert downloader.process_chapter(MANGA_ID, manga_info(site), CHAPTER) == "skipped"
    assert site.calls == []
    assert CHAPTER["id"] in downloader.downloaded[MANGA_ID]["chapters"]


def test_no_part_file_is_left_after_a_good_write(site):
    downloader = make_downloader(site)
    assert downloader.process_chapter(MANGA_ID, manga_info(site), CHAPTER) == "ok"
    assert sorted(path.name for path in series_dir(downloader).iterdir()) == ["Chapter 001.cbz"]

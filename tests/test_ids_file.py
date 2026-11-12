import manga_dl


def test_batch_resolves_urls_in_the_ids_file(site, workspace):
    site.add_manga("abc123", "From Url", [{"id": "c1", "number": 1, "scanId": "g"}])
    (workspace / "manga_ids.txt").write_text("https://atsu.moe/manga/abc123\n")
    downloader = manga_dl.MangaDownloader()
    downloader.run(batch=True)
    assert set(site.requested_manga_ids()) == {"abc123"}
    assert (downloader.download_path / "Manga" / "From Url" / "Chapter 001.cbz").is_file()


def test_the_ids_file_parser_ignores_bom_and_comments(workspace):
    (workspace / "manga_ids.txt").write_bytes(
        "﻿abc123\n   # indented comment\ndef456   # inline note\n\n# full comment\n".encode("utf-8")
    )
    downloader = manga_dl.MangaDownloader()
    assert downloader.load_manga_ids() == ["abc123", "def456"]

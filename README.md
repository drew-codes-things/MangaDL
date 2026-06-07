<div align="center">

# MangaDownloader

**A Python downloader for atsu.moe that saves manga chapters as CBZ files with embedded ComicInfo.xml metadata.**

[![Python](https://img.shields.io/badge/python-3.9+-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)

</div>

---

## Overview

MangaDownloader fetches manga from [atsu.moe](https://atsu.moe), downloads each chapter's pages as `.webp` images in parallel using a `ThreadPoolExecutor`, then packages them into a `.cbz` archive alongside a `ComicInfo.xml` file. The XML embeds title, series, chapter number, author, artist, genres, reading direction, and external IDs (AniList, MAL, Kitsu, MangaUpdates, MangaBaka). Chapters are tracked in `downloaded.json` so re-runs skip already-complete chapters. Incomplete downloads are marked `_INCOMPLETE.cbz` and can be detected and re-queued at startup.

---

## Features

| Feature | Detail |
|---|---|
| CBZ output | Each chapter saved as a standard CBZ archive |
| ComicInfo.xml | Embeds series metadata and cross-database IDs per chapter |
| Preferred scan group | Auto-selected by highest chapter count, then most decimal chapters, then oldest upload |
| Chapter filter | Download specific chapters or ranges (e.g. `1-10,42.5`) |
| Dry-run mode | Preview what would be downloaded without writing any files |
| Resume support | Detects `_INCOMPLETE.cbz` files and stale temp dirs, offers to re-download |
| Parallel downloads | Configurable page workers per chapter (default: 16) |
| Exponential backoff | Retries on 429 and 5xx responses with configurable base delay |
| Atomic state saves | `downloaded.json` written via `.tmp` rename to prevent corruption |
| tqdm progress bars | Per-chapter page progress and overall batch progress |
| URL or ID input | Accepts raw manga IDs or full `atsu.moe` manga URLs |
| Adult content filter | Skipped by default; enable with `allow_adult: true` in config or `--allow-adult` |

---

## Setup

### 1. Install dependencies

```bash
pip install requests tqdm
```

### 2. Configure

Copy `config.example.json` to `config.json`. On first run the tool creates `config.json` automatically with defaults if it does not exist.

```json
{
  "download_path": "./downloads",
  "temp_path": "./temp",
  "manga_workers": 3,
  "page_workers": 16,
  "retry_attempts": 3,
  "retry_delay_seconds": 5,
  "request_timeout_seconds": 30,
  "delay_between_manga_seconds": 0,
  "menu_enabled": true,
  "ids_file": "manga_ids.txt",
  "allow_adult": false
}
```

| Key | Description |
|---|---|
| `download_path` | Where finished CBZ files are saved |
| `temp_path` | Temporary directory for in-progress page downloads |
| `manga_workers` | Concurrent manga processed at once (CLI `--ids` mode) |
| `page_workers` | Concurrent page downloads per chapter |
| `retry_attempts` | Number of retries before giving up on a request |
| `retry_delay_seconds` | Base delay for exponential backoff (doubles each retry) |
| `ids_file` | Path to the batch IDs file (default: `manga_ids.txt`) |
| `menu_enabled` | Show the interactive menu on startup (set `false` for batch-only) |
| `allow_adult` | Download adult-rated titles (default: `false`) |

---

## Usage

### Interactive menu

```bash
python manga_dl.py
```

Menu options:

```
[1] Download single manga by ID
[2] Download single manga (advanced options: dry-run, force, chapter filter)
[3] Download from manga_ids.txt (batch)
[4] Update only -- new chapters only
[5] Dry-run preview (nothing will be downloaded)
[6] List all tracked manga
[7] Check and resume incomplete downloads
[8] Exit
```

### CLI mode

```bash
python manga_dl.py --ids abc123 def456
```

```bash
python manga_dl.py --batch
```

```bash
python manga_dl.py --ids abc123 --dry-run
```

```bash
python manga_dl.py --ids abc123 --chapters 1-10,42.5
```

```bash
python manga_dl.py --batch --update
```

```bash
python manga_dl.py --check-resume
```

### Full CLI reference

| Flag | Description |
|---|---|
| `--ids ID [...]` | One or more manga IDs or full atsu.moe URLs |
| `--batch` | Batch-download all IDs in `ids_file` |
| `--dry-run` | Preview without downloading |
| `--force` | Re-download chapters even if they already exist |
| `--update` | Only process manga with new chapters since last run |
| `--chapters RANGE` | Comma-separated chapter numbers or ranges (e.g. `1-10,42.5`) |
| `--allow-adult` | Override `allow_adult=false` in config |
| `--list` | Print all tracked manga with chapter counts, then exit |
| `--list-updates` | Show which manga have new chapters (no downloads), then exit |
| `--watch` | Daemon mode: keep checking for and downloading new chapters on a schedule |
| `--interval SECONDS` | Seconds between checks in `--watch` mode (default: `3600`) |
| `--check-resume` | Scan for incomplete downloads, offer to re-download, then exit |

---

## Output Structure

```
downloads/
  Manga/
    Series Title/
      cover.jpg
      Chapter 001.cbz
      Chapter 002.cbz
  Manwha/
    Series Title/
      Chapter 001.cbz
  Manhua/
    ...
```

Each CBZ contains:
- `000.webp`, `001.webp`, ... (chapter pages)
- `ComicInfo.xml` (series metadata)

---

## Batch File

Add manga IDs or URLs to `manga_ids.txt`, one per line. Lines starting with `#` are treated as comments.

```
# My reading list
abc123
https://atsu.moe/manga/def456
```

---

## Notes

- Chapter filter ranges use a step of `0.5` to include half-chapter values like `6.5` or `12.5`.
- Completed manga IDs are moved to the end of `manga_ids.txt` automatically so unfinished titles are always processed first.
- `downloaded.json` is backed up to `downloaded.json.bak` automatically if corruption is detected on load.
- All activity is logged to `StarMangaDL.log` in the working directory.

---

---

## Install as a command (pipx)

Install this folder as a CLI so it is available on your PATH:

```bash
pipx install .
manga-dl
```

Logging: set `LOG_LEVEL` (e.g. `DEBUG`) and `LOG_FILE` to also write logs to a file.


## License

MIT - made by [Drew](https://github.com/drew-codes-things)

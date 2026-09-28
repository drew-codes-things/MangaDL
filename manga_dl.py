import os
import re
import json
import time
import shutil
import zipfile
import threading
import logging
import argparse
import requests
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from requests.exceptions import RequestException
from urllib.parse import urlparse

try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)
file_handler = logging.FileHandler("StarMangaDL.log", encoding="utf-8")
file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(message)s"))
logger.addHandler(file_handler)

BASE_URL = "https://atsu.moe"
STATIC_BASE_URL = "https://cdn.atsu.moe"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://atsu.moe/"
}
PROVIDER = "StarMangaDL"

BANNER = r"""
                              StarMangaDL
"""

CONFIG_TYPE_CHECKS = {
    "download_path": str,
    "temp_path": str,
    "manga_workers": int,
    "page_workers": int,
    "retry_attempts": int,
    "retry_delay_seconds": (int, float),
    "request_timeout_seconds": (int, float),
    "delay_between_manga_seconds": (int, float),
    "menu_enabled": bool,
    "ids_file": str,
    "allow_adult": bool,
}



def find_incomplete_cbz(download_path: Path) -> list:
    """Return all *_INCOMPLETE.cbz paths under download_path."""
    return list(download_path.rglob("*_INCOMPLETE.cbz"))


def find_incomplete_temp_dirs(temp_path: Path) -> list:
    """
    Return temp chapter dirs that exist but appear incomplete.
    A dir is considered incomplete if it contains *any* .webp files but
    is missing at least one page based on the highest page index found.
    """
    incomplete = []
    if not temp_path.exists():
        return incomplete
    for d in temp_path.iterdir():
        if not d.is_dir():
            continue
        pages = sorted(d.glob("*.webp"))
        if not pages:
            incomplete.append(d)
            continue
        indices = []
        for p in pages:
            try:
                indices.append(int(p.stem))
            except ValueError:
                pass
        if indices:
            expected = set(range(min(indices), max(indices) + 1))
            actual = set(indices)
            if expected != actual:
                incomplete.append(d)
    return incomplete


def check_and_offer_resume(downloader) -> list:
    """
    Scan for incomplete CBZ files and stale temp dirs.
    Print a summary and ask the user whether to re-download them.
    Returns a list of manga IDs to prepend to the run queue (may be empty).
    """
    incomplete_cbz = find_incomplete_cbz(downloader.download_path)
    stale_temps = find_incomplete_temp_dirs(downloader.temp_path)

    if not incomplete_cbz and not stale_temps:
        return []

    print("\n" + "=" * 60)
    print(" INCOMPLETE DOWNLOADS DETECTED")
    print("=" * 60)

    if incomplete_cbz:
        print(f"\n  {len(incomplete_cbz)} incomplete CBZ file(s) found:")
        for p in incomplete_cbz:
            print(f"    * {p.relative_to(downloader.download_path)}")

    if stale_temps:
        print(f"\n  {len(stale_temps)} stale temp folder(s) found:")
        for d in stale_temps:
            pages = list(d.glob("*.webp"))
            print(f"    * {d.name}  ({len(pages)} page(s) on disk)")

    print()
    answer = input("  Delete incomplete files and re-download affected chapters? [y/N]: ").strip().lower()
    if answer != "y":
        print("  Skipping resume check -- incomplete files left as-is.\n")
        return []

    for p in incomplete_cbz:
        try:
            p.unlink()
            logger.info("[RESUME] Deleted incomplete CBZ: %s", p)
        except OSError as e:
            logger.warning("[RESUME] Could not delete %s: %s", p, e)

    for d in stale_temps:
        try:
            shutil.rmtree(d, ignore_errors=True)
            logger.info("[RESUME] Deleted stale temp dir: %s", d)
        except OSError as e:
            logger.warning("[RESUME] Could not delete temp dir %s: %s", d, e)

    stale_chapter_ids = {d.name for d in stale_temps}
    manga_ids_to_retry = set()
    title_to_manga_ids = {}

    for manga_id, data in downloader.downloaded.items():
        title = data.get("title")
        if not title:
            continue
        key = downloader.sanitize_filename(str(title))
        title_to_manga_ids.setdefault(key, []).append(manga_id)

    for manga_id, data in downloader.downloaded.items():
        keys_to_delete = [
            chapter_id
            for chapter_id in data.get("chapters", {})
            if chapter_id in stale_chapter_ids
        ]
        for chapter_id in keys_to_delete:
            del downloader.downloaded[manga_id]["chapters"][chapter_id]
            manga_ids_to_retry.add(manga_id)

    for p in incomplete_cbz:
        chapter_num_match = re.search(r"Chapter (\d+(?:\.\d+)?)", p.stem)
        if not chapter_num_match:
            continue
        ch_num = float(chapter_num_match.group(1))
        series_key = p.parent.name
        candidate_manga_ids = title_to_manga_ids.get(series_key, [])
        if not candidate_manga_ids:
            logger.warning(
                "[RESUME] Could not map incomplete chapter '%s' to a tracked manga title. "
                "Skipping downloaded.json cleanup for this file.",
                p,
            )
            continue

        for manga_id in candidate_manga_ids:
            data = downloader.downloaded.get(manga_id, {})
            keys_to_delete = []
            for chapter_id, ch_data in data.get("chapters", {}).items():
                try:
                    if float(ch_data.get("number", -1)) == ch_num:
                        keys_to_delete.append(chapter_id)
                        manga_ids_to_retry.add(manga_id)
                        break
                except (ValueError, TypeError):
                    pass
            for chapter_id in keys_to_delete:
                del downloader.downloaded[manga_id]["chapters"][chapter_id]

    if manga_ids_to_retry:
        downloader.save_downloaded()
        print(f"\n  Queued {len(manga_ids_to_retry)} manga ID(s) for re-download.\n")
    else:
        print("  Incomplete files removed. Run again to re-download.\n")

    return list(manga_ids_to_retry)



class MangaDownloader:
    def __init__(self, dry_run=False, force=False, update_only=False, chapter_filter=None):
        self.config = self.load_config()
        self.dry_run = dry_run
        self.force = force
        self.update_only = update_only
        self.chapter_filter = self._parse_chapter_filter(chapter_filter)
        self.downloaded_lock = threading.Lock()
        self.ids_lock = threading.Lock()
        self.downloaded = self.load_downloaded()
        self.downloaded_dirty = False
        self.pending_downloaded_writes = 0
        self.save_every_n_chapters = 10
        self.temp_path = Path(self.config["temp_path"])
        self.download_path = Path(self.config["download_path"])
        self.ensure_dirs()

    def _parse_chapter_filter(self, raw):
        """
        Parse a chapter filter string like "1-10,42.5" into explicit values
        plus inclusive ranges.

        Ranges are matched as real intervals (lo <= num <= hi), so any decimal
        chapter inside the range is included -- not just half-steps. Returns
        {"values": set[float], "ranges": list[(lo, hi)]} or None.
        """
        if not raw:
            return None
        values = set()
        ranges = []
        for part in [p.strip() for p in raw.split(",")]:
            if not part:
                continue
            range_match = re.match(r'^(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)$', part)
            if range_match:
                lo = float(range_match.group(1))
                hi = float(range_match.group(2))
                if lo > hi:
                    lo, hi = hi, lo
                ranges.append((lo, hi))
            elif "-" in part:
                logger.warning("[FILTER] Invalid chapter range '%s' -- skipping.", part)
            else:
                try:
                    val = float(part)
                    if val < 0:
                        logger.warning(
                            "[FILTER] Negative chapter number %s -- skipping.", val
                        )
                        continue
                    values.add(val)
                except (ValueError, TypeError):
                    logger.warning("[FILTER] Could not parse chapter value '%s' -- skipping.", part)
        if not values and not ranges:
            return None
        return {"values": values, "ranges": ranges}

    def _chapter_matches(self, number):
        """True if a chapter number passes the active chapter filter."""
        if not self.chapter_filter:
            return True
        try:
            num = float(number)
        except (ValueError, TypeError):
            return False
        if num in self.chapter_filter["values"]:
            return True
        return any(lo <= num <= hi for lo, hi in self.chapter_filter["ranges"])

    def _extract_manga_id(self, raw: str) -> str:
        """
        Accept either a raw manga ID or an atsu.moe URL and return a safe ID.
        Returns an empty string if a URL is provided but does not include an ID.
        """
        value = (raw or "").strip()
        if not value:
            return ""
        if value.startswith("http"):
            parsed = urlparse(value)
            path = parsed.path.strip("/")
            if path.startswith("manga/"):
                parts = path.split("/")
                if len(parts) >= 2 and parts[1].strip():
                    return parts[1].strip()
            return ""
        return value

    def load_config(self):
        config_path = Path("config.json")
        default = {
            "download_path": "./downloads",
            "temp_path": "./temp",
            "manga_workers": 3,
            "page_workers": 16,
            "retry_attempts": 3,
            "retry_delay_seconds": 5,
            "request_timeout_seconds": 30,
            "delay_between_manga_seconds": 0,
            "menu_enabled": True,
            "ids_file": "manga_ids.txt",
            "allow_adult": False
        }
        if not config_path.exists():
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(default, f, indent=2)
            print("\n[StarMangaDL] config.json not found -- created with defaults.")
            print("Please edit config.json, then re-run the program.\n")
            raise SystemExit(0)
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        for k, v in default.items():
            cfg.setdefault(k, v)
        for key, expected_type in CONFIG_TYPE_CHECKS.items():
            if key in cfg and not isinstance(cfg[key], expected_type):
                raise ValueError(
                    f"config.json: '{key}' must be {expected_type if isinstance(expected_type, type) else '/'.join(t.__name__ for t in expected_type)}, "
                    f"got {type(cfg[key]).__name__} ({cfg[key]!r})"
                )
        return cfg

    def load_downloaded(self):
        path = Path("downloaded.json")
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning("Corrupted downloaded.json -- backed up to downloaded.json.bak (%s)", e)
                shutil.copy(path, "downloaded.json.bak")
                return {}
        return {}

    def save_downloaded(self):
        path = Path("downloaded.json")
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.downloaded, f, indent=2)
        os.replace(tmp, path)

    def flush_downloaded(self, force=False):
        with self.downloaded_lock:
            if not self.downloaded_dirty:
                return
            if not force and self.pending_downloaded_writes < self.save_every_n_chapters:
                return
            self.save_downloaded()
            self.downloaded_dirty = False
            self.pending_downloaded_writes = 0

    def ensure_dirs(self):
        """
        Create the download and temp directories if they do not exist.

        Temp subdirectories are NOT wiped here -- cleaning up stale temp dirs
        is handled by check_and_offer_resume() before any download begins.
        """
        self.download_path.mkdir(parents=True, exist_ok=True)
        self.temp_path.mkdir(parents=True, exist_ok=True)

    def sanitize_filename(self, name: str) -> str:
        name = re.sub(r'[/\\:*?"<>|]', "", name)
        return name.strip() or "Unknown"

    def get_type_folder(self, manga_type: str) -> str:
        if manga_type in ("Manwha", "Manhua"):
            return manga_type
        return "Manga"

    def format_chapter_filename(self, number, incomplete: bool = False) -> str:
        if number is None:
            num_str = "000"
        else:
            try:
                num = float(number)
                if num == int(num):
                    num_str = f"{int(num):03d}"
                else:
                    int_part, frac_part = str(num).split(".")
                    num_str = f"{int(int_part):03d}.{frac_part}"
            except (ValueError, TypeError, OverflowError):
                num_str = "000"
        suffix = "_INCOMPLETE" if incomplete else ""
        return f"Chapter {num_str}{suffix}.cbz"

    def format_page_filename(self, index: int) -> str:
        return f"{index:03d}.webp"

    def retry_delay(self, attempt: int) -> float:
        """Exponential back-off: base * 2^attempt (e.g. 5s, 10s, 20s)."""
        base = self.config["retry_delay_seconds"]
        return base * (2 ** attempt)

    def request_get(self, url: str, params=None, stream: bool = False, allow_404: bool = False):
        """
        GET wrapper with retry/backoff for transient HTTP/network failures.
        """
        for attempt in range(self.config["retry_attempts"]):
            try:
                r = requests.get(
                    url,
                    params=params,
                    headers=HEADERS,
                    timeout=self.config["request_timeout_seconds"],
                    stream=stream,
                )
                if r.status_code == 429 and attempt < self.config["retry_attempts"] - 1:
                    try:
                        retry_after = int(r.headers.get("Retry-After", 0) or 0)
                    except (TypeError, ValueError):
                        retry_after = 0
                    delay = retry_after if retry_after > 0 else self.retry_delay(attempt)
                    logger.warning("429 Rate limited for %s, retrying in %.1fs", url, delay)
                    time.sleep(delay)
                    continue
                if 500 <= r.status_code < 600 and attempt < self.config["retry_attempts"] - 1:
                    delay = self.retry_delay(attempt)
                    logger.warning("HTTP %s for %s, retrying in %.1fs", r.status_code, url, delay)
                    time.sleep(delay)
                    continue
                if r.status_code == 404 and allow_404:
                    return r
                return r
            except RequestException as e:
                logger.warning("Request failed (attempt %d/%d): %s",
                               attempt + 1, self.config["retry_attempts"], e)
                if attempt == self.config["retry_attempts"] - 1:
                    return None
                time.sleep(self.retry_delay(attempt))
        return None

    def fetch_json(self, endpoint: str, params=None):
        url = f"{BASE_URL}{endpoint}"
        r = self.request_get(url, params=params, allow_404=True)
        if r is None:
            logger.error("All retries exhausted for %s", url)
            return None
        if r.status_code == 200:
            return r.json()
        if r.status_code == 404:
            logger.warning("404 Not Found: %s", url)
            return None
        logger.warning("HTTP %s for %s", r.status_code, url)
        return None

    def get_manga_info(self, manga_id: str):
        return self.fetch_json("/api/manga/info", {"mangaId": manga_id})

    def get_manga_page(self, manga_id: str):
        raw = self.fetch_json("/api/manga/page", {"id": manga_id})
        if raw is None:
            return None
        if "mangaPage" in raw:
            return raw["mangaPage"]
        return raw

    def download_cover(self, info: dict, series_dir: Path):
        if self.dry_run:
            return
        poster = info.get("poster") or info.get("image") or info.get("cover")
        if not poster:
            return
        url = f"{STATIC_BASE_URL}{poster}" if not poster.startswith(("http://", "https://")) else poster
        save_path = series_dir / "cover.jpg"
        try:
            r = self.request_get(url, stream=True)
            if r is None:
                logger.warning("Cover download failed after retries: %s", url)
                return
            if r.status_code == 200:
                with open(save_path, "wb") as f:
                    for chunk in r.iter_content(8192):
                        f.write(chunk)
                logger.info("Cover downloaded: %s", save_path.name)
            else:
                logger.warning("Cover download HTTP %s: %s", r.status_code, url)
        except Exception as e:
            logger.warning("Cover download failed: %s", e)

    def get_all_chapters(self, info: dict, page_data: dict) -> list:
        seen_ids = set()
        merged = []
        for ch in info.get("chapters", []):
            cid = ch.get("id")
            if cid and cid not in seen_ids:
                seen_ids.add(cid)
                merged.append(ch)
        for ch in page_data.get("chapters", []):
            cid = ch.get("id")
            if cid and cid not in seen_ids:
                seen_ids.add(cid)
                merged.append(ch)
        return merged

    def get_chapter_pages(self, manga_id: str, chapter_id: str):
        data = self.fetch_json("/api/read/chapter", {"mangaId": manga_id, "chapterId": chapter_id})
        if not data:
            return []
        return data.get("readChapter", {}).get("pages", [])

    def select_preferred_group(self, chapters: list) -> str:
        if not chapters:
            return None
        group_count = defaultdict(int)
        group_decimals = defaultdict(int)
        group_oldest = {}
        for ch in chapters:
            gid = ch.get("scanId")
            group_count[gid] += 1
            num = ch.get("number")
            if num is not None:
                try:
                    fnum = float(num)
                    if fnum != int(fnum):
                        group_decimals[gid] += 1
                except (ValueError, TypeError, OverflowError):
                    pass
            created = ch.get("createdAt")
            if created is not None:
                try:
                    created = float(created)
                except (ValueError, TypeError):
                    created = float("inf")
                if gid not in group_oldest or created < group_oldest[gid]:
                    group_oldest[gid] = created
        max_count = max(group_count.values())
        candidates = [g for g, c in group_count.items() if c == max_count]
        if len(candidates) == 1:
            return candidates[0]
        max_dec = max(group_decimals[g] for g in candidates)
        candidates = [g for g in candidates if group_decimals[g] == max_dec]
        if len(candidates) == 1:
            return candidates[0]
        candidates.sort(key=lambda g: group_oldest.get(g, float("inf")))
        return candidates[0]

    def build_chapter_list(self, all_chapters: list, preferred_group: str) -> list:
        chapters_by_number = {}
        unnumbered_chapters = []
        for ch in all_chapters:
            num = ch.get("number")
            if num is None or str(num).strip() == "":
                unnumbered_chapters.append(ch)
                continue
            try:
                key = str(float(num))
            except (TypeError, ValueError):
                key = str(num).strip()
            if key not in chapters_by_number:
                chapters_by_number[key] = ch
            elif ch.get("scanId") == preferred_group:
                chapters_by_number[key] = ch
        def sort_key(c):
            try:
                return float(c.get("number") or 0)
            except (TypeError, ValueError):
                return float("inf")
        numbered_chapters = sorted(chapters_by_number.values(), key=sort_key)
        return numbered_chapters + unnumbered_chapters

    def filter_alternative_names(self, names: list) -> list:
        if not names:
            return []
        EXCLUDE = r'[\uAC00-\uD7A3]|[\u0600-\u06FF]|[\u0E00-\u0E7F]|[\u0400-\u04FF]'
        KEEP_JP = r'[\u3040-\u30FF\u4E00-\u9FFF]'
        KEEP_LATIN = r'^[\w\s\-.,:;!?\'\/()[\]\u00C0-\u024F]+$'
        result = []
        seen = set()
        for name in names:
            n = name.strip()
            if not n or n in seen:
                continue
            if re.search(EXCLUDE, n):
                continue
            if re.search(KEEP_JP, n) or re.match(KEEP_LATIN, n):
                seen.add(n)
                result.append(n)
        return result

    def create_comic_info(self, manga_info: dict, chapter: dict, force_strip: bool = False) -> str:
        manga_type = manga_info.get("type", "Manga")
        vertical = manga_type in ("Manwha", "Manhua") or force_strip
        title = chapter.get("title") or f"Chapter {chapter.get('number')}"
        series = manga_info.get("title", "Unknown")
        number = str(chapter.get("number", ""))
        count = manga_info.get("totalChapterCount") or ""
        alts = self.filter_alternative_names(manga_info.get("otherNames", []))
        alt_series = " | ".join(alts) if alts else ""
        authors = manga_info.get("authors", [])
        writer = next((a["name"] for a in authors if a.get("type") == "Author"), "")
        penciller = next((a["name"] for a in authors if a.get("type") == "Artist"), "")
        genres = ", ".join(g.get("name", "") for g in manga_info.get("genres", []))
        is_adult = str(manga_info.get("isAdult", False)).lower()

        root = ET.Element("ComicInfo")

        def _add(tag, value):
            if value is not None and str(value).strip():
                el = ET.SubElement(root, tag)
                el.text = str(value)

        _add("Title", title)
        _add("Series", series)
        _add("Number", number)
        if count != "":
            _add("Count", count)
        _add("AlternativeSeries", alt_series)
        _add("Writer", writer)
        _add("Penciller", penciller)
        _add("Genre", genres)
        if vertical:
            _add("ReadingDirection", "Vertical")
        _add("LanguageISO", "en")
        _add("isAdult", is_adult)

        id_fields = [
            ("AniListID", manga_info.get("anilistId")),
            ("MAL-ID", manga_info.get("malId")),
            ("KitsuID", manga_info.get("kitsuId")),
            ("ANNID", manga_info.get("annId")),
            ("MangaBakaID", manga_info.get("mangaBakaId")),
            ("MangaUpdatesID", manga_info.get("mangaUpdatesId")),
        ]
        for tag, val in id_fields:
            _add(tag, val)

        _add("Provider", PROVIDER)

        ET.indent(root, space=" ")
        return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode")

    def download_image(self, url: str, save_path: Path) -> bool:
        r = self.request_get(url, stream=True, allow_404=True)
        if r is None:
            logger.error("Failed to download after %d attempts: %s", self.config["retry_attempts"], url)
            return False
        if r.status_code == 200:
            with open(save_path, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
            return True
        if r.status_code == 404:
            logger.warning("Page 404: %s", url)
            return False
        logger.warning("Image download HTTP %s: %s", r.status_code, url)
        return False


    def dry_run_preview_manga(self, manga_id: str):
        """Fetch metadata and print a preview of what would be downloaded without writing anything."""
        print(f"\n{'=' * 60}")
        print(f"  [DRY-RUN] Fetching metadata for: {manga_id}")
        print(f"{'=' * 60}")

        info = self.get_manga_info(manga_id)
        if not info:
            print(f"  ERROR: ID '{manga_id}' not found on atsu.moe.")
            return

        if info.get("isAdult") and not self.config.get("allow_adult", False):
            print(f"  SKIP: '{info.get('title')}' -- adult content (allow_adult=false in config).")
            return

        page_data = self.get_manga_page(manga_id)
        if not page_data:
            print(f"  ERROR: Could not fetch page data for '{manga_id}'.")
            return

        title = info.get("title", "Unknown")
        manga_type = info.get("type", "Manga")
        type_folder = self.get_type_folder(manga_type)
        series_dir = self.download_path / type_folder / self.sanitize_filename(title)

        all_chapters = self.get_all_chapters(info, page_data)
        preferred = self.select_preferred_group(all_chapters)
        chapters = self.build_chapter_list(all_chapters, preferred)

        if self.chapter_filter:
            chapters = [ch for ch in chapters if self._chapter_matches(ch.get("number"))]

        pending = []
        already_done = []
        for ch in chapters:
            ch_id = ch.get("id")
            cbz_ok = series_dir / self.format_chapter_filename(ch.get("number"), incomplete=False)
            with self.downloaded_lock:
                in_db = ch_id in self.downloaded.get(manga_id, {}).get("chapters", {})
            if in_db or cbz_ok.exists():
                already_done.append(ch)
            else:
                pending.append(ch)

        print(f"  Title      : {title}")
        print(f"  Type       : {manga_type}")
        print(f"  Output dir : {series_dir}")
        print(f"  Total chaps: {len(chapters)}  |  Already done: {len(already_done)}  |  Would download: {len(pending)}")

        if not pending:
            print("  Nothing to download -- all chapters already present.")
            print()
            return

        first_num = pending[0].get("number", "?")
        last_num = pending[-1].get("number", "?")
        print(f"  Chapter range to download: {first_num} -> {last_num}")
        print()
        print(f"  {'Ch':>6}  {'Title':<35}  {'Pages':>5}  {'CBZ name':<35}")
        print(f"  {'='*6}  {'='*35}  {'='*5}  {'='*35}")

        total_pages = 0
        for ch in pending:
            ch_num = ch.get("number", "?")
            ch_title = (ch.get("title") or "")[:35]
            ch_id = ch.get("id")
            pages = self.get_chapter_pages(manga_id, ch_id)
            page_count = len(pages)
            total_pages += page_count
            cbz_name = self.format_chapter_filename(ch_num)[:35]
            print(f"  {str(ch_num):>6}  {ch_title:<35}  {page_count:>5}  {cbz_name:<35}")

        print()
        print(f"  Estimated total pages  : {total_pages}")
        print(f"  Estimated files on disk: {total_pages + len(pending)}  "
              f"(pages + 1 ComicInfo.xml per chapter)")
        print()


    def process_chapter(self, manga_id: str, manga_info: dict, chapter: dict, force_strip=False):
        chapter_id = chapter["id"]
        chapter_num = chapter.get("number")
        chapter_num_str = str(chapter_num) if chapter_num is not None else "Unknown"

        if self.dry_run:
            return "ok"

        with self.downloaded_lock:
            if not self.force and manga_id in self.downloaded:
                if chapter_id in self.downloaded[manga_id].get("chapters", {}):
                    logger.info("[SKIPPED] Chapter %s (already downloaded)", chapter_num_str)
                    return "skipped"

        cbz_name_ok = self.format_chapter_filename(chapter_num, incomplete=False)
        cbz_name_inc = self.format_chapter_filename(chapter_num, incomplete=True)
        type_folder = self.get_type_folder(manga_info.get("type", "Manga"))
        series_dir = self.download_path / type_folder / self.sanitize_filename(manga_info.get("title", "Unknown"))

        if (series_dir / cbz_name_ok).exists() and not self.force:
            logger.info("[SKIPPED] Chapter %s (cbz exists)", chapter_num_str)
            self._mark_downloaded(manga_id, chapter_id, manga_info, chapter)
            return "skipped"

        incomplete_path = series_dir / cbz_name_inc
        if incomplete_path.exists():
            logger.info("[RETRY] Deleting incomplete CBZ for chapter %s and re-downloading", chapter_num_str)
            incomplete_path.unlink()

        temp_dir = self.temp_path / chapter_id
        temp_dir.mkdir(exist_ok=True)

        pages = self.get_chapter_pages(manga_id, chapter_id)
        if not pages:
            logger.error("No pages for chapter %s", chapter_num_str)
            return "failed"

        logger.info("Chapter %s: %d pages", chapter_num_str, len(pages))

        failed_pages = []
        futures_map = {}
        with ThreadPoolExecutor(max_workers=self.config["page_workers"]) as executor:
            for i, page in enumerate(pages):
                image = page.get("image")
                if not image:
                    logger.warning("Page %d missing image for chapter %s -- skipping", i, chapter_num_str)
                    failed_pages.append(i)
                    continue
                url = f"{STATIC_BASE_URL}{image}"
                save_path = temp_dir / self.format_page_filename(i)
                futures_map[executor.submit(self.download_image, url, save_path)] = i

            if TQDM_AVAILABLE:
                with tqdm(total=len(futures_map), desc=f" Ch.{chapter_num_str}", unit="pg", leave=False) as bar:
                    for future in as_completed(futures_map):
                        try:
                            if not future.result():
                                failed_pages.append(futures_map[future])
                        except Exception as e:
                            logger.warning("Page %d error: %s", futures_map[future], e)
                            failed_pages.append(futures_map[future])
                        bar.update(1)
            else:
                for future in as_completed(futures_map):
                    try:
                        if not future.result():
                            failed_pages.append(futures_map[future])
                    except Exception as e:
                        logger.warning("Page %d error: %s", futures_map[future], e)
                        failed_pages.append(futures_map[future])

        incomplete = len(failed_pages) > 0
        if incomplete:
            logger.warning("Chapter %s: %d page(s) failed -- marking INCOMPLETE", chapter_num_str, len(failed_pages))

        with open(temp_dir / "ComicInfo.xml", "w", encoding="utf-8") as f:
            f.write(self.create_comic_info(manga_info, chapter, force_strip))

        series_dir.mkdir(parents=True, exist_ok=True)
        cbz_name = cbz_name_inc if incomplete else cbz_name_ok
        cbz_path = series_dir / cbz_name

        try:
            with zipfile.ZipFile(cbz_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for file in sorted(temp_dir.glob("*")):
                    zf.write(file, file.name)
        except Exception as e:
            logger.error("CBZ write failed for chapter %s: %s", chapter_num_str, e)
            return "failed"

        if not incomplete:
            self._mark_downloaded(manga_id, chapter_id, manga_info, chapter)

        logger.info("%s %s", "[INCOMPLETE]" if incomplete else "[OK]", cbz_name)
        shutil.rmtree(temp_dir, ignore_errors=True)
        return "incomplete" if incomplete else "ok"

    def _mark_downloaded(self, manga_id, chapter_id, manga_info, chapter):
        with self.downloaded_lock:
            if manga_id not in self.downloaded:
                self.downloaded[manga_id] = {"title": manga_info.get("title"), "chapters": {}}
            if chapter_id not in self.downloaded[manga_id]["chapters"]:
                self.downloaded[manga_id]["chapters"][chapter_id] = {
                    "number": chapter.get("number"),
                    "title": chapter.get("title", ""),
                    "downloadedAt": datetime.now().isoformat()
                }
                self.downloaded_dirty = True
                self.pending_downloaded_writes += 1
        self.flush_downloaded(force=False)

    def has_new_chapters(self, manga_id: str) -> bool:
        info = self.get_manga_info(manga_id)
        page_data = self.get_manga_page(manga_id)
        if not info and not page_data:
            return False
        all_chapters = self.get_all_chapters(info or {}, page_data or {})
        remote_ids = {ch["id"] for ch in all_chapters}
        with self.downloaded_lock:
            local_ids = set(self.downloaded.get(manga_id, {}).get("chapters", {}).keys())
        return bool(remote_ids - local_ids)

    def count_new_chapters(self, manga_id: str):
        """Return (title, new_chapter_count) without downloading. Count is None on fetch error."""
        info = self.get_manga_info(manga_id)
        page_data = self.get_manga_page(manga_id)
        if not info and not page_data:
            return manga_id, None
        all_chapters = self.get_all_chapters(info or {}, page_data or {})
        remote_ids = {ch["id"] for ch in all_chapters}
        with self.downloaded_lock:
            local = self.downloaded.get(manga_id, {})
            local_ids = set(local.get("chapters", {}).keys())
        title = (info or {}).get("title") or local.get("title") or manga_id
        return title, len(remote_ids - local_ids)

    def resolve_ids(self, items: list) -> list:
        ids = []
        for item in items:
            resolved = self._extract_manga_id(item)
            if resolved:
                ids.append(resolved)
            else:
                logger.warning("Skipping invalid manga URL/ID: %s", item)
        return ids

    def list_updates(self, ids: list):
        """Print which tracked manga have new chapters, without downloading anything."""
        print("\n=== Update check (no downloads) ===")
        any_new = False
        for mid in ids:
            title, new = self.count_new_chapters(mid)
            if new is None:
                print(f"  {mid} -> [error fetching]")
            elif new > 0:
                any_new = True
                print(f"  {title} ({mid}) -> {new} new chapter(s)")
            else:
                print(f"  {title} ({mid}) -> up to date")
        if not any_new:
            print("  No manga have new chapters.")
        print()

    def watch(self, interval: int, ids: list = None):
        """Daemon mode: periodically check for new chapters and download only those.

        Each cycle does a lightweight count_new_chapters() pass first (one or two
        cheap requests per manga) and only runs the full download pipeline for
        titles that actually have new chapters -- avoiding a full process_manga()
        fetch-and-build-chapter-list pass for titles that are already up to date.
        """
        self.update_only = True
        logger.info("Watch mode: update check every %d seconds. Press Ctrl+C to stop.", interval)
        try:
            while True:
                cycle_ids = ids if ids else self.load_manga_ids()
                if not cycle_ids:
                    logger.info("No IDs to check.")
                else:
                    due = []
                    for mid in cycle_ids:
                        title, new = self.count_new_chapters(mid)
                        if new:
                            logger.info("%s (%s): %d new chapter(s) -- queued", title, mid, new)
                            due.append(mid)
                    if due:
                        self.run_batch(due)
                    else:
                        logger.info("No new chapters found for any tracked manga.")
                logger.info("Sleeping %d seconds until next update check...", interval)
                time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("Watch mode stopped.")

    def process_manga(self, manga_id: str, skip_update_check: bool = False) -> bool:
        if self.dry_run:
            self.dry_run_preview_manga(manga_id)
            return True

        if self.update_only and not skip_update_check and not self.has_new_chapters(manga_id) and not self.force:
            logger.info("[SKIPPED] %s -- no new chapters", manga_id)
            return True

        logger.info("=== Processing %s ===", manga_id)
        info = self.get_manga_info(manga_id)
        if not info:
            logger.error("[ERROR] ID %s not found on atsu.moe", manga_id)
            return False
        if info.get("isAdult") and not self.config.get("allow_adult", False):
            logger.info("[SKIPPED] %s -- adult content", info.get("title"))
            return False

        page_data = self.get_manga_page(manga_id)
        if not page_data:
            logger.error("Failed to fetch page data for %s", manga_id)
            return False

        force_strip = page_data.get("forceStrip", False)
        all_chapters = self.get_all_chapters(info, page_data)
        if not all_chapters:
            logger.error("No chapters found for %s", manga_id)
            return False

        preferred = self.select_preferred_group(all_chapters)
        chapters = self.build_chapter_list(all_chapters, preferred)

        if self.chapter_filter:
            chapters = [ch for ch in chapters if self._chapter_matches(ch.get("number"))]

        logger.info("%d chapters to process | preferred group: %s", len(chapters), preferred)

        type_folder = self.get_type_folder(info.get("type", "Manga"))
        series_dir = self.download_path / type_folder / self.sanitize_filename(info.get("title", "Unknown"))
        series_dir.mkdir(parents=True, exist_ok=True)
        self.download_cover(info, series_dir)

        counts = {"ok": 0, "skipped": 0, "failed": 0, "incomplete": 0}
        for chapter in chapters:
            result = self.process_chapter(manga_id, info, chapter, force_strip)
            counts[result] = counts.get(result, 0) + 1

        logger.info(
            "Done %s | Written: %d | Skipped: %d | Incomplete: %d | Failed: %d",
            info.get("title"), counts["ok"], counts["skipped"], counts["incomplete"], counts["failed"]
        )
        self.flush_downloaded(force=True)
        return counts["failed"] == 0 and counts["incomplete"] == 0

    def load_manga_ids(self) -> list:
        path = Path(self.config["ids_file"])
        if not path.exists():
            path.write_text("# StarMangaDL -- manga IDs file\n# One ID per line. Lines starting with # are ignored.\n")
            logger.info("Created %s -- add manga IDs and re-run.", path)
            return []
        with open(path, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip() and not line.startswith("#")]

    def reorder_manga_ids(self, completed_id: str):
        path = Path(self.config["ids_file"])
        if not path.exists():
            return
        with self.ids_lock:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            comments = [l for l in lines if not l.strip() or l.strip().startswith("#")]
            ids = [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]
            if completed_id not in ids:
                return
            ids.remove(completed_id)
            ids.append(completed_id)
            with open(path, "w", encoding="utf-8") as f:
                for c in comments:
                    f.write(c if c.endswith("\n") else c + "\n")
                for i in ids:
                    f.write(i + "\n")

    def split_ids_by_status(self, ids: list):
        not_downloaded = []
        completed = []
        for manga_id in ids:
            with self.downloaded_lock:
                already = manga_id in self.downloaded
            if not already or self.has_new_chapters(manga_id):
                not_downloaded.append(manga_id)
            else:
                completed.append(manga_id)
        return not_downloaded, completed

    def show_list(self):
        print("\n=== Tracked Manga ===")
        for mid, data in sorted(self.downloaded.items()):
            title = data.get("title", "Unknown")
            count = len(data.get("chapters", {}))
            print(f"  {mid} -> {title} ({count} chapters)")
        print()

    def run_batch(self, ids: list):
        not_downloaded, completed_ids = self.split_ids_by_status(ids)
        total = len(ids)
        print(f" {total} manga found | {len(not_downloaded)} to download | {len(completed_ids)} already complete\n")

        ordered = not_downloaded + completed_ids
        if TQDM_AVAILABLE and len(ordered) > 1:
            with tqdm(total=len(ordered), desc="Overall Progress", unit="manga") as pbar:
                for mid in ordered:
                    if self.process_manga(mid, skip_update_check=True):
                        self.reorder_manga_ids(mid)
                    pbar.update(1)
                    if self.config.get("delay_between_manga_seconds", 0) > 0:
                        time.sleep(self.config["delay_between_manga_seconds"])
        else:
            for mid in ordered:
                if self.process_manga(mid, skip_update_check=True):
                    self.reorder_manga_ids(mid)
                if self.config.get("delay_between_manga_seconds", 0) > 0:
                    time.sleep(self.config["delay_between_manga_seconds"])

    def show_menu(self) -> str:
        print(BANNER)
        print(" [1] Download single manga by ID")
        print(" [2] Download single manga (advanced options)")
        print(" [3] Download from manga_ids.txt (batch)")
        print(" [4] Update only -- new chapters only")
        print(" [5] Dry-run preview (nothing will be downloaded)")
        print(" [6] List all tracked manga")
        print(" [7] Check & resume incomplete downloads")
        print(" [8] Exit")
        print()
        return input("Select option: ").strip()

    def _download_single(self, normal=True):
        manga_id = input("\nEnter manga ID or full URL: ").strip()
        if not manga_id:
            return
        manga_id = self._extract_manga_id(manga_id)
        if not manga_id:
            print("Invalid manga URL/ID. Please provide a valid atsu.moe manga URL or ID.")
            return

        if not normal:
            self.dry_run = input("Dry-run only (preview)? [y/N]: ").strip().lower() == "y"
            self.force = input("Force redownload? [y/N]: ").strip().lower() == "y"
            ch = input("Specific chapters (e.g. 1-10,42.5) or Enter for all: ").strip()
            self.chapter_filter = self._parse_chapter_filter(ch) if ch else None

        self.process_manga(manga_id)
        self.reorder_manga_ids(manga_id)

        if not normal:
            self.dry_run = False
            self.force = False
            self.chapter_filter = None

    def run(self, cli_ids=None, batch=False):
        if cli_ids:
            ids = []
            for item in cli_ids:
                resolved = self._extract_manga_id(item)
                if not resolved:
                    logger.warning("Skipping invalid manga URL/ID: %s", item)
                    continue
                ids.append(resolved)
            if not ids:
                logger.error("No valid manga IDs were provided.")
                return
            with ThreadPoolExecutor(max_workers=self.config["manga_workers"]) as executor:
                futures = {executor.submit(self.process_manga, mid): mid for mid in ids}
                for future in as_completed(futures):
                    mid = futures[future]
                    try:
                        if future.result():
                            self.reorder_manga_ids(mid)
                    except Exception as e:
                        logger.error("Error processing %s: %s", mid, e)
            return

        if batch or not self.config.get("menu_enabled", True):
            ids = self.load_manga_ids()
            if not ids:
                print("No IDs in manga_ids.txt -- nothing to do.")
                return
            self.run_batch(ids)
            return

        while True:
            choice = self.show_menu()
            if choice == "1":
                self._download_single(normal=True)
            elif choice == "2":
                self._download_single(normal=False)
            elif choice == "3":
                ids = self.load_manga_ids()
                if ids:
                    self.run_batch(ids)
            elif choice == "4":
                prev_update_only = self.update_only
                self.update_only = True
                ids = self.load_manga_ids()
                if ids:
                    self.run_batch(ids)
                self.update_only = prev_update_only
            elif choice == "5":
                prev_dry_run = self.dry_run
                self.dry_run = True
                ids = self.load_manga_ids()
                if ids:
                    self.run_batch(ids)
                self.dry_run = prev_dry_run
            elif choice == "6":
                self.show_list()
            elif choice == "7":
                check_and_offer_resume(self)
            elif choice == "8":
                print("Goodbye!")
                break
            else:
                print("Invalid option.")


def parse_args():
    p = argparse.ArgumentParser(
        description="StarMangaDL -- atsu.moe manga downloader",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python manga_dl.py --ids abc123 def456
  python manga_dl.py --batch
  python manga_dl.py --ids abc123 --dry-run
  python manga_dl.py --ids abc123 --chapters 1-10,42.5
  python manga_dl.py --batch --update
  python manga_dl.py --check-resume
"""
    )
    p.add_argument("--ids", nargs="+", metavar="ID",
                   help="One or more manga IDs or full atsu.moe URLs to download")
    p.add_argument("--batch", action="store_true",
                   help="Batch-download all IDs listed in the ids_file (manga_ids.txt by default)")
    p.add_argument("--dry-run", action="store_true",
                   help="Preview what would be downloaded without writing any files")
    p.add_argument("--force", action="store_true",
                   help="Re-download chapters even if they already exist on disk")
    p.add_argument("--update", action="store_true",
                   help="Only process manga that have new chapters since the last run")
    p.add_argument("--chapters", metavar="RANGE",
                   help="Comma-separated chapter numbers or ranges to download, e.g. 1-10,42.5")
    p.add_argument("--allow-adult", action="store_true",
                   help="Override allow_adult=false in config and download adult-rated titles")
    p.add_argument("--list", action="store_true",
                   help="Print all tracked manga and their downloaded chapter counts, then exit")
    p.add_argument("--list-updates", action="store_true",
                   help="Show which manga have new chapters (no downloads), then exit")
    p.add_argument("--watch", action="store_true",
                   help="Daemon mode: keep checking for and downloading new chapters on a schedule")
    p.add_argument("--interval", type=int, default=3600, metavar="SECONDS",
                   help="Seconds between checks in --watch mode (default: 3600)")
    p.add_argument("--check-resume", action="store_true",
                   help="Scan for incomplete downloads and offer to re-download them, then exit")
    return p.parse_args()


def main():
    args = parse_args()
    downloader = MangaDownloader(
        dry_run=args.dry_run,
        force=args.force,
        update_only=args.update,
        chapter_filter=args.chapters
    )
    if args.allow_adult:
        downloader.config["allow_adult"] = True

    if args.list:
        downloader.show_list()
        return

    if args.list_updates:
        ids = downloader.resolve_ids(args.ids) if args.ids else downloader.load_manga_ids()
        if not ids:
            print("No manga IDs to check.")
            return
        downloader.list_updates(ids)
        return

    if args.watch:
        if args.interval < 1:
            print("--interval must be at least 1 second.")
            return
        ids = downloader.resolve_ids(args.ids) if args.ids else None
        downloader.watch(args.interval, ids=ids)
        return

    if args.check_resume:
        check_and_offer_resume(downloader)
        return

    if not args.dry_run:
        check_and_offer_resume(downloader)

    downloader.run(cli_ids=args.ids, batch=args.batch)
    downloader.flush_downloaded(force=True)


if __name__ == "__main__":
    main()

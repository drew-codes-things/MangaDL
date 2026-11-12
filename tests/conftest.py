import json

import pytest
import requests

import manga_dl

FAST_CONFIG = {"retry_attempts": 1, "retry_delay_seconds": 0}
PAGE_BYTES = b"RIFF0000WEBPVP8 fake page"


class FakeResponse:
    def __init__(self, status_code=200, body=b"", headers=None, payload=None):
        self.status_code = status_code
        self.body = body
        self.headers = headers if headers is not None else {}
        self.payload = payload

    def iter_content(self, size):
        yield self.body

    def json(self):
        if self.payload is None:
            raise requests.exceptions.JSONDecodeError("Expecting value", "", 0)
        return self.payload


class FakeSite:
    def __init__(self):
        self.manga = {}
        self.page_overrides = {}
        self.info_overrides = {}
        self.calls = []

    def add_manga(self, manga_id, title, chapters, pages_per_chapter=2, manga_type="Manga"):
        self.manga[manga_id] = {
            "info": {"title": title, "type": manga_type, "chapters": chapters},
            "page": {"mangaPage": {"chapters": []}},
            "pages": {
                chapter["id"]: [{"image": f"/static/{manga_id}/{chapter['id']}/{index}.webp"}
                                for index in range(pages_per_chapter)]
                for chapter in chapters
            },
        }

    def get(self, url, params=None, headers=None, timeout=None, stream=False):
        self.calls.append((url, dict(params or {})))
        params = params or {}
        if url in self.page_overrides:
            return self.page_overrides[url]
        if url == f"{manga_dl.BASE_URL}/api/manga/info":
            manga_id = params.get("mangaId")
            if manga_id in self.info_overrides:
                return self.info_overrides[manga_id]
            entry = self.manga.get(manga_id)
            return FakeResponse(payload=entry["info"]) if entry else FakeResponse(404)
        if url == f"{manga_dl.BASE_URL}/api/manga/page":
            entry = self.manga.get(params.get("id"))
            return FakeResponse(payload=entry["page"]) if entry else FakeResponse(404)
        if url == f"{manga_dl.BASE_URL}/api/read/chapter":
            entry = self.manga.get(params.get("mangaId"))
            pages = entry["pages"].get(params.get("chapterId")) if entry else None
            return FakeResponse(payload={"readChapter": {"pages": pages}}) if pages else FakeResponse(404)
        if url.startswith(manga_dl.STATIC_BASE_URL):
            return FakeResponse(body=PAGE_BYTES, headers={"Content-Type": "image/webp"})
        return FakeResponse(404)

    def requested_manga_ids(self):
        return [params["mangaId"] for url, params in self.calls if url.endswith("/api/manga/info")]


@pytest.fixture(autouse=True)
def refuse_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError(f"test tried to reach the network: {args} {kwargs}")

    monkeypatch.setattr(requests, "get", refuse)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps(FAST_CONFIG))
    return tmp_path


@pytest.fixture
def site(workspace, monkeypatch):
    fake = FakeSite()
    monkeypatch.setattr(requests, "get", fake.get)
    return fake

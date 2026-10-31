"""刮削源注册：source 名 -> {search, fetch}。"""

from __future__ import annotations

from .base import ScrapeError, WorkMetadata
from . import douban, tmdb

SOURCES = ("tmdb", "douban")


def search(source: str, query: str, tmdb_key: str | None, limit: int = 12) -> list[WorkMetadata]:
    if source == "tmdb":
        return tmdb.search(tmdb_key, query, limit)
    if source == "douban":
        return douban.search(query, limit)
    raise ScrapeError(f"未知刮削源：{source}")


def fetch(source: str, external_id: str, work_type: str, tmdb_key: str | None) -> WorkMetadata:
    if source == "tmdb":
        return tmdb.fetch(tmdb_key, external_id, work_type)
    if source == "douban":
        return douban.fetch(external_id, work_type)
    raise ScrapeError(f"未知刮削源：{source}")

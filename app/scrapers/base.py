"""刮削源抽象。每个源把站外条目统一转成 WorkMetadata。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EpisodeMeta:
    number: int
    title: str = ""
    air_date: str = ""
    runtime: int | None = None  # 分钟


@dataclass
class SeasonMeta:
    number: int
    title: str = ""
    overview: str = ""
    episodes: list[EpisodeMeta] = field(default_factory=list)


@dataclass
class WorkMetadata:
    source: str                      # 'tmdb' | 'douban' | 'manual'
    external_id: str                 # 源站条目 id
    type: str                        # 'movie' | 'tv'
    title: str
    original_title: str = ""
    search_alias: str = ""
    year: int | None = None
    region: str = ""
    genres: list[str] = field(default_factory=list)
    directors: list[str] = field(default_factory=list)
    cast: list[str] = field(default_factory=list)
    summary: str = ""
    poster_url: str = ""
    rating: float | None = None      # 站外 10 分制评分
    imdb_id: str = ""
    douban_id: str = ""
    tmdb_id: int | None = None
    seasons: list[SeasonMeta] = field(default_factory=list)


class ScrapeError(Exception):
    """源站不可用 / 未配置 / 解析失败时抛出，前端展示原始信息。"""

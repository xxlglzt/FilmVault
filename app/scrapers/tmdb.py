"""TMDB 刮削源（官方 API，需要用户在设置里填 v3 API Key）。

TMDB 本身就是 10 分制，和豆瓣口径接近，vote_average 直接作为站外评分。
"""

from __future__ import annotations

import httpx

from .base import EpisodeMeta, ScrapeError, SeasonMeta, WorkMetadata

API_BASE = "https://api.themoviedb.org/3"
IMG_BASE = "https://image.tmdb.org/t/p/w780"

_COUNTRY_ZH = {
    "US": "美国", "GB": "英国", "CN": "中国大陆", "HK": "中国香港", "TW": "中国台湾",
    "JP": "日本", "KR": "韩国", "FR": "法国", "DE": "德国", "IT": "意大利",
    "ES": "西班牙", "IN": "印度", "TH": "泰国", "RU": "俄罗斯", "CA": "加拿大",
    "AU": "澳大利亚", "SE": "瑞典", "DK": "丹麦", "NO": "挪威", "BR": "巴西",
    "MX": "墨西哥", "IR": "伊朗", "TR": "土耳其", "PL": "波兰", "NL": "荷兰",
    "BE": "比利时", "AR": "阿根廷", "IE": "爱尔兰", "NZ": "新西兰", "ZA": "南非",
}


def _need_key(api_key: str | None) -> str:
    if not api_key:
        raise ScrapeError("未配置 TMDB API Key：请到「设置」页填写（tmdb.org 免费申请）")
    return api_key


def _get(api_key: str, path: str, params: dict | None = None) -> dict:
    params = dict(params or {})
    params["api_key"] = api_key
    params["language"] = "zh-CN"
    try:
        r = httpx.get(API_BASE + path, params=params, timeout=15)
    except httpx.HTTPError as e:
        raise ScrapeError(f"连接 TMDB 失败：{e}") from e
    if r.status_code == 401:
        raise ScrapeError("TMDB API Key 无效（401）")
    if r.status_code != 200:
        raise ScrapeError(f"TMDB 返回 {r.status_code}: {r.text[:200]}")
    return r.json()


def _region(names: list[dict]) -> str:
    out = []
    for c in names or []:
        iso = c.get("iso_3166_1", "")
        out.append(_COUNTRY_ZH.get(iso, c.get("name") or iso))
    return " / ".join([x for x in out if x])


def search(api_key: str | None, query: str, limit: int = 12) -> list[WorkMetadata]:
    key = _need_key(api_key)
    data = _get(key, "/search/multi", {"query": query, "include_adult": "false"})
    results = []
    for item in data.get("results", []):
        if item.get("media_type") not in ("movie", "tv"):
            continue
        md = _brief(item)
        if md:
            results.append(md)
        if len(results) >= limit:
            break
    return results


def _brief(item: dict) -> WorkMetadata | None:
    media_type = item.get("media_type", "")
    title = item.get("title") or item.get("name") or ""
    if not title:
        return None
    date = item.get("release_date") or item.get("first_air_date") or ""
    return WorkMetadata(
        source="tmdb",
        external_id=str(item.get("id")),
        type="movie" if media_type == "movie" else "tv",
        title=title,
        original_title=item.get("original_title") or item.get("original_name") or "",
        year=int(date[:4]) if date[:4].isdigit() else None,
        summary=item.get("overview") or "",
        poster_url=IMG_BASE + item["poster_path"] if item.get("poster_path") else "",
        rating=round(item["vote_average"], 1) if item.get("vote_average") else None,
        tmdb_id=item.get("id"),
    )


def fetch(api_key: str | None, external_id: str, work_type: str) -> WorkMetadata:
    key = _need_key(api_key)
    if work_type == "movie":
        return _fetch_movie(key, external_id)
    return _fetch_tv(key, external_id)


def _fetch_movie(key: str, movie_id: str) -> WorkMetadata:
    d = _get(key, f"/movie/{movie_id}", {"append_to_response": "credits"})
    md = WorkMetadata(
        source="tmdb",
        external_id=movie_id,
        type="movie",
        title=d.get("title") or "",
        original_title=d.get("original_title") or "",
        year=int(d["release_date"][:4]) if (d.get("release_date") or "")[:4].isdigit() else None,
        region=_region(d.get("production_countries")),
        genres=[g["name"] for g in d.get("genres", [])],
        directors=[c["name"] for c in d.get("credits", {}).get("crew", []) if c.get("job") == "Director"],
        cast=[c["name"] for c in d.get("credits", {}).get("cast", [])[:15]],
        summary=d.get("overview") or "",
        poster_url=IMG_BASE + d["poster_path"] if d.get("poster_path") else "",
        rating=round(d["vote_average"], 1) if d.get("vote_average") else None,
        imdb_id=d.get("imdb_id") or "",
        tmdb_id=d.get("id"),
    )
    return md


def _fetch_tv(key: str, tv_id: str) -> WorkMetadata:
    d = _get(key, f"/tv/{tv_id}", {"append_to_response": "credits"})
    md = WorkMetadata(
        source="tmdb",
        external_id=tv_id,
        type="tv",
        title=d.get("name") or "",
        original_title=d.get("original_name") or "",
        year=int(d["first_air_date"][:4]) if (d.get("first_air_date") or "")[:4].isdigit() else None,
        region=_region(d.get("production_countries")),
        genres=[g["name"] for g in d.get("genres", [])],
        directors=[c["name"] for c in d.get("created_by", [])],
        cast=[c["name"] for c in d.get("credits", {}).get("cast", [])[:15]],
        summary=d.get("overview") or "",
        poster_url=IMG_BASE + d["poster_path"] if d.get("poster_path") else "",
        rating=round(d["vote_average"], 1) if d.get("vote_average") else None,
        tmdb_id=d.get("id"),
    )
    for s in d.get("seasons", []):
        if s.get("season_number", 0) < 1:  # 跳过 Specials
            continue
        md.seasons.append(SeasonMeta(
            number=s["season_number"],
            title=s.get("name") or "",
            overview=s.get("overview") or "",
        ))
    # 逐季补剧集列表；失败不影响整体导入
    for season in md.seasons:
        try:
            sd = _get(key, f"/tv/{tv_id}/season/{season.number}")
            for e in sd.get("episodes", []):
                season.episodes.append(EpisodeMeta(
                    number=e.get("episode_number", 0),
                    title=e.get("name") or "",
                    air_date=e.get("air_date") or "",
                    runtime=e.get("runtime"),
                ))
        except ScrapeError:
            continue
    return md

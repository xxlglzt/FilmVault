"""FilmVault 服务端：REST API + 静态前端 + 本地视频流。

参考 peach 的 local-first 思路：单进程 FastAPI + SQLite，
数据全部落在 ./data，刮削源可插拔（TMDB / 豆瓣 / 手工）。
"""

from __future__ import annotations

import difflib
import json
import mimetypes
import os
import re
import sqlite3
import string
import threading
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, library, player
from .scrapers import SOURCES, ScrapeError, fetch as scrape_fetch, search as scrape_search
from .scrapers.base import WorkMetadata

app = FastAPI(title="FilmVault", version="0.1.0")

STATUSES = ("none", "want", "watching", "watched", "on_hold", "dropped")


# ---------- 通用 ----------

def get_setting(key: str) -> str:
    row = db.get_conn().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else ""


@app.on_event("startup")
def startup() -> None:
    db.init_db()


@app.exception_handler(ScrapeError)
def scrape_error_handler(_req: Request, exc: ScrapeError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": str(exc)})


def _touch(conn: sqlite3.Connection, work_id: int) -> None:
    conn.execute("UPDATE works SET updated_at=datetime('now','localtime') WHERE id=?", (work_id,))


def _insert_work(conn: sqlite3.Connection, md: WorkMetadata) -> int:
    cur = conn.execute(
        """INSERT INTO works (type,title,original_title,year,region,genres,directors,cast,
                              summary,douban_id,tmdb_id,imdb_id,douban_rating)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (md.type, md.title, md.original_title, md.year, md.region,
         db.jdump(md.genres), db.jdump(md.directors), db.jdump(md.cast),
         md.summary, md.douban_id, md.tmdb_id, md.imdb_id, md.rating),
    )
    work_id = cur.lastrowid
    for season in md.seasons:
        scur = conn.execute(
            "INSERT INTO seasons (work_id,season_number,title,overview) VALUES (?,?,?,?)",
            (work_id, season.number, season.title, season.overview),
        )
        season_id = scur.lastrowid
        conn.executemany(
            "INSERT OR IGNORE INTO episodes (season_id,episode_number,title,air_date,runtime) VALUES (?,?,?,?,?)",
            [(season_id, e.number, e.title, e.air_date, e.runtime) for e in season.episodes],
        )
    return work_id


def _cache_poster(work_id: int, url: str) -> None:
    """把海报下载到本地；失败不阻断导入。豆瓣图床有防盗链，需要 Referer。"""
    if not url:
        return
    headers = {}
    if "doubanio.com" in url:
        headers["Referer"] = "https://movie.douban.com/"
    try:
        get = None
        try:
            from curl_cffi import requests as cffi_requests
            get = lambda: cffi_requests.get(url, impersonate="chrome", timeout=20, headers=headers)
        except ImportError:
            get = lambda: httpx.get(url, timeout=20, headers=headers)
        resp = get()
        content = resp.content
        if len(content) < 200:
            return
        ext = ".jpg"
        cd = getattr(resp, "headers", {}).get("content-type", "")
        if "png" in cd:
            ext = ".png"
        elif "webp" in cd:
            ext = ".webp"
        fname = f"{work_id}{ext}"
        (db.POSTER_DIR / fname).write_bytes(content)
        db.get_conn().execute("UPDATE works SET poster_file=? WHERE id=?", (fname, work_id))
        db.get_conn().commit()
    except Exception:
        pass


def _find_existing(conn: sqlite3.Connection, md: WorkMetadata) -> int | None:
    if md.tmdb_id:
        row = conn.execute("SELECT id FROM works WHERE tmdb_id=?", (md.tmdb_id,)).fetchone()
        if row:
            return row["id"]
    if md.douban_id:
        row = conn.execute("SELECT id FROM works WHERE douban_id=?", (md.douban_id,)).fetchone()
        if row:
            return row["id"]
    return None


# ---------- 设置 ----------

class Settings(BaseModel):
    tmdb_api_key: str = ""
    local_player_path: str = ""


@app.get("/api/settings")
def read_settings() -> Settings:
    return Settings(
        tmdb_api_key=get_setting("tmdb_api_key"),
        local_player_path=get_setting("local_player_path"),
    )


@app.put("/api/settings")
def write_settings(s: Settings) -> Settings:
    if s.local_player_path and not Path(s.local_player_path.strip().strip('"')).is_file():
        raise HTTPException(400, "本地播放器程序路径不存在")
    conn = db.get_conn()
    values = {"tmdb_api_key": s.tmdb_api_key.strip(), "local_player_path": s.local_player_path.strip()}
    for key, value in values.items():
        conn.execute(
            "INSERT INTO settings (key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    conn.commit()
    return s


# ---------- 作品 ----------

class WorkIn(BaseModel):
    type: str = Field(pattern="^(movie|tv)$")
    title: str
    original_title: str = ""
    year: int | None = None
    region: str = ""
    genres: list[str] = []
    directors: list[str] = []
    cast: list[str] = []
    summary: str = ""
    douban_rating: float | None = None
    tmdb_id: int | None = None
    douban_id: str = ""
    imdb_id: str = ""
    status: str = "none"
    my_rating: float | None = Field(default=None, ge=0, le=10)


SORTS = {
    "created": "w.created_at DESC, w.id DESC",
    "my_rating": "w.my_rating IS NULL, w.my_rating DESC",
    "douban_rating": "w.douban_rating IS NULL, w.douban_rating DESC",
    "year": "w.year IS NULL, w.year DESC",
    "title": "w.title COLLATE NOCASE",
}


@app.get("/api/works")
def list_works(
    type: str | None = None,
    status: str | None = None,
    tag: str | None = None,
    q: str = "",
    sort: str = "created",
) -> dict:
    conn = db.get_conn()
    sql = "SELECT w.* FROM works w"
    args: list = []
    where = []
    if tag:
        sql += " JOIN work_tags wt ON wt.work_id=w.id JOIN tags t ON t.id=wt.tag_id"
        where.append("t.name=?")
        args.append(tag)
    if type:
        where.append("w.type=?")
        args.append(type)
    if status:
        where.append("w.status=?")
        args.append(status)
    if q:
        where.append("(w.title LIKE ? OR w.original_title LIKE ? OR w.directors LIKE ?)")
        like = f"%{q}%"
        args += [like, like, like]
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY " + SORTS.get(sort, SORTS["created"])
    rows = conn.execute(sql, args).fetchall()
    items = []
    for r in rows:
        w = db.row_to_work(r)
        w["tags"] = [t["name"] for t in conn.execute(
            "SELECT t.name FROM tags t JOIN work_tags wt ON wt.tag_id=t.id WHERE wt.work_id=? ORDER BY t.name",
            (w["id"],))]
        w["file_count"] = conn.execute(
            "SELECT COUNT(*) c FROM files WHERE work_id=?", (w["id"],)).fetchone()["c"]
        first_file = conn.execute(
            "SELECT id,episode_id,progress_seconds,duration_seconds FROM files WHERE work_id=? ORDER BY id LIMIT 1",
            (w["id"],)).fetchone()
        w["first_file_id"] = first_file["id"] if first_file else None
        w["first_episode_id"] = first_file["episode_id"] if first_file else None
        w["first_progress_seconds"] = first_file["progress_seconds"] if first_file else 0
        w["first_duration_seconds"] = first_file["duration_seconds"] if first_file else 0
        items.append(w)
    return {"items": items, "total": len(items)}


@app.post("/api/works", status_code=201)
def create_work(w: WorkIn) -> dict:
    conn = db.get_conn()
    md = WorkMetadata(source="manual", external_id="", type=w.type, title=w.title,
                      original_title=w.original_title, year=w.year, region=w.region,
                      genres=w.genres, directors=w.directors, cast=w.cast, summary=w.summary,
                      rating=w.douban_rating, tmdb_id=w.tmdb_id,
                      douban_id=w.douban_id, imdb_id=w.imdb_id)
    work_id = _insert_work(conn, md)
    conn.execute("UPDATE works SET status=?, my_rating=? WHERE id=?",
                 (w.status if w.status in STATUSES else "none", w.my_rating, work_id))
    conn.commit()
    return {"id": work_id}


@app.get("/api/works/{work_id}")
def get_work(work_id: int) -> dict:
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM works WHERE id=?", (work_id,)).fetchone()
    if not row:
        raise HTTPException(404, "作品不存在")
    w = db.row_to_work(row)
    w["tags"] = [t["name"] for t in conn.execute(
        "SELECT t.name FROM tags t JOIN work_tags wt ON wt.tag_id=t.id WHERE wt.work_id=? ORDER BY t.name",
        (work_id,))]
    w["reviews"] = [dict(r) for r in conn.execute(
        "SELECT * FROM reviews WHERE work_id=? ORDER BY created_at DESC", (work_id,))]
    w["seasons"] = []
    for s in conn.execute("SELECT * FROM seasons WHERE work_id=? ORDER BY season_number", (work_id,)):
        season = dict(s)
        season["episodes"] = [dict(e) for e in conn.execute(
            "SELECT e.*, f.id AS file_id, f.path AS file_path FROM episodes e "
            "LEFT JOIN files f ON f.episode_id=e.id WHERE e.season_id=? ORDER BY e.episode_number",
            (season["id"],))]
        w["seasons"].append(season)
    w["files"] = [dict(f) for f in conn.execute(
        "SELECT f.*, e.episode_number, s.season_number FROM files f "
        "LEFT JOIN episodes e ON f.episode_id=e.id LEFT JOIN seasons s ON e.season_id=s.id "
        "WHERE f.work_id=? ORDER BY f.path", (work_id,))]
    return w


@app.put("/api/works/{work_id}")
def update_work(work_id: int, w: WorkIn) -> dict:
    conn = db.get_conn()
    if not conn.execute("SELECT 1 FROM works WHERE id=?", (work_id,)).fetchone():
        raise HTTPException(404, "作品不存在")
    conn.execute(
        """UPDATE works SET type=?,title=?,original_title=?,year=?,region=?,genres=?,
             directors=?,cast=?,summary=?,douban_rating=?,tmdb_id=?,douban_id=?,imdb_id=?,
             status=?,my_rating=?,updated_at=datetime('now','localtime') WHERE id=?""",
        (w.type, w.title, w.original_title, w.year, w.region, db.jdump(w.genres),
         db.jdump(w.directors), db.jdump(w.cast), w.summary, w.douban_rating,
         w.tmdb_id, w.douban_id, w.imdb_id,
         w.status if w.status in STATUSES else "none", w.my_rating, work_id),
    )
    conn.commit()
    return {"id": work_id}


@app.delete("/api/works/{work_id}", status_code=204)
def delete_work(work_id: int) -> Response:
    conn = db.get_conn()
    row = conn.execute("SELECT poster_file FROM works WHERE id=?", (work_id,)).fetchone()
    if not row:
        raise HTTPException(404, "作品不存在")
    if row["poster_file"]:
        try:
            (db.POSTER_DIR / row["poster_file"]).unlink(missing_ok=True)
        except OSError:
            pass
    conn.execute("DELETE FROM works WHERE id=?", (work_id,))
    conn.commit()
    return Response(status_code=204)


class StatePatch(BaseModel):
    my_rating: float | None = Field(default=None, ge=0, le=10)
    status: str | None = None


@app.patch("/api/works/{work_id}/state")
def patch_state(work_id: int, patch: StatePatch) -> dict:
    conn = db.get_conn()
    if not conn.execute("SELECT 1 FROM works WHERE id=?", (work_id,)).fetchone():
        raise HTTPException(404, "作品不存在")
    if patch.status is not None:
        if patch.status not in STATUSES:
            raise HTTPException(400, f"非法状态：{patch.status}")
        conn.execute("UPDATE works SET status=? WHERE id=?", (patch.status, work_id))
    if patch.my_rating is not None:
        conn.execute("UPDATE works SET my_rating=? WHERE id=?", (patch.my_rating, work_id))
    _touch(conn, work_id)
    conn.commit()
    row = conn.execute("SELECT status,my_rating FROM works WHERE id=?", (work_id,)).fetchone()
    return dict(row)


# ---------- 短评 ----------

class ReviewIn(BaseModel):
    rating: float | None = Field(default=None, ge=0, le=10)
    content: str = Field(min_length=1)


@app.post("/api/works/{work_id}/reviews", status_code=201)
def add_review(work_id: int, r: ReviewIn) -> dict:
    conn = db.get_conn()
    if not conn.execute("SELECT 1 FROM works WHERE id=?", (work_id,)).fetchone():
        raise HTTPException(404, "作品不存在")
    cur = conn.execute("INSERT INTO reviews (work_id,rating,content) VALUES (?,?,?)",
                       (work_id, r.rating, r.content))
    _touch(conn, work_id)
    conn.commit()
    return {"id": cur.lastrowid}


@app.put("/api/reviews/{review_id}")
def update_review(review_id: int, r: ReviewIn) -> dict:
    conn = db.get_conn()
    row = conn.execute("SELECT work_id FROM reviews WHERE id=?", (review_id,)).fetchone()
    if not row:
        raise HTTPException(404, "短评不存在")
    conn.execute("UPDATE reviews SET rating=?,content=?,updated_at=datetime('now','localtime') WHERE id=?",
                 (r.rating, r.content, review_id))
    _touch(conn, row["work_id"])
    conn.commit()
    return {"id": review_id}


@app.delete("/api/reviews/{review_id}", status_code=204)
def delete_review(review_id: int) -> Response:
    conn = db.get_conn()
    conn.execute("DELETE FROM reviews WHERE id=?", (review_id,))
    conn.commit()
    return Response(status_code=204)


# ---------- 标签 ----------

@app.get("/api/tags")
def list_tags() -> dict:
    rows = db.get_conn().execute(
        "SELECT t.id, t.name, COUNT(wt.work_id) AS count FROM tags t "
        "LEFT JOIN work_tags wt ON wt.tag_id=t.id GROUP BY t.id ORDER BY t.name").fetchall()
    return {"items": [dict(r) for r in rows]}


class TagsIn(BaseModel):
    tags: list[str]


@app.put("/api/works/{work_id}/tags")
def set_tags(work_id: int, body: TagsIn) -> dict:
    conn = db.get_conn()
    if not conn.execute("SELECT 1 FROM works WHERE id=?", (work_id,)).fetchone():
        raise HTTPException(404, "作品不存在")
    conn.execute("DELETE FROM work_tags WHERE work_id=?", (work_id,))
    for name in body.tags:
        name = name.strip()
        if not name:
            continue
        conn.execute("INSERT INTO tags (name) VALUES (?) ON CONFLICT(name) DO NOTHING", (name,))
        conn.execute(
            "INSERT INTO work_tags (work_id,tag_id) VALUES (?,"
            "(SELECT id FROM tags WHERE name=?)) ON CONFLICT DO NOTHING",
            (work_id, name))
    _touch(conn, work_id)
    conn.commit()
    return {"tags": body.tags}


# ---------- 刮削 ----------

@app.get("/api/scrape/sources")
def scrape_sources() -> dict:
    return {"items": list(SOURCES)}


@app.get("/api/scrape/search")
def scrape_search_api(source: str, q: str = Query(min_length=1)) -> dict:
    results = scrape_search(source, q, get_setting("tmdb_api_key"))
    return {"items": [md.__dict__ | {"seasons": len(md.seasons)} for md in results]}


class ImportIn(BaseModel):
    source: str
    external_id: str
    type: str = Field(pattern="^(movie|tv)$")


@app.post("/api/scrape/import", status_code=201)
def scrape_import(body: ImportIn) -> dict:
    conn = db.get_conn()
    md = scrape_fetch(body.source, body.external_id, body.type, get_setting("tmdb_api_key"))
    existing = _find_existing(conn, md)
    if existing:
        return {"id": existing, "duplicated": True}
    work_id = _insert_work(conn, md)
    conn.commit()
    if md.poster_url:
        _cache_poster(work_id, md.poster_url)
    return {"id": work_id, "duplicated": False}


@app.post("/api/works/{work_id}/rescrape")
def rescrape(work_id: int, body: ImportIn) -> dict:
    """用刮削结果刷新元数据，保留我的评分/状态/短评/文件关联。"""
    conn = db.get_conn()
    if not conn.execute("SELECT 1 FROM works WHERE id=?", (work_id,)).fetchone():
        raise HTTPException(404, "作品不存在")
    md = scrape_fetch(body.source, body.external_id, body.type, get_setting("tmdb_api_key"))
    conn.execute(
        """UPDATE works SET title=?,original_title=?,year=?,region=?,genres=?,directors=?,
             cast=?,summary=?,douban_id=?,tmdb_id=?,imdb_id=?,douban_rating=?,
             updated_at=datetime('now','localtime') WHERE id=?""",
        (md.title, md.original_title, md.year, md.region, db.jdump(md.genres),
         db.jdump(md.directors), db.jdump(md.cast), md.summary, md.douban_id,
         md.tmdb_id, md.imdb_id, md.rating, work_id))
    if md.seasons:  # 只补不删：手工改过的季集不丢
        for s in md.seasons:
            conn.execute(
                "INSERT INTO seasons (work_id,season_number,title,overview) VALUES (?,?,?,?) "
                "ON CONFLICT(work_id,season_number) DO NOTHING",
                (work_id, s.number, s.title, s.overview))
            srow = conn.execute(
                "SELECT id FROM seasons WHERE work_id=? AND season_number=?",
                (work_id, s.number)).fetchone()
            conn.executemany(
                "INSERT OR IGNORE INTO episodes (season_id,episode_number,title,air_date,runtime) VALUES (?,?,?,?,?)",
                [(srow["id"], e.number, e.title, e.air_date, e.runtime) for e in s.episodes])
    conn.commit()
    if md.poster_url:
        _cache_poster(work_id, md.poster_url)
    return {"id": work_id}


# ---------- 海报 ----------

@app.get("/api/works/{work_id}/poster")
def poster(work_id: int) -> Response:
    row = db.get_conn().execute("SELECT poster_file FROM works WHERE id=?", (work_id,)).fetchone()
    if row and row["poster_file"]:
        p = db.POSTER_DIR / row["poster_file"]
        if p.is_file():
            mt = "image/webp" if p.suffix == ".webp" else None
            return FileResponse(p, media_type=mt)
    # 占位图
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="300" height="450">'
           '<rect width="100%" height="100%" fill="#2c2c34"/>'
           '<text x="50%" y="50%" fill="#8a8a94" font-size="18" text-anchor="middle">No Poster</text></svg>')
    return Response(svg, media_type="image/svg+xml")


# ---------- 本地文件 ----------

class ScanIn(BaseModel):
    folder: str


SCAN_JOBS: dict[str, dict] = {}


def _find_episode_id(conn: sqlite3.Connection, work_id: int, season: int | None,
                     episode: int | None) -> int | None:
    if season is None or episode is None:
        return None
    row = conn.execute(
        "SELECT e.id FROM episodes e JOIN seasons s ON e.season_id=s.id "
        "WHERE s.work_id=? AND s.season_number=? AND e.episode_number=?",
        (work_id, season, episode)).fetchone()
    return row["id"] if row else None


def _ensure_episode_id(conn: sqlite3.Connection, work_id: int, season: int | None,
                       episode: int | None) -> int | None:
    episode_id = _find_episode_id(conn, work_id, season, episode)
    if episode_id is not None or season is None or episode is None:
        return episode_id
    work = conn.execute("SELECT type FROM works WHERE id=?", (work_id,)).fetchone()
    if not work or work["type"] != "tv":
        return None
    conn.execute(
        "INSERT INTO seasons (work_id,season_number,title) VALUES (?,?,?) "
        "ON CONFLICT(work_id,season_number) DO NOTHING",
        (work_id, season, f"第 {season} 季"))
    season_row = conn.execute(
        "SELECT id FROM seasons WHERE work_id=? AND season_number=?", (work_id, season)).fetchone()
    conn.execute(
        "INSERT INTO episodes (season_id,episode_number,title) VALUES (?,?,?) "
        "ON CONFLICT(season_id,episode_number) DO NOTHING",
        (season_row["id"], episode, f"第 {episode} 集"))
    return _find_episode_id(conn, work_id, season, episode)


def _link_file(conn: sqlite3.Connection, path: str, size: int, work_id: int,
               episode_id: int | None) -> None:
    size = size or (os.path.getsize(path) if os.path.exists(path) else 0)
    conn.execute(
        "INSERT INTO files (path,size,work_id,episode_id) VALUES (?,?,?,?) "
        "ON CONFLICT(path) DO UPDATE SET work_id=excluded.work_id, episode_id=excluded.episode_id",
        (path, size, work_id, episode_id))


def _title_score(query: str, title: str) -> float:
    q = library.normalize(query)
    t = library.normalize(title)
    if not q or not t:
        return 0
    if q == t:
        return 100
    if len(q) >= 2 and len(t) >= 2 and (q in t or t in q):
        return 76 + 18 * min(len(q), len(t)) / max(len(q), len(t))
    return difflib.SequenceMatcher(None, q, t).ratio() * 72


def _hit_score(pf: library.ParsedFile, hit: WorkMetadata) -> float:
    actual_score = max((_title_score(q, title) for q in pf.queries
                        for title in (hit.title, hit.original_title) if title), default=0)
    alias_score = max((_title_score(q, hit.search_alias) for q in pf.queries), default=0)
    score = max(actual_score, min(alias_score, 82))
    try:
        hit_year = int(hit.year) if hit.year else None
    except (TypeError, ValueError):
        hit_year = None
    if pf.year and hit_year:
        score += 12 if pf.year == hit_year else (-8 if abs(pf.year - hit_year) == 1 else -25)
    if pf.work_type and hit.source == "tmdb":
        score += 10 if pf.work_type == hit.type else -45
    if hit.source == "douban":
        score += 2
    return score


def _local_match(conn: sqlite3.Connection, pf: library.ParsedFile) -> int | None:
    best = (0.0, None)
    for row in conn.execute("SELECT id,type,title,original_title,year FROM works"):
        score = max((_title_score(q, title) for q in pf.queries
                     for title in (row["title"], row["original_title"]) if title), default=0)
        if pf.year and row["year"]:
            score += 12 if pf.year == row["year"] else -25
        if pf.work_type:
            score += 8 if pf.work_type == row["type"] else -35
        if score > best[0]:
            best = (score, row["id"])
    return best[1] if best[0] >= 82 else None


def _existing_by_title(conn: sqlite3.Connection, md: WorkMetadata) -> int | None:
    titles = {library.normalize(t) for t in (md.title, md.original_title) if t}
    for row in conn.execute("SELECT id,title,original_title,year FROM works WHERE type=?", (md.type,)):
        row_titles = {library.normalize(t) for t in (row["title"], row["original_title"]) if t}
        if titles & row_titles and (not md.year or not row["year"] or md.year == row["year"]):
            return row["id"]
    return None


def _resolve_online(conn: sqlite3.Connection, pf: library.ParsedFile) -> tuple[int | None, bool, str]:
    if not pf.queries:
        return None, False, "无法从文件名或目录名提取片名"
    tmdb_key = get_setting("tmdb_api_key")
    sources = ["douban"] + (["tmdb"] if tmdb_key else [])
    candidates: dict[tuple[str, str], tuple[float, WorkMetadata]] = {}
    errors = []
    for query in pf.queries[:3]:
        for source in sources:
            try:
                hits = scrape_search(source, query, tmdb_key, limit=6)
            except ScrapeError as exc:
                errors.append(str(exc))
                continue
            for hit in hits:
                key = (source, hit.external_id)
                score = _hit_score(pf, hit)
                if key not in candidates or score > candidates[key][0]:
                    candidates[key] = (score, hit)
        if any(score >= 100 for score, _ in candidates.values()):
            break
    ranked = sorted(candidates.values(), key=lambda item: item[0], reverse=True)
    for score, hit in ranked[:5]:
        if score < 75:
            continue
        work_type = pf.work_type or hit.type
        if hit.source == "douban" and not pf.work_type:
            work_type = "auto"
        try:
            md = scrape_fetch(hit.source, hit.external_id, work_type, tmdb_key)
        except ScrapeError as exc:
            errors.append(str(exc))
            continue
        if not md.title:
            continue
        work_id = _find_existing(conn, md) or _existing_by_title(conn, md)
        created = work_id is None
        if created:
            work_id = _insert_work(conn, md)
        conn.commit()
        if created and md.poster_url:
            _cache_poster(work_id, md.poster_url)
        return work_id, created, ""
    if not ranked:
        return None, False, "未搜索到匹配作品" + ("；" + errors[-1] if errors else "")
    return None, False, "搜索结果可信度不足，已跳过"


def _run_scan_job(job_id: str, folder: str) -> None:
    job = SCAN_JOBS[job_id]
    conn = db.get_conn()
    resolutions: dict[str, tuple[int | None, bool, str]] = {}
    try:
        parsed = library.scan_folder(folder)
        job["total"] = len(parsed)
        job["phase"] = "自动识别与入库"
        if not parsed:
            job["status"] = "done"
            job["phase"] = "未发现视频文件"
            return
        for pf in parsed:
            job["current"] = Path(pf.path).name
            if conn.execute("SELECT 1 FROM files WHERE path=?", (pf.path,)).fetchone():
                job["skipped"] += 1
                job["processed"] += 1
                continue
            key = f"{pf.work_type or ''}:{pf.year or ''}:{library.normalize(pf.title)}"
            if key not in resolutions:
                local_id = _local_match(conn, pf)
                resolutions[key] = (local_id, False, "") if local_id else _resolve_online(conn, pf)
            work_id, created, error = resolutions[key]
            if not work_id:
                job["failed"] += 1
                job["errors"].append({"path": pf.path, "reason": error})
            else:
                episode_id = _ensure_episode_id(conn, work_id, pf.season, pf.episode)
                _link_file(conn, pf.path, pf.size, work_id, episode_id)
                conn.commit()
                if created and work_id not in job["work_ids"]:
                    job["added"] += 1
                else:
                    job["updated"] += 1
                if work_id not in job["work_ids"]:
                    job["work_ids"].append(work_id)
            job["processed"] += 1
        job["status"] = "done"
        job["phase"] = "扫描完成"
        job["current"] = ""
    except Exception as exc:
        job["status"] = "error"
        job["phase"] = "扫描中断"
        job["errors"].append({"path": folder, "reason": str(exc)})


@app.post("/api/library/scan", status_code=202)
def scan_library(body: ScanIn) -> dict:
    root = Path(body.folder)
    if not root.is_dir():
        raise HTTPException(400, f"目录不存在：{body.folder}")
    if any(job["status"] == "running" for job in SCAN_JOBS.values()):
        raise HTTPException(409, "已有扫描任务进行中")
    folder = str(root.resolve())
    folders = json.loads(get_setting("media_folders") or "[]")
    if folder not in folders:
        folders.append(folder)
        conn = db.get_conn()
        conn.execute(
            "INSERT INTO settings (key,value) VALUES ('media_folders',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(folders),))
        conn.commit()
    job_id = uuid.uuid4().hex
    SCAN_JOBS[job_id] = {
        "id": job_id, "status": "running", "phase": "正在扫描目录", "folder": folder,
        "processed": 0, "total": 0, "added": 0, "updated": 0, "skipped": 0,
        "failed": 0, "current": "", "errors": [], "work_ids": [],
    }
    threading.Thread(target=_run_scan_job, args=(job_id, folder), daemon=True).start()
    return SCAN_JOBS[job_id].copy()


@app.get("/api/library/scan/{job_id}")
def scan_job(job_id: str) -> dict:
    job = SCAN_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "扫描任务不存在")
    return job.copy()


@app.get("/api/library/folders")
def media_folders() -> dict:
    return {"items": json.loads(get_setting("media_folders") or "[]")}


@app.get("/api/system/directories")
def browse_directories(path: str = "") -> dict:
    if not path:
        if os.name == "nt":
            items = [{"name": f"{letter}:\\", "path": f"{letter}:\\"}
                     for letter in string.ascii_uppercase if Path(f"{letter}:\\").exists()]
        else:
            items = [{"name": str(p), "path": str(p)} for p in Path("/").iterdir() if p.is_dir()]
        return {"current": "", "parent": None, "items": items}
    current = Path(path)
    if not current.is_dir():
        raise HTTPException(400, "目录不存在或不可访问")
    items = []
    try:
        for child in current.iterdir():
            try:
                if child.is_dir():
                    items.append({"name": child.name or str(child), "path": str(child)})
            except OSError:
                continue
    except OSError as exc:
        raise HTTPException(400, f"无法读取目录：{exc}")
    items.sort(key=lambda item: item["name"].casefold())
    parent = str(current.parent) if current.parent != current else ""
    return {"current": str(current), "parent": parent, "items": items}


def _parse_existing_path(path: str) -> library.ParsedFile:
    folders = json.loads(get_setting("media_folders") or "[]")
    root = None
    for folder in sorted(folders, key=len, reverse=True):
        try:
            if Path(path).is_relative_to(folder):
                root = folder
                break
        except (OSError, ValueError):
            continue
    return library.parse_path(path, root)


def _file_details(file_id: int) -> dict:
    row = db.get_conn().execute(
        "SELECT f.*,w.title AS work_title,w.year AS work_year,w.type AS work_type FROM files f "
        "LEFT JOIN works w ON w.id=f.work_id WHERE f.id=?", (file_id,)).fetchone()
    if not row:
        raise HTTPException(404, "文件不存在")
    parsed = _parse_existing_path(row["path"])
    return {
        "id": row["id"], "path": row["path"], "size": row["size"],
        "work_id": row["work_id"], "work_title": row["work_title"],
        "work_year": row["work_year"], "work_type": row["work_type"],
        "episode_id": row["episode_id"], "progress_seconds": row["progress_seconds"],
        "duration_seconds": row["duration_seconds"], "watched": row["watched"],
        "parsed_title": parsed.title, "year": parsed.year, "season": parsed.season,
        "episode": parsed.episode, "queries": parsed.queries,
    }


@app.get("/api/files")
def list_files() -> dict:
    ids = [row["id"] for row in db.get_conn().execute("SELECT id FROM files ORDER BY path")]
    items = [_file_details(file_id) for file_id in ids]
    return {"items": items, "total": len(items)}


@app.post("/api/files/{file_id}/reidentify")
def reidentify_file(file_id: int) -> dict:
    conn = db.get_conn()
    row = conn.execute("SELECT path FROM files WHERE id=?", (file_id,)).fetchone()
    if not row:
        raise HTTPException(404, "文件不存在")
    parsed = _parse_existing_path(row["path"])
    work_id = _local_match(conn, parsed)
    created = False
    error = ""
    if not work_id:
        work_id, created, error = _resolve_online(conn, parsed)
    if not work_id:
        raise HTTPException(422, error or "无法识别此文件")
    episode_id = _ensure_episode_id(conn, work_id, parsed.season, parsed.episode)
    _link_file(conn, parsed.path, parsed.size, work_id, episode_id)
    conn.commit()
    result = _file_details(file_id)
    result["created"] = created
    return result


class FileAssignIn(BaseModel):
    source: str
    external_id: str
    type: str = Field(pattern="^(movie|tv)$")
    season: int | None = None
    episode: int | None = None


@app.post("/api/files/{file_id}/assign")
def assign_file(file_id: int, body: FileAssignIn) -> dict:
    conn = db.get_conn()
    row = conn.execute("SELECT path,size FROM files WHERE id=?", (file_id,)).fetchone()
    if not row:
        raise HTTPException(404, "文件不存在")
    md = scrape_fetch(body.source, body.external_id, body.type, get_setting("tmdb_api_key"))
    work_id = _find_existing(conn, md) or _existing_by_title(conn, md)
    if work_id is None:
        work_id = _insert_work(conn, md)
        conn.commit()
        if md.poster_url:
            _cache_poster(work_id, md.poster_url)
    episode_id = _ensure_episode_id(conn, work_id, body.season, body.episode)
    _link_file(conn, row["path"], row["size"], work_id, episode_id)
    conn.commit()
    return _file_details(file_id)


class ManualAssignIn(BaseModel):
    title: str = Field(min_length=1)
    year: int | None = None
    type: str = Field(pattern="^(movie|tv)$")
    season: int | None = None
    episode: int | None = None


@app.post("/api/files/{file_id}/manual-assign")
def manual_assign_file(file_id: int, body: ManualAssignIn) -> dict:
    conn = db.get_conn()
    row = conn.execute("SELECT path,size FROM files WHERE id=?", (file_id,)).fetchone()
    if not row:
        raise HTTPException(404, "文件不存在")
    md = WorkMetadata(source="manual", external_id="", type=body.type,
                      title=body.title.strip(), year=body.year)
    work_id = _existing_by_title(conn, md)
    if work_id is None:
        work_id = _insert_work(conn, md)
    episode_id = _ensure_episode_id(conn, work_id, body.season, body.episode)
    _link_file(conn, row["path"], row["size"], work_id, episode_id)
    conn.commit()
    return _file_details(file_id)


class LinkIn(BaseModel):
    path: str
    size: int = 0
    work_id: int
    episode_id: int | None = None


@app.post("/api/files", status_code=201)
def link_file(body: LinkIn) -> dict:
    conn = db.get_conn()
    if not conn.execute("SELECT 1 FROM works WHERE id=?", (body.work_id,)).fetchone():
        raise HTTPException(404, "作品不存在")
    size = body.size or (os.path.getsize(body.path) if os.path.exists(body.path) else 0)
    cur = conn.execute(
        "INSERT INTO files (path,size,work_id,episode_id) VALUES (?,?,?,?) "
        "ON CONFLICT(path) DO UPDATE SET work_id=excluded.work_id, episode_id=excluded.episode_id",
        (body.path, size, body.work_id, body.episode_id))
    conn.commit()
    return {"id": cur.lastrowid}


@app.delete("/api/files/{file_id}", status_code=204)
def unlink_file(file_id: int) -> Response:
    conn = db.get_conn()
    conn.execute("DELETE FROM files WHERE id=?", (file_id,))
    conn.commit()
    return Response(status_code=204)


CHUNK = 1024 * 1024


@app.get("/api/files/{file_id}/stream")
def stream_file(file_id: int, request: Request) -> Response:
    row = db.get_conn().execute("SELECT path FROM files WHERE id=?", (file_id,)).fetchone()
    if not row or not os.path.isfile(row["path"]):
        raise HTTPException(404, "文件不存在")
    path = row["path"]
    size = os.path.getsize(path)
    range_header = request.headers.get("range", "")
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"

    m = re.match(r"bytes=(\d*)-(\d*)", range_header)
    if m and (m.group(1) or m.group(2)):
        start = int(m.group(1) or 0)
        end = int(m.group(2) or size - 1)
        end = min(end, size - 1)

        def iter_range():
            with open(path, "rb") as f:
                f.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = f.read(min(CHUNK, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        return StreamingResponse(
            iter_range(), media_type=mime, status_code=206,
            headers={"Content-Range": f"bytes {start}-{end}/{size}",
                     "Accept-Ranges": "bytes",
                     "Content-Length": str(end - start + 1)})
    def iter_full():
        with open(path, "rb") as f:
            while chunk := f.read(CHUNK):
                yield chunk

    return StreamingResponse(
        iter_full(), media_type=mime,
        headers={"Accept-Ranges": "bytes", "Content-Length": str(size)})


class ProgressIn(BaseModel):
    position_seconds: int | None = None
    duration_seconds: int | None = None
    watched: bool | None = None


def _record_file_progress(file_id: int, position_seconds: int | None,
                          duration_seconds: int | None, watched: bool | None) -> dict:
    conn = db.get_conn()
    row = conn.execute(
        "SELECT f.*,w.type AS work_type FROM files f LEFT JOIN works w ON w.id=f.work_id WHERE f.id=?",
        (file_id,)).fetchone()
    if not row:
        return {"id": file_id, "missing": True}
    position = max(0, row["progress_seconds"] if position_seconds is None else position_seconds)
    duration = max(0, row["duration_seconds"] if duration_seconds is None else duration_seconds)
    if watched is None:
        is_watched = bool(row["watched"]) or (duration > 0 and position >= duration * .9)
    else:
        is_watched = watched
    conn.execute(
        "UPDATE files SET progress_seconds=?,duration_seconds=?,watched=?,"
        "watched_at=CASE WHEN ?=1 THEN COALESCE(watched_at,datetime('now','localtime')) ELSE NULL END WHERE id=?",
        (position, duration, int(is_watched), int(is_watched), file_id))
    if row["episode_id"]:
        conn.execute(
            "UPDATE episodes SET progress_seconds=?,duration_seconds=?,watched=?,"
            "watched_at=CASE WHEN ?=1 THEN COALESCE(watched_at,datetime('now','localtime')) ELSE NULL END WHERE id=?",
            (position, duration, int(is_watched), int(is_watched), row["episode_id"]))
    if row["work_id"]:
        if row["work_type"] == "movie" and is_watched:
            conn.execute("UPDATE works SET status='watched',updated_at=datetime('now','localtime') WHERE id=?", (row["work_id"],))
        elif position > 0 or is_watched:
            conn.execute(
                "UPDATE works SET status='watching',updated_at=datetime('now','localtime') "
                "WHERE id=? AND status IN ('none','want')", (row["work_id"],))
        if row["work_type"] == "tv":
            counts = conn.execute(
                "SELECT COUNT(*) total,SUM(watched) watched FROM episodes e "
                "JOIN seasons s ON e.season_id=s.id WHERE s.work_id=?", (row["work_id"],)).fetchone()
            if counts["total"] and counts["watched"] == counts["total"]:
                conn.execute("UPDATE works SET status='watched' WHERE id=?", (row["work_id"],))
    conn.commit()
    return {"id": file_id, "position_seconds": position, "duration_seconds": duration, "watched": is_watched}


@app.post("/api/files/{file_id}/progress")
def update_file_progress(file_id: int, body: ProgressIn) -> dict:
    if not db.get_conn().execute("SELECT 1 FROM files WHERE id=?", (file_id,)).fetchone():
        raise HTTPException(404, "文件不存在")
    return _record_file_progress(file_id, body.position_seconds, body.duration_seconds, body.watched)


@app.post("/api/episodes/{episode_id}/progress")
def update_progress(episode_id: int, body: ProgressIn) -> dict:
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
    if not row:
        raise HTTPException(404, "剧集不存在")
    file = conn.execute("SELECT id FROM files WHERE episode_id=? ORDER BY id LIMIT 1", (episode_id,)).fetchone()
    if file:
        _record_file_progress(file["id"], body.position_seconds, body.duration_seconds, body.watched)
    else:
        position = row["progress_seconds"] if body.position_seconds is None else body.position_seconds
        duration = row["duration_seconds"] if body.duration_seconds is None else body.duration_seconds
        is_watched = bool(row["watched"]) if body.watched is None else body.watched
        conn.execute(
            "UPDATE episodes SET progress_seconds=?,duration_seconds=?,watched=?,"
            "watched_at=CASE WHEN ?=1 THEN COALESCE(watched_at,datetime('now','localtime')) ELSE NULL END WHERE id=?",
            (position, duration, int(is_watched), int(is_watched), episode_id))
        conn.commit()
    return {"id": episode_id}


@app.patch("/api/episodes/{episode_id}")
def patch_episode(episode_id: int, body: ProgressIn) -> dict:
    return update_progress(episode_id, body)


@app.post("/api/files/{file_id}/local-play")
def local_play(file_id: int) -> dict:
    row = db.get_conn().execute(
        "SELECT f.*,w.title FROM files f LEFT JOIN works w ON w.id=f.work_id WHERE f.id=?", (file_id,)).fetchone()
    if not row or not os.path.isfile(row["path"]):
        raise HTTPException(404, "本地视频文件不存在")
    try:
        return player.launch(
            file_id, row["path"], row["title"] or Path(row["path"]).stem,
            get_setting("local_player_path"), row["progress_seconds"], row["duration_seconds"],
            _record_file_progress,
        )
    except player.LocalPlayerError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/player/local")
def local_player_status() -> dict:
    return player.status()


# ---------- 统计 ----------

@app.get("/api/stats")
def stats() -> dict:
    conn = db.get_conn()
    total = conn.execute("SELECT COUNT(*) c FROM works").fetchone()["c"]
    by_status = {r["status"]: r["c"] for r in conn.execute(
        "SELECT status, COUNT(*) c FROM works GROUP BY status")}
    by_type = {r["type"]: r["c"] for r in conn.execute(
        "SELECT type, COUNT(*) c FROM works GROUP BY type")}
    avg = conn.execute("SELECT ROUND(AVG(my_rating),1) a FROM works WHERE my_rating IS NOT NULL").fetchone()["a"]
    watched_eps = conn.execute("SELECT COUNT(*) c FROM episodes WHERE watched=1").fetchone()["c"]
    total_eps = conn.execute("SELECT COUNT(*) c FROM episodes").fetchone()["c"]
    by_year = [dict(r) for r in conn.execute(
        "SELECT year, COUNT(*) c FROM works WHERE year IS NOT NULL GROUP BY year ORDER BY year")]
    by_region = [dict(r) for r in conn.execute(
        "SELECT region, COUNT(*) c FROM works WHERE region!='' GROUP BY region ORDER BY c DESC LIMIT 12")]
    top = [db.row_to_work(r) for r in conn.execute(
        "SELECT * FROM works WHERE my_rating IS NOT NULL ORDER BY my_rating DESC, updated_at DESC LIMIT 10")]
    recent_reviews = [dict(r) for r in conn.execute(
        "SELECT r.id, r.rating, r.content, r.created_at, w.id AS work_id, w.title, w.type "
        "FROM reviews r JOIN works w ON w.id=r.work_id ORDER BY r.created_at DESC LIMIT 10")]
    return {
        "total": total, "by_status": by_status, "by_type": by_type,
        "avg_my_rating": avg, "watched_episodes": watched_eps, "total_episodes": total_eps,
        "by_year": by_year, "by_region": by_region,
        "top_rated": [{"id": w["id"], "title": w["title"], "my_rating": w["my_rating"],
                       "poster_url": f"/api/works/{w['id']}/poster"} for w in top],
        "recent_reviews": recent_reviews,
    }


# ---------- 静态前端 ----------

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

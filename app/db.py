"""SQLite 数据层：作品 / 季 / 集 / 评分 / 短评 / 标签 / 本地文件。

local-first：所有数据都在项目 data/ 目录下，可直接备份整个目录。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
POSTER_DIR = DATA_DIR / "posters"
DB_PATH = DATA_DIR / "filmvault.db"

_local = threading.local()

SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS works (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL CHECK (type IN ('movie', 'tv')),
    title TEXT NOT NULL,
    original_title TEXT DEFAULT '',
    year INTEGER,
    region TEXT DEFAULT '',
    genres TEXT DEFAULT '[]',          -- JSON array
    directors TEXT DEFAULT '[]',       -- JSON array
    cast TEXT DEFAULT '[]',            -- JSON array
    summary TEXT DEFAULT '',
    poster_file TEXT DEFAULT '',       -- data/posters 下的文件名
    douban_id TEXT DEFAULT '',
    tmdb_id INTEGER,
    imdb_id TEXT DEFAULT '',
    douban_rating REAL,                -- 站外评分（刮削得到）
    my_rating REAL CHECK (my_rating IS NULL OR (my_rating >= 0 AND my_rating <= 10)),
    status TEXT NOT NULL DEFAULT 'none'
        CHECK (status IN ('none', 'want', 'watching', 'watched', 'on_hold', 'dropped')),
    origin TEXT NOT NULL DEFAULT 'auto',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS seasons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    season_number INTEGER NOT NULL,
    title TEXT DEFAULT '',
    overview TEXT DEFAULT '',
    UNIQUE (work_id, season_number)
);

CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id INTEGER NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    episode_number INTEGER NOT NULL,
    title TEXT DEFAULT '',
    air_date TEXT DEFAULT '',
    runtime INTEGER,                   -- 分钟
    watched INTEGER NOT NULL DEFAULT 0,
    watched_at TEXT,
    progress_seconds INTEGER NOT NULL DEFAULT 0,
    duration_seconds INTEGER NOT NULL DEFAULT 0,
    UNIQUE (season_id, episode_number)
);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    rating REAL CHECK (rating IS NULL OR (rating >= 0 AND rating <= 10)),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS work_tags (
    work_id INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (work_id, tag_id)
);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    size INTEGER NOT NULL DEFAULT 0,
    work_id INTEGER REFERENCES works(id) ON DELETE SET NULL,
    episode_id INTEGER REFERENCES episodes(id) ON DELETE SET NULL,
    progress_seconds INTEGER NOT NULL DEFAULT 0,
    duration_seconds INTEGER NOT NULL DEFAULT 0,
    watched INTEGER NOT NULL DEFAULT 0,
    watched_at TEXT,
    added_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_works_status ON works(status);
CREATE INDEX IF NOT EXISTS idx_works_type ON works(type);
CREATE INDEX IF NOT EXISTS idx_episodes_season ON episodes(season_id);
CREATE INDEX IF NOT EXISTS idx_files_work ON files(work_id);
"""


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        POSTER_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        _local.conn = conn
    return conn


def init_db() -> None:
    conn = get_conn()
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < SCHEMA_VERSION:
        conn.executescript(SCHEMA)
        if version < 2:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(files)")}
            migrations = {
                "progress_seconds": "INTEGER NOT NULL DEFAULT 0",
                "duration_seconds": "INTEGER NOT NULL DEFAULT 0",
                "watched": "INTEGER NOT NULL DEFAULT 0",
                "watched_at": "TEXT",
            }
            for name, definition in migrations.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE files ADD COLUMN {name} {definition}")
            conn.execute(
                "UPDATE files SET progress_seconds=COALESCE((SELECT progress_seconds FROM episodes WHERE id=files.episode_id),0), "
                "duration_seconds=COALESCE((SELECT duration_seconds FROM episodes WHERE id=files.episode_id),0), "
                "watched=COALESCE((SELECT watched FROM episodes WHERE id=files.episode_id),0), "
                "watched_at=(SELECT watched_at FROM episodes WHERE id=files.episode_id) WHERE episode_id IS NOT NULL")
        if version < 3:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(works)")}
            if "origin" not in columns:
                conn.execute("ALTER TABLE works ADD COLUMN origin TEXT NOT NULL DEFAULT 'auto'")
            conn.execute("UPDATE works SET origin='manual' WHERE NOT EXISTS (SELECT 1 FROM files WHERE work_id=works.id)")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()


def row_to_work(row: sqlite3.Row) -> dict:
    d = dict(row)
    for key in ("genres", "directors", "cast"):
        d[key] = json.loads(d[key] or "[]")
    return d


def jdump(value) -> str:
    return json.dumps(value or [], ensure_ascii=False)

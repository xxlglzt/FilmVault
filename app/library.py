"""本地影视文件扫描与容错标题解析。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".ts", ".m4v", ".webm", ".rmvb", ".iso"}

TECH_WORDS = (
    r"1080p|1080i|720p|2160p|480p|576p|4k|8k|uhd|hdr10\+?|hdr|dv|hd|sd|bd|"
    r"brrip|bdrip|blu-?ray|web(?:-?dl)?|webrip|hdtv|dvdrip|dvd|remux|hdrip|tvrip|"
    r"x26[45]|h\.?26[45]|hevc|av1|vc-?1|xvid|divx|avc|10bit|8bit|yuv420p10|"
    r"aac|ac3|dts(?:-hd)?(?:\.ma)?|lpcm|pcm|flac|truehd|atmos|ddp?[257]\.?\d*|mp3|opus|"
    r"\d+audio|audio|complete|proper|repack|extended|unrated|limited|theatrical|imax|"
    r"chs|cht|big5|eng|ita|简繁|简体|繁体|中英字幕|中文字幕|中英双字|中字|双字|双语|国英双语|"
    r"内嵌|内封|外挂|特效字幕|老片修复版|修复版"
)
GENERIC_DIR_NAMES = {
    "电影", "电视剧", "影视", "视频", "剧集", "动漫", "纪录片", "合集",
    "movie", "movies", "tv", "tvshow", "tvshows", "series", "video", "videos", "media",
}
TECH_RE = re.compile(r"(?<![0-9a-z])(?:" + TECH_WORDS + r")(?![0-9a-z])", re.I)


@dataclass
class ParsedFile:
    path: str
    size: int
    title: str = ""
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    work_type: str | None = None
    queries: list[str] = field(default_factory=list)


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", s)


def _normalize(s: str) -> str:
    return normalize(s)


def _clean_title(value: str, remove_leading_group: bool = True) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"(?i)\b(?:lpcm|pcm|flac|dts(?:-hd)?(?:\.ma)?|ddp?[257])[ .]?\d(?:\.\d)?", " ", value)
    value = re.sub(r"中英双字|中英字幕|中文字幕|国英双语", " ", value)
    value = re.sub(r"[._]+", " ", value)
    value = re.sub(r"(?i)\b(?:www\.)?[a-z0-9-]+\.(?:com|net|org|cc|tv|me)\b", " ", value)

    brackets = list(re.finditer(r"[\[\(【《]([^\]\)】》]*)[\]\)】》]", value))
    for i, match in reversed(list(enumerate(brackets))):
        content = match.group(1).strip()
        remainder = TECH_RE.sub(" ", value[match.end():])
        has_title_after = bool(re.search(r"[0-9a-z\u4e00-\u9fff]", remainder, re.I))
        is_leading_group = i == 0 and not value[:match.start()].strip() and has_title_after
        if TECH_RE.search(content) or (remove_leading_group and is_leading_group):
            value = value[:match.start()] + " " + value[match.end():]
        else:
            value = value[:match.start()] + " " + content + " " + value[match.end():]

    value = TECH_RE.sub(" ", value)
    value = re.sub(r"(?<!\d)[1257]\.[01](?!\d)", " ", value)
    value = re.sub(r"(?<![0-9a-z])(?:ws|fs)(?![0-9a-z])", " ", value, flags=re.I)
    value = re.sub(r"\s*[-–—_]+\s*", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" -–—._")
    return value


def _query_variants(title: str) -> list[str]:
    if not title or title.isdigit():
        return []
    variants = [title]
    words = title.split()
    for cut in range(1, min(3, len(words) - 1) + 1):
        candidate = " ".join(words[:-cut]).strip()
        if len(normalize(candidate)) >= 2:
            variants.append(candidate)
    without_year = re.sub(r"(?<!\d)(?:19|20)\d{2}(?!\d)", "", title)
    without_year = re.sub(r"\s+", " ", without_year).strip()
    if without_year and without_year != title:
        variants.append(without_year)
    return list(dict.fromkeys(variants))


def parse_path(path: str | Path, root: str | Path | None = None) -> ParsedFile:
    p = Path(path)
    stem = p.stem
    original = unicodedata.normalize("NFKC", stem)
    season = episode = year = None
    working = original

    patterns = [
        r"(?<!\w)[Ss](\d{1,3})[ ._-]*[Ee][Pp]?(\d{1,4})(?!\w)",
        r"(?<!\w)(\d{1,2})[xX](\d{1,4})(?!\w)",
    ]
    for pattern in patterns:
        match = re.search(pattern, working)
        if match:
            season, episode = int(match.group(1)), int(match.group(2))
            working = working[:match.start()] + " " + working[match.end():]
            break

    if episode is None:
        match = re.search(r"第\s*(\d{1,4})\s*[集话回]", working)
        if match:
            episode = int(match.group(1))
            working = working[:match.start()] + " " + working[match.end():]
        else:
            match = re.search(r"(?<!\w)[Ee][Pp]?(\d{1,4})(?!\w)", working)
            if match:
                episode = int(match.group(1))
                working = working[:match.start()] + " " + working[match.end():]

    match = re.search(r"第\s*(\d{1,3})\s*季", working)
    if match:
        season = int(match.group(1))
        working = working[:match.start()] + " " + working[match.end():]

    match = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", working)
    if match:
        year = int(match.group(1))
        working = working[:match.start()] + " " + working[match.end():]

    if episode is None:
        match = re.search(r"(?:^|[\s\-–—])(?:第\s*)?(\d{1,3})(?:\s*(?:end|END))?\s*$", working)
        if match and working[:match.start()].strip(" -–—._"):
            episode = int(match.group(1))
            working = working[:match.start()]

    if season is None and episode is not None:
        season = 1

    title = _clean_title(working)
    queries = _query_variants(title)

    try:
        root_path = Path(root).resolve() if root else None
        parents = list(p.resolve().parents if root else p.parents)
        if root_path:
            parent_dirs = [parent for parent in parents if parent != root_path and root_path in parent.parents]
            parent_dirs = sorted(parent_dirs, key=lambda x: len(x.parts), reverse=True)
        else:
            parent_dirs = [p.parent]
    except (OSError, ValueError):
        parent_dirs = [p.parent]

    for parent in parent_dirs[:2]:
        cleaned = _clean_title(parent.name)
        cleaned = re.sub(r"(?i)^(?:season|s)\s*\d{1,3}$", "", cleaned).strip()
        cleaned = re.sub(r"^第\s*\d{1,3}\s*季$", "", cleaned).strip()
        if (cleaned and not cleaned.isdigit() and normalize(cleaned) not in GENERIC_DIR_NAMES
                and normalize(cleaned) != normalize(title)):
            queries.extend(_query_variants(cleaned))
            if not title or title.isdigit() or len(normalize(title)) < 2:
                title = cleaned

    if not title and queries:
        title = queries[0]
    if episode is None and parent_dirs and re.fullmatch(r"\d{1,2}", stem.strip()):
        episode = int(stem)
        season = 1
    unique_queries = list(dict.fromkeys(q for q in queries if len(normalize(q)) >= 2))
    work_type = "tv" if season is not None or episode is not None else None
    size = p.stat().st_size if p.exists() else 0
    return ParsedFile(path=str(p), size=size, title=title, year=year,
                      season=season, episode=episode, work_type=work_type,
                      queries=unique_queries)


def parse_filename(name: str) -> tuple[str, int | None, int | None, int | None]:
    dummy = Path(name)
    if dummy.suffix.lower() not in VIDEO_EXTS:
        dummy = dummy.with_suffix(".mkv")
    try:
        parsed = parse_path(dummy)
        return parsed.title, parsed.year, parsed.season, parsed.episode
    except OSError:
        return "", None, None, None


def scan_folder(folder: str, known_works: list[dict] | None = None) -> list[ParsedFile]:
    root = Path(folder)
    if not root.is_dir():
        raise FileNotFoundError(f"目录不存在：{folder}")
    out = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in VIDEO_EXTS:
            continue
        try:
            out.append(parse_path(p, root))
        except OSError:
            continue
    return out

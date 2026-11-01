"""豆瓣刮削源（非官方，best-effort）。

豆瓣没有公开 API 且反爬严格：
- 搜索走 movie.douban.com/j/subject_suggest 建议接口（JSON）
- 条目页前置一个 SHA-512 proof-of-work 挑战（JS 计算 nonce 后提交表单），
  这里在服务端用 hashlib 解挑战换 cookie，再继续抓正文
- 任何一步失败都抛 ScrapeError，前端会把原因显示出来
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
import threading
from urllib.parse import quote_plus

from .base import ScrapeError, WorkMetadata

SEARCH_URL = "https://movie.douban.com/j/subject_suggest"
SUBJECT_URL = "https://movie.douban.com/subject/{id}/"

_local = threading.local()


def _session():
    """带 cookie 持久化的 curl_cffi 会话（线程隔离）。"""
    s = getattr(_local, "session", None)
    if s is None:
        try:
            from curl_cffi import requests as cffi_requests
            s = cffi_requests.Session(impersonate="chrome", timeout=20)
        except ImportError:
            import httpx
            s = httpx.Client(headers={
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")}, timeout=20)
        _local.session = s
    return s


def _text(resp) -> str:
    return resp.text if hasattr(resp, "text") else resp.content.decode("utf-8", "ignore")


def _solve_pow(cha: str, difficulty: int = 4) -> int:
    """计算 nonce：sha512(cha+nonce) 的十六进制以 difficulty 个 0 开头。"""
    target = "0" * difficulty
    nonce = 0
    while True:
        nonce += 1
        h = hashlib.sha512((cha + str(nonce)).encode()).hexdigest()
        if h.startswith(target):
            return nonce


def _get(url: str) -> str:
    """GET 页面，自动多轮处理豆瓣的 proof-of-work 挑战链。"""
    s = _session()
    resp = s.get(url)
    if getattr(resp, "status_code", 200) != 200:
        raise ScrapeError(f"豆瓣返回 HTTP {resp.status_code}（可能被反爬拦截）")
    html = _text(resp)
    for _ in range(5):
        if 'name="sol"' not in html:
            return html
        # 解析挑战表单：action + 全部隐藏字段
        from urllib.parse import urljoin
        m_form = re.search(r'<form[^>]*action="([^"]*)"[^>]*>', html)
        action = urljoin(resp.url, m_form.group(1)) if m_form else resp.url
        fields = dict(re.findall(r'<input[^>]*name="([^"]*)"[^>]*value="([^"]*)"', html))
        cha = fields.get("cha", "")
        if not cha:
            raise ScrapeError("豆瓣反爬挑战页解析失败")
        fields["sol"] = str(_solve_pow(cha))
        resp = s.post(action, data=fields, allow_redirects=True)
        if getattr(resp, "status_code", 200) != 200:
            raise ScrapeError(f"豆瓣挑战提交失败 HTTP {resp.status_code}")
        html = _text(resp)
    raise ScrapeError("豆瓣反爬挑战连续多轮未通过，请稍后重试")


def _large_poster(url: str) -> str:
    return (url.replace("/s_ratio_poster/", "/l_ratio_poster/")
            .replace("/view/photo/s/", "/view/photo/l/")
            .replace("/s/", "/l/"))


def search(query: str, limit: int = 12) -> list[WorkMetadata]:
    s = _session()
    try:
        resp = s.get(SEARCH_URL, params={"q": query})
    except Exception as e:
        raise ScrapeError(f"连接豆瓣失败（可能需要代理）：{type(e).__name__}: {e}") from e
    if getattr(resp, "status_code", 200) != 200:
        raise ScrapeError(f"豆瓣搜索返回 HTTP {resp.status_code}（可能被反爬拦截）")
    try:
        items = json.loads(_text(resp))
    except (json.JSONDecodeError, ValueError):
        raise ScrapeError("豆瓣搜索返回格式异常（疑似被拦截），请稍后重试或换 TMDB 源")
    results = []
    for item in items:
        if not isinstance(item, dict) or "id" not in item:
            continue
        results.append(WorkMetadata(
            source="douban",
            external_id=str(item["id"]),
            type="movie",  # suggest 接口不区分电影/电视剧，导入时由用户指定
            title=re.sub(r"\s*\(豆瓣\)\s*$", "", item.get("title", "")),
            search_alias=query,
            year=item.get("year") or None,
            poster_url=_large_poster(item.get("img", "")),
            douban_id=str(item["id"]),
        ))
        if len(results) >= limit:
            break
    if not results:
        results = _web_search(query, limit)
    return results


def _web_search(query: str, limit: int) -> list[WorkMetadata]:
    page = _get(f"https://www.douban.com/search?cat=1002&q={quote_plus(query)}")

    def clean(fragment: str) -> str:
        return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", "", fragment))).strip()

    results = []
    for block in page.split('<div class="result">')[1:]:
        id_match = re.search(r"subject(?:%2F|/)(\d+)", block)
        title_match = re.search(r'<div class="title">.*?<a[^>]*>(.*?)</a>', block, re.S)
        if not id_match or not title_match:
            continue
        title = clean(title_match.group(1))
        poster_match = re.search(r'<img src="([^"]+)"', block)
        rating_match = re.search(r'<span class="rating_nums">([\d.]+)</span>', block)
        year_match = re.search(r"/\s*((?:19|20)\d{2})(?:\s|<)", block)
        summary_match = re.search(r"<p>(.*?)</p>", block, re.S)
        results.append(WorkMetadata(
            source="douban", external_id=id_match.group(1),
            type="tv" if re.search(r"电视剧|剧集", block[:1500]) else "movie",
            title=title, search_alias=query,
            year=int(year_match.group(1)) if year_match else None,
            summary=clean(summary_match.group(1)) if summary_match else "",
            poster_url=_large_poster(html_lib.unescape(poster_match.group(1))) if poster_match else "",
            rating=float(rating_match.group(1)) if rating_match else None,
            douban_id=id_match.group(1),
        ))
        if len(results) >= limit:
            break
    return results


def fetch(external_id: str, work_type: str) -> WorkMetadata:
    html = _get(SUBJECT_URL.format(id=external_id))
    if "检测到有异常请求" in html:
        raise ScrapeError("豆瓣触发了反爬验证，建议改用 TMDB 源或稍后再试")

    def m(pattern: str, group: int = 1, default: str = "") -> str:
        mm = re.search(pattern, html, re.S)
        return mm.group(group).strip() if mm else default

    title = m(r'<span property="v:itemreviewed">(.*?)</span>') or m(r"<title>(.*?)</title>")
    title = re.sub(r"\s*\(豆瓣\)\s*$", "", title)
    rating_str = m(r'<strong class="ll rating_num"[^>]*>([\d.]+)</strong>')
    year_str = m(r"<span class=\"year\">\((\d{4})\)</span>") or m(r"\((\d{4})\)")

    info = m(r'<div id="info">(.*?)</div>')
    region = ""
    rm = re.search(r"制片国家/地区:</span>\s*([^<]+)<", info)
    if rm:
        region = rm.group(1).strip()
    genres = re.findall(r'<span property="v:genre">(.*?)</span>', info)
    directors = re.findall(r'rel="v:directedBy">([^<]+)</a>', info)
    cast = re.findall(r'(?s)<a href="[^"]*(?:celebrity|personage)/[^"]+" rel="v:starring">([^<]+)</a>', html)
    summary_html = m(r'<span property="v:summary"[^>]*>(.*?)</span>')
    summary = re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", summary_html))).strip()
    imdb_m = re.search(r"tt\d{7,}", html)
    imdb_id = imdb_m.group(0) if imdb_m else ""
    poster = m(r'<img src="([^"]+)"[^>]*rel="v:image"')
    if not poster:
        poster = m(r'<a class="nbgnbg"[^>]*>.*?<img src="([^"]+)"')
    poster = _large_poster(poster)

    resolved_type = work_type
    if work_type == "auto":
        resolved_type = "tv" if re.search(r"集数:</span>\s*\d+", info) else "movie"

    return WorkMetadata(
        source="douban",
        external_id=external_id,
        type=resolved_type,
        title=title,
        year=int(year_str) if year_str.isdigit() else None,
        region=region,
        genres=genres,
        directors=directors,
        cast=cast[:15],
        summary=summary,
        poster_url=poster,
        rating=float(rating_str) if rating_str else None,
        imdb_id=imdb_id,
        douban_id=external_id,
    )

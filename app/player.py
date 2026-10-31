"""本地播放器启动与观看进度监控。"""

from __future__ import annotations

import socket
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable

import httpx

ProgressCallback = Callable[[int, int, int, bool | None], None]


class LocalPlayerError(Exception):
    pass


_lock = threading.Lock()
_session: dict | None = None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _vlc_status(port: int) -> tuple[int, int, str] | None:
    try:
        resp = httpx.get(
            f"http://127.0.0.1:{port}/requests/status.xml",
            auth=("", "filmvault"), timeout=1.2,
        )
        if resp.status_code != 200:
            return None
        root = ET.fromstring(resp.text)
        return int(root.findtext("time", "0")), int(root.findtext("length", "0")), root.findtext("state", "")
    except (httpx.HTTPError, ET.ParseError, ValueError):
        return None


def status() -> dict:
    with _lock:
        if not _session:
            return {"active": False}
        return {key: value for key, value in _session.items()
                if key not in ("process", "port", "started_monotonic")}


def launch(file_id: int, video_path: str, title: str, executable: str,
           start_position: int, duration: int, on_progress: ProgressCallback) -> dict:
    global _session
    executable = executable.strip().strip('"')
    if not executable:
        raise LocalPlayerError("请先在设置中填写本地播放器程序路径")
    player_path = Path(executable)
    if not player_path.is_file():
        raise LocalPlayerError(f"播放器程序不存在：{executable}")
    with _lock:
        if _session and _session["active"]:
            raise LocalPlayerError("已有本地播放任务，请先关闭当前播放器")
        is_vlc = player_path.stem.lower() == "vlc"
        port = _free_port() if is_vlc else None
        command = [str(player_path)]
        if is_vlc:
            command += [
                "--extraintf", "http", "--http-host", "127.0.0.1",
                "--http-port", str(port), "--http-password", "filmvault",
            ]
        command.append(video_path)
        try:
            process = subprocess.Popen(command)
        except OSError as exc:
            raise LocalPlayerError(f"无法启动本地播放器：{exc}") from exc
        _session = {
            "active": True, "file_id": file_id, "title": title,
            "process": process, "port": port, "is_vlc": is_vlc,
            "sync_mode": "realtime" if is_vlc else "elapsed",
            "position_seconds": start_position, "duration_seconds": duration,
            "started_monotonic": time.monotonic(), "error": "",
        }
        thread = threading.Thread(target=_monitor, args=(_session, on_progress), daemon=True)
        thread.start()
        return {key: value for key, value in _session.items()
                if key not in ("process", "port", "started_monotonic")}


def _monitor(session: dict, on_progress: ProgressCallback) -> None:
    process: subprocess.Popen = session["process"]
    start_position = session["position_seconds"]
    last_position = start_position
    duration = session["duration_seconds"]
    seen_playing = False
    try:
        while True:
            if session["is_vlc"]:
                vlc = _vlc_status(session["port"])
                if vlc:
                    position, media_duration, state = vlc
                    if position or state == "playing":
                        seen_playing = True
                    if position:
                        last_position = position
                    if media_duration:
                        duration = media_duration
                    session["position_seconds"] = last_position
                    session["duration_seconds"] = duration
                    on_progress(session["file_id"], last_position, duration, None)
                    if state == "stopped" and seen_playing:
                        break
            if process.poll() is not None:
                break
            time.sleep(2)
        if not session["is_vlc"] or last_position == start_position:
            elapsed = max(0, int(time.monotonic() - session["started_monotonic"]))
            last_position = start_position + elapsed
        on_progress(session["file_id"], last_position, duration, None)
        session["position_seconds"] = last_position
        session["duration_seconds"] = duration
    except Exception as exc:
        session["error"] = str(exc)
    finally:
        session["active"] = False

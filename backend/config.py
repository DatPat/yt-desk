from __future__ import annotations

import os
import secrets
import socket
import sys
from pathlib import Path


APP_NAME = "YT Desk"
PORT = int(os.environ.get("YTDESK_PORT", "8765"))
HOST = os.environ.get("YTDESK_HOST", "0.0.0.0")


def _extend_tool_path() -> None:
    """Apps started from Finder or the Dock get a bare PATH without Homebrew."""
    if sys.platform != "darwin":
        return
    parts = os.environ.get("PATH", "").split(os.pathsep)
    extra = [d for d in ("/opt/homebrew/bin", "/usr/local/bin") if d not in parts and os.path.isdir(d)]
    if extra:
        os.environ["PATH"] = os.pathsep.join([p for p in parts if p] + extra)


_extend_tool_path()


def app_data_dir() -> Path:
    override = os.environ.get("YTDESK_HOME")
    if override:
        path = Path(override)
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        path = base / "YTDesk"
    elif sys.platform == "darwin" and not (Path.home() / ".ytdesk").exists():
        path = Path.home() / "Library" / "Application Support" / "YTDesk"
    else:
        path = Path.home() / ".ytdesk"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_library_dir() -> Path:
    folder = "Movies" if sys.platform == "darwin" else "Videos"
    videos = Path.home() / folder / "YT Desk"
    try:
        videos.mkdir(parents=True, exist_ok=True)
        return videos
    except OSError:
        fallback = app_data_dir() / "library"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def db_path() -> Path:
    return app_data_dir() / "ytdesk.db"


QUALITY_PRESETS = {
    "audio-opus": "bestaudio/best",
    "audio-m4a": "bestaudio/best",
    "audio-mp3": "bestaudio/best",
    "1080p": "bv*[height<=1080]+ba/b[height<=1080]/b",
    "720p": "bv*[height<=720]+ba/b[height<=720]/b",
    "480p": "bv*[height<=480]+ba/b[height<=480]/b",
    "best": "bv*+ba/b",
}

AUDIO_EXTS = {
    "audio-opus": {".opus", ".ogg"},
    "audio-m4a": {".m4a"},
    "audio-mp3": {".mp3"},
}

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov"}


def file_matches_quality(path: Path, quality: str) -> bool:
    ext = path.suffix.lower()
    if quality in AUDIO_EXTS:
        return ext in AUDIO_EXTS[quality]
    if quality in {"1080p", "720p", "480p", "best"}:
        return ext in VIDEO_EXTS
    return True


DEFAULT_SETTINGS = {
    "library_dir": str(default_library_dir()),
    "quality": "audio-opus",
    "concurrent": "1",
    "cookies_path": "",
    "sponsorblock": "0",
    "sync_token": "",
    "sync_pin": "",
    "sync_enabled": "1",
    "yt_client_id": "",
    "yt_client_secret": "",
}


def new_sync_secrets() -> tuple[str, str]:
    token = secrets.token_urlsafe(24)
    pin = f"{secrets.randbelow(1_000_000):06d}"
    return token, pin


def is_loopback(host: str | None) -> bool:
    if not host:
        return False
    return host in {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"}


def lan_ips() -> list[str]:
    found: list[str] = []

    def add(ip: str) -> None:
        if (
            ip
            and not ip.startswith("127.")
            and ip != "0.0.0.0"
            and not ip.startswith("169.254.")
            and ip not in found
        ):
            found.append(ip)

    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("1.1.1.1", 80))
        add(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            add(info[4][0])
    except OSError:
        pass

    def rank(ip: str) -> int:
        if ip.startswith("192.168."):
            return 0
        if ip.startswith("10."):
            return 1
        if ip.startswith("172."):
            try:
                second = int(ip.split(".")[1])
            except ValueError:
                return 3
            return 2 if 16 <= second <= 31 else 3
        return 4

    found.sort(key=rank)
    return found

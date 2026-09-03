from __future__ import annotations

import base64
import re
import threading
import unicodedata
import urllib.request
from pathlib import Path
from typing import Any

from .events import hub


WIN_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
SPACES = re.compile(r"\s+")
ID_PREFIX = re.compile(r"^([A-Za-z0-9_-]{11}) - (.+)$")
RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

_norm_lock = threading.Lock()
_norm_started = False


def safe_filename(text: str, max_len: int = 120) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = WIN_BAD.sub(" ", text)
    text = SPACES.sub(" ", text).strip(" .")
    if not text:
        text = "audio"
    if text.upper() in RESERVED:
        text = f"_{text}"
    if len(text) > max_len:
        text = text[:max_len].rstrip(" .")
    return text or "audio"


def library_stem(video: dict[str, Any] | None, video_id: str = "") -> str:
    from .match import identity_for

    video = video or {}
    ident = identity_for(video) if video.get("id") or video.get("title") else None
    artist = (ident.artist_display if ident else "") or (video.get("artist") or "").strip()
    title = (ident.track_display if ident else "") or (video.get("track") or video.get("title") or "").strip()
    if artist and title and not title.lower().startswith(artist.lower() + " -"):
        stem = f"{artist} - {title}"
    else:
        stem = title or video_id or "audio"
    return safe_filename(stem)


def youtube_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def unique_path(dest: Path, stem: str, suffix: str, video_id: str) -> Path:
    suffix = suffix if suffix.startswith(".") else f".{suffix}"
    candidate = dest / f"{stem}{suffix}"
    if not candidate.exists():
        return candidate
    if _file_youtube_id(candidate) == video_id:
        return candidate
    n = 2
    while True:
        candidate = dest / f"{stem} ({n}){suffix}"
        if not candidate.exists() or _file_youtube_id(candidate) == video_id:
            return candidate
        n += 1


def _file_youtube_id(path: Path) -> str | None:
    try:
        from mutagen import File

        audio = File(path)
        if not audio or not audio.tags:
            return None
        tags = audio.tags
        for key in ("youtube_id", "YOUTUBE_ID", "YOUTUBEID"):
            if key in tags:
                value = tags[key]
                if isinstance(value, list) and value:
                    return str(value[0])
                return str(value)
        raw = getattr(tags, "get", lambda *_: None)("----:com.apple.iTunes:YouTube ID")
        if raw:
            item = raw[0] if isinstance(raw, list) else raw
            if isinstance(item, bytes):
                return item.decode("utf-8", "ignore")
            return str(item)
    except Exception:
        return None
    match = ID_PREFIX.match(path.name)
    return match.group(1) if match else None


def has_cover(path: Path) -> bool:
    try:
        from mutagen import File

        audio = File(path)
        if not audio:
            return False
        if getattr(audio, "pictures", None):
            return True
        tags = audio.tags or {}
        if "metadata_block_picture" in tags or "METADATA_BLOCK_PICTURE" in tags:
            return True
        if "covr" in tags or "APIC:" in str(list(tags.keys())):
            return True
        for key in tags.keys():
            key_s = str(key)
            if "APIC" in key_s or "covr" in key_s.lower() or "picture" in key_s.lower():
                return True
    except Exception:
        return False
    return False


def fetch_cover(video_id: str, thumbnail_url: str | None = None) -> bytes | None:
    urls: list[str] = []
    if thumbnail_url:
        urls.append(thumbnail_url)
    urls.extend(
        [
            f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
            f"https://i.ytimg.com/vi/{video_id}/sddefault.jpg",
            f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        ]
    )
    seen: set[str] = set()
    for url in urls:
        if not url or url in seen:
            continue
        seen.add(url)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = resp.read()
            if len(data) < 5000:
                continue
            if data[:2] == b"\xff\xd8" or data[:8] == b"\x89PNG\r\n\x1a\n":
                return data
        except Exception:
            continue
    return None


def apply_tags(
    path: Path,
    video_id: str,
    *,
    title: str,
    artist: str = "",
    album: str = "",
    cover: bytes | None = None,
) -> None:
    ext = path.suffix.lower()
    url = youtube_url(video_id)
    if ext in {".opus", ".ogg"}:
        _tag_ogg(path, video_id, title, artist, album, url, cover)
    elif ext in {".m4a", ".mp4", ".m4b"}:
        _tag_mp4(path, video_id, title, artist, album, url, cover)
    elif ext == ".mp3":
        _tag_mp3(path, video_id, title, artist, album, url, cover)


def _picture_b64(cover: bytes, mime: str = "image/jpeg") -> str:
    from mutagen.flac import Picture

    pic = Picture()
    pic.type = 3
    pic.mime = mime
    pic.desc = "Cover"
    pic.data = cover
    return base64.b64encode(pic.write()).decode("ascii")


def _tag_ogg(
    path: Path,
    video_id: str,
    title: str,
    artist: str,
    album: str,
    url: str,
    cover: bytes | None,
) -> None:
    from mutagen import File

    audio = File(path)
    if audio is None:
        raise RuntimeError(f"Could not open {path.name} for tagging.")
    if title:
        audio["title"] = title
    if artist:
        audio["artist"] = artist
        audio["albumartist"] = artist
    if album:
        audio["album"] = album
    audio["youtube_id"] = video_id
    audio["purl"] = url
    if "genre" in audio and str(audio["genre"][0]).lower() in {
        "people & blogs",
        "entertainment",
        "music",
        "film & animation",
    }:
        del audio["genre"]
    if cover and "metadata_block_picture" not in audio:
        mime = "image/png" if cover[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
        audio["metadata_block_picture"] = _picture_b64(cover, mime)
    audio.save()


def _tag_mp4(
    path: Path,
    video_id: str,
    title: str,
    artist: str,
    album: str,
    url: str,
    cover: bytes | None,
) -> None:
    from mutagen.mp4 import MP4, MP4Cover

    audio = MP4(path)
    if title:
        audio["\xa9nam"] = [title]
    if artist:
        audio["\xa9ART"] = [artist]
        audio["aART"] = [artist]
    if album:
        audio["\xa9alb"] = [album]
    audio["\xa9cmt"] = [f"YouTube {video_id}"]
    audio["----:com.apple.iTunes:YouTube ID"] = [video_id.encode("utf-8")]
    if cover and not audio.get("covr"):
        fmt = MP4Cover.FORMAT_PNG if cover[:8] == b"\x89PNG\r\n\x1a\n" else MP4Cover.FORMAT_JPEG
        audio["covr"] = [MP4Cover(cover, fmt)]
    audio.save()


def _tag_mp3(
    path: Path,
    video_id: str,
    title: str,
    artist: str,
    album: str,
    url: str,
    cover: bytes | None,
) -> None:
    from mutagen.id3 import APIC, COMM, ID3, TALB, TIT2, TPE1, TPE2, TXXX, WOAR, ID3NoHeaderError

    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    if title:
        tags.setall("TIT2", [TIT2(encoding=3, text=title)])
    if artist:
        tags.setall("TPE1", [TPE1(encoding=3, text=artist)])
        tags.setall("TPE2", [TPE2(encoding=3, text=artist)])
    if album:
        tags.setall("TALB", [TALB(encoding=3, text=album)])
    tags.setall("TXXX:YouTube ID", [TXXX(encoding=3, desc="YouTube ID", text=video_id)])
    tags.setall("COMM", [COMM(encoding=3, lang="eng", desc="YouTube", text=url)])
    tags.setall("WOAR", [WOAR(url=url)])
    if cover and not any(k.startswith("APIC") for k in tags.keys()):
        mime = "image/png" if cover[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
        tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=cover))
    tags.save(path, v2_version=3)


def display_fields(video: dict[str, Any]) -> tuple[str, str, str]:
    from .match import identity_for

    ident = identity_for(video)
    artist = ident.artist_display or (video.get("artist") or "").strip()
    title = ident.track_display or (video.get("track") or video.get("title") or "").strip()
    album = (video.get("album") or "").strip()
    if not album:
        playlists = video.get("playlists") or []
        if playlists:
            album = (playlists[0].get("title") or "").strip()
    return artist, title, album


def normalize_file(path: Path, video: dict[str, Any], fetch_missing_cover: bool = False) -> Path:
    if not path.exists() or not path.is_file():
        return path
    video_id = str(video.get("id") or "")
    artist, title, album = display_fields(video)
    cover = None
    if fetch_missing_cover and not has_cover(path):
        cover = fetch_cover(video_id, video.get("thumbnail"))
    try:
        apply_tags(path, video_id, title=title or path.stem, artist=artist, album=album, cover=cover)
    except Exception:
        pass
    stem = library_stem(video, video_id)
    dest = unique_path(path.parent, stem, path.suffix, video_id)
    if dest.resolve() == path.resolve():
        return path
    if dest.exists() and dest.resolve() != path.resolve():
        return path
    path.rename(dest)
    return dest


def normalize_library() -> None:
    from .db import db

    try:
        from . import android_usb, usb_sync

        if android_usb.snapshot().get("status") == "running":
            return
        if usb_sync.snapshot().get("status") == "running":
            return
    except Exception:
        pass
    try:
        videos = db.downloaded_videos()
    except Exception:
        return
    for video in videos:
        raw = video.get("filepath") or ""
        path = Path(raw)
        if not path.exists():
            continue
        try:
            new_path = normalize_file(path, video, fetch_missing_cover=False)
        except Exception:
            continue
        if new_path != path and video.get("download_id"):
            try:
                db.update_download(
                    int(video["download_id"]),
                    filepath=str(new_path),
                    filename=new_path.name,
                )
            except Exception:
                pass
    hub.publish("library", {"normalized": True})


def start_library_normalize() -> None:
    global _norm_started
    with _norm_lock:
        if _norm_started:
            return
        _norm_started = True
    threading.Thread(target=normalize_library, daemon=True).start()

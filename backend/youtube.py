from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from yt_dlp import YoutubeDL
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.utils import DownloadError, ExtractorError


YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)


def split_urls(text: str) -> list[str]:
    found: list[str] = []
    for raw in text.replace(",", "\n").splitlines():
        chunk = raw.strip().strip("<>").strip()
        if not chunk:
            continue
        if chunk.startswith("http://") or chunk.startswith("https://"):
            found.append(chunk.split()[0])
            continue
        found.extend(URL_RE.findall(chunk))
    unique: list[str] = []
    for url in found:
        if url not in unique:
            unique.append(url)
    return unique


def playlist_id_from_url(url: str) -> str | None:
    parsed = urlparse(url.strip())
    host = (parsed.netloc or "").lower()
    qs = parse_qs(parsed.query)
    if "list" in qs and qs["list"]:
        value = qs["list"][0]
        if value:
            return value
    parts = [p for p in parsed.path.split("/") if p]
    if "playlist" in parts:
        try:
            idx = parts.index("playlist")
            if idx + 1 < len(parts):
                return parts[idx + 1]
        except ValueError:
            pass
    if host.endswith("youtu.be") and qs.get("list"):
        return qs["list"][0]
    return None


def video_id_from_url(url: str) -> str | None:
    parsed = urlparse(url.strip())
    host = (parsed.netloc or "").lower()
    qs = parse_qs(parsed.query)
    if "v" in qs and qs["v"]:
        return qs["v"][0]
    parts = [p for p in parsed.path.split("/") if p]
    if host.endswith("youtu.be") and parts:
        return parts[0]
    if "shorts" in parts:
        idx = parts.index("shorts")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    if "embed" in parts:
        idx = parts.index("embed")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return None


def _artist_from_entry(entry: dict[str, Any]) -> str:
    artist = entry.get("artist")
    if isinstance(artist, str) and artist.strip():
        return artist.strip()
    artists = entry.get("artists") or entry.get("creators")
    if isinstance(artists, list) and artists:
        first = artists[0]
        if isinstance(first, dict):
            return str(first.get("name") or "").strip()
        if first:
            return str(first).strip()
    creator = entry.get("creator")
    if isinstance(creator, str):
        return creator.strip()
    return ""


def _thumbnail(entry: dict[str, Any], video_id: str | None = None) -> str | None:
    thumb = entry.get("thumbnail")
    if isinstance(thumb, str) and thumb:
        return thumb
    thumbs = entry.get("thumbnails") or []
    if thumbs:
        best = thumbs[-1]
        if isinstance(best, dict) and best.get("url"):
            return best["url"]
        if isinstance(best, str):
            return best
    if video_id:
        return f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"
    return None


def looks_like_playlist_id(value: str | None) -> bool:
    if not value:
        return False
    return value.startswith(("PL", "UU", "FL", "OL", "LL", "RD")) or value in {"WL", "LL"}


def listing_url(url: str) -> str:
    """Prefer a canonical playlist URL so watch?v=…&list=… is not treated as one video."""
    pid = playlist_id_from_url(url)
    if not pid:
        return url.strip()
    host = (urlparse(url.strip()).netloc or "").lower()
    if "music.youtube" in host:
        return f"https://music.youtube.com/playlist?list={pid}"
    return f"https://www.youtube.com/playlist?list={pid}"


def _ydl_base(cookies_path: str | None = None) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "noplaylist": False,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "cachedir": False,
    }
    if cookies_path and Path(cookies_path).exists():
        opts["cookiefile"] = cookies_path
    return opts


def _consume_entries(info: dict[str, Any]) -> dict[str, Any]:
    entries = info.get("entries")
    if entries is None:
        info["entries"] = []
        return info
    if not isinstance(entries, list):
        info["entries"] = [entry for entry in entries if entry]
    else:
        info["entries"] = [entry for entry in entries if entry]
    return info


def fetch_listing(url: str, cookies_path: str | None = None) -> dict[str, Any]:
    opts = _ydl_base(cookies_path)
    target = listing_url(url)
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(target, download=False)
    if not info:
        raise RuntimeError("yt-dlp could not read that URL. It may be private, region-locked, or invalid.")
    info = _consume_entries(info)

    stub = info.get("_type") in {"url", "url_transparent"} or (
        looks_like_playlist_id(info.get("id")) and not info.get("entries")
    )
    if stub:
        follow = listing_url(info.get("url") or info.get("webpage_url") or target)
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(follow, download=False)
        if not info:
            raise RuntimeError("yt-dlp could not expand that playlist.")
        info = _consume_entries(info)
    return info


def normalize_listing(info: dict[str, Any], original_url: str) -> dict[str, Any]:
    entries = info.get("entries") or []
    if not isinstance(entries, list):
        entries = [entry for entry in entries if entry]
    known_playlist = playlist_id_from_url(original_url) or (
        info.get("id") if looks_like_playlist_id(info.get("id")) else None
    )
    # Single video: wrap as a one-item list so it still lives in the library.
    if info.get("_type") not in {"playlist", "multi_video"} and not entries:
        if known_playlist:
            raise RuntimeError(
                "That playlist URL did not return its videos. Refresh, or paste the playlist link (not a single watch URL)."
            )
        video_id = info.get("id") or video_id_from_url(original_url)
        if not video_id or looks_like_playlist_id(video_id):
            raise RuntimeError("Could not find a playlist or video at that URL.")
        entries = [info]
        playlist_id = f"video:{video_id}"
        title = info.get("title") or video_id
        uploader = info.get("uploader") or info.get("channel") or ""
    else:
        playlist_id = info.get("id") or playlist_id_from_url(original_url) or info.get("webpage_url_basename")
        title = info.get("title") or "Untitled playlist"
        uploader = info.get("uploader") or info.get("channel") or info.get("playlist_uploader") or ""
        real_entries = []
        for entry in entries:
            if not entry:
                continue
            real_entries.append(entry)
        entries = real_entries

    videos: list[dict[str, Any]] = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            continue
        vid = entry.get("id")
        if not vid or looks_like_playlist_id(str(vid)):
            continue
        availability = entry.get("availability") or entry.get("live_status")
        if entry.get("ie_key") is None and entry.get("_type") == "url" and not entry.get("title"):
            availability = availability or "unknown"
        videos.append(
            {
                "id": vid,
                "title": entry.get("title") or vid,
                "channel": entry.get("uploader")
                or entry.get("channel")
                or entry.get("playlist_uploader")
                or "",
                "channel_id": entry.get("channel_id") or entry.get("uploader_id"),
                "duration": entry.get("duration"),
                "thumbnail": _thumbnail(entry, vid),
                "url": entry.get("url")
                or entry.get("webpage_url")
                or f"https://www.youtube.com/watch?v={vid}",
                "upload_date": entry.get("upload_date"),
                "description": entry.get("description") or "",
                "availability": availability,
                "track": entry.get("track") or "",
                "artist": _artist_from_entry(entry),
                "album": entry.get("album") or "",
                "position": index,
            }
        )

    return {
        "id": str(playlist_id),
        "url": info.get("webpage_url") or info.get("original_url") or original_url,
        "title": title,
        "uploader": uploader,
        "thumbnail": _thumbnail(info) or (videos[0]["thumbnail"] if videos else None),
        "description": info.get("description") or "",
        "video_count": len(videos),
        "videos": videos,
        "extractor": info.get("extractor_key") or info.get("ie_key"),
    }


class CancelledDownload(Exception):
    pass


class LibraryTagsPP(PostProcessor):
    """Write library artist/title/album onto the info dict before ffmpeg tags."""

    def __init__(self, downloader=None, tags: dict[str, str] | None = None):
        super().__init__(downloader)
        self.tags = {k: v for k, v in (tags or {}).items() if v}

    def run(self, information):
        for key, value in self.tags.items():
            information[key] = value
        # YouTube descriptions are often unrelated; keep the file tags clean.
        information["description"] = ""
        return [], information


def _library_tags(video_id: str) -> dict[str, str]:
    try:
        from .db import db
        from .match import identity_for
    except Exception:
        return {}
    video = db.get_video(video_id)
    if not video:
        return {}
    ident = identity_for(video)
    album = (video.get("album") or "").strip()
    if not album:
        playlists = video.get("playlists") or []
        if playlists:
            album = (playlists[0].get("title") or "").strip()
    artist = ident.artist_display or (video.get("artist") or "").strip()
    title = ident.track_display or (video.get("track") or "").strip()
    tags: dict[str, str] = {}
    if artist:
        tags["artist"] = artist
        tags["album_artist"] = artist
    if title:
        # FFmpegMetadata maps `track` → TITLE, `track_number` → track no.
        tags["track"] = title
        tags["title"] = title
    if album:
        tags["album"] = album
    upload = video.get("upload_date") or ""
    if len(upload) >= 4:
        tags["upload_date"] = upload
    tags["webpage_url"] = f"https://www.youtube.com/watch?v={video_id}"
    return tags


def _postprocessor_phase(name: str, quality: str) -> str:
    key = (name or "").lower()
    codec = "Opus" if quality == "audio-opus" else "audio"
    if "extractaudio" in key or "ffmpegextractaudio" in key:
        return f"Converting to {codec}"
    if "thumbnail" in key and "embed" in key:
        return "Embedding cover art"
    if "thumbnail" in key:
        return "Preparing cover art"
    if "metadata" in key or "librarytags" in key:
        return "Writing tags"
    if "merger" in key:
        return "Merging video and audio"
    if "sponsor" in key or "chapter" in key:
        return "Removing sponsor segments"
    if "movefile" in key:
        return "Saving file"
    if name:
        return f"Processing · {name}"
    return "Processing file"


def _audio_postprocessors(codec: str) -> list[dict[str, Any]]:
    preferred = "192" if codec == "mp3" else "0"
    return [
        {"key": "FFmpegExtractAudio", "preferredcodec": codec, "preferredquality": preferred},
        {"key": "FFmpegMetadata", "add_metadata": True, "add_chapters": True},
        {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"},
        {"key": "EmbedThumbnail", "already_have_thumbnail": False},
    ]


def _cleanup_sidecars(dest_dir: Path, video_id: str, keep: Path, stem: str | None = None) -> None:
    junk = {".part", ".ytdl", ".temp", ".jpg", ".webp", ".png", ".json"}
    media = {".mp4", ".mkv", ".webm", ".mov", ".m4a", ".mp3", ".opus", ".ogg"}
    keep_resolved = keep.resolve() if keep.exists() else keep
    patterns = [f"{video_id} - *"]
    if stem:
        patterns.append(f"{stem}.*")
    seen: set[Path] = set()
    for pattern in patterns:
        for path in dest_dir.glob(pattern):
            if path in seen:
                continue
            seen.add(path)
            try:
                if path.resolve() == keep_resolved:
                    continue
            except OSError:
                continue
            ext = path.suffix.lower()
            if ext in junk or ext in media or path.name.endswith(".part"):
                try:
                    path.unlink()
                except OSError:
                    pass


def _prefer_output(
    dest_dir: Path,
    video_id: str,
    quality: str,
    reported: str | None,
    stem: str | None = None,
) -> Path | None:
    from .config import AUDIO_EXTS, VIDEO_EXTS

    skip = {".part", ".ytdl", ".temp", ".jpg", ".webp", ".png", ".json"}

    def usable(path: Path) -> bool:
        return path.is_file() and path.suffix.lower() not in skip and not path.name.endswith(".part")

    candidates: list[Path] = []
    if reported:
        reported_path = Path(reported)
        if reported_path.exists() and usable(reported_path):
            candidates.append(reported_path)
        parent = reported_path.parent if reported_path.parent.exists() else dest_dir
        for path in parent.glob(f"{reported_path.stem}.*"):
            if usable(path):
                candidates.append(path)
    if stem:
        for path in dest_dir.glob(f"{stem}.*"):
            if usable(path):
                candidates.append(path)
    for path in dest_dir.glob(f"{video_id} - *"):
        if usable(path):
            candidates.append(path)

    unique: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        try:
            key = path.resolve()
        except OSError:
            key = path
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)

    expected = AUDIO_EXTS.get(quality)
    if expected:
        preferred = [p for p in unique if p.suffix.lower() in expected]
        if preferred:
            return preferred[-1]
    if unique:
        if quality not in AUDIO_EXTS:
            video = [p for p in unique if p.suffix.lower() in VIDEO_EXTS]
            if video:
                return video[-1]
        return unique[-1]
    return None


def download_video(
    video_id: str,
    dest_dir: Path,
    quality: str,
    cookies_path: str | None,
    sponsorblock: bool,
    progress_cb,
    should_cancel,
) -> dict[str, Any]:
    from .config import QUALITY_PRESETS

    dest_dir.mkdir(parents=True, exist_ok=True)
    fmt = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["audio-opus"])
    from .db import db
    from . import media_tags

    video = db.get_video(video_id) or {"id": video_id, "title": video_id}
    stem = media_tags.library_stem(video, video_id)
    outtmpl = str(dest_dir / f"{stem}.%(ext)s")
    audio = quality.startswith("audio")

    def _speed_text(raw: Any) -> str:
        if isinstance(raw, (int, float)):
            if raw >= 1024 * 1024:
                return f"{raw / 1024 / 1024:.1f} MB/s"
            if raw >= 1024:
                return f"{raw / 1024:.0f} KB/s"
            return f"{raw:.0f} B/s"
        text = str(raw or "").strip()
        return text.replace("iB", "B") if text else ""

    def hook(d: dict[str, Any]) -> None:
        if should_cancel():
            raise CancelledDownload("Cancelled")
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            pct = float(downloaded) / float(total) * 100 if total else 0.0
            fragment = d.get("fragment_index")
            fragment_count = d.get("fragment_count")
            if audio:
                phase = "Downloading audio"
            else:
                phase = "Downloading video"
            if fragment and fragment_count:
                phase = f"{phase} · piece {fragment}/{fragment_count}"
            progress_cb(
                {
                    "status": "downloading",
                    "progress": round(min(pct, 99.5), 1),
                    "speed": _speed_text(d.get("_speed_str") or d.get("speed")),
                    "eta": d.get("eta"),
                    "filename": d.get("filename"),
                    "phase": phase,
                    "downloaded_bytes": int(downloaded) if downloaded else 0,
                    "total_bytes": int(total) if total else 0,
                }
            )
        elif status == "finished":
            progress_cb(
                {
                    "status": "processing",
                    "progress": 99.5,
                    "speed": "",
                    "eta": 0,
                    "filename": d.get("filename"),
                    "phase": "Download finished · converting",
                }
            )

    def post_hook(d: dict[str, Any]) -> None:
        if should_cancel():
            raise CancelledDownload("Cancelled")
        name = str(d.get("postprocessor") or "")
        phase = _postprocessor_phase(name, quality)
        if d.get("status") in {"started", "processing"}:
            progress_cb(
                {
                    "status": "processing",
                    "progress": 99.5,
                    "speed": "",
                    "eta": 0,
                    "phase": phase,
                }
            )

    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": False,
        "noplaylist": True,
        "format": fmt,
        "outtmpl": outtmpl,
        "windowsfilenames": True,
        "progress_hooks": [hook],
        "postprocessor_hooks": [post_hook],
        "noprogress": True,
        "concurrent_fragment_downloads": 4,
        "overwrites": True if audio else False,
        "retries": 3,
        "fragment_retries": 10,
        "keepvideo": False,
    }
    if not audio:
        opts["merge_output_format"] = "mp4"
    if cookies_path and Path(cookies_path).exists():
        opts["cookiefile"] = cookies_path

    post: list[dict[str, Any]] = []
    if sponsorblock:
        post.extend(
            [
                {"key": "SponsorBlock", "categories": ["sponsor", "selfpromo", "interaction"]},
                {
                    "key": "ModifyChapters",
                    "remove_sponsor_segments": ["sponsor", "selfpromo", "interaction"],
                },
            ]
        )
    if quality == "audio-opus":
        opts["writethumbnail"] = True
        post.extend(_audio_postprocessors("opus"))
    elif quality == "audio-m4a":
        opts["writethumbnail"] = True
        post.extend(_audio_postprocessors("m4a"))
    elif quality == "audio-mp3":
        opts["writethumbnail"] = True
        post.extend(_audio_postprocessors("mp3"))
    if post:
        opts["postprocessors"] = post

    tags = _library_tags(video_id) if audio else {}
    url = f"https://www.youtube.com/watch?v={video_id}"
    progress_cb(
        {
            "status": "downloading",
            "progress": 0,
            "speed": "",
            "eta": None,
            "phase": "Connecting to YouTube",
        }
    )
    try:
        with YoutubeDL(opts) as ydl:
            if tags:
                ydl.add_post_processor(LibraryTagsPP(ydl, tags), when="before_dl")
            info = ydl.extract_info(url, download=True)
    except (DownloadError, ExtractorError) as exc:
        raise RuntimeError(str(exc)) from exc

    if not info:
        raise RuntimeError("Download produced no metadata.")

    requested = info.get("requested_downloads") or []
    reported = None
    if requested and requested[0].get("filepath"):
        reported = requested[0]["filepath"]
    elif info.get("filepath"):
        reported = info["filepath"]
    elif info.get("_filename"):
        reported = info["_filename"]

    path = _prefer_output(dest_dir, video_id, quality, reported, stem)
    if not path:
        raise RuntimeError("Download finished but the file could not be located.")

    artist, song_title, album = media_tags.display_fields(video)
    if not song_title:
        song_title = str(info.get("track") or info.get("title") or stem)
    tagged = {**video, "title": song_title, "artist": artist, "album": album, "id": video_id}
    path = media_tags.normalize_file(path, tagged, fetch_missing_cover=audio)

    _cleanup_sidecars(dest_dir, video_id, path, stem)

    return {
        "filepath": str(path),
        "filename": path.name,
        "filesize": path.stat().st_size if path.exists() else 0,
        "title": song_title or info.get("title") or video_id,
        "ext": path.suffix.lstrip("."),
    }

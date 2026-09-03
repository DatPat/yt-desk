from __future__ import annotations

import asyncio
import hmac
import os
import shutil
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from . import youtube
from .config import APP_NAME, PORT, QUALITY_PRESETS, app_data_dir, is_loopback
from .db import db
from .downloads import manager
from .events import hub
from .sync import media_path, rotate_secrets, sync_payload, token_ok


ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "frontend" / "dist"


class LanGuard(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        client = request.client.host if request.client else ""
        path = request.url.path
        if is_loopback(client):
            return await call_next(request)
        if path in {"/", "/index.html"}:
            qs = request.url.query
            return RedirectResponse("/m" + (f"?{qs}" if qs else ""), status_code=302)

        public_prefixes = (
            "/m",
            "/api/mobile",
            "/media",
            "/dav",
            "/assets",
            "/favicon",
            "/manifest",
        )
        public_exact = {"/", "/index.html", "/favicon.svg", "/manifest.webmanifest"}
        allowed = path in public_exact or any(path.startswith(p) for p in public_prefixes)
        # Built SPA files (js/css/svg) must load on phones.
        if path.startswith("/assets") or Path(path).suffix in {
            ".js",
            ".css",
            ".svg",
            ".png",
            ".woff2",
            ".ico",
            ".webmanifest",
        }:
            allowed = True
        if not allowed:
            return JSONResponse({"error": "This endpoint is only available on the PC."}, status_code=403)

        if path.startswith("/api/mobile") or path.startswith("/media"):
            if path.rstrip("/").endswith("/pair"):
                return await call_next(request)
            token = request.headers.get("X-YTDesk-Token") or request.query_params.get("t") or ""
            if not token_ok(token):
                return JSONResponse({"error": "Invalid or missing sync token."}, status_code=401)
        return await call_next(request)


@asynccontextmanager
async def lifespan(_application: FastAPI):
    hub.bind(asyncio.get_running_loop())
    manager.start()
    Path(db.get_setting("library_dir")).mkdir(parents=True, exist_ok=True)
    from .media_tags import start_library_normalize

    start_library_normalize()
    yield


def create_app() -> FastAPI:
    application = FastAPI(title=APP_NAME, lifespan=lifespan)
    application.add_middleware(LanGuard)

    @application.get("/api/health")
    def health() -> dict[str, Any]:
        yt_ver = None
        try:
            from yt_dlp.version import __version__ as yv

            yt_ver = yv
        except Exception:
            yt_ver = None
        ffmpeg = shutil.which("ffmpeg")
        return {
            "ok": True,
            "app": APP_NAME,
            "yt_dlp": yt_ver,
            "ffmpeg": bool(ffmpeg),
            "ffmpeg_path": ffmpeg,
            "data_dir": str(app_data_dir()),
            "port": PORT,
            "platform": sys.platform,
            "is_mac": sys.platform == "darwin",
            "is_windows": sys.platform == "win32",
        }

    @application.get("/api/stats")
    def stats() -> dict[str, Any]:
        return db.stats()

    @application.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        data = db.settings()
        return {
            **data,
            "qualities": list(QUALITY_PRESETS.keys()),
            "library_exists": Path(data.get("library_dir") or "").exists(),
        }

    @application.put("/api/settings")
    async def put_settings(request: Request) -> dict[str, Any]:
        body = await request.json()
        allowed = {
            "library_dir",
            "quality",
            "concurrent",
            "cookies_path",
            "sponsorblock",
            "sync_enabled",
            "yt_client_id",
            "yt_client_secret",
        }
        patch = {k: str(v) for k, v in body.items() if k in allowed}
        if "library_dir" in patch:
            path = Path(patch["library_dir"]).expanduser()
            path.mkdir(parents=True, exist_ok=True)
            patch["library_dir"] = str(path)
        if "quality" in patch and patch["quality"] not in QUALITY_PRESETS:
            raise HTTPException(400, "Unknown quality preset")
        if "concurrent" in patch:
            try:
                n = int(patch["concurrent"])
            except ValueError as exc:
                raise HTTPException(400, "concurrent must be 1-3") from exc
            patch["concurrent"] = str(max(1, min(3, n)))
        updated = db.set_settings(patch)
        manager.kick()
        return {**updated, "qualities": list(QUALITY_PRESETS.keys())}

    @application.post("/api/settings/pick-folder")
    def pick_folder() -> dict[str, str]:
        path = _pick_folder()
        if not path:
            return {"path": db.get_setting("library_dir")}
        Path(path).mkdir(parents=True, exist_ok=True)
        db.set_settings({"library_dir": path})
        return {"path": path}

    @application.post("/api/settings/open-folder")
    def open_folder() -> dict[str, bool]:
        path = Path(db.get_setting("library_dir"))
        path.mkdir(parents=True, exist_ok=True)
        _open_path(path)
        return {"ok": True}

    @application.get("/api/youtube/status")
    def youtube_status() -> dict[str, Any]:
        from . import youtube_account as yta

        return yta.status()

    @application.post("/api/youtube/connect")
    def youtube_connect() -> dict[str, str]:
        from . import youtube_account as yta

        try:
            url = yta.auth_url()
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc
        import webbrowser

        webbrowser.open(url)
        return {"url": url}

    @application.get("/api/youtube/callback")
    def youtube_callback(code: str = "", state: str = "", error: str = ""):
        from . import youtube_account as yta

        if error:
            return RedirectResponse(f"/?youtube=error&reason={error}")
        try:
            yta.finish_auth(code, state)
        except Exception:
            return RedirectResponse("/?youtube=error")
        return RedirectResponse("/?youtube=connected")

    @application.post("/api/youtube/disconnect")
    def youtube_disconnect() -> dict[str, bool]:
        from . import youtube_account as yta

        yta.disconnect()
        return {"ok": True}

    @application.get("/api/youtube/mine")
    def youtube_mine() -> dict[str, Any]:
        from . import youtube_account as yta

        try:
            return {"playlists": yta.list_owned_playlists()}
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @application.post("/api/youtube/preview")
    async def youtube_preview(request: Request) -> dict[str, Any]:
        from . import youtube_account as yta

        body = await request.json()
        try:
            return yta.preview(body.get("playlist_ids") or [], body.get("mode") or "song")
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @application.post("/api/youtube/merge")
    async def youtube_merge(request: Request) -> dict[str, Any]:
        from . import youtube_account as yta

        body = await request.json()
        try:
            return yta.start_merge(
                body.get("playlist_ids") or [],
                body.get("mode") or "song",
                title=str(body.get("title") or "Merged playlist"),
                privacy=str(body.get("privacy") or "private"),
                destination_id=body.get("destination_id") or None,
                add_to_library=bool(body.get("add_to_library", True)),
            )
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @application.post("/api/youtube/clean")
    async def youtube_clean(request: Request) -> dict[str, Any]:
        from . import youtube_account as yta

        body = await request.json()
        try:
            return yta.start_clean(str(body.get("playlist_id") or ""), body.get("mode") or "song")
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @application.post("/api/youtube/cancel")
    def youtube_cancel() -> dict[str, Any]:
        from . import youtube_account as yta

        return yta.cancel()

    @application.get("/api/youtube/job")
    def youtube_job() -> dict[str, Any]:
        from . import youtube_account as yta

        return yta.snapshot()

    @application.get("/api/playlists")
    def list_playlists() -> list[dict[str, Any]]:
        return db.list_playlists()

    @application.post("/api/playlists")
    async def add_playlists(request: Request) -> dict[str, Any]:
        body = await request.json()
        text = body.get("url") or body.get("urls") or ""
        if isinstance(text, list):
            urls = [str(u).strip() for u in text if str(u).strip()]
        else:
            urls = youtube.split_urls(str(text))
        if not urls:
            raise HTTPException(400, "Paste one or more YouTube playlist URLs.")
        accepted = []
        for url in urls:
            placeholder_id = youtube.playlist_id_from_url(url) or youtube.video_id_from_url(url)
            if placeholder_id and not youtube.playlist_id_from_url(url) and youtube.video_id_from_url(url):
                placeholder_id = f"video:{placeholder_id}"
            if not placeholder_id:
                placeholder_id = f"pending:{abs(hash(url))}"
            existing = db.get_playlist(placeholder_id)
            playlist = db.upsert_playlist(
                {
                    "id": placeholder_id,
                    "url": url,
                    "title": (existing or {}).get("title") or "Fetching…",
                    "status": "fetching",
                    "error": None,
                    "video_count": (existing or {}).get("video_count") or 0,
                    "thumbnail": (existing or {}).get("thumbnail"),
                    "uploader": (existing or {}).get("uploader") or "",
                }
            )
            threading.Thread(target=_fetch_playlist, args=(url, placeholder_id), daemon=True).start()
            accepted.append(playlist)
        return {"playlists": accepted}

    @application.post("/api/playlists/refresh-all")
    def refresh_all_playlists() -> dict[str, Any]:
        playlists = db.list_playlists()
        if not playlists:
            raise HTTPException(400, "No playlists to refetch.")
        for playlist in playlists:
            db.set_playlist_status(playlist["id"], "fetching")
        hub.publish("playlists", db.list_playlists())
        threading.Thread(target=_refresh_all_playlists, args=(playlists,), daemon=True).start()
        return {"ok": True, "count": len(playlists)}

    @application.post("/api/playlists/compare")
    async def compare_playlists(request: Request) -> dict[str, Any]:
        body = await request.json()
        left_id = str(body.get("left") or body.get("a") or "")
        right_id = str(body.get("right") or body.get("b") or "")
        mode = str(body.get("mode") or "song")
        left_pl = db.get_playlist(left_id)
        right_pl = db.get_playlist(right_id)
        if not left_pl or not right_pl:
            raise HTTPException(404, "Pick two playlists that are already in YT Desk.")
        if left_id == right_id:
            raise HTTPException(400, "Choose two different playlists.")
        from .match import compare_playlists as compare

        result = compare(db.playlist_videos(left_id), db.playlist_videos(right_id), mode=mode)
        return {
            "left": {"id": left_pl["id"], "title": left_pl["title"], "count": left_pl["video_count"]},
            "right": {"id": right_pl["id"], "title": right_pl["title"], "count": right_pl["video_count"]},
            "mode": mode if mode in {"video", "song"} else "song",
            "counts": result["counts"],
            "only_left": [_slim_video(v) for v in result["only_left"]],
            "only_right": [_slim_video(v) for v in result["only_right"]],
            "both": [
                {"key": row["key"], "left": _slim_video(row["left"]), "right": _slim_video(row["right"])}
                for row in result["both"]
            ],
        }

    @application.get("/api/playlists/{playlist_id}")
    def get_playlist(playlist_id: str) -> dict[str, Any]:
        playlist = db.get_playlist(playlist_id)
        if not playlist:
            raise HTTPException(404, "Playlist not found")
        playlist["videos"] = db.playlist_videos(playlist_id)
        return playlist

    @application.delete("/api/playlists/{playlist_id}")
    def delete_playlist(playlist_id: str) -> dict[str, bool]:
        db.delete_playlist(playlist_id)
        hub.publish("playlists", db.list_playlists())
        return {"ok": True}

    @application.post("/api/playlists/{playlist_id}/refresh")
    def refresh_playlist(playlist_id: str) -> dict[str, Any]:
        playlist = db.get_playlist(playlist_id)
        if not playlist:
            raise HTTPException(404, "Playlist not found")
        db.set_playlist_status(playlist_id, "fetching")
        hub.publish("playlist", db.get_playlist(playlist_id))
        threading.Thread(target=_fetch_playlist, args=(playlist["url"], playlist_id), daemon=True).start()
        return db.get_playlist(playlist_id)  # type: ignore[return-value]

    @application.get("/api/videos")
    def all_videos(q: str = "") -> list[dict[str, Any]]:
        return db.all_videos(q)

    @application.get("/api/videos/{video_id}")
    def get_video(video_id: str) -> dict[str, Any]:
        video = db.get_video(video_id)
        if not video:
            raise HTTPException(404, "Video not found")
        return video

    @application.get("/api/duplicates")
    def duplicates() -> dict[str, Any]:
        return db.duplicates()

    @application.get("/api/downloads")
    def list_downloads() -> list[dict[str, Any]]:
        return db.list_downloads()

    @application.post("/api/downloads")
    async def start_downloads(request: Request) -> dict[str, Any]:
        body = await request.json()
        video_ids = body.get("video_ids") or []
        playlist_id = body.get("playlist_id")
        quality = body.get("quality")
        unique_only = bool(body.get("unique_only") or body.get("unique_songs"))
        records: list[dict[str, Any]] = []
        if playlist_id:
            records = db.playlist_videos(playlist_id)
            video_ids = [v["id"] for v in records]
        if unique_only:
            from .match import unique_song_ids

            if not records and video_ids:
                records = [row for vid in video_ids if (row := db.get_video(vid))]
            if records:
                video_ids = unique_song_ids(records)
        if not video_ids:
            raise HTTPException(400, "No videos to download")
        jobs = manager.enqueue(list(dict.fromkeys(video_ids)), quality)
        return {"jobs": jobs}

    @application.post("/api/downloads/{download_id}/cancel")
    def cancel_download(download_id: int) -> dict[str, Any]:
        job = manager.cancel(download_id)
        if not job:
            raise HTTPException(404, "Download not found")
        return job

    @application.post("/api/downloads/{download_id}/retry")
    def retry_download(download_id: int) -> dict[str, Any]:
        job = manager.retry(download_id)
        if not job:
            raise HTTPException(404, "Download not found")
        return job

    @application.post("/api/downloads/{download_id}/reveal")
    def reveal_download(download_id: int) -> dict[str, bool]:
        job = db.get_download(download_id)
        if not job or not job.get("filepath"):
            raise HTTPException(404, "File not found")
        path = Path(job["filepath"])
        if not path.exists():
            raise HTTPException(404, "File missing from disk")
        _reveal(path)
        return {"ok": True}

    @application.get("/api/events")
    async def events():
        async def gen():
            async for item in hub.stream():
                yield f"event: {item['event']}\ndata: {item['data']}\n\n"

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @application.post("/api/mobile/pair")
    async def mobile_pair(request: Request) -> dict[str, str]:
        body = await request.json()
        pin = str(body.get("pin") or "").strip()
        expected = db.get_setting("sync_pin")
        if not expected or not pin or len(pin) != len(expected) or not hmac.compare_digest(pin, expected):
            raise HTTPException(401, "Wrong PIN")
        return {"token": db.get_setting("sync_token")}

    @application.get("/api/usb")
    def usb_status() -> dict[str, Any]:
        from . import usb_sync

        return usb_sync.list_devices()

    @application.post("/api/usb/sync")
    async def usb_start(request: Request) -> dict[str, Any]:
        from . import usb_sync

        body = await request.json()
        try:
            return usb_sync.start_sync(body.get("udid"), body.get("app") or "vlc")
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @application.post("/api/usb/cancel")
    def usb_cancel() -> dict[str, Any]:
        from . import usb_sync

        return usb_sync.cancel()

    @application.get("/api/android")
    def android_status() -> dict[str, Any]:
        from . import android_usb

        return android_usb.list_devices()

    @application.post("/api/android/sync")
    async def android_start(request: Request) -> dict[str, Any]:
        from . import android_usb

        body = await request.json()
        try:
            return android_usb.start_sync(body.get("id"), body.get("kind"))
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @application.post("/api/android/cancel")
    def android_cancel() -> dict[str, Any]:
        from . import android_usb

        return android_usb.cancel()

    @application.get("/api/sync")
    def get_sync() -> dict[str, Any]:
        return sync_payload()

    @application.post("/api/sync/rotate")
    def rotate_sync() -> dict[str, Any]:
        rotate_secrets()
        return sync_payload()

    @application.get("/api/mobile/library")
    def mobile_library() -> dict[str, Any]:
        videos = db.downloaded_videos()
        playlists = db.list_playlists()
        grouped = []
        by_pl: dict[str, list] = {p["id"]: [] for p in playlists}
        loose: list = []
        for video in videos:
            membership = video.get("playlists") or []
            if not membership:
                loose.append(video)
            for pl in membership:
                by_pl.setdefault(pl["id"], []).append(video)
        for playlist in playlists:
            items = by_pl.get(playlist["id"]) or []
            if items:
                grouped.append({**playlist, "videos": items, "downloaded_count": len(items)})
        return {
            "playlists": grouped,
            "loose": loose,
            "videos": videos,
            "count": len(videos),
        }

    @application.get("/api/mobile/playlist.m3u")
    def mobile_m3u(
        request: Request,
        playlist_id: str | None = None,
        t: str = Query(""),
    ):
        videos = db.downloaded_videos()
        if playlist_id:
            videos = [v for v in videos if any(p["id"] == playlist_id for p in v.get("playlists") or [])]
        host = request.headers.get("host") or f"127.0.0.1:{PORT}"
        scheme = request.url.scheme
        lines = ["#EXTM3U"]
        for video in videos:
            duration = int(video.get("duration") or -1)
            title = (video.get("title") or video["id"]).replace("\n", " ")
            lines.append(f"#EXTINF:{duration},{title}")
            lines.append(f"{scheme}://{host}/media/{video['id']}?t={t}")
        return PlainTextResponse("\n".join(lines) + "\n", media_type="audio/x-mpegurl")

    @application.get("/media/{video_id}")
    def stream_media(video_id: str, t: str = Query("")):
        path = media_path(video_id)
        if not path:
            raise HTTPException(404, "Not downloaded")
        suffix = path.suffix.lower()
        mime = {
            ".mp4": "video/mp4",
            ".webm": "video/webm",
            ".mkv": "video/x-matroska",
            ".m4a": "audio/mp4",
            ".mp3": "audio/mpeg",
            ".opus": "audio/ogg",
            ".ogg": "audio/ogg",
            ".oga": "audio/ogg",
        }.get(suffix, "application/octet-stream")
        return FileResponse(
            path,
            media_type=mime,
            filename=path.name,
            content_disposition_type="inline",
        )

    try:
        from a2wsgi import WSGIMiddleware

        from .webdav import build_dav_app

        dav = build_dav_app()
        if dav is not None:
            application.mount("/dav", WSGIMiddleware(dav))
    except Exception:
        pass

    if DIST.exists():
        application.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

        @application.get("/favicon.svg")
        def favicon():
            path = DIST / "favicon.svg"
            if path.exists():
                return FileResponse(path)
            raise HTTPException(404)

        @application.get("/manifest.webmanifest")
        def manifest():
            path = DIST / "manifest.webmanifest"
            if path.exists():
                return FileResponse(path, media_type="application/manifest+json")
            raise HTTPException(404)

        @application.get("/m")
        @application.get("/m/{rest:path}")
        def mobile_spa(rest: str = ""):
            return FileResponse(DIST / "index.html")

        @application.get("/")
        def root():
            return FileResponse(DIST / "index.html")

        @application.get("/{rest:path}")
        def spa(rest: str):
            candidate = DIST / rest
            if candidate.exists() and candidate.is_file():
                return FileResponse(candidate)
            if rest.startswith("api/") or rest.startswith("media/") or rest.startswith("dav"):
                raise HTTPException(404)
            return FileResponse(DIST / "index.html")

    return application


def _slim_video(video: dict[str, Any]) -> dict[str, Any]:
    download = video.get("download")
    return {
        "id": video.get("id"),
        "title": video.get("title"),
        "channel": video.get("channel"),
        "duration": video.get("duration"),
        "thumbnail": video.get("thumbnail"),
        "url": video.get("url"),
        "download": download,
        "song_artist": video.get("song_artist") or "",
        "song_title": video.get("song_title") or "",
    }


def _fetch_playlist(url: str, placeholder_id: str) -> None:
    try:
        cookies = db.get_setting("cookies_path") or None
        info = youtube.fetch_listing(url, cookies)
        listing = youtube.normalize_listing(info, url)
        real_id = listing["id"]
        if real_id != placeholder_id:
            existing = db.get_playlist(placeholder_id)
            db.delete_playlist(placeholder_id)
            listing["added_at"] = (existing or {}).get("added_at")
        db.upsert_playlist({**listing, "status": "idle", "error": None})
        db.replace_playlist_videos(real_id, listing["videos"])
        hub.publish("playlist", db.get_playlist(real_id))
        hub.publish("playlists", db.list_playlists())
        hub.publish("duplicates", db.duplicates())
    except Exception as exc:
        current = db.get_playlist(placeholder_id) or {"id": placeholder_id, "url": url}
        title = current.get("title") or url
        if title.startswith("Fetching"):
            title = url
        db.upsert_playlist({**current, "title": title, "status": "error", "error": str(exc)})
        hub.publish("playlist", db.get_playlist(placeholder_id))
        hub.publish("playlists", db.list_playlists())


def _refresh_all_playlists(playlists: list[dict]) -> None:
    import time

    for playlist in playlists:
        _fetch_playlist(playlist["url"], playlist["id"])
        time.sleep(0.4)


def _pick_folder() -> str:
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        chosen = filedialog.askdirectory(title="YT Desk library folder")
        root.destroy()
        return chosen or ""
    except Exception:
        return ""


def _open_path(path: Path) -> None:
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _reveal(path: Path) -> None:
    if os.name == "nt":
        subprocess.Popen(["explorer", "/select,", str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        _open_path(path.parent)


app = create_app()

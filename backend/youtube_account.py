from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any
from .config import PORT, app_data_dir
from .db import db
from .events import hub
from .match import YT_VIDEO_ID, dedupe_ordered


SCOPES = ["https://www.googleapis.com/auth/youtube"]
TOKEN_PATH = app_data_dir() / "youtube_oauth.json"

_flow_lock = threading.Lock()
_pending: dict[str, Any] = {}
_job_lock = threading.Lock()
_job: dict[str, Any] = {
    "status": "idle",
    "action": None,
    "added": 0,
    "skipped": 0,
    "failed": 0,
    "total": 0,
    "message": "",
    "playlist_id": None,
    "playlist_url": None,
    "error": None,
}
_cancel = threading.Event()


def redirect_uri() -> str:
    return f"http://127.0.0.1:{PORT}/api/youtube/callback"


def token_path() -> Path:
    return TOKEN_PATH


def _client_config(client_id: str, client_secret: str) -> dict[str, Any]:
    uri = redirect_uri()
    body = {
        "client_id": client_id,
        "client_secret": client_secret,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [uri, f"http://localhost:{PORT}/api/youtube/callback"],
    }
    return {"installed": body, "web": body}


def status() -> dict[str, Any]:
    client_id = db.get_setting("yt_client_id")
    connected = False
    channel = None
    channel_id = None
    error = None
    if TOKEN_PATH.exists() and client_id:
        try:
            yt = _service()
            resp = yt.channels().list(part="snippet", mine=True).execute()
            items = resp.get("items") or []
            if items:
                connected = True
                channel = items[0]["snippet"]["title"]
                channel_id = items[0]["id"]
        except Exception as exc:
            error = str(exc)
            connected = False
    return {
        "configured": bool(client_id and db.get_setting("yt_client_secret")),
        "connected": connected,
        "channel": channel,
        "channel_id": channel_id,
        "redirect_uri": redirect_uri(),
        "error": error,
        "job": snapshot(),
    }


def snapshot() -> dict[str, Any]:
    with _job_lock:
        return dict(_job)


def _set_job(**fields: Any) -> dict[str, Any]:
    with _job_lock:
        _job.update(fields)
        data = dict(_job)
    hub.publish("youtube", data)
    return data


def auth_url() -> str:
    from google_auth_oauthlib.flow import Flow

    client_id = db.get_setting("yt_client_id").strip()
    client_secret = db.get_setting("yt_client_secret").strip()
    if not client_id or not client_secret:
        raise RuntimeError("Add a Google OAuth client ID and secret in Settings first.")
    flow = Flow.from_client_config(
        _client_config(client_id, client_secret),
        scopes=SCOPES,
        redirect_uri=redirect_uri(),
    )
    url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    with _flow_lock:
        _pending["state"] = state
        _pending["flow"] = flow
    return url


def finish_auth(code: str, state: str | None) -> None:
    with _flow_lock:
        flow = _pending.get("flow")
        expected = _pending.get("state")
    if not flow:
        raise RuntimeError("No sign-in is in progress. Start Connect YouTube again.")
    if expected and state and expected != state:
        raise RuntimeError("OAuth state mismatch. Start Connect YouTube again.")
    flow.fetch_token(code=code)
    creds = flow.credentials
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    with _flow_lock:
        _pending.clear()


def disconnect() -> None:
    if TOKEN_PATH.exists():
        TOKEN_PATH.unlink()


def _credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not TOKEN_PATH.exists():
        raise RuntimeError("YouTube is not connected.")
    data = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    creds = Credentials.from_authorized_user_info(data, SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
        else:
            raise RuntimeError("YouTube login expired. Connect again.")
    return creds


def _service():
    from googleapiclient.discovery import build

    return build("youtube", "v3", credentials=_credentials(), cache_discovery=False)


def list_owned_playlists() -> list[dict[str, Any]]:
    yt = _service()
    out: list[dict[str, Any]] = []
    token = None
    while True:
        resp = yt.playlists().list(
            part="snippet,contentDetails",
            mine=True,
            maxResults=50,
            pageToken=token,
        ).execute()
        for item in resp.get("items") or []:
            out.append(
                {
                    "id": item["id"],
                    "title": item["snippet"]["title"],
                    "privacy": (item.get("status") or {}).get("privacyStatus"),
                    "item_count": (item.get("contentDetails") or {}).get("itemCount"),
                }
            )
        token = resp.get("nextPageToken")
        if not token:
            break
    return out


def collect_source_videos(playlist_ids: list[str]) -> list[dict[str, Any]]:
    videos: list[dict[str, Any]] = []
    for pid in playlist_ids:
        videos.extend(db.playlist_videos(pid))
    return videos


def _unique_queue(playlist_ids: list[str], mode: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    source = collect_source_videos(playlist_ids)
    kept, dropped = dedupe_ordered(source, mode=mode)
    valid: list[dict[str, Any]] = []
    for video in kept:
        if YT_VIDEO_ID.match(str(video.get("id") or "")):
            valid.append(video)
        else:
            dropped.append({**video, "drop_reason": "not a YouTube video"})
    return source, valid, dropped


def preview(playlist_ids: list[str], mode: str) -> dict[str, Any]:
    if not playlist_ids:
        raise RuntimeError("Pick at least one playlist to merge.")
    if mode not in {"video", "song"}:
        mode = "song"
    source, kept, dropped = _unique_queue(playlist_ids, mode)
    reasons: dict[str, int] = {}
    for item in dropped:
        reasons[item.get("drop_reason") or "other"] = reasons.get(item.get("drop_reason") or "other", 0) + 1
    create_cost = 50
    insert_cost = 50 * len(kept)
    return {
        "source_count": len(source),
        "kept": len(kept),
        "dropped": len(dropped),
        "reasons": reasons,
        "quota_estimate": create_cost + insert_cost,
        "quota_warning": (create_cost + insert_cost) > 8000,
        "over_limit": len(kept) > 5000,
        "items": [
            {
                "id": v.get("id"),
                "title": v.get("title"),
                "channel": v.get("channel"),
                "duration": v.get("duration"),
            }
            for v in kept[:40]
        ],
        "dropped_examples": [
            {
                "id": v.get("id"),
                "title": v.get("title"),
                "channel": v.get("channel"),
                "reason": v.get("drop_reason"),
            }
            for v in dropped[:30]
        ],
    }


def start_merge(
    playlist_ids: list[str],
    mode: str,
    *,
    title: str,
    privacy: str,
    destination_id: str | None,
    add_to_library: bool,
) -> dict[str, Any]:
    if snapshot()["status"] in {"running"}:
        raise RuntimeError("A YouTube merge is already running.")
    preview_data = preview(playlist_ids, mode)
    if preview_data["over_limit"]:
        raise RuntimeError("YouTube playlists cap at 5,000 videos. Drop more duplicates or fewer sources.")
    _source, kept, _dropped = _unique_queue(playlist_ids, mode)
    _cancel.clear()
    _set_job(
        status="running",
        action="merge",
        added=0,
        skipped=0,
        failed=0,
        total=len(kept),
        message="Starting…",
        playlist_id=destination_id,
        playlist_url=None,
        error=None,
    )
    thread = threading.Thread(
        target=_run_merge,
        args=(kept, title, privacy, destination_id, add_to_library),
        daemon=True,
    )
    thread.start()
    return snapshot()


def start_clean(playlist_id: str, mode: str) -> dict[str, Any]:
    if snapshot()["status"] == "running":
        raise RuntimeError("A YouTube job is already running.")
    if not playlist_id:
        raise RuntimeError("Pick a playlist to clean.")
    _cancel.clear()
    _set_job(
        status="running",
        action="clean",
        added=0,
        skipped=0,
        failed=0,
        total=0,
        message="Reading playlist…",
        playlist_id=playlist_id,
        playlist_url=f"https://www.youtube.com/playlist?list={playlist_id}",
        error=None,
    )
    threading.Thread(target=_run_clean, args=(playlist_id, mode), daemon=True).start()
    return snapshot()


def cancel() -> dict[str, Any]:
    _cancel.set()
    return snapshot()


def _run_merge(
    kept: list[dict[str, Any]],
    title: str,
    privacy: str,
    destination_id: str | None,
    add_to_library: bool,
) -> None:
    try:
        yt = _service()
        if destination_id:
            playlist_id = destination_id
            existing = {item["video_id"] for item in _iter_items(yt, playlist_id)}
            _set_job(message="Adding unique videos to the existing playlist…", playlist_id=playlist_id)
        else:
            privacy = privacy if privacy in {"private", "unlisted", "public"} else "private"
            created = yt.playlists().insert(
                part="snippet,status",
                body={
                    "snippet": {
                        "title": (title or "Merged playlist").strip()[:150],
                        "description": "Merged in YT Desk. Duplicates dropped.",
                    },
                    "status": {"privacyStatus": privacy},
                },
            ).execute()
            playlist_id = created["id"]
            existing = set()
            _set_job(
                message="Created playlist, adding videos…",
                playlist_id=playlist_id,
                playlist_url=f"https://www.youtube.com/playlist?list={playlist_id}",
            )
        url = f"https://www.youtube.com/playlist?list={playlist_id}"
        _set_job(playlist_url=url)
        added = skipped = failed = 0
        for index, video in enumerate(kept, start=1):
            if _cancel.is_set():
                _set_job(status="cancelled", message="Cancelled.", added=added, skipped=skipped, failed=failed)
                return
            vid = video["id"]
            if vid in existing:
                skipped += 1
                _set_job(skipped=skipped, message=f"Skipping {index}/{len(kept)} (already in playlist)")
                continue
            try:
                _insert_video(yt, playlist_id, vid)
                added += 1
                existing.add(vid)
            except Exception as exc:
                failed += 1
                _set_job(failed=failed, message=f"Could not add {video.get('title') or vid}: {exc}")
            _set_job(
                added=added,
                skipped=skipped,
                failed=failed,
                message=f"{index}/{len(kept)} · added {added}",
            )
            time.sleep(0.12)
        if add_to_library:
            _import_into_library(url, playlist_id)
        _set_job(
            status="done",
            added=added,
            skipped=skipped,
            failed=failed,
            message=f"Done. Added {added}, skipped {skipped}, failed {failed}.",
            playlist_id=playlist_id,
            playlist_url=url,
        )
    except Exception as exc:
        _set_job(status="error", error=str(exc), message=str(exc))


def _run_clean(playlist_id: str, mode: str) -> None:
    try:
        yt = _service()
        items = _iter_items(yt, playlist_id)
        videos = []
        for item in items:
            local = db.get_video(item["video_id"]) or {}
            videos.append(
                {
                    "id": item["video_id"],
                    "title": local.get("title") or item.get("title") or item["video_id"],
                    "channel": local.get("channel") or item.get("channel") or "",
                    "duration": local.get("duration"),
                    "item_id": item["item_id"],
                }
            )
        kept, _dropped = dedupe_ordered(videos, mode=mode if mode in {"video", "song"} else "song")
        keep_ids = {v["id"] for v in kept}
        first: set[str] = set()
        to_delete = []
        for video in videos:
            vid = video["id"]
            if vid in first or vid not in keep_ids:
                to_delete.append(video)
            else:
                first.add(vid)
        _set_job(total=len(to_delete), message=f"Removing {len(to_delete)} duplicates…")
        removed = failed = 0
        for index, video in enumerate(to_delete, start=1):
            if _cancel.is_set():
                _set_job(status="cancelled", message="Cancelled.", added=removed, failed=failed)
                return
            try:
                yt.playlistItems().delete(id=video["item_id"]).execute()
                removed += 1
            except Exception:
                failed += 1
            _set_job(
                added=removed,
                failed=failed,
                skipped=0,
                message=f"Removed {removed}/{len(to_delete)}",
            )
            time.sleep(0.12)
        _set_job(
            status="done",
            added=removed,
            failed=failed,
            message=f"Removed {removed} duplicate entries.",
        )
    except Exception as exc:
        _set_job(status="error", error=str(exc), message=str(exc))


def _iter_items(yt, playlist_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    token = None
    while True:
        resp = yt.playlistItems().list(
            part="snippet,contentDetails",
            playlistId=playlist_id,
            maxResults=50,
            pageToken=token,
        ).execute()
        for item in resp.get("items") or []:
            vid = (item.get("contentDetails") or {}).get("videoId") or (
                (item.get("snippet") or {}).get("resourceId") or {}
            ).get("videoId")
            if not vid:
                continue
            snippet = item.get("snippet") or {}
            out.append(
                {
                    "item_id": item["id"],
                    "video_id": vid,
                    "title": snippet.get("title") or "",
                    "channel": snippet.get("videoOwnerChannelTitle") or snippet.get("channelTitle") or "",
                    "position": snippet.get("position") or 0,
                }
            )
        token = resp.get("nextPageToken")
        if not token:
            break
    out.sort(key=lambda row: row.get("position") or 0)
    return out


def _insert_video(yt, playlist_id: str, video_id: str) -> None:
    from googleapiclient.errors import HttpError

    body = {
        "snippet": {
            "playlistId": playlist_id,
            "resourceId": {"kind": "youtube#video", "videoId": video_id},
        }
    }
    last_error = None
    for attempt in range(5):
        try:
            yt.playlistItems().insert(part="snippet", body=body).execute()
            return
        except HttpError as exc:
            last_error = exc
            status = getattr(exc.resp, "status", None)
            text = exc.content.decode("utf-8", errors="replace") if getattr(exc, "content", None) else str(exc)
            if status == 409 or "alreadyExists" in text or "duplicate" in text.lower():
                return
            if status in {403, 429} and attempt < 4 and "quotaExceeded" not in text:
                time.sleep(1.5 * (attempt + 1))
                continue
            if "quotaExceeded" in text:
                raise RuntimeError(
                    "YouTube API quota exceeded for today. Merges cost 50 quota units per video."
                ) from exc
            raise RuntimeError(text[:300]) from exc
    if last_error:
        raise RuntimeError(str(last_error))


def _import_into_library(url: str, playlist_id: str) -> None:
    from . import youtube

    try:
        cookies = db.get_setting("cookies_path") or None
        info = youtube.fetch_listing(url, cookies)
        listing = youtube.normalize_listing(info, url)
        db.upsert_playlist({**listing, "id": listing.get("id") or playlist_id, "status": "idle", "error": None})
        db.replace_playlist_videos(listing.get("id") or playlist_id, listing["videos"])
        hub.publish("playlist", db.get_playlist(listing.get("id") or playlist_id))
        hub.publish("playlists", db.list_playlists())
        hub.publish("duplicates", db.duplicates())
    except Exception:
        db.upsert_playlist(
            {
                "id": playlist_id,
                "url": url,
                "title": "Merged playlist",
                "status": "idle",
            }
        )
        hub.publish("playlists", db.list_playlists())

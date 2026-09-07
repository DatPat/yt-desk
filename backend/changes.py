from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import app_data_dir
from .db import db
from .events import hub
from . import youtube


REPORT_PATH = app_data_dir() / "playlist_changes.json"

_job_lock = threading.Lock()
_job: dict[str, Any] = {
    "status": "idle",
    "message": "",
    "error": None,
    "checked": 0,
    "total": 0,
    "added": 0,
    "removed": 0,
    "previous_at": None,
    "checked_at": None,
    "playlists": [],
    "added_items": [],
    "removed_items": [],
    "baseline": False,
}


def snapshot() -> dict[str, Any]:
    with _job_lock:
        return json.loads(json.dumps(_job, default=str))


def _set_job(**fields: Any) -> dict[str, Any]:
    with _job_lock:
        _job.update(fields)
        data = json.loads(json.dumps(_job, default=str))
    hub.publish("changes", data)
    return data


def _save(data: dict[str, Any]) -> None:
    try:
        REPORT_PATH.write_text(json.dumps(data, default=str), encoding="utf-8")
    except OSError:
        pass


def load_saved() -> dict[str, Any]:
    try:
        if REPORT_PATH.exists():
            saved = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                with _job_lock:
                    if _job["status"] != "running":
                        _job.update(saved)
                return snapshot()
    except Exception:
        pass
    return snapshot()


def start_check() -> dict[str, Any]:
    if snapshot()["status"] == "running":
        raise RuntimeError("A playlist check is already running.")
    playlists = db.list_playlists()
    if not playlists:
        raise RuntimeError("Add a playlist first.")
    previous = db.get_setting("last_playlist_check") or None
    _set_job(
        status="running",
        message="Checking YouTube playlists for new songs…",
        error=None,
        checked=0,
        total=len(playlists),
        added=0,
        removed=0,
        previous_at=previous,
        checked_at=None,
        playlists=[],
        added_items=[],
        removed_items=[],
        baseline=not db.has_seen_snapshot(),
    )
    threading.Thread(target=_run_check, args=(playlists, previous), daemon=True).start()
    return snapshot()


def _run_check(playlists: list[dict[str, Any]], previous: str | None) -> None:
    try:
        seen_map = db.all_seen_ids()
        use_seen = db.has_seen_snapshot()
        cookies = db.get_setting("cookies_path") or None
        groups: list[dict[str, Any]] = []
        added_items: list[dict[str, Any]] = []
        removed_items: list[dict[str, Any]] = []
        for index, playlist in enumerate(playlists, start=1):
            _set_job(
                checked=index - 1,
                message=f"Checking {index}/{len(playlists)} · {playlist.get('title') or playlist['id']}",
            )
            try:
                group = _check_one(playlist, seen_map, use_seen, cookies)
            except Exception as exc:
                db.set_playlist_status(playlist["id"], "error", str(exc)[:400])
                groups.append(
                    {
                        "id": playlist["id"],
                        "title": playlist.get("title") or playlist["id"],
                        "error": str(exc)[:400],
                        "added": 0,
                        "removed": 0,
                        "added_items": [],
                        "removed_items": [],
                    }
                )
                hub.publish("playlists", db.list_playlists())
                time.sleep(0.4)
                continue
            groups.append(group)
            added_items.extend(group["added_items"])
            removed_items.extend(group["removed_items"])
            _set_job(
                checked=index,
                added=len(added_items),
                removed=len(removed_items),
                playlists=groups,
                added_items=added_items,
                removed_items=removed_items,
            )
            time.sleep(0.4)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        db.set_settings({"last_playlist_check": now})
        baseline = not use_seen
        if baseline:
            for group in groups:
                group["added_items"] = []
                group["removed_items"] = []
                group["added"] = 0
                group["removed"] = 0
            added_items = []
            removed_items = []
            message = (
                f"Saved a baseline of {len(playlists)} playlist(s). "
                "Next time you run this, you’ll see songs added since now."
            )
        elif added_items:
            extra = f" {len(removed_items)} removed." if removed_items else ""
            message = f"{len(added_items)} new song(s) since {_short_when(previous)}.{extra}"
        elif removed_items:
            message = f"No new songs since {_short_when(previous)}. {len(removed_items)} removed."
        else:
            message = f"No new songs since {_short_when(previous)}."
        data = _set_job(
            status="done",
            checked=len(playlists),
            added=len(added_items),
            removed=len(removed_items),
            previous_at=previous,
            checked_at=now,
            playlists=groups,
            added_items=added_items,
            removed_items=removed_items,
            baseline=baseline,
            message=message,
            error=None,
        )
        _save(data)
        hub.publish("playlists", db.list_playlists())
        hub.publish("duplicates", db.duplicates())
    except Exception as exc:
        data = _set_job(status="error", error=str(exc)[:400], message=str(exc)[:400])
        _save(data)


def _short_when(iso: str | None) -> str:
    if not iso:
        return "the last check"
    try:
        then = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return then.astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso


def _check_one(
    playlist: dict[str, Any],
    seen_map: dict[str, set[str]],
    use_seen: bool,
    cookies: str | None,
) -> dict[str, Any]:
    db.set_playlist_status(playlist["id"], "fetching")
    hub.publish("playlist", db.get_playlist(playlist["id"]))
    info = youtube.fetch_listing(playlist["url"], cookies)
    listing = youtube.normalize_listing(info, playlist["url"])
    real_id = listing["id"]
    if real_id != playlist["id"]:
        existing = db.get_playlist(playlist["id"])
        db.delete_playlist(playlist["id"])
        listing["added_at"] = (existing or {}).get("added_at")
    db.upsert_playlist({**listing, "status": "idle", "error": None})
    videos = listing.get("videos") or []
    new_ids = [v["id"] for v in videos if v.get("id")]
    if use_seen:
        old_ids = seen_map.get(playlist["id"], set()) | seen_map.get(real_id, set())
    else:
        old_ids = db.playlist_video_ids(playlist["id"]) | db.playlist_video_ids(real_id)
    added = [v for v in videos if v.get("id") and v["id"] not in old_ids]
    removed_ids = [vid for vid in old_ids if vid not in set(new_ids)]
    db.replace_playlist_videos(real_id, videos)
    db.replace_seen(real_id, new_ids)
    if real_id != playlist["id"]:
        db.replace_seen(playlist["id"], [])
    added_out = []
    for video in added:
        full = db.get_video(video["id"]) or video
        added_out.append(_item(real_id, listing.get("title") or playlist.get("title"), full))
    removed_out = []
    for vid in removed_ids:
        full = db.get_video(vid) or {"id": vid, "title": vid, "channel": ""}
        removed_out.append(_item(real_id, listing.get("title") or playlist.get("title"), full))
    hub.publish("playlist", db.get_playlist(real_id))
    return {
        "id": real_id,
        "title": listing.get("title") or playlist.get("title") or real_id,
        "error": None,
        "added": len(added_out),
        "removed": len(removed_out),
        "added_items": added_out,
        "removed_items": removed_out,
    }


def _item(playlist_id: str, playlist_title: str, video: dict[str, Any]) -> dict[str, Any]:
    download = video.get("download")
    return {
        "id": video.get("id"),
        "title": video.get("title"),
        "channel": video.get("channel"),
        "duration": video.get("duration"),
        "thumbnail": video.get("thumbnail"),
        "url": video.get("url") or f"https://www.youtube.com/watch?v={video.get('id')}",
        "download": download,
        "playlist_id": playlist_id,
        "playlist_title": playlist_title,
        "song_artist": video.get("song_artist") or video.get("artist") or "",
        "song_title": video.get("song_title") or video.get("track") or "",
    }

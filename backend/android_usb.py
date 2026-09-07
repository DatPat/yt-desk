from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from .db import db
from .events import hub


MUSIC_NAMES = ("Music", "music", "Musica", "Música", "Musique", "Musik")
PLAYLIST_FOLDER = "Playlists"
MEDIA_EXTS = {
    ".opus",
    ".ogg",
    ".oga",
    ".m4a",
    ".mp3",
    ".flac",
    ".wav",
    ".aac",
    ".mp4",
    ".mkv",
    ".webm",
    ".m4b",
}
SKIP_DIR_NAMES = {
    ".thumbnails",
    "android",
    "playlists",
    "alarms",
    "notifications",
    "ringtones",
    "podcasts",
    "audiobooks",
    "recordings",
}
STORAGE_PREFER = (
    "internal shared storage",
    "internal storage",
    "phone",
    "internal",
    "storage",
)
WPD_GUID = "6ac27878-a6fa-4155-ba85-f98f491d4f33"
_DISK_TYPES = {
    "local disk",
    "network drive",
    "cd drive",
    "ram disk",
    "dvd drive",
    "compact disc",
    "removable disk",
}

_job_lock = threading.Lock()
_cancel = threading.Event()
_last_devices: list[dict[str, Any]] = []
_job: dict[str, Any] = {
    "status": "idle",
    "copied": 0,
    "skipped": 0,
    "failed": 0,
    "total": 0,
    "message": "",
    "device": None,
    "error": None,
    "kind": None,
    "phase": "",
    "current": None,
    "bytes_done": 0,
    "bytes_total": 0,
    "started_at": None,
}


def _dest_label(phone_name: str | None = None) -> str:
    name = (phone_name or "").strip() or "Android phone"
    return f"This PC\\{name}\\Internal shared storage\\Music"


def snapshot() -> dict[str, Any]:
    with _job_lock:
        return dict(_job)


def _set_job(**fields: Any) -> dict[str, Any]:
    with _job_lock:
        _job.update(fields)
        data = dict(_job)
    hub.publish("android", data)
    return data


def _local_files() -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for video in db.downloaded_videos():
        path = Path(video.get("filepath") or "")
        if not path.exists() or not path.is_file():
            continue
        if path.suffix.lower() in {".part", ".tmp", ".ytdl"}:
            continue
        if path.name in seen:
            continue
        seen.add(path.name)
        out.append(path)
    return out


def _track_display(video: dict[str, Any]) -> str:
    from .media_tags import display_fields

    artist, title, _album = display_fields(video)
    title = (title or video.get("title") or video.get("id") or "track").replace("\n", " ").strip()
    artist = (artist or "").replace("\n", " ").strip()
    if artist and title and not title.lower().startswith(artist.lower()):
        return f"{artist} - {title}"
    return title


def _downloaded_tracks_for_playlist(playlist_id: str) -> list[dict[str, Any]]:
    tracks: list[dict[str, Any]] = []
    seen_files: set[str] = set()
    for video in db.playlist_videos(playlist_id):
        download = video.get("download") or {}
        path = Path(download.get("filepath") or video.get("filepath") or "")
        if not path.exists() or not path.is_file():
            continue
        if path.suffix.lower() in {".part", ".tmp", ".ytdl"}:
            continue
        duration = video.get("duration")
        try:
            duration_i = int(duration) if duration is not None else -1
        except (TypeError, ValueError):
            duration_i = -1
        tracks.append(
            {
                "filename": path.name,
                "duration": duration_i,
                "display": _track_display(video),
                "repeat": path.name in seen_files,
            }
        )
        seen_files.add(path.name)
    return tracks


def m3u8_text(playlist_title: str, tracks: list[dict[str, Any]]) -> str:
    title = (playlist_title or "Playlist").replace("\n", " ").strip()
    lines = ["#EXTM3U", "#EXTENC:UTF-8", f"#PLAYLIST:{title}"]
    for track in tracks:
        display = str(track.get("display") or track.get("filename") or "track").replace("\n", " ")
        duration = track.get("duration", -1)
        try:
            duration_i = int(duration)
        except (TypeError, ValueError):
            duration_i = -1
        filename = str(track.get("filename") or "").replace("\\", "/")
        if not filename:
            continue
        lines.append(f"#EXTINF:{duration_i},{display}")
        lines.append(f"../{filename}")
    lines.append("")
    return "\n".join(lines)


def _build_playlist_files(folder: Path) -> list[Path]:
    from .media_tags import safe_filename

    folder.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    used: set[str] = set()
    for playlist in db.list_playlists():
        tracks = _downloaded_tracks_for_playlist(playlist["id"])
        if not tracks:
            continue
        title = (playlist.get("title") or "Playlist").strip() or "Playlist"
        stem = safe_filename(title)
        name = f"{stem}.m3u8"
        n = 2
        while name.lower() in used:
            name = f"{stem} ({n}).m3u8"
            n += 1
        used.add(name.lower())
        path = folder / name
        path.write_text(m3u8_text(title, tracks), encoding="utf-8")
        written.append(path)
    return written


def _adb_path() -> str | None:
    found = shutil.which("adb")
    if found:
        return found
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    extra = [
        local / "Android" / "Sdk" / "platform-tools" / "adb.exe",
        Path(os.environ.get("ANDROID_HOME", "") or "") / "platform-tools" / "adb.exe",
        Path(os.environ.get("ANDROID_SDK_ROOT", "") or "") / "platform-tools" / "adb.exe",
    ]
    for path in extra:
        if path.is_file():
            return str(path)
    winget = local / "Microsoft" / "WinGet" / "Packages"
    if winget.is_dir():
        matches = sorted(winget.glob("Google.PlatformTools*/platform-tools/adb.exe"))
        if matches:
            return str(matches[-1])
    return None


def _run_adb(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    adb = _adb_path()
    if not adb:
        raise RuntimeError("adb not found")
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(
        [adb, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=flags,
    )


def _should_skip_existing(exists: bool, local_size: int, remote_size: int | None) -> bool:
    """Skip copy when the phone already has this name.

    Recopy only when we have a real remote size that does not match.
    MTP often reports size 0 — treat that as 'exists', not as empty.
    """
    if not exists:
        return False
    if remote_size is None or remote_size < 0:
        return True
    if remote_size == 0:
        return True
    return remote_size == local_size


def _parse_adb_ls(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("total") or line.startswith("ls:"):
            continue
        if line.startswith("d") and " " in line and len(line.split()) >= 6:
            continue
        parts = line.split()
        if len(parts) >= 8 and parts[0][:1] in {"-", "l"}:
            try:
                size = int(parts[4])
            except ValueError:
                size = -1
            name = " ".join(parts[7:])
            if name and name not in {".", ".."}:
                prev = out.get(name, -1)
                if size >= 0 or prev < 0:
                    out[name] = size
            continue
        if line not in {".", ".."}:
            out.setdefault(line, -1)
    return out


def _adb_list_files(serial: str, remote_dir: str) -> dict[str, int]:
    listed = _run_adb(["-s", serial, "shell", "ls", "-1", remote_dir], timeout=30)
    names = _parse_adb_ls(listed.stdout or "") if listed.returncode == 0 else {}
    detailed = _run_adb(["-s", serial, "shell", "ls", "-l", remote_dir], timeout=30)
    if detailed.returncode == 0:
        for name, size in _parse_adb_ls(detailed.stdout or "").items():
            if size >= 0 or name not in names:
                names[name] = size
    return names


def _mtp_item_size(item) -> int | None:
    for getter in (
        lambda: item.ExtendedProperty("System.Size"),
        lambda: item.ExtendedProperty("Size"),
        lambda: getattr(item, "Size", None),
    ):
        try:
            value = getter()
            if value is None or value == "":
                continue
            return int(value)
        except Exception:
            continue
    return None


def _mtp_lookup(folder, name: str) -> tuple[bool, int | None]:
    try:
        item = folder.ParseName(name)
    except Exception:
        item = None
    if item is None:
        return False, None
    return True, _mtp_item_size(item)


def _parse_adb_devices(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("List of devices"):
            continue
        if line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        meta = {}
        for token in parts[2:]:
            if ":" in token:
                key, value = token.split(":", 1)
                meta[key] = value.replace("_", " ")
        name = meta.get("model") or meta.get("device") or serial
        ready = state == "device"
        error = None
        if state == "unauthorized":
            error = "Allow USB debugging on the phone, then Refresh."
        elif state == "offline":
            error = "Phone is offline. Unplug and plug it back in."
        elif not ready:
            error = f"adb status: {state}"
        out.append(
            {
                "id": f"adb:{serial}",
                "serial": serial,
                "name": name,
                "kind": "adb",
                "ready": ready,
                "error": error,
            }
        )
    return out


def _list_adb() -> list[dict[str, Any]]:
    if not _adb_path():
        return []
    try:
        proc = _run_adb(["devices", "-l"], timeout=12)
    except Exception:
        return []
    return _parse_adb_devices(proc.stdout or "")


def _com_ok() -> bool:
    try:
        import pythoncom  # noqa: F401
        from win32com.client import Dispatch  # noqa: F401

        return True
    except ImportError:
        return False


def _with_com(fn):
    import pythoncom

    pythoncom.CoInitialize()
    try:
        return fn()
    finally:
        pythoncom.CoUninitialize()


def _shell():
    from win32com.client import Dispatch

    return Dispatch("Shell.Application")


def _this_pc(shell):
    return shell.NameSpace(17)


def _iter_items(folder) -> list[Any]:
    try:
        return list(folder.Items())
    except Exception:
        return []


def _child(folder, *names: str):
    wanted = {n.lower() for n in names if n}
    for item in _iter_items(folder):
        try:
            if str(item.Name).lower() in wanted:
                return item
        except Exception:
            continue
    return None


def _looks_like_android(name: str, kind: str, path: str) -> bool:
    kind_l = (kind or "").lower()
    path_l = (path or "").replace("/", "\\").lower()
    name_l = (name or "").lower()
    if name_l.startswith("iphone") or name_l.startswith("ipad") or "apple" in kind_l:
        return False
    if kind_l in _DISK_TYPES:
        return False
    if re.match(r"^[a-z]:\\", path_l) or re.match(r"^[a-z]:$", path_l):
        return False
    if "mobile phone" in kind_l or kind_l in {"phone", "android phone", "android device"}:
        return True
    if "portable device" in kind_l or "portable media" in kind_l:
        return True
    if "android" in kind_l or "mtp" in kind_l:
        return True
    if WPD_GUID in path_l:
        return True
    if "android" in name_l:
        return True
    return False


def _skip_wpd_name(name: str) -> bool:
    text = (name or "").strip()
    if not text:
        return True
    if re.match(r"^[A-Za-z]:\\?$", text):
        return True
    lowered = text.lower()
    if lowered in {"new volume", "local disk"}:
        return True
    junk = ("rufus", "cccoma", "flash drive")
    return any(token in lowered for token in junk)


def _list_wpd_names() -> list[str]:
    if sys.platform != "win32" or not _com_ok():
        return []

    def inner() -> list[str]:
        from win32com.client import Dispatch

        names: list[str] = []
        try:
            locator = Dispatch("WbemScripting.SWbemLocator")
            svc = locator.ConnectServer(".", "root\\CIMV2")
            rows = svc.ExecQuery(
                "SELECT Name, Status, PNPClass FROM Win32_PnPEntity WHERE PNPClass = 'WPD'"
            )
            for row in rows:
                if str(getattr(row, "Status", "") or "") != "OK":
                    continue
                name = str(getattr(row, "Name", "") or "").strip()
                if _skip_wpd_name(name):
                    continue
                if name not in names:
                    names.append(name)
        except Exception:
            return names
        return names

    try:
        return _with_com(inner)
    except Exception:
        return []


def _pick_storage(phone_folder):
    items = _iter_items(phone_folder)
    if not items:
        return None
    ranked: list[tuple[int, Any]] = []
    for item in items:
        try:
            name = str(item.Name or "").lower()
        except Exception:
            continue
        score = 0
        if any(token in name for token in STORAGE_PREFER):
            score += 10
        if any(token in name for token in ("sd card", "sdcard", "card", "external", "microsd")):
            score -= 4
        try:
            folder = item.GetFolder
            if folder and _child(folder, *MUSIC_NAMES):
                score += 6
        except Exception:
            pass
        ranked.append((score, item))
    ranked.sort(key=lambda row: -row[0])
    return ranked[0][1]


def _list_mtp() -> list[dict[str, Any]]:
    if sys.platform != "win32" or not _com_ok():
        return []

    def inner() -> list[dict[str, Any]]:
        shell = _shell()
        computer = _this_pc(shell)
        if computer is None:
            return []
        found: list[dict[str, Any]] = []
        for item in _iter_items(computer):
            try:
                kind = str(item.Type or "")
                name = str(item.Name or "")
            except Exception:
                continue
            path = str(getattr(item, "Path", "") or "")
            if not _looks_like_android(name, kind, path):
                continue
            found.append(
                {
                    "id": f"mtp:{name}",
                    "serial": name,
                    "name": name or "Android phone",
                    "kind": "mtp",
                    "ready": True,
                    "error": None,
                }
            )
        return found

    try:
        return _with_com(inner)
    except Exception:
        return []


def list_devices() -> dict[str, Any]:
    global _last_devices
    if snapshot()["status"] == "running" and _last_devices:
        files = _local_files()
        return {
            "platform": sys.platform,
            "is_windows": sys.platform == "win32",
            "adb": bool(_adb_path()),
            "com": _com_ok(),
            "devices": list(_last_devices),
            "library_count": len(files),
            "dest": _dest_label((_last_devices[0] or {}).get("name") if _last_devices else None),
            "error": None,
            "job": snapshot(),
        }
    adb_devices = _list_adb()
    mtp_devices = _list_mtp()
    # Prefer ADB when the same phone is also mounted as MTP.
    adb_names = {re.sub(r"\s+", "", d["name"]).lower() for d in adb_devices if d.get("ready")}
    devices = list(adb_devices)
    for item in mtp_devices:
        compact = re.sub(r"\s+", "", item["name"]).lower()
        if compact in adb_names:
            continue
        devices.append(item)
    _last_devices = devices
    error = None
    if not devices:
        wpd = _list_wpd_names()
        if sys.platform == "win32":
            if wpd:
                shown = ", ".join(wpd[:4])
                error = (
                    f"Windows sees {shown} on USB, but not as a phone folder. "
                    "Unlock the phone, swipe the USB notification, choose File transfer / MTP, then Refresh."
                )
            else:
                error = (
                    "No Android phone on USB. Plug any Android in with a data cable, unlock it, "
                    "set USB to File transfer / MTP, then Refresh."
                )
        else:
            error = "USB copy on this OS uses adb. Plug the phone in with USB debugging on."
    files = _local_files()
    return {
        "platform": sys.platform,
        "is_windows": sys.platform == "win32",
        "adb": bool(_adb_path()),
        "com": _com_ok(),
        "devices": devices,
        "library_count": len(files),
        "dest": _dest_label((devices[0] or {}).get("name") if devices else None),
        "error": error,
        "job": snapshot(),
    }


def start_sync(device_id: str | None = None, kind: str | None = None) -> dict[str, Any]:
    if snapshot()["status"] == "running":
        raise RuntimeError("A USB copy is already running.")
    files = _local_files()
    if not files:
        raise RuntimeError("Nothing downloaded yet. Download tracks on this PC first.")
    info = list_devices()
    devices = info["devices"]
    match = None
    if device_id:
        match = next((d for d in devices if d["id"] == device_id), None)
        if match is None:
            raise RuntimeError("That phone is no longer connected. Refresh and try again.")
    else:
        ready = [d for d in devices if d.get("ready")]
        if kind:
            ready = [d for d in ready if d.get("kind") == kind] or ready
        match = next((d for d in ready if d["kind"] == "adb"), None) or (ready[0] if ready else None)
    if match is None:
        raise RuntimeError(info.get("error") or "No Android phone on USB.")
    if not match.get("ready"):
        raise RuntimeError(match.get("error") or "Phone is connected but not ready.")
    _cancel.clear()
    bytes_total = 0
    for path in files:
        try:
            bytes_total += path.stat().st_size
        except OSError:
            pass
    _set_job(
        status="running",
        copied=0,
        skipped=0,
        failed=0,
        total=len(files),
        message=f"Connecting to {match['name']}…",
        device=match["name"],
        error=None,
        kind=match["kind"],
        phase="Connecting",
        current=None,
        bytes_done=0,
        bytes_total=bytes_total,
        started_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )
    threading.Thread(target=_run_sync, args=(match, files), daemon=True).start()
    return snapshot()


def cancel() -> dict[str, Any]:
    _cancel.set()
    return snapshot()


def _run_sync(device: dict[str, Any], files: list[Path]) -> None:
    try:
        if device["kind"] == "adb":
            _adb_sync(device, files)
        else:
            _mtp_sync(device, files)
    except Exception as exc:
        _set_job(status="error", error=_friendly_error(exc), message=_friendly_error(exc))


def _heartbeat(stop: threading.Event) -> None:
    while not stop.wait(1.0):
        data = snapshot()
        if data.get("status") != "running":
            return
        hub.publish("android", data)


def _adb_sync(device: dict[str, Any], files: list[Path]) -> None:
    serial = device.get("serial") or device["id"].split(":", 1)[-1]
    remote_dir = "/sdcard/Music"
    dest = _dest_label(device.get("name"))
    _set_job(
        phase="Preparing phone",
        current=None,
        message=f"Copying into {dest}…",
    )
    mkdir = _run_adb(["-s", serial, "shell", "mkdir", "-p", remote_dir], timeout=20)
    if mkdir.returncode != 0:
        raise RuntimeError((mkdir.stderr or mkdir.stdout or "mkdir failed").strip())
    _set_job(phase="Checking phone", message="Checking which tracks are already on the phone…")
    remote_names = _adb_list_files(serial, remote_dir)
    copied = skipped = failed = 0
    bytes_done = 0
    bytes_total = snapshot().get("bytes_total") or 0
    for index, path in enumerate(files, start=1):
        if _cancel.is_set():
            _set_job(
                status="cancelled",
                copied=copied,
                skipped=skipped,
                failed=failed,
                phase="Cancelled",
                message="Cancelled.",
            )
            return
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        remote = f"{remote_dir}/{path.name}"
        exists = path.name in remote_names
        remote_size = remote_names.get(path.name)
        if _should_skip_existing(exists, size, remote_size):
            skipped += 1
            bytes_done += size
            _set_job(
                skipped=skipped,
                bytes_done=bytes_done,
                current=path.name,
                phase="Already on the phone",
                message=f"{index} of {len(files)} already there · {path.name}",
            )
            continue
        _set_job(
            current=path.name,
            phase="Copying",
            message=f"{index} of {len(files)} · copying {path.name}",
        )
        push = _run_adb(["-s", serial, "push", str(path), remote], timeout=180)
        if push.returncode != 0:
            failed += 1
            err = (push.stderr or push.stdout or "push failed").strip()[:200]
            _set_job(
                failed=failed,
                current=path.name,
                phase="Failed a file",
                message=f"{index} of {len(files)} failed · {path.name}: {err}",
            )
            continue
        copied += 1
        bytes_done += size
        _set_job(
            copied=copied,
            bytes_done=bytes_done,
            bytes_total=bytes_total,
            current=path.name,
            phase="Copying",
            message=f"{index} of {len(files)} copied · {path.name}",
        )
    _scan_media(serial, remote_dir)
    playlists = 0
    if not _cancel.is_set():
        playlists = _adb_push_playlists(serial)
        _scan_media(serial, f"{remote_dir}/{PLAYLIST_FOLDER}")
    _set_job(
        status="done",
        copied=copied,
        skipped=skipped,
        failed=failed,
        bytes_done=bytes_done,
        current=None,
        phase="Done",
        message=(
            f"Done. Copied {copied}, skipped {skipped}, failed {failed}. "
            f"Files are in {dest}. {playlists} playlist file(s) in {PLAYLIST_FOLDER}."
        ),
    )


def _adb_push_playlists(serial: str) -> int:
    stage = Path(tempfile.mkdtemp(prefix="ytdesk-pl-"))
    try:
        files = _build_playlist_files(stage)
        if not files:
            return 0
        mkdir = _run_adb(
            ["-s", serial, "shell", "mkdir", "-p", f"/sdcard/Music/{PLAYLIST_FOLDER}"],
            timeout=20,
        )
        if mkdir.returncode != 0:
            raise RuntimeError((mkdir.stderr or mkdir.stdout or "Could not create Playlists folder").strip())
        existing = _adb_list_files(serial, f"/sdcard/Music/{PLAYLIST_FOLDER}")
        to_push = [
            path
            for path in files
            if not _should_skip_existing(
                path.name in existing, path.stat().st_size, existing.get(path.name)
            )
        ]
        _set_job(
            phase="Writing playlists",
            current=None,
            message=f"{len(files) - len(to_push)} playlist(s) already there, {len(to_push)} to copy…",
        )
        pushed = len(files) - len(to_push)
        for path in to_push:
            if _cancel.is_set():
                break
            remote = f"/sdcard/Music/{PLAYLIST_FOLDER}/{path.name}"
            result = _run_adb(["-s", serial, "push", str(path), remote], timeout=30)
            if result.returncode == 0:
                pushed += 1
        return pushed
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _scan_media(serial: str, remote_dir: str) -> None:
    try:
        _run_adb(
            [
                "-s",
                serial,
                "shell",
                "am",
                "broadcast",
                "-a",
                "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
                "-d",
                f"file://{remote_dir}",
            ],
            timeout=15,
        )
    except Exception:
        pass


def _mtp_sync(device: dict[str, Any], files: list[Path]) -> None:
    if sys.platform != "win32":
        raise RuntimeError("MTP copy is only available on Windows. Enable USB debugging and use adb.")
    if not _com_ok():
        raise RuntimeError("USB copy needs pywin32 (pip install pywin32) in the YT Desk venv.")
    _with_com(lambda: _mtp_sync_com(device, files))


def _mtp_sync_com(device: dict[str, Any], files: list[Path]) -> None:
    import pythoncom

    shell = _shell()
    computer = _this_pc(shell)
    if computer is None:
        raise RuntimeError("Could not open This PC. Plug the phone in again.")
    phone_item = None
    want = device.get("name") or ""
    for item in _iter_items(computer):
        try:
            name = str(item.Name or "")
            kind = str(item.Type or "")
        except Exception:
            continue
        path = str(getattr(item, "Path", "") or "")
        if want and name == want:
            phone_item = item
            break
        if phone_item is None and _looks_like_android(name, kind, path):
            phone_item = item
    if phone_item is None:
        raise RuntimeError(
            "Windows does not see the phone. Unlock it, set USB to File transfer / MTP, then try again."
        )
    storage_item = _pick_storage(phone_item.GetFolder)
    if storage_item is None:
        raise RuntimeError("Unlock the phone — storage is not visible yet.")
    storage = storage_item.GetFolder
    music_item = _child(storage, *MUSIC_NAMES)
    if music_item is None:
        try:
            storage.NewFolder("Music")
            time.sleep(0.4)
        except Exception:
            pass
        music_item = _child(storage, *MUSIC_NAMES)
    if music_item is None:
        raise RuntimeError("Could not open the phone’s Music folder.")
    dest = music_item.GetFolder
    dest_label = _dest_label(str(phone_item.Name or device.get("name") or ""))
    _set_job(message=f"Checking {len(files)} tracks on the phone…", phase="Checking phone")
    listed: set[str] = set()
    for item in _iter_items(dest):
        try:
            if item.IsFolder:
                continue
            listed.add(str(item.Name))
        except Exception:
            continue
    missing: list[Path] = []
    skipped = 0
    bytes_done = 0
    size_of = {}
    for index, path in enumerate(files, start=1):
        if _cancel.is_set():
            _set_job(status="cancelled", skipped=skipped, phase="Cancelled", message="Cancelled.")
            return
        try:
            size_of[path.name] = path.stat().st_size
        except OSError:
            size_of[path.name] = 0
        exists = path.name in listed
        remote_size = None
        if not exists:
            exists, remote_size = _mtp_lookup(dest, path.name)
        elif index == 1 or index % 25 == 0:
            _set_job(message=f"Checking {index}/{len(files)} · {path.name}")
        if _should_skip_existing(exists, size_of[path.name], remote_size):
            skipped += 1
            bytes_done += size_of[path.name]
            continue
        missing.append(path)
    _set_job(
        skipped=skipped,
        total=len(files),
        bytes_done=bytes_done,
        phase="Checking phone",
        current=None,
        message=(
            f"{skipped} already on the phone, {len(missing)} to copy into {dest_label}."
        ),
    )
    if not missing:
        playlists = _mtp_push_playlists(shell, dest)
        _set_job(
            status="done",
            copied=0,
            skipped=skipped,
            failed=0,
            bytes_done=bytes_done,
            phase="Done",
            message=(
                f"Already on the phone. {skipped} tracks in {dest_label}. "
                f"{playlists} playlist file(s) in {PLAYLIST_FOLDER}."
            ),
        )
        return
    if _cancel.is_set():
        _set_job(status="cancelled", skipped=skipped, phase="Cancelled", message="Cancelled.")
        return
    copied = 0
    failed = 0
    batch_size = 8
    for start in range(0, len(missing), batch_size):
        if _cancel.is_set():
            _set_job(
                status="cancelled",
                copied=copied,
                skipped=skipped,
                failed=failed,
                message="Cancelled.",
            )
            return
        chunk = missing[start : start + batch_size]
        stage = Path(tempfile.mkdtemp(prefix="ytdesk-android-"))
        staged: list[Path] = []
        try:
            for path in chunk:
                exists, remote_size = _mtp_lookup(dest, path.name)
                if _should_skip_existing(exists, size_of.get(path.name, 0), remote_size):
                    skipped += 1
                    bytes_done += size_of.get(path.name, 0)
                    _set_job(skipped=skipped, bytes_done=bytes_done, message=f"Already on the phone · {path.name}")
                    continue
                target = stage / path.name
                try:
                    os.link(path, target)
                except OSError:
                    try:
                        shutil.copy2(path, target)
                    except OSError as exc:
                        failed += 1
                        _set_job(failed=failed, message=f"Failed to stage {path.name}: {exc}")
                        continue
                staged.append(target)
            if not staged:
                continue
            first = staged[0].name
            last_index = copied + len(staged)
            _set_job(
                current=first,
                phase="Copying",
                message=(
                    f"Copying {copied + 1}–{last_index} of {len(missing)} to {device['name']} · {first}"
                    + (" and more" if len(staged) > 1 else "")
                    + ". Keep the phone unlocked."
                ),
            )
            stage_ns = shell.NameSpace(str(stage))
            if stage_ns is None:
                raise RuntimeError("Could not open the staging folder.")
            items = stage_ns.Items()
            stop_beat = threading.Event()
            beater = threading.Thread(target=_heartbeat, args=(stop_beat,), daemon=True)
            beater.start()
            try:
                # 16 = Yes to All. Do not use 4 (no UI) — MTP drops silent copies.
                dest.CopyHere(items, 16)
            finally:
                stop_beat.set()
            expected = {p.name for p in staged}
            timeout = max(60.0, sum(p.stat().st_size for p in staged) / (512 * 1024) + 30)
            deadline = time.time() + timeout
            remaining = set(expected)
            while remaining and time.time() < deadline:
                if _cancel.is_set():
                    break
                pythoncom.PumpWaitingMessages()
                still: set[str] = set()
                for name in remaining:
                    try:
                        if dest.ParseName(name) is None:
                            still.add(name)
                    except Exception:
                        still.add(name)
                remaining = still
                batch_copied = len(expected) - len(remaining)
                _set_job(
                    copied=copied + batch_copied,
                    skipped=skipped,
                    failed=failed,
                    bytes_done=bytes_done + sum(size_of.get(n, 0) for n in expected if n not in remaining),
                    current=next(iter(remaining), first),
                    phase="Copying",
                    message=(
                        f"{copied + batch_copied} of {len(missing)} copied to {device['name']}"
                        + (f" · waiting on {next(iter(remaining))}" if remaining else "")
                    ),
                )
                time.sleep(0.4)
            copied += len(expected) - len(remaining)
            failed += len(remaining)
            bytes_done += sum(size_of.get(n, 0) for n in expected if n not in remaining)
            if remaining and not _cancel.is_set():
                _set_job(
                    phase="Retrying",
                    message=f"{len(remaining)} in this batch did not finish; continuing…",
                )
        finally:
            shutil.rmtree(stage, ignore_errors=True)
    if _cancel.is_set():
        _set_job(
            status="cancelled",
            copied=copied,
            skipped=skipped,
            failed=failed,
            message=f"Cancelled. {copied} already on the phone.",
        )
        return
    playlists = 0
    if not _cancel.is_set():
        playlists = _mtp_push_playlists(shell, dest)
    status = "done" if failed == 0 else "error"
    _set_job(
        status=status,
        copied=copied,
        skipped=skipped,
        failed=failed,
        bytes_done=bytes_done,
        current=None,
        phase="Done" if failed == 0 else "Finished with errors",
        error=None if failed == 0 else f"{failed} files did not copy. Unlock the phone and retry.",
        message=(
            f"Done. Copied {copied}, skipped {skipped}, failed {failed}. "
            f"Files are in {dest_label}. {playlists} playlist file(s) in {PLAYLIST_FOLDER}."
        ),
    )


def _ensure_folder(folder, name: str):
    import pythoncom

    item = _child(folder, name)
    if item:
        return item.GetFolder
    folder.NewFolder(name)
    deadline = time.time() + 8
    while time.time() < deadline:
        pythoncom.PumpWaitingMessages()
        item = _child(folder, name)
        if item:
            return item.GetFolder
        time.sleep(0.2)
    raise RuntimeError(f"Could not create the {name} folder on the phone.")


def _mtp_copy_named(shell, dest_folder, path: Path, timeout: float = 25) -> bool:
    import pythoncom

    parent = shell.NameSpace(str(path.parent))
    if parent is None:
        return False
    item = parent.ParseName(path.name)
    if item is None:
        return False
    dest_folder.CopyHere(item, 16)
    deadline = time.time() + timeout
    while time.time() < deadline:
        pythoncom.PumpWaitingMessages()
        try:
            if dest_folder.ParseName(path.name) is not None:
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def _mtp_push_playlists(shell, music_folder) -> int:
    files_dir = Path(tempfile.mkdtemp(prefix="ytdesk-pl-"))
    try:
        files = _build_playlist_files(files_dir)
        if not files:
            return 0
        _set_job(
            phase="Writing playlists",
            current=None,
            message=f"Copying {len(files)} playlist(s) into Music\\{PLAYLIST_FOLDER}…",
        )
        dest = _ensure_folder(music_folder, PLAYLIST_FOLDER)
        to_copy: list[Path] = []
        for path in files:
            exists, remote_size = _mtp_lookup(dest, path.name)
            if _should_skip_existing(exists, path.stat().st_size, remote_size):
                continue
            to_copy.append(path)
        already = len(files) - len(to_copy)
        if not to_copy:
            return already
        _set_job(
            phase="Writing playlists",
            message=f"{already} playlist(s) already there, {len(to_copy)} to copy…",
        )
        landed = 0
        missing: list[str] = []
        for index, path in enumerate(to_copy, start=1):
            if _cancel.is_set():
                break
            _set_job(
                current=path.name,
                phase="Writing playlists",
                message=f"Copying playlist {index}/{len(to_copy)} · {path.name}",
            )
            if _mtp_copy_named(shell, dest, path):
                landed += 1
                continue
            missing.append(path.name)
        if missing and not _cancel.is_set():
            raise RuntimeError(
                "Tracks copied, but these playlists did not land in Music\\Playlists: "
                + ", ".join(missing)
                + ". Unlock the phone and sync again."
            )
        return already + landed
    finally:
        shutil.rmtree(files_dir, ignore_errors=True)


def _friendly_error(exc: BaseException) -> str:
    text = str(exc) or exc.__class__.__name__
    lowered = text.lower()
    if "unauthorized" in lowered:
        return "Allow USB debugging on the phone, then try again."
    if "no devices" in lowered or "not found" in lowered and "adb" in lowered:
        return "No phone over adb. Use File transfer (MTP), or enable USB debugging."
    if "com" in lowered and "reject" in lowered:
        return "Windows is busy with the phone. Close File Explorer windows opened on the phone and retry."
    return text[:400]


_orphan_lock = threading.Lock()
_orphan_cancel = threading.Event()
_orphan: dict[str, Any] = {
    "status": "idle",
    "message": "",
    "error": None,
    "files": [],
    "deleted": 0,
    "failed": 0,
    "total": 0,
    "device": None,
}


def orphan_snapshot() -> dict[str, Any]:
    with _orphan_lock:
        return json_safe(_orphan)


def json_safe(data: dict[str, Any]) -> dict[str, Any]:
    import json as _json

    return _json.loads(_json.dumps(data, default=str))


def _set_orphan(**fields: Any) -> dict[str, Any]:
    with _orphan_lock:
        _orphan.update(fields)
        data = json_safe(_orphan)
    hub.publish("android-orphans", data)
    return data


def extra_phone_files(phone_files: list[dict[str, Any]], library_names: set[str]) -> list[dict[str, Any]]:
    library = {name.lower() for name in library_names}
    extras = [item for item in phone_files if str(item.get("name") or "").lower() not in library]
    extras.sort(key=lambda item: str(item.get("path") or item.get("name") or "").lower())
    return extras


def _library_names() -> set[str]:
    return {path.name for path in _local_files()}


def _require_device(device_id: str | None = None, kind: str | None = None) -> dict[str, Any]:
    info = list_devices()
    devices = info["devices"]
    match = None
    if device_id:
        match = next((d for d in devices if d["id"] == device_id), None)
    else:
        ready = [d for d in devices if d.get("ready")]
        if kind:
            ready = [d for d in ready if d.get("kind") == kind] or ready
        match = next((d for d in ready if d["kind"] == "adb"), None) or (ready[0] if ready else None)
    if match is None:
        raise RuntimeError(info.get("error") or "No Android phone on USB.")
    if not match.get("ready"):
        raise RuntimeError(match.get("error") or "Phone is connected but not ready.")
    return match


def start_orphan_scan(device_id: str | None = None, kind: str | None = None) -> dict[str, Any]:
    if snapshot()["status"] == "running":
        raise RuntimeError("Wait for the USB copy to finish first.")
    if orphan_snapshot()["status"] in {"scanning", "deleting"}:
        raise RuntimeError("Already scanning or deleting files on the phone.")
    device = _require_device(device_id, kind)
    _orphan_cancel.clear()
    _set_orphan(
        status="scanning",
        message=f"Scanning Music on {device['name']}…",
        error=None,
        files=[],
        deleted=0,
        failed=0,
        total=0,
        device=device["name"],
    )
    threading.Thread(target=_run_orphan_scan, args=(device,), daemon=True).start()
    return orphan_snapshot()


def start_orphan_delete(
    paths: list[str],
    device_id: str | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    if snapshot()["status"] == "running":
        raise RuntimeError("Wait for the USB copy to finish first.")
    if orphan_snapshot()["status"] in {"scanning", "deleting"}:
        raise RuntimeError("Already scanning or deleting files on the phone.")
    clean = [str(p).replace("\\", "/").lstrip("/") for p in paths if str(p).strip()]
    clean = [p for p in clean if ".." not in p.split("/")]
    if not clean:
        raise RuntimeError("Pick at least one file to delete from the phone.")
    device = _require_device(device_id, kind)
    _orphan_cancel.clear()
    _set_orphan(
        status="deleting",
        message=f"Deleting {len(clean)} file(s) from {device['name']}…",
        error=None,
        deleted=0,
        failed=0,
        total=len(clean),
        device=device["name"],
    )
    threading.Thread(target=_run_orphan_delete, args=(device, clean), daemon=True).start()
    return orphan_snapshot()


def cancel_orphans() -> dict[str, Any]:
    _orphan_cancel.set()
    return orphan_snapshot()


def _run_orphan_scan(device: dict[str, Any]) -> None:
    try:
        library = _library_names()
        if device["kind"] == "adb":
            phone_files = _adb_list_media(device.get("serial") or device["id"].split(":", 1)[-1])
        else:
            phone_files = _with_com(lambda: _mtp_list_media(device))
        extras = extra_phone_files(phone_files, library)
        _set_orphan(
            status="done",
            files=extras,
            total=len(extras),
            message=(
                f"{len(extras)} file(s) on the phone are not in your YT Desk library."
                if extras
                else "Every music file on the phone is in your YT Desk library."
            ),
            error=None,
        )
    except Exception as exc:
        _set_orphan(status="error", error=_friendly_error(exc), message=_friendly_error(exc))


def _run_orphan_delete(device: dict[str, Any], paths: list[str]) -> None:
    try:
        deleted = failed = 0
        failures: list[str] = []
        if device["kind"] == "adb":
            serial = device.get("serial") or device["id"].split(":", 1)[-1]
            for index, rel in enumerate(paths, start=1):
                if _orphan_cancel.is_set():
                    break
                _set_orphan(message=f"Deleting {index}/{len(paths)} · {rel}", current=rel)
                if _adb_delete_rel(serial, rel):
                    deleted += 1
                else:
                    failed += 1
                    failures.append(rel)
                _set_orphan(deleted=deleted, failed=failed)
        else:
            def inner() -> None:
                nonlocal deleted, failed
                shell_music = _mtp_open_music(device)
                dest = shell_music[1]
                for index, rel in enumerate(paths, start=1):
                    if _orphan_cancel.is_set():
                        break
                    _set_orphan(message=f"Deleting {index}/{len(paths)} · {rel}")
                    if _mtp_delete_rel(dest, rel):
                        deleted += 1
                    else:
                        failed += 1
                        failures.append(rel)
                    _set_orphan(deleted=deleted, failed=failed)

            _with_com(inner)
        deleted_set = set(paths) - set(failures)
        remaining = [
            item
            for item in (orphan_snapshot().get("files") or [])
            if str(item.get("path") or "") not in deleted_set
        ]
        status = "done" if failed == 0 else "error"
        msg = f"Deleted {deleted} file(s) from the phone."
        if failed:
            msg += f" {failed} could not be removed."
        _set_orphan(
            status=status,
            files=remaining,
            total=len(remaining),
            deleted=deleted,
            failed=failed,
            message=msg,
            error=None if failed == 0 else msg,
        )
    except Exception as exc:
        _set_orphan(status="error", error=_friendly_error(exc), message=_friendly_error(exc))


def _adb_list_media(serial: str) -> list[dict[str, Any]]:
    proc = _run_adb(["-s", serial, "shell", "find", "/sdcard/Music", "-type", "f"], timeout=60)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "Could not list Music on the phone").strip())
    out: list[dict[str, Any]] = []
    for raw in (proc.stdout or "").splitlines():
        line = raw.strip().replace("\\", "/")
        prefix = "/sdcard/Music/"
        if not line.startswith(prefix):
            continue
        rel = line[len(prefix) :]
        parts = [p for p in rel.split("/") if p]
        if any(part.lower() in SKIP_DIR_NAMES or part.startswith(".") for part in parts[:-1]):
            continue
        name = parts[-1] if parts else ""
        if Path(name).suffix.lower() not in MEDIA_EXTS:
            continue
        out.append({"path": rel, "name": name, "size": -1})
    return out


def _adb_delete_rel(serial: str, rel: str) -> bool:
    remote = "/sdcard/Music/" + rel.replace("\\", "/").lstrip("/")
    quoted = "'" + remote.replace("'", "'\"'\"'") + "'"
    proc = _run_adb(["-s", serial, "shell", f"rm -f -- {quoted}"], timeout=20)
    return proc.returncode == 0


def _mtp_open_music(device: dict[str, Any]):
    shell = _shell()
    computer = _this_pc(shell)
    if computer is None:
        raise RuntimeError("Could not open This PC. Plug the phone in again.")
    phone_item = None
    want = device.get("name") or ""
    for item in _iter_items(computer):
        try:
            name = str(item.Name or "")
            kind = str(item.Type or "")
        except Exception:
            continue
        path = str(getattr(item, "Path", "") or "")
        if want and name == want:
            phone_item = item
            break
        if phone_item is None and _looks_like_android(name, kind, path):
            phone_item = item
    if phone_item is None:
        raise RuntimeError("Windows does not see the phone. Unlock it and set USB to File transfer.")
    storage_item = _pick_storage(phone_item.GetFolder)
    if storage_item is None:
        raise RuntimeError("Unlock the phone — storage is not visible yet.")
    music_item = _child(storage_item.GetFolder, *MUSIC_NAMES)
    if music_item is None:
        raise RuntimeError("Could not open the phone’s Music folder.")
    return shell, music_item.GetFolder


def _mtp_list_media(device: dict[str, Any]) -> list[dict[str, Any]]:
    _shell_unused, music = _mtp_open_music(device)
    return _mtp_walk_media(music, "", 0)


def _mtp_walk_media(folder, prefix: str, depth: int) -> list[dict[str, Any]]:
    if depth > 4:
        return []
    out: list[dict[str, Any]] = []
    for item in _iter_items(folder):
        try:
            name = str(item.Name or "")
        except Exception:
            continue
        if not name or name in {".", ".."}:
            continue
        is_folder = False
        try:
            is_folder = bool(item.IsFolder)
        except Exception:
            is_folder = False
        if is_folder:
            if name.lower() in SKIP_DIR_NAMES or name.startswith("."):
                continue
            try:
                child = item.GetFolder
            except Exception:
                continue
            rel = f"{prefix}{name}/"
            out.extend(_mtp_walk_media(child, rel, depth + 1))
            continue
        if Path(name).suffix.lower() not in MEDIA_EXTS:
            continue
        size = _mtp_item_size(item)
        out.append({"path": f"{prefix}{name}", "name": name, "size": int(size or 0)})
    return out


def _mtp_delete_rel(music_folder, rel: str) -> bool:
    import pythoncom

    parts = [p for p in rel.replace("\\", "/").split("/") if p]
    if not parts:
        return False
    folder = music_folder
    for part in parts[:-1]:
        item = _child(folder, part)
        if item is None:
            return False
        folder = item.GetFolder
    name = parts[-1]
    try:
        item = folder.ParseName(name)
    except Exception:
        item = None
    if item is None:
        return False
    try:
        item.InvokeVerbEx("delete", "")
    except Exception:
        try:
            item.InvokeVerb("delete")
        except Exception:
            return False
    deadline = time.time() + 20
    while time.time() < deadline:
        pythoncom.PumpWaitingMessages()
        try:
            if folder.ParseName(name) is None:
                return True
        except Exception:
            return True
        time.sleep(0.2)
    try:
        return folder.ParseName(name) is None
    except Exception:
        return True


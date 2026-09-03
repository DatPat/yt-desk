from __future__ import annotations

import asyncio
import inspect
import sys
import threading
from pathlib import Path
from typing import Any

from .db import db
from .events import hub


REMOTE_DIR = "YT Desk"
USB_APPS = [
    {"id": "vlc", "bundle": "org.videolan.vlc-ios", "label": "VLC", "hint": "Best for Opus. Open VLC after the copy."},
    {"id": "infuse", "bundle": "com.firecore.infuse", "label": "Infuse", "hint": "Opens copied files in Infuse."},
]

_job_lock = threading.Lock()
_cancel = threading.Event()
_job: dict[str, Any] = {
    "status": "idle",
    "copied": 0,
    "skipped": 0,
    "failed": 0,
    "total": 0,
    "message": "",
    "device": None,
    "error": None,
}


def snapshot() -> dict[str, Any]:
    with _job_lock:
        return dict(_job)


def _set_job(**fields: Any) -> dict[str, Any]:
    with _job_lock:
        _job.update(fields)
        data = dict(_job)
    hub.publish("usb", data)
    return data


def _run(coro):
    return asyncio.run(coro)


def available() -> dict[str, Any]:
    try:
        import pymobiledevice3  # noqa: F401

        installed = True
    except ImportError:
        installed = False
    return {
        "platform": sys.platform,
        "is_mac": sys.platform == "darwin",
        "library": installed,
        "apps": USB_APPS,
        "job": snapshot(),
    }


def list_devices() -> dict[str, Any]:
    info = available()
    if not info["library"]:
        info["devices"] = []
        info["error"] = "USB copy needs pymobiledevice3 (pip install pymobiledevice3)."
        return info
    try:
        devices = _run(_async_list_devices())
        info["devices"] = devices
        info["error"] = None
    except Exception as exc:
        info["devices"] = []
        info["error"] = _friendly_error(exc)
    return info


async def _maybe(value):
    if inspect.isawaitable(value):
        return await value
    return value


async def _async_list_devices() -> list[dict[str, Any]]:
    from pymobiledevice3.lockdown import create_using_usbmux
    from pymobiledevice3.usbmux import list_devices as mux_list

    mux_devices = await _maybe(mux_list())
    out: list[dict[str, Any]] = []
    for mux in mux_devices or []:
        serial = getattr(mux, "serial", None) or getattr(mux, "udid", None)
        conn = getattr(mux, "connection_type", None) or "USB"
        if not serial:
            continue
        if str(conn).lower() == "network":
            continue
        entry: dict[str, Any] = {
            "udid": serial,
            "name": serial,
            "product": "",
            "paired": False,
            "connection": str(conn),
            "apps": [],
            "error": None,
        }
        try:
            lockdown = await create_using_usbmux(
                serial=serial,
                connection_type="USB",
                autopair=False,
            )
            short = lockdown.short_info
            entry["name"] = short.get("DeviceName") or lockdown.display_name or serial
            entry["product"] = short.get("ProductType") or ""
            entry["paired"] = bool(getattr(lockdown, "paired", False))
            if not entry["paired"]:
                try:
                    lockdown = await create_using_usbmux(
                        serial=serial,
                        connection_type="USB",
                        autopair=True,
                        pair_timeout=0,
                    )
                    entry["paired"] = bool(getattr(lockdown, "paired", False))
                    short = lockdown.short_info
                    entry["name"] = short.get("DeviceName") or entry["name"]
                except Exception as exc:
                    entry["error"] = _friendly_error(exc)
            if entry["paired"]:
                entry["apps"] = await _installed_targets(lockdown)
            await lockdown.close()
        except Exception as exc:
            entry["error"] = _friendly_error(exc)
        out.append(entry)
    return out


async def _installed_targets(lockdown) -> list[str]:
    try:
        from pymobiledevice3.services.installation_proxy import InstallationProxyService

        async with InstallationProxyService(lockdown) as proxy:
            apps = await proxy.get_apps(application_type="User")
        found = []
        for spec in USB_APPS:
            if spec["bundle"] in apps:
                found.append(spec["id"])
        return found
    except Exception:
        return []


def start_sync(udid: str | None, app_id: str = "vlc") -> dict[str, Any]:
    if snapshot()["status"] == "running":
        raise RuntimeError("A USB copy is already running.")
    spec = next((a for a in USB_APPS if a["id"] == app_id), USB_APPS[0])
    files = _local_files()
    if not files:
        raise RuntimeError("Nothing downloaded yet. Download tracks on this Mac first.")
    _cancel.clear()
    _set_job(
        status="running",
        copied=0,
        skipped=0,
        failed=0,
        total=len(files),
        message="Connecting to iPhone…",
        device=udid,
        error=None,
    )
    threading.Thread(target=_run_sync, args=(udid, spec, files), daemon=True).start()
    return snapshot()


def cancel() -> dict[str, Any]:
    _cancel.set()
    return snapshot()


def _local_files() -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for video in db.downloaded_videos():
        path = Path(video.get("filepath") or "")
        if not path.exists() or not path.is_file():
            continue
        if path.name in seen:
            continue
        seen.add(path.name)
        out.append(path)
    return out


def _run_sync(udid: str | None, spec: dict[str, str], files: list[Path]) -> None:
    try:
        _run(_async_sync(udid, spec, files))
    except Exception as exc:
        _set_job(status="error", error=_friendly_error(exc), message=_friendly_error(exc))


async def _async_sync(udid: str | None, spec: dict[str, str], files: list[Path]) -> None:
    from pymobiledevice3.lockdown import create_using_usbmux
    from pymobiledevice3.services.house_arrest import HouseArrestService

    _set_job(message="Unlock the iPhone and tap Trust if asked…")
    lockdown = await create_using_usbmux(
        serial=udid,
        connection_type="USB",
        autopair=True,
        pair_timeout=60,
    )
    name = lockdown.short_info.get("DeviceName") or udid
    _set_job(device=name, message=f"Copying into {spec['label']} on {name}…")
    try:
        async with await HouseArrestService.create(
            lockdown=lockdown, bundle_id=spec["bundle"], documents_only=True
        ) as afc:
            if not await afc.exists(REMOTE_DIR):
                await afc.makedirs(REMOTE_DIR)
            copied = skipped = failed = 0
            for index, path in enumerate(files, start=1):
                if _cancel.is_set():
                    _set_job(
                        status="cancelled",
                        copied=copied,
                        skipped=skipped,
                        failed=failed,
                        message="Cancelled.",
                    )
                    return
                remote = f"{REMOTE_DIR}/{path.name}"
                try:
                    if await afc.exists(remote):
                        info = await afc.stat(remote)
                        if int(info.get("st_size") or 0) == path.stat().st_size:
                            skipped += 1
                            _set_job(
                                skipped=skipped,
                                message=f"{index}/{len(files)} skipped {path.name}",
                            )
                            continue
                    await afc.push(str(path), remote, progress_bar=False)
                    copied += 1
                    _set_job(copied=copied, message=f"{index}/{len(files)} copied {path.name}")
                except Exception as exc:
                    failed += 1
                    _set_job(failed=failed, message=f"Failed {path.name}: {_friendly_error(exc)}")
            _set_job(
                status="done",
                copied=copied,
                skipped=skipped,
                failed=failed,
                message=f"Done. Copied {copied}, skipped {skipped}, failed {failed}. Open {spec['label']} on the iPhone.",
            )
    except Exception as exc:
        raise RuntimeError(_friendly_error(exc, spec["label"])) from exc
    finally:
        try:
            await lockdown.close()
        except Exception:
            pass


def _friendly_error(exc: BaseException, app_label: str | None = None) -> str:
    text = str(exc) or exc.__class__.__name__
    lowered = text.lower()
    if "notpaired" in lowered or "pair" in lowered and "denied" in lowered:
        return "Unlock the iPhone and tap Trust This Computer, then try again."
    if "password" in lowered or "protected" in lowered:
        return "Unlock the iPhone (enter the passcode) and try again."
    if "appnotinstalled" in lowered or "not installed" in lowered:
        label = app_label or "VLC"
        return f"{label} is not installed on this iPhone, or File Sharing is off. Install VLC from the App Store."
    if "nodevice" in lowered or "no device" in lowered:
        return "No iPhone on USB. Plug it in with a data cable (not charge-only)."
    if "userdenied" in lowered:
        return "Trust was declined on the iPhone. Replug and tap Trust."
    return text[:400]

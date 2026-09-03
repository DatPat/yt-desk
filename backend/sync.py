from __future__ import annotations

import io
from pathlib import Path
from typing import Any
from urllib.parse import quote

import qrcode
from qrcode.image.svg import SvgPathImage

from .config import PORT, lan_ips, new_sync_secrets
from .db import db


def rotate_secrets() -> dict[str, str]:
    token, pin = new_sync_secrets()
    return db.set_settings({"sync_token": token, "sync_pin": pin})


def public_base(ip: str | None = None) -> str:
    host = ip or (lan_ips()[0] if lan_ips() else "127.0.0.1")
    return f"http://{host}:{PORT}"


def qr_svg(payload: str) -> str:
    img = qrcode.make(payload, image_factory=SvgPathImage, box_size=10, border=2)
    buffer = io.BytesIO()
    img.save(buffer)
    return buffer.getvalue().decode("utf-8")


def sync_payload() -> dict[str, Any]:
    settings = db.settings()
    token = settings.get("sync_token") or ""
    pin = settings.get("sync_pin") or ""
    ips = lan_ips()
    primary = ips[0] if ips else "127.0.0.1"
    base = public_base(primary)
    mobile = f"{base}/m?t={quote(token)}"
    dav = f"{base}/dav"
    library = settings.get("library_dir") or ""
    return {
        "enabled": settings.get("sync_enabled", "1") == "1",
        "pin": pin,
        "token": token,
        "ips": ips,
        "port": PORT,
        "mobile_url": mobile,
        "dav_url": dav,
        "dav_user": "ytdesk",
        "library_dir": library,
        "qr_svg": qr_svg(mobile),
        "urls": [
            {
                "ip": ip,
                "mobile_url": f"{public_base(ip)}/m?t={quote(token)}",
                "dav_url": f"{public_base(ip)}/dav",
            }
            for ip in ips
        ],
    }


def token_ok(candidate: str | None) -> bool:
    expected = db.get_setting("sync_token")
    if not expected or not candidate:
        return False
    if len(candidate) != len(expected):
        return False
    result = 0
    for a, b in zip(candidate.encode(), expected.encode()):
        result |= a ^ b
    return result == 0


def media_path(video_id: str) -> Path | None:
    job = db.latest_download(video_id)
    if not job or job.get("status") != "done" or not job.get("filepath"):
        return None
    path = Path(job["filepath"])
    if path.exists():
        return path
    return None

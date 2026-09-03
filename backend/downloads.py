from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from . import youtube
from .db import db
from .events import hub


class DownloadManager:
    def __init__(self) -> None:
        self._cancel: set[int] = set()
        self._lock = threading.Lock()
        self._workers: list[threading.Thread] = []
        self._busy = 0
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self.kick()

    def _concurrency(self) -> int:
        try:
            return max(1, min(3, int(db.get_setting("concurrent", "1"))))
        except ValueError:
            return 1

    def kick(self) -> None:
        with self._lock:
            while self._busy < self._concurrency():
                queued = db.queued_downloads()
                taken = None
                for job in queued:
                    if job["id"] not in self._cancel:
                        taken = job
                        break
                if not taken:
                    break
                db.update_download(
                    taken["id"],
                    status="downloading",
                    progress=0,
                    phase="Starting",
                    speed="",
                    eta=None,
                )
                self._busy += 1
                thread = threading.Thread(target=self._run_job, args=(taken,), daemon=True)
                self._workers.append(thread)
                thread.start()
                hub.publish("download", db.get_download(taken["id"]))

    def enqueue(self, video_ids: list[str], quality: str | None = None) -> list[dict[str, Any]]:
        quality = quality or db.get_setting("quality", "audio-opus")
        jobs: list[dict[str, Any]] = []
        for video_id in video_ids:
            video = db.get_video(video_id)
            title = (video or {}).get("title") or video_id
            job = db.enqueue_download(video_id, title, quality)
            jobs.append(job)
            hub.publish("download", job)
        self.kick()
        return jobs

    def cancel(self, download_id: int) -> dict[str, Any] | None:
        self._cancel.add(download_id)
        job = db.get_download(download_id)
        if not job:
            return None
        if job["status"] == "queued":
            updated = db.update_download(download_id, status="cancelled", error="Cancelled")
            hub.publish("download", updated)
            return updated
        return job

    def retry(self, download_id: int) -> dict[str, Any] | None:
        job = db.get_download(download_id)
        if not job:
            return None
        self._cancel.discard(download_id)
        return self.enqueue([job["video_id"]], job.get("quality"))[0]

    def _run_job(self, job: dict[str, Any]) -> None:
        download_id = job["id"]
        video_id = job["video_id"]
        settings = db.settings()
        dest = Path(settings.get("library_dir") or "")
        cookies = settings.get("cookies_path") or None
        sponsorblock = settings.get("sponsorblock") == "1"
        quality = job.get("quality") or settings.get("quality") or "audio-opus"

        def should_cancel() -> bool:
            return download_id in self._cancel

        last_pub = [0.0, -1.0, ""]

        def on_progress(payload: dict[str, Any]) -> None:
            now = time.monotonic()
            progress = float(payload.get("progress") or 0)
            phase = str(payload.get("phase") or "")
            status = payload.get("status") or "downloading"
            force = (
                status == "processing"
                or phase != last_pub[2]
                or progress >= 99
                or last_pub[1] < 0
            )
            if not force and now - last_pub[0] < 0.3 and abs(progress - last_pub[1]) < 0.8:
                return
            last_pub[0], last_pub[1], last_pub[2] = now, progress, phase
            updated = db.update_download(
                download_id,
                status=status,
                progress=progress,
                speed=payload.get("speed") or "",
                eta=payload.get("eta"),
                filename=payload.get("filename"),
                phase=phase or None,
                downloaded_bytes=payload.get("downloaded_bytes"),
                total_bytes=payload.get("total_bytes"),
            )
            hub.publish("download", updated)

        try:
            result = youtube.download_video(
                video_id=video_id,
                dest_dir=dest,
                quality=quality,
                cookies_path=cookies,
                sponsorblock=sponsorblock,
                progress_cb=on_progress,
                should_cancel=should_cancel,
            )
            if should_cancel():
                raise youtube.CancelledDownload("Cancelled")
            updated = db.update_download(
                download_id,
                status="done",
                progress=100,
                speed="",
                eta=0,
                filepath=result["filepath"],
                filename=result["filename"],
                filesize=result["filesize"],
                error=None,
                phase="Saved",
            )
            hub.publish("download", updated)
            hub.publish("library", {"video_id": video_id})
        except youtube.CancelledDownload:
            updated = db.update_download(
                download_id, status="cancelled", error="Cancelled", speed=""
            )
            hub.publish("download", updated)
            self._cleanup_partial(dest, video_id)
        except Exception as exc:
            updated = db.update_download(
                download_id,
                status="error",
                error=str(exc),
                speed="",
            )
            hub.publish("download", updated)
        finally:
            self._cancel.discard(download_id)
            with self._lock:
                self._busy = max(0, self._busy - 1)
            self.kick()

    def _cleanup_partial(self, dest: Path, video_id: str) -> None:
        if not dest.exists():
            return
        from . import media_tags

        video = db.get_video(video_id) or {"id": video_id}
        stem = media_tags.library_stem(video, video_id)
        patterns = [f"{video_id} - *", f"{stem}.*"]
        for pattern in patterns:
            for path in dest.glob(pattern):
                if path.suffix in {".part", ".ytdl", ".temp"} or path.name.endswith(".part"):
                    try:
                        path.unlink()
                    except OSError:
                        pass


manager = DownloadManager()

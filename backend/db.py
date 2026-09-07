from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DEFAULT_SETTINGS, db_path, file_matches_quality, new_sync_secrets


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or db_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _init(self) -> None:
        with self._lock, self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS playlists (
                    id TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    title TEXT,
                    uploader TEXT,
                    thumbnail TEXT,
                    description TEXT,
                    video_count INTEGER DEFAULT 0,
                    added_at TEXT,
                    last_synced TEXT,
                    status TEXT DEFAULT 'idle',
                    error TEXT
                );

                CREATE TABLE IF NOT EXISTS videos (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    channel TEXT,
                    channel_id TEXT,
                    duration INTEGER,
                    thumbnail TEXT,
                    url TEXT,
                    upload_date TEXT,
                    description TEXT,
                    availability TEXT,
                    track TEXT,
                    artist TEXT,
                    album TEXT
                );

                CREATE TABLE IF NOT EXISTS playlist_videos (
                    playlist_id TEXT NOT NULL,
                    video_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    PRIMARY KEY (playlist_id, video_id, position),
                    FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE,
                    FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_pv_video ON playlist_videos(video_id);
                CREATE INDEX IF NOT EXISTS idx_pv_playlist ON playlist_videos(playlist_id, position);

                CREATE TABLE IF NOT EXISTS downloads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT NOT NULL,
                    title TEXT,
                    status TEXT NOT NULL,
                    progress REAL DEFAULT 0,
                    eta INTEGER,
                    speed TEXT,
                    filepath TEXT,
                    filename TEXT,
                    filesize INTEGER,
                    quality TEXT,
                    error TEXT,
                    created_at TEXT,
                    updated_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_dl_video ON downloads(video_id, id);
                CREATE INDEX IF NOT EXISTS idx_dl_status ON downloads(status);

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );

                CREATE TABLE IF NOT EXISTS playlist_seen (
                    playlist_id TEXT NOT NULL,
                    video_id TEXT NOT NULL,
                    PRIMARY KEY (playlist_id, video_id)
                );
                """
            )
            for key, value in DEFAULT_SETTINGS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                    (key, value),
                )
            cols = {row[1] for row in conn.execute("PRAGMA table_info(videos)").fetchall()}
            for name, decl in (("track", "TEXT"), ("artist", "TEXT"), ("album", "TEXT")):
                if name not in cols:
                    conn.execute(f"ALTER TABLE videos ADD COLUMN {name} {decl}")
            dl_cols = {row[1] for row in conn.execute("PRAGMA table_info(downloads)").fetchall()}
            for name, decl in (
                ("phase", "TEXT"),
                ("downloaded_bytes", "INTEGER"),
                ("total_bytes", "INTEGER"),
            ):
                if name not in dl_cols:
                    conn.execute(f"ALTER TABLE downloads ADD COLUMN {name} {decl}")
            if self._get_setting(conn, "quality") == "1080p":
                conn.execute(
                    "UPDATE settings SET value = ? WHERE key = 'quality'",
                    ("audio-opus",),
                )
            token = self._get_setting(conn, "sync_token")
            pin = self._get_setting(conn, "sync_pin")
            if not token or not pin:
                new_token, new_pin = new_sync_secrets()
                conn.execute(
                    "UPDATE settings SET value = ? WHERE key = 'sync_token'", (new_token,)
                )
                conn.execute(
                    "UPDATE settings SET value = ? WHERE key = 'sync_pin'", (new_pin,)
                )

    def _get_setting(self, conn: sqlite3.Connection, key: str) -> str:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else ""

    def settings(self) -> dict[str, str]:
        with self._lock, self.connect() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
            return {row["key"]: row["value"] for row in rows}

    def get_setting(self, key: str, default: str = "") -> str:
        with self._lock, self.connect() as conn:
            value = self._get_setting(conn, key)
            return value if value != "" else default

    def set_settings(self, values: dict[str, str]) -> dict[str, str]:
        with self._lock, self.connect() as conn:
            for key, value in values.items():
                conn.execute(
                    "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, str(value)),
                )
        return self.settings()

    def upsert_playlist(self, data: dict[str, Any]) -> dict[str, Any]:
        fields = {
            "id": data["id"],
            "url": data.get("url") or "",
            "title": data.get("title") or "Untitled playlist",
            "uploader": data.get("uploader") or "",
            "thumbnail": data.get("thumbnail"),
            "description": data.get("description") or "",
            "video_count": int(data.get("video_count") or 0),
            "added_at": data.get("added_at") or _now(),
            "last_synced": data.get("last_synced"),
            "status": data.get("status") or "idle",
            "error": data.get("error"),
        }
        with self._lock, self.connect() as conn:
            existing = conn.execute(
                "SELECT added_at FROM playlists WHERE id = ?", (fields["id"],)
            ).fetchone()
            if existing:
                fields["added_at"] = existing["added_at"]
            conn.execute(
                """
                INSERT INTO playlists(id, url, title, uploader, thumbnail, description, video_count, added_at, last_synced, status, error)
                VALUES (:id, :url, :title, :uploader, :thumbnail, :description, :video_count, :added_at, :last_synced, :status, :error)
                ON CONFLICT(id) DO UPDATE SET
                    url = excluded.url,
                    title = excluded.title,
                    uploader = excluded.uploader,
                    thumbnail = excluded.thumbnail,
                    description = excluded.description,
                    video_count = excluded.video_count,
                    last_synced = excluded.last_synced,
                    status = excluded.status,
                    error = excluded.error
                """,
                fields,
            )
        return self.get_playlist(fields["id"])  # type: ignore[return-value]

    def set_playlist_status(self, playlist_id: str, status: str, error: str | None = None) -> None:
        with self._lock, self.connect() as conn:
            conn.execute(
                "UPDATE playlists SET status = ?, error = ? WHERE id = ?",
                (status, error, playlist_id),
            )

    def replace_playlist_videos(self, playlist_id: str, videos: list[dict[str, Any]]) -> None:
        with self._lock, self.connect() as conn:
            conn.execute("DELETE FROM playlist_videos WHERE playlist_id = ?", (playlist_id,))
            for video in videos:
                row = {
                    "id": video["id"],
                    "title": video.get("title") or video["id"],
                    "channel": video.get("channel") or "",
                    "channel_id": video.get("channel_id"),
                    "duration": video.get("duration"),
                    "thumbnail": video.get("thumbnail"),
                    "url": video.get("url") or "",
                    "upload_date": video.get("upload_date"),
                    "description": video.get("description") or "",
                    "availability": video.get("availability"),
                    "track": video.get("track") or "",
                    "artist": video.get("artist") or "",
                    "album": video.get("album") or "",
                }
                conn.execute(
                    """
                    INSERT INTO videos(id, title, channel, channel_id, duration, thumbnail, url, upload_date, description, availability, track, artist, album)
                    VALUES (:id, :title, :channel, :channel_id, :duration, :thumbnail, :url, :upload_date, :description, :availability, :track, :artist, :album)
                    ON CONFLICT(id) DO UPDATE SET
                        title = excluded.title,
                        channel = excluded.channel,
                        channel_id = COALESCE(excluded.channel_id, videos.channel_id),
                        duration = COALESCE(excluded.duration, videos.duration),
                        thumbnail = COALESCE(excluded.thumbnail, videos.thumbnail),
                        url = excluded.url,
                        upload_date = COALESCE(excluded.upload_date, videos.upload_date),
                        description = COALESCE(excluded.description, videos.description),
                        availability = excluded.availability,
                        track = CASE WHEN excluded.track != '' THEN excluded.track ELSE videos.track END,
                        artist = CASE WHEN excluded.artist != '' THEN excluded.artist ELSE videos.artist END,
                        album = CASE WHEN excluded.album != '' THEN excluded.album ELSE videos.album END
                    """,
                    row,
                )
                conn.execute(
                    """
                    INSERT INTO playlist_videos(playlist_id, video_id, position)
                    VALUES (?, ?, ?)
                    """,
                    (playlist_id, video["id"], video["position"]),
                )
            conn.execute(
                "UPDATE playlists SET video_count = ?, last_synced = ?, status = 'idle', error = NULL WHERE id = ?",
                (len(videos), _now(), playlist_id),
            )
            conn.execute(
                """
                DELETE FROM videos
                WHERE id NOT IN (SELECT DISTINCT video_id FROM playlist_videos)
                  AND id NOT IN (SELECT video_id FROM downloads WHERE status = 'done')
                """
            )

    def list_playlists(self) -> list[dict[str, Any]]:
        with self._lock, self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM playlists ORDER BY added_at DESC"
            ).fetchall()
            return [dict(row) for row in rows]

    def get_playlist(self, playlist_id: str) -> dict[str, Any] | None:
        with self._lock, self.connect() as conn:
            row = conn.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
            return dict(row) if row else None

    def delete_playlist(self, playlist_id: str) -> None:
        with self._lock, self.connect() as conn:
            conn.execute("DELETE FROM playlist_videos WHERE playlist_id = ?", (playlist_id,))
            conn.execute("DELETE FROM playlist_seen WHERE playlist_id = ?", (playlist_id,))
            conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))

    def playlist_video_ids(self, playlist_id: str) -> set[str]:
        with self._lock, self.connect() as conn:
            rows = conn.execute(
                "SELECT video_id FROM playlist_videos WHERE playlist_id = ?",
                (playlist_id,),
            ).fetchall()
            return {row["video_id"] for row in rows}

    def all_seen_ids(self) -> dict[str, set[str]]:
        with self._lock, self.connect() as conn:
            rows = conn.execute("SELECT playlist_id, video_id FROM playlist_seen").fetchall()
            out: dict[str, set[str]] = {}
            for row in rows:
                out.setdefault(row["playlist_id"], set()).add(row["video_id"])
            return out

    def has_seen_snapshot(self) -> bool:
        with self._lock, self.connect() as conn:
            row = conn.execute("SELECT 1 FROM playlist_seen LIMIT 1").fetchone()
            return bool(row)

    def replace_seen(self, playlist_id: str, video_ids: list[str]) -> None:
        with self._lock, self.connect() as conn:
            conn.execute("DELETE FROM playlist_seen WHERE playlist_id = ?", (playlist_id,))
            conn.executemany(
                "INSERT OR IGNORE INTO playlist_seen(playlist_id, video_id) VALUES (?, ?)",
                [(playlist_id, vid) for vid in video_ids],
            )

    def playlist_videos(self, playlist_id: str) -> list[dict[str, Any]]:
        with self._lock, self.connect() as conn:
            rows = conn.execute(
                """
                SELECT v.*, pv.position, pv.playlist_id
                FROM playlist_videos pv
                JOIN videos v ON v.id = pv.video_id
                WHERE pv.playlist_id = ?
                ORDER BY pv.position ASC
                """,
                (playlist_id,),
            ).fetchall()
            videos = [dict(row) for row in rows]
            videos = self._attach_playlists(conn, videos)
            videos = self._attach_downloads(conn, videos)
            return self._attach_songs(videos)

    def all_videos(self, query: str = "") -> list[dict[str, Any]]:
        sql = """
            SELECT v.*, GROUP_CONCAT(pv.playlist_id, '|||') AS playlist_ids
            FROM videos v
            JOIN playlist_videos pv ON pv.video_id = v.id
        """
        params: list[Any] = []
        if query:
            sql += " WHERE v.title LIKE ? OR v.channel LIKE ? OR v.id LIKE ?"
            like = f"%{query}%"
            params.extend([like, like, like])
        sql += " GROUP BY v.id ORDER BY v.title COLLATE NOCASE"
        with self._lock, self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            videos = [dict(row) for row in rows]
            playlists = {
                row["id"]: row["title"]
                for row in conn.execute("SELECT id, title FROM playlists").fetchall()
            }
            for video in videos:
                ids = [pid for pid in (video.pop("playlist_ids") or "").split("|||") if pid]
                video["playlists"] = [
                    {"id": pid, "title": playlists.get(pid, pid)} for pid in dict.fromkeys(ids)
                ]
            videos = self._attach_downloads(conn, videos)
            return self._attach_songs(videos)

    def get_video(self, video_id: str) -> dict[str, Any] | None:
        with self._lock, self.connect() as conn:
            row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
            if not row:
                return None
            video = dict(row)
            playlists = conn.execute(
                """
                SELECT p.id, p.title, pv.position
                FROM playlist_videos pv
                JOIN playlists p ON p.id = pv.playlist_id
                WHERE pv.video_id = ?
                ORDER BY p.title
                """,
                (video_id,),
            ).fetchall()
            video["playlists"] = [dict(p) for p in playlists]
            return self._attach_downloads(conn, [video])[0]

    def _attach_playlists(
        self, conn: sqlite3.Connection, videos: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not videos:
            return videos
        titles = {
            row["id"]: row["title"]
            for row in conn.execute("SELECT id, title FROM playlists").fetchall()
        }
        ids = [video["id"] for video in videos]
        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT playlist_id, video_id FROM playlist_videos WHERE video_id IN ({placeholders})",
            ids,
        ).fetchall()
        by_video: dict[str, list[dict[str, str]]] = {}
        for row in rows:
            bucket = by_video.setdefault(row["video_id"], [])
            item = {"id": row["playlist_id"], "title": titles.get(row["playlist_id"], row["playlist_id"])}
            if item["id"] not in {existing["id"] for existing in bucket}:
                bucket.append(item)
        for video in videos:
            video["playlists"] = by_video.get(video["id"], video.get("playlists") or [])
        return videos

    def _attach_downloads(
        self, conn: sqlite3.Connection, videos: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not videos:
            return videos
        ids = [v["id"] for v in videos]
        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(
            f"""
            SELECT d.* FROM downloads d
            JOIN (
                SELECT video_id, MAX(id) AS max_id
                FROM downloads
                WHERE video_id IN ({placeholders})
                GROUP BY video_id
            ) latest ON latest.max_id = d.id
            """,
            ids,
        ).fetchall()
        by_id = {row["video_id"]: dict(row) for row in rows}
        for video in videos:
            video["download"] = by_id.get(video["id"])
        return videos

    def duplicates(self) -> dict[str, Any]:
        with self._lock, self.connect() as conn:
            playlists = {
                row["id"]: dict(row)
                for row in conn.execute("SELECT * FROM playlists").fetchall()
            }
            across_rows = conn.execute(
                """
                SELECT video_id, COUNT(DISTINCT playlist_id) AS playlist_count
                FROM playlist_videos
                GROUP BY video_id
                HAVING playlist_count > 1
                ORDER BY playlist_count DESC
                """
            ).fetchall()
            across: list[dict[str, Any]] = []
            for row in across_rows:
                video = conn.execute(
                    "SELECT * FROM videos WHERE id = ?", (row["video_id"],)
                ).fetchone()
                if not video:
                    continue
                membership = conn.execute(
                    """
                    SELECT p.id, p.title, pv.position
                    FROM playlist_videos pv
                    JOIN playlists p ON p.id = pv.playlist_id
                    WHERE pv.video_id = ?
                    ORDER BY p.title
                    """,
                    (row["video_id"],),
                ).fetchall()
                item = dict(video)
                item["playlists"] = [dict(m) for m in membership]
                item["playlist_count"] = row["playlist_count"]
                across.append(item)
            across = self._attach_downloads(conn, across)

            within_rows = conn.execute(
                """
                SELECT playlist_id, video_id, COUNT(*) AS copies
                FROM playlist_videos
                GROUP BY playlist_id, video_id
                HAVING copies > 1
                ORDER BY copies DESC
                """
            ).fetchall()
            within: list[dict[str, Any]] = []
            for row in within_rows:
                video = conn.execute(
                    "SELECT * FROM videos WHERE id = ?", (row["video_id"],)
                ).fetchone()
                if not video:
                    continue
                item = dict(video)
                item["copies"] = row["copies"]
                item["playlist"] = playlists.get(row["playlist_id"])
                within.append(item)
            within = self._attach_downloads(conn, within)

            catalog_rows = conn.execute(
                """
                SELECT v.* FROM videos v
                JOIN playlist_videos pv ON pv.video_id = v.id
                GROUP BY v.id
                """
            ).fetchall()
            catalog = [dict(row) for row in catalog_rows]
            catalog = self._attach_playlists(conn, catalog)
            catalog = self._attach_downloads(conn, catalog)
            from .match import group_songs, song_group_to_dict

            songs = [
                song_group_to_dict(group)
                for group in group_songs(catalog)
                if group.distinct_ids >= 2
            ]
            return {"across": across, "within": within, "songs": songs}

    def enqueue_download(self, video_id: str, title: str, quality: str) -> dict[str, Any]:
        with self._lock, self.connect() as conn:
            active = conn.execute(
                """
                SELECT * FROM downloads
                WHERE video_id = ? AND status IN ('queued', 'downloading', 'processing')
                ORDER BY id DESC LIMIT 1
                """,
                (video_id,),
            ).fetchone()
            if active:
                return dict(active)
            done = conn.execute(
                """
                SELECT * FROM downloads
                WHERE video_id = ? AND status = 'done' AND filepath IS NOT NULL
                ORDER BY id DESC LIMIT 1
                """,
                (video_id,),
            ).fetchone()
            if (
                done
                and done["filepath"]
                and Path(done["filepath"]).exists()
                and (not quality or done["quality"] == quality)
                and file_matches_quality(Path(done["filepath"]), quality)
            ):
                return dict(done)
            now = _now()
            cur = conn.execute(
                """
                INSERT INTO downloads(video_id, title, status, progress, quality, created_at, updated_at)
                VALUES (?, ?, 'queued', 0, ?, ?, ?)
                """,
                (video_id, title, quality, now, now),
            )
            row = conn.execute(
                "SELECT * FROM downloads WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
            return dict(row)

    def update_download(self, download_id: int, **fields: Any) -> dict[str, Any] | None:
        fields = {
            k: v
            for k, v in fields.items()
            if v is not None or k in {"error", "filepath", "filename", "eta", "speed", "phase"}
        }
        fields["updated_at"] = _now()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        values = list(fields.values()) + [download_id]
        with self._lock, self.connect() as conn:
            conn.execute(f"UPDATE downloads SET {assignments} WHERE id = ?", values)
            row = conn.execute("SELECT * FROM downloads WHERE id = ?", (download_id,)).fetchone()
            return dict(row) if row else None

    def get_download(self, download_id: int) -> dict[str, Any] | None:
        with self._lock, self.connect() as conn:
            row = conn.execute("SELECT * FROM downloads WHERE id = ?", (download_id,)).fetchone()
            return dict(row) if row else None

    def latest_download(self, video_id: str) -> dict[str, Any] | None:
        with self._lock, self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM downloads WHERE video_id = ? ORDER BY id DESC LIMIT 1",
                (video_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_downloads(self) -> list[dict[str, Any]]:
        with self._lock, self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM downloads ORDER BY id DESC LIMIT 400"
            ).fetchall()
            return [dict(row) for row in rows]

    def queued_downloads(self) -> list[dict[str, Any]]:
        with self._lock, self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM downloads WHERE status = 'queued' ORDER BY id ASC"
            ).fetchall()
            return [dict(row) for row in rows]

    def active_downloads(self) -> list[dict[str, Any]]:
        with self._lock, self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM downloads WHERE status IN ('queued', 'downloading', 'processing') ORDER BY id ASC"
            ).fetchall()
            return [dict(row) for row in rows]

    def downloaded_videos(self) -> list[dict[str, Any]]:
        with self._lock, self.connect() as conn:
            rows = conn.execute(
                """
                SELECT v.*, d.filepath, d.filename, d.filesize, d.quality, d.id AS download_id
                FROM videos v
                JOIN downloads d ON d.video_id = v.id
                WHERE d.status = 'done'
                  AND d.id IN (SELECT MAX(id) FROM downloads WHERE status = 'done' GROUP BY video_id)
                ORDER BY v.title COLLATE NOCASE
                """
            ).fetchall()
            videos = [dict(row) for row in rows]
            playlists = {
                row["id"]: row["title"]
                for row in conn.execute("SELECT id, title FROM playlists").fetchall()
            }
            membership = conn.execute(
                "SELECT playlist_id, video_id FROM playlist_videos"
            ).fetchall()
            by_video: dict[str, list[dict[str, str]]] = {}
            for row in membership:
                by_video.setdefault(row["video_id"], []).append(
                    {"id": row["playlist_id"], "title": playlists.get(row["playlist_id"], row["playlist_id"])}
                )
            existing: list[dict[str, Any]] = []
            for video in videos:
                path = video.get("filepath")
                if path and Path(path).exists():
                    video["playlists"] = by_video.get(video["id"], [])
                    existing.append(video)
            return existing

    def stats(self) -> dict[str, Any]:
        with self._lock, self.connect() as conn:
            playlists = conn.execute("SELECT COUNT(*) AS n FROM playlists").fetchone()["n"]
            videos = conn.execute(
                "SELECT COUNT(DISTINCT video_id) AS n FROM playlist_videos"
            ).fetchone()["n"]
            dupes = conn.execute(
                """
                SELECT COUNT(*) AS n FROM (
                    SELECT video_id FROM playlist_videos
                    GROUP BY video_id HAVING COUNT(DISTINCT playlist_id) > 1
                )
                """
            ).fetchone()["n"]
            done = conn.execute(
                "SELECT COUNT(*) AS n FROM downloads WHERE status = 'done'"
            ).fetchone()["n"]
            active = conn.execute(
                "SELECT COUNT(*) AS n FROM downloads WHERE status IN ('queued', 'downloading', 'processing')"
            ).fetchone()["n"]
            catalog = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT v.id, v.title, v.channel, v.duration, v.track, v.artist, v.availability
                    FROM videos v
                    JOIN playlist_videos pv ON pv.video_id = v.id
                    GROUP BY v.id
                    """
                ).fetchall()
            ]
            from .match import group_songs

            song_dupes = sum(1 for group in group_songs(catalog) if group.distinct_ids >= 2)
            return {
                "playlists": playlists,
                "videos": videos,
                "duplicates": dupes + song_dupes,
                "exact_duplicates": dupes,
                "song_duplicates": song_dupes,
                "downloaded": done,
                "active_downloads": active,
            }

    def _attach_songs(self, videos: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not videos:
            return videos
        from .match import group_songs

        conn = self.connect()
        try:
            catalog = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT v.id, v.title, v.channel, v.duration, v.track, v.artist, v.availability
                    FROM videos v
                    JOIN playlist_videos pv ON pv.video_id = v.id
                    GROUP BY v.id
                    """
                ).fetchall()
            ]
        finally:
            conn.close()
        meta: dict[str, tuple] = {}
        for group in group_songs(catalog):
            n = group.distinct_ids
            if n < 2:
                continue
            for item in group.videos:
                vid = item.get("id")
                if vid:
                    meta[vid] = (n, group.artist, group.title, group.reason)
        for video in videos:
            info = meta.get(video.get("id"))
            if info:
                video["song_mates"] = info[0]
                video["song_artist"] = info[1]
                video["song_title"] = info[2]
                video["song_reason"] = info[3]
            else:
                video["song_mates"] = 1
                video["song_artist"] = ""
                video["song_title"] = ""
                video["song_reason"] = ""
        return videos


db = Database()

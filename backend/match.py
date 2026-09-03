from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterable


DASH_SPLIT = re.compile(r"\s+[-–—−]+\s+")
PIPE_SPLIT = re.compile(r"\s+\|\s+")
QUOTED_TITLE = re.compile(r'^(.+?)\s+[“"\'](.+?)[”"\']$')
BY_ARTIST = re.compile(r"^(?P<track>.+?)\s+by\s+(?P<artist>.+)$", re.I)
TOPIC_CHANNEL = re.compile(r"\s*-\s*topic$", re.I)
VEVO_CHANNEL = re.compile(r"vevo$", re.I)
FEAT = re.compile(
    r"""
    \s*
    (?:
        [\(\[]\s*(?:feat\.?|ft\.?|featuring|with)\s+[^\)\]]{1,60}[\)\]]
        |
        \b(?:feat|ft|featuring)\.?\s+[^-–—|(\[]+
    )
    """,
    re.I | re.X,
)
TRACK_NO = re.compile(r"^\s*\d{1,2}\s*[\.\)\-–:]\s+")
SPACES = re.compile(r"\s+")
NON_ALNUM = re.compile(r"[^a-z0-9\s]+")
BRACKET = re.compile(r"[\(\[\{【（]([^\)\]\}】）]{0,80})[\)\]\}】）]")
LEADING_THE = re.compile(r"^the\s+")

JUNK_BRACKET = re.compile(
    r"""^(
        official(\s+(music\s+)?video|\s+audio|\s+visualizer|\s+mv)? |
        (music|lyric[s]?)\s+video |
        lyric[s]? |
        audio(\s+only)? |
        visualizer |
        mv | m/?v | pv |
        hd | hq | uhd | hdr | 4k | 8k | 1080p? | 720p | 2160p |
        explicit | clean |
        (eng(lish)?|kor(ean)?|spa(nish)?|por(tuguese)?|jp|ja|ch|cn)(\s*/\s*(eng|kor|spa))?(\s+sub(title)?s?)? |
        sub(title)?s? | cc | closed\s+captions? |
        color\s*coded(\s+lyric[s]?)? |
        youtube | vevo | topic |
        full\s+(hd|song|audio|video) |
        high\s+quality |
        uploaded |
        original(\s+song)? |
        version |
        audio\s+track
    )$""",
    re.I | re.X,
)

# Recordings that should stay in their own bucket.
VARIANT_KEEP = re.compile(
    r"\b(remix|bootleg|mashup|instrumental|karaoke|nightcore|slowed|reverb|sped\s*up|dubstep|edit)\b",
    re.I,
)

COVER_LIVE = re.compile(r"\b(cover|live|acoustic|remaster(ed)?|radio\s+edit|extended)\b", re.I)

DELETED = re.compile(r"^\[(deleted|private|unavailable) video\]$", re.I)


@dataclass
class Identity:
    video_id: str
    artist_key: str
    track_key: str
    artist_display: str
    track_display: str
    duration: int | None
    reason: str
    groupable: bool
    variant: str = ""


@dataclass
class SongGroup:
    artist: str
    title: str
    duration_min: int | None
    duration_max: int | None
    confidence: str
    reason: str
    key: str
    videos: list[dict[str, Any]] = field(default_factory=list)

    @property
    def distinct_ids(self) -> int:
        return len({v.get("id") for v in self.videos})


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.lower()


def _clean_spaces(text: str) -> str:
    return SPACES.sub(" ", text).strip()


def _artist_key(text: str) -> str:
    folded = _fold(text)
    folded = folded.replace("&", " and ")
    folded = NON_ALNUM.sub(" ", folded)
    folded = LEADING_THE.sub("", folded)
    return _clean_spaces(folded)


def _canon_artist(key: str) -> str:
    key = LEADING_THE.sub("", key or "")
    return key.replace(" ", "")


def _track_key(text: str) -> str:
    folded = _fold(text)
    folded = folded.replace("&", " and ")
    folded = FEAT.sub(" ", folded)
    folded = NON_ALNUM.sub(" ", folded)
    return _clean_spaces(folded)


def _strip_brackets(title: str) -> tuple[str, str]:
    variants: list[str] = []

    def repl(match: re.Match[str]) -> str:
        inner = match.group(1).strip()
        if VARIANT_KEEP.search(inner):
            variants.append(_clean_spaces(inner.lower()))
            return " "
        if COVER_LIVE.search(inner) or JUNK_BRACKET.match(inner.strip()):
            return " "
        if len(inner) <= 28:
            return " "
        return match.group(0)

    return _clean_spaces(BRACKET.sub(repl, title)), ", ".join(variants)


def _strip_trailing_junk(title: str) -> str:
    title = TRACK_NO.sub("", title)
    title = FEAT.sub(" ", title)
    title = COVER_LIVE.sub(" ", title)
    for phrase in (
        "official music video",
        "official video",
        "official audio",
        "official visualizer",
        "lyric video",
        "lyrics video",
        "music video",
        "with lyrics",
        "lyrics",
        "visualizer",
        "audio only",
        "full hd",
    ):
        title = re.sub(rf"\b{re.escape(phrase)}\b", " ", title, flags=re.I)
    return _clean_spaces(title)


def _artist_from_channel(channel: str) -> str:
    channel = (channel or "").strip()
    if not channel:
        return ""
    if TOPIC_CHANNEL.search(channel):
        return _clean_spaces(TOPIC_CHANNEL.sub("", channel))
    if VEVO_CHANNEL.search(channel):
        name = VEVO_CHANNEL.sub("", channel)
        name = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
        name = re.sub(r"([A-Za-z])(\d)", r"\1 \2", name)
        return _clean_spaces(name)
    return ""


def _split_artist_title(title: str) -> tuple[str, str]:
    quoted = QUOTED_TITLE.match(title)
    if quoted:
        return quoted.group(1).strip(), quoted.group(2).strip()

    by_match = BY_ARTIST.match(title)
    if by_match and len(by_match.group("artist").split()) <= 5:
        return by_match.group("artist").strip(), by_match.group("track").strip()

    parts = DASH_SPLIT.split(title, maxsplit=1)
    if len(parts) == 2 and 1 <= len(parts[0]) <= 48:
        left, right = parts[0].strip(), parts[1].strip()
        if left and right and not left.lower().startswith("http"):
            return left, right

    pipes = PIPE_SPLIT.split(title, maxsplit=1)
    if len(pipes) == 2 and len(pipes[0].split()) <= 6:
        return pipes[0].strip(), pipes[1].strip()

    return "", title


def _meta_artist(video: dict[str, Any]) -> str:
    raw = video.get("artist") or ""
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
        if isinstance(raw, dict):
            raw = raw.get("name") or ""
    artists = video.get("artists")
    if not raw and isinstance(artists, list) and artists:
        first = artists[0]
        raw = first.get("name") if isinstance(first, dict) else first
    return str(raw or "").strip()


def identity_for(video: dict[str, Any]) -> Identity:
    vid = str(video.get("id") or "")
    title_raw = (video.get("title") or "").strip()
    channel = (video.get("channel") or "").strip()
    duration = video.get("duration")
    try:
        duration = int(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration = None

    if not title_raw or DELETED.match(title_raw):
        return Identity(vid, "", "", "", title_raw, duration, "skipped", False)

    stripped, variant = _strip_brackets(title_raw)
    stripped = _strip_trailing_junk(stripped)

    meta_artist = _meta_artist(video)
    meta_track = str(video.get("track") or "").strip()
    channel_artist = _artist_from_channel(channel)

    parsed_artist, parsed_track = _split_artist_title(stripped)
    artist = meta_artist or ""
    track = meta_track or stripped
    reason = "title"

    if meta_artist and meta_track:
        artist, track = meta_artist, meta_track
        reason = "youtube music tags"
    elif parsed_artist and parsed_track:
        if channel_artist and _canon_artist(_artist_key(channel_artist)) == _canon_artist(
            _artist_key(parsed_track)
        ):
            artist = channel_artist
            track = parsed_artist
            reason = "title + channel artist"
        else:
            artist = parsed_artist
            track = parsed_track
            reason = "artist – title in the video name"
    elif channel_artist:
        artist = channel_artist
        track = stripped
        reason = "YouTube Topic / VEVO channel"

    artist_key = _artist_key(artist)
    track_key = _track_key(track)
    tokens = track_key.split()
    distinctive = len(tokens) >= 4 or (len(tokens) >= 3 and len(track_key) >= 18)
    groupable = bool(track_key) and (bool(artist_key) or distinctive)

    return Identity(
        video_id=vid,
        artist_key=artist_key,
        track_key=track_key,
        artist_display=artist or "",
        track_display=track or title_raw,
        duration=duration,
        reason=reason,
        groupable=groupable,
        variant=variant,
    )


def _duration_close(a: int | None, b: int | None) -> bool:
    if a is None or b is None:
        return True
    mean = (a + b) / 2 or 1
    tolerance = min(25, max(6, int(mean * 0.08)))
    return abs(a - b) <= tolerance


def _similar(a: str, b: str, threshold: float) -> bool:
    if not a or not b:
        return False
    if a == b or _canon_artist(a) == _canon_artist(b):
        return True
    return SequenceMatcher(None, a, b).ratio() >= threshold or SequenceMatcher(
        None, _canon_artist(a), _canon_artist(b)
    ).ratio() >= threshold


def _blocking_key(ident: Identity) -> str | None:
    if not ident.groupable:
        return None
    if ident.variant:
        variant = _track_key(ident.variant)
    else:
        variant = ""
    if ident.artist_key:
        return f"a:{_canon_artist(ident.artist_key)}::{ident.track_key}::{variant}"
    bucket = ident.duration // 5 if ident.duration is not None else "na"
    return f"t:{ident.track_key}::{bucket}::{variant}"


class _UF:
    def __init__(self, n: int) -> None:
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def group_songs(
    videos: Iterable[dict[str, Any]],
    *,
    include_singletons: bool = False,
    min_distinct_ids: int = 2,
) -> list[SongGroup]:
    items = list(videos)
    if not items:
        return []
    idents = [identity_for(v) for v in items]
    uf = _UF(len(items))
    confidence: dict[int, str] = {}
    reasons: dict[int, str] = {}

    buckets: dict[str, list[int]] = {}
    for i, ident in enumerate(idents):
        key = _blocking_key(ident)
        if not key:
            continue
        buckets.setdefault(key, []).append(i)
    for members in buckets.values():
        if len(members) < 2:
            continue
        for a, i in enumerate(members):
            for j in members[a + 1 :]:
                if not _duration_close(idents[i].duration, idents[j].duration):
                    continue
                uf.union(i, j)
                root = uf.find(i)
                confidence[root] = "high"
                reasons[root] = idents[i].reason

    # Second pass: fuzzy artist+title inside a duration window.
    timed: dict[int, list[int]] = {}
    for i, ident in enumerate(idents):
        if not ident.groupable or not ident.artist_key:
            continue
        bucket = (ident.duration or 0) // 5
        timed.setdefault(bucket, []).append(i)
        timed.setdefault(bucket - 1, []).append(i)
        timed.setdefault(bucket + 1, []).append(i)

    seen_pairs: set[tuple[int, int]] = set()
    for members in timed.values():
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                i, j = members[a], members[b]
                pair = (min(i, j), max(i, j))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                if uf.find(i) == uf.find(j):
                    continue
                left, right = idents[i], idents[j]
                if left.variant != right.variant and (left.variant or right.variant):
                    continue
                if not _duration_close(left.duration, right.duration):
                    continue
                if not _similar(left.artist_key, right.artist_key, 0.86):
                    continue
                if not _similar(left.track_key, right.track_key, 0.88):
                    continue
                uf.union(i, j)
                root = uf.find(i)
                confidence[root] = confidence.get(root, "medium")
                if confidence[root] != "high":
                    confidence[root] = "medium"
                reasons[root] = "close artist and title · similar length"

    # Title-only upload (lyrics channel, meme reupload) joins a tagged artist+title.
    by_track: dict[str, list[int]] = {}
    for i, ident in enumerate(idents):
        if ident.track_key:
            by_track.setdefault(ident.track_key, []).append(i)
    for members in by_track.values():
        if len(members) < 2:
            continue
        for a, i in enumerate(members):
            for j in members[a + 1 :]:
                if uf.find(i) == uf.find(j):
                    continue
                left, right = idents[i], idents[j]
                if left.artist_key and right.artist_key:
                    continue
                if not _duration_close(left.duration, right.duration):
                    continue
                if left.variant != right.variant and (left.variant or right.variant):
                    continue
                orphan = left if not left.artist_key else right
                tokens = orphan.track_key.split()
                if not (len(tokens) >= 4 or (len(tokens) >= 2 and len(orphan.track_key) >= 12)):
                    continue
                uf.union(i, j)
                root = uf.find(i)
                if confidence.get(root) != "high":
                    confidence[root] = "medium"
                reasons[root] = "same title and length"

    components: dict[int, list[int]] = {}
    for i in range(len(items)):
        components.setdefault(uf.find(i), []).append(i)

    groups: list[SongGroup] = []
    for root, idxs in components.items():
        vids = [items[i] for i in idxs]
        distinct = {v.get("id") for v in vids}
        if not include_singletons and len(distinct) < min_distinct_ids:
            continue
        if include_singletons and len(idxs) == 1:
            ident = idents[idxs[0]]
            groups.append(
                SongGroup(
                    artist=ident.artist_display,
                    title=ident.track_display,
                    duration_min=ident.duration,
                    duration_max=ident.duration,
                    confidence="high",
                    reason=ident.reason,
                    key=_blocking_key(ident) or f"id:{ident.video_id}",
                    videos=vids,
                )
            )
            continue
        if len(distinct) < min_distinct_ids:
            continue
        sample = [idents[i] for i in idxs]
        with_artist = next((s for s in sample if s.artist_display), sample[0])
        durs = [s.duration for s in sample if s.duration is not None]
        groups.append(
            SongGroup(
                artist=with_artist.artist_display,
                title=with_artist.track_display,
                duration_min=min(durs) if durs else None,
                duration_max=max(durs) if durs else None,
                confidence=confidence.get(root, "high"),
                reason=reasons.get(root, with_artist.reason),
                key=_blocking_key(with_artist) or f"cluster:{root}",
                videos=vids,
            )
        )

    groups.sort(key=lambda g: (-len(g.videos), (g.artist or "").lower(), (g.title or "").lower()))
    return groups


def pick_canonical(videos: list[dict[str, Any]]) -> dict[str, Any]:
    def score(video: dict[str, Any]) -> tuple:
        download = video.get("download") or {}
        saved = download.get("status") == "done" and download.get("filepath")
        channel = (video.get("channel") or "").lower()
        title = (video.get("title") or "").lower()
        official = ("- topic" in channel) or channel.endswith("vevo") or "official" in title
        return (0 if saved else 1, 0 if official else 1, len(title))

    return sorted(videos, key=score)[0]


def annotate_songs(videos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups = group_songs(videos, min_distinct_ids=2)
    mates: dict[str, int] = {}
    meta: dict[str, SongGroup] = {}
    for group in groups:
        for video in group.videos:
            vid = video.get("id")
            if not vid:
                continue
            mates[vid] = len({v.get("id") for v in group.videos})
            meta[vid] = group
    for video in videos:
        vid = video.get("id")
        group = meta.get(vid) if vid else None
        video["song_mates"] = mates.get(vid, 1) if vid else 1
        video["song_artist"] = group.artist if group else ""
        video["song_title"] = group.title if group else ""
        video["song_reason"] = group.reason if group else ""
    return videos


YT_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def unusable_reason(video: dict[str, Any]) -> str | None:
    vid = str(video.get("id") or "")
    if not vid:
        return "not a YouTube video"
    title = video.get("title") or ""
    if DELETED.match(title) or "deleted video" in title.lower() or "private video" in title.lower():
        return "unavailable"
    availability = (video.get("availability") or "").lower()
    if availability in {"private", "unavailable", "needs_auth"}:
        return "unavailable"
    return None


def dedupe_ordered(
    videos: list[dict[str, Any]], mode: str = "song"
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep the first occurrence; drop later copies of the same video or song."""
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_keys: set[str] = set()
    key_by_id: dict[str, str] = {}
    if mode == "song":
        usable = [video for video in videos if not unusable_reason(video)]
        for group in group_songs(usable, include_singletons=True, min_distinct_ids=1):
            for video in group.videos:
                vid = video.get("id")
                if vid:
                    key_by_id[vid] = group.key
    for video in videos:
        reason = unusable_reason(video)
        if reason:
            dropped.append({**video, "drop_reason": reason})
            continue
        vid = video["id"]
        if vid in seen_ids:
            dropped.append({**video, "drop_reason": "same video"})
            continue
        if mode == "song":
            key = key_by_id.get(vid) or f"id:{vid}"
            if key in seen_keys:
                dropped.append({**video, "drop_reason": "same song"})
                seen_ids.add(vid)
                continue
            seen_keys.add(key)
        seen_ids.add(vid)
        kept.append(video)
    return kept, dropped


def _unique_keyed(
    videos: list[dict[str, Any]], mode: str, key_by_id: dict[str, str]
) -> list[tuple[str, dict[str, Any]]]:
    seen: set[str] = set()
    out: list[tuple[str, dict[str, Any]]] = []
    for video in videos:
        if unusable_reason(video):
            continue
        vid = video.get("id")
        if not vid:
            continue
        key = vid if mode == "video" else key_by_id.get(vid) or f"id:{vid}"
        if key in seen:
            continue
        seen.add(key)
        out.append((key, video))
    return out


def compare_playlists(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
    mode: str = "song",
) -> dict[str, Any]:
    """Set difference of two playlists by video ID or same-song identity."""
    if mode not in {"video", "song"}:
        mode = "song"
    combined: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for video in left + right:
        vid = video.get("id")
        if not vid or vid in seen_ids:
            continue
        seen_ids.add(vid)
        combined.append(video)
    key_by_id: dict[str, str] = {}
    if mode == "song":
        usable = [video for video in combined if not unusable_reason(video)]
        for group in group_songs(usable, include_singletons=True, min_distinct_ids=1):
            for video in group.videos:
                vid = video.get("id")
                if vid:
                    key_by_id[vid] = group.key
    left_items = _unique_keyed(left, mode, key_by_id)
    right_items = _unique_keyed(right, mode, key_by_id)
    left_map = {key: video for key, video in left_items}
    right_map = {key: video for key, video in right_items}
    only_left = [video for key, video in left_items if key not in right_map]
    only_right = [video for key, video in right_items if key not in left_map]
    both = [
        {"key": key, "left": video, "right": right_map[key]}
        for key, video in left_items
        if key in right_map
    ]
    return {
        "only_left": only_left,
        "only_right": only_right,
        "both": both,
        "counts": {
            "left": len(left_items),
            "right": len(right_items),
            "only_left": len(only_left),
            "only_right": len(only_right),
            "both": len(both),
        },
    }


def unique_song_ids(videos: list[dict[str, Any]]) -> list[str]:
    groups = group_songs(videos, include_singletons=True, min_distinct_ids=1)
    seen: set[str] = set()
    picked: list[str] = []
    for group in groups:
        choice = pick_canonical(group.videos)
        cid = choice.get("id")
        if cid and cid not in seen:
            seen.add(cid)
            picked.append(cid)
        for video in group.videos:
            vid = video.get("id")
            if vid:
                seen.add(vid)
    for video in videos:
        vid = video.get("id")
        if vid and vid not in seen:
            seen.add(vid)
            picked.append(vid)
    return picked


def song_group_to_dict(group: SongGroup) -> dict[str, Any]:
    first = group.videos[0] if group.videos else {}
    title = group.title or first.get("title") or "Unknown track"
    artist = group.artist or first.get("channel") or ""
    return {
        "artist": artist,
        "title": title,
        "duration_min": group.duration_min,
        "duration_max": group.duration_max,
        "confidence": group.confidence,
        "reason": group.reason,
        "key": group.key,
        "count": group.distinct_ids,
        "videos": group.videos,
    }

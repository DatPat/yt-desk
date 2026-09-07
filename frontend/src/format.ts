export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || Number.isNaN(seconds)) return "—";
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h) return `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  return `${m}:${String(sec).padStart(2, "0")}`;
}

export function formatBytes(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v < 10 && i > 0 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
}

export function formatEta(seconds: number | null | undefined): string {
  if (seconds == null || Number.isNaN(seconds)) return "";
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function formatWhen(iso: string | null | undefined): string {
  if (!iso) return "never";
  const start = Date.parse(iso);
  if (Number.isNaN(start)) return iso;
  const delta = Math.max(0, Date.now() - start);
  const mins = Math.floor(delta / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 14) return `${days}d ago`;
  return new Date(start).toLocaleString();
}

export function formatElapsed(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "";
  const start = Date.parse(iso);
  if (Number.isNaN(start)) return "";
  return formatEta(Math.max(0, (now - start) / 1000));
}

export function downloadPhase(job: {
  status?: string;
  phase?: string | null;
  progress?: number | null;
  error?: string | null;
}): string {
  if (job.phase) return job.phase;
  switch (job.status) {
    case "queued":
      return "Waiting in queue";
    case "downloading":
      return job.progress ? `Downloading ${Math.round(job.progress)}%` : "Starting…";
    case "processing":
      return "Converting and tagging";
    case "done":
      return "Saved";
    case "error":
      return job.error || "Failed";
    case "cancelled":
      return "Cancelled";
    default:
      return job.status || "";
  }
}

export function isUnavailable(video: { title?: string; availability?: string | null }): boolean {
  const title = (video.title || "").toLowerCase();
  if (title.includes("[deleted video]") || title.includes("[private video]") || title.includes("[unavailable]")) {
    return true;
  }
  const a = (video.availability || "").toLowerCase();
  return ["private", "unavailable", "needs_auth", "premium_only"].includes(a);
}

export function isDuplicate(video: { playlists?: { id: string }[]; song_mates?: number }): boolean {
  return (video.playlists || []).length > 1 || (video.song_mates || 0) > 1;
}

export function isDownloaded(video: { download?: { status?: string; filepath?: string | null } | null; filepath?: string | null }): boolean {
  if (video.filepath) return true;
  return video.download?.status === "done" && Boolean(video.download.filepath);
}

export function qualityLabel(q: string): string {
  const labels: Record<string, string> = {
    "audio-opus": "Audio (Opus) — cover + tags",
    "audio-m4a": "Audio (m4a / AAC)",
    "audio-mp3": "Audio (mp3)",
    "1080p": "1080p video",
    "720p": "720p video",
    "480p": "480p video",
    best: "Best video",
  };
  return labels[q] || q;
}

export function isAudioFile(video: {
  download?: { filename?: string | null; filepath?: string | null } | null;
  filepath?: string | null;
}): boolean {
  const name = `${video.download?.filename || ""} ${video.download?.filepath || ""} ${video.filepath || ""}`;
  return /\.(opus|ogg|m4a|mp3|flac)$/i.test(name);
}

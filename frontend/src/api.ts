import type {
  CompareReport,
  DuplicateReport,
  Health,
  MobileLibrary,
  Playlist,
  Settings,
  Stats,
  SyncInfo,
  Video,
  Download,
  YoutubeJob,
  YoutubePreview,
  YoutubeStatus,
  UsbStatus,
  UsbJob,
  AndroidStatus,
  AndroidOrphans,
  ChangesReport,
} from "./types";

async function readError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    const detail = body.detail ?? body.error;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
    return JSON.stringify(body);
  } catch {
    return res.statusText;
  }
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<T>;
}

export const api = {
  health: () => fetch("/api/health").then((r) => json<Health>(r)),
  stats: () => fetch("/api/stats").then((r) => json<Stats>(r)),
  settings: () => fetch("/api/settings").then((r) => json<Settings>(r)),
  saveSettings: (patch: Record<string, string>) =>
    fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }).then((r) => json<Settings>(r)),
  pickFolder: () =>
    fetch("/api/settings/pick-folder", { method: "POST" }).then((r) => json<{ path: string }>(r)),
  openFolder: () => fetch("/api/settings/open-folder", { method: "POST" }).then((r) => json(r)),
  playlists: () => fetch("/api/playlists").then((r) => json<Playlist[]>(r)),
  addPlaylists: (urls: string) =>
    fetch("/api/playlists", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: urls }),
    }).then((r) => json<{ playlists: Playlist[] }>(r)),
  playlist: (id: string) => fetch(`/api/playlists/${encodeURIComponent(id)}`).then((r) => json<Playlist & { videos: Video[] }>(r)),
  deletePlaylist: (id: string) =>
    fetch(`/api/playlists/${encodeURIComponent(id)}`, { method: "DELETE" }).then((r) => json(r)),
  refreshPlaylist: (id: string) =>
    fetch(`/api/playlists/${encodeURIComponent(id)}/refresh`, { method: "POST" }).then((r) => json<Playlist>(r)),
  refreshAllPlaylists: () =>
    fetch("/api/playlists/refresh-all", { method: "POST" }).then((r) => json<{ ok: boolean; count: number }>(r)),
  playlistChanges: () => fetch("/api/playlists/changes").then((r) => json<ChangesReport>(r)),
  checkNewPlaylistItems: () =>
    fetch("/api/playlists/check-new", { method: "POST" }).then((r) => json<ChangesReport>(r)),
  videos: (q = "") => fetch(`/api/videos?q=${encodeURIComponent(q)}`).then((r) => json<Video[]>(r)),
  duplicates: () => fetch("/api/duplicates").then((r) => json<DuplicateReport>(r)),
  comparePlaylists: (body: { left: string; right: string; mode: string }) =>
    fetch("/api/playlists/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<CompareReport>(r)),
  downloads: () => fetch("/api/downloads").then((r) => json<Download[]>(r)),
  startDownloads: (body: {
    video_ids?: string[];
    playlist_id?: string;
    quality?: string;
    unique_only?: boolean;
    unique_songs?: boolean;
  }) =>
    fetch("/api/downloads", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<{ jobs: Download[] }>(r)),
  cancelDownload: (id: number) =>
    fetch(`/api/downloads/${id}/cancel`, { method: "POST" }).then((r) => json<Download>(r)),
  retryDownload: (id: number) =>
    fetch(`/api/downloads/${id}/retry`, { method: "POST" }).then((r) => json<Download>(r)),
  revealDownload: (id: number) =>
    fetch(`/api/downloads/${id}/reveal`, { method: "POST" }).then((r) => json(r)),
  sync: () => fetch("/api/sync").then((r) => json<SyncInfo>(r)),
  rotateSync: () => fetch("/api/sync/rotate", { method: "POST" }).then((r) => json<SyncInfo>(r)),
  pair: (pin: string) =>
    fetch("/api/mobile/pair", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pin }),
    }).then((r) => json<{ token: string }>(r)),
  mobileLibrary: (token: string) =>
    fetch("/api/mobile/library", { headers: { "X-YTDesk-Token": token } }).then((r) =>
      json<MobileLibrary>(r),
    ),
  youtubeStatus: () => fetch("/api/youtube/status").then((r) => json<YoutubeStatus>(r)),
  youtubeConnect: () => fetch("/api/youtube/connect", { method: "POST" }).then((r) => json<{ url: string }>(r)),
  youtubeDisconnect: () => fetch("/api/youtube/disconnect", { method: "POST" }).then((r) => json(r)),
  youtubeMine: () => fetch("/api/youtube/mine").then((r) => json<{ playlists: { id: string; title: string; item_count?: number }[] }>(r)),
  youtubePreview: (body: { playlist_ids: string[]; mode: string }) =>
    fetch("/api/youtube/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<YoutubePreview>(r)),
  youtubeMerge: (body: {
    playlist_ids: string[];
    mode: string;
    title: string;
    privacy: string;
    destination_id?: string | null;
    add_to_library?: boolean;
  }) =>
    fetch("/api/youtube/merge", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<YoutubeJob>(r)),
  youtubeClean: (body: { playlist_id: string; mode: string }) =>
    fetch("/api/youtube/clean", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<YoutubeJob>(r)),
  youtubeCancel: () => fetch("/api/youtube/cancel", { method: "POST" }).then((r) => json<YoutubeJob>(r)),
  usbStatus: () => fetch("/api/usb").then((r) => json<UsbStatus>(r)),
  usbSync: (body: { udid?: string; app?: string }) =>
    fetch("/api/usb/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<UsbJob>(r)),
  usbCancel: () => fetch("/api/usb/cancel", { method: "POST" }).then((r) => json<UsbJob>(r)),
  androidStatus: () => fetch("/api/android").then((r) => json<AndroidStatus>(r)),
  androidSync: (body: { id?: string; kind?: string }) =>
    fetch("/api/android/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<UsbJob>(r)),
  androidCancel: () => fetch("/api/android/cancel", { method: "POST" }).then((r) => json<UsbJob>(r)),
  androidOrphans: () => fetch("/api/android/orphans").then((r) => json<AndroidOrphans>(r)),
  androidOrphansScan: (body: { id?: string; kind?: string }) =>
    fetch("/api/android/orphans/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<AndroidOrphans>(r)),
  androidOrphansDelete: (body: { id?: string; kind?: string; paths: string[] }) =>
    fetch("/api/android/orphans/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<AndroidOrphans>(r)),
  androidOrphansCancel: () =>
    fetch("/api/android/orphans/cancel", { method: "POST" }).then((r) => json<AndroidOrphans>(r)),
};

export function mediaUrl(videoId: string, token?: string): string {
  const t = token ? `?t=${encodeURIComponent(token)}` : "";
  return `/media/${encodeURIComponent(videoId)}${t}`;
}

export function m3uUrl(token: string, playlistId?: string): string {
  const params = new URLSearchParams({ t: token });
  if (playlistId) params.set("playlist_id", playlistId);
  return `/api/mobile/playlist.m3u?${params.toString()}`;
}

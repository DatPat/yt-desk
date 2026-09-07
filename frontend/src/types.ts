export type Playlist = {
  id: string;
  url: string;
  title: string;
  uploader: string;
  thumbnail: string | null;
  description: string;
  video_count: number;
  added_at: string | null;
  last_synced: string | null;
  status: "idle" | "fetching" | "error";
  error: string | null;
};

export type Download = {
  id: number;
  video_id: string;
  title: string | null;
  status: "queued" | "downloading" | "processing" | "done" | "error" | "cancelled";
  progress: number;
  eta: number | null;
  speed: string | null;
  filepath: string | null;
  filename: string | null;
  filesize: number | null;
  quality: string | null;
  error: string | null;
  phase?: string | null;
  downloaded_bytes?: number | null;
  total_bytes?: number | null;
};

export type PlaylistRef = {
  id: string;
  title: string;
  position?: number;
};

export type Video = {
  id: string;
  title: string;
  channel: string;
  channel_id?: string | null;
  duration: number | null;
  thumbnail: string | null;
  url: string;
  upload_date: string | null;
  availability: string | null;
  position?: number;
  playlists?: PlaylistRef[];
  playlist_count?: number;
  copies?: number;
  playlist?: Playlist | null;
  download: Download | null;
  filepath?: string | null;
  song_mates?: number;
  song_artist?: string;
  song_title?: string;
  song_reason?: string;
  track?: string;
  artist?: string;
};

export type SongGroup = {
  artist: string;
  title: string;
  duration_min: number | null;
  duration_max: number | null;
  confidence: "high" | "medium";
  reason: string;
  key: string;
  count: number;
  videos: Video[];
};

export type DuplicateReport = {
  across: Video[];
  within: Video[];
  songs: SongGroup[];
};

export type SyncInfo = {
  enabled: boolean;
  pin: string;
  token: string;
  ips: string[];
  port: number;
  mobile_url: string;
  dav_url: string;
  dav_user: string;
  library_dir: string;
  qr_svg: string;
  urls: { ip: string; mobile_url: string; dav_url: string }[];
};

export type Settings = {
  library_dir: string;
  quality: string;
  concurrent: string;
  cookies_path: string;
  sponsorblock: string;
  sync_enabled: string;
  qualities: string[];
  yt_client_id?: string;
  yt_client_secret?: string;
};

export type YoutubeStatus = {
  configured: boolean;
  connected: boolean;
  channel: string | null;
  channel_id: string | null;
  redirect_uri: string;
  error: string | null;
  job: YoutubeJob;
};

export type YoutubeJob = {
  status: "idle" | "running" | "done" | "error" | "cancelled";
  action: string | null;
  added: number;
  skipped: number;
  failed: number;
  total: number;
  message: string;
  playlist_id: string | null;
  playlist_url: string | null;
  error: string | null;
};

export type YoutubePreview = {
  source_count: number;
  kept: number;
  dropped: number;
  reasons: Record<string, number>;
  quota_estimate: number;
  quota_warning: boolean;
  over_limit: boolean;
  items: { id: string; title: string; channel: string; duration: number | null }[];
  dropped_examples: { id: string; title: string; channel: string; reason: string }[];
};

export type Health = {
  ok: boolean;
  yt_dlp: string | null;
  ffmpeg: boolean;
  data_dir: string;
  platform?: string;
  is_mac?: boolean;
  is_windows?: boolean;
};

export type UsbDevice = {
  udid: string;
  name: string;
  product: string;
  paired: boolean;
  connection: string;
  apps: string[];
  error: string | null;
};

export type UsbJob = {
  status: "idle" | "running" | "done" | "error" | "cancelled";
  copied: number;
  skipped: number;
  failed: number;
  total: number;
  message: string;
  device: string | null;
  error: string | null;
  phase?: string | null;
  current?: string | null;
  bytes_done?: number;
  bytes_total?: number;
  started_at?: string | null;
};

export type UsbStatus = {
  platform: string;
  is_mac: boolean;
  library: boolean;
  apps: { id: string; bundle: string; label: string; hint: string }[];
  devices: UsbDevice[];
  error: string | null;
  job: UsbJob;
};

export type AndroidDevice = {
  id: string;
  serial: string;
  name: string;
  kind: "adb" | "mtp";
  ready: boolean;
  error: string | null;
};

export type AndroidStatus = {
  platform: string;
  is_windows: boolean;
  adb: boolean;
  com: boolean;
  devices: AndroidDevice[];
  library_count: number;
  dest: string;
  error: string | null;
  job: UsbJob;
};

export type AndroidOrphan = {
  path: string;
  name: string;
  size: number;
};

export type AndroidOrphans = {
  status: "idle" | "scanning" | "deleting" | "done" | "error";
  message: string;
  error: string | null;
  files: AndroidOrphan[];
  deleted: number;
  failed: number;
  total: number;
  device: string | null;
};

export type Stats = {
  playlists: number;
  videos: number;
  duplicates: number;
  downloaded: number;
  active_downloads: number;
};

export type ChangeItem = {
  id: string;
  title: string;
  channel: string;
  duration: number | null;
  thumbnail: string | null;
  url: string;
  download: Download | null;
  playlist_id: string;
  playlist_title: string;
  song_artist?: string;
  song_title?: string;
};

export type ChangesReport = {
  status: "idle" | "running" | "done" | "error";
  message: string;
  error: string | null;
  checked: number;
  total: number;
  added: number;
  removed: number;
  previous_at: string | null;
  checked_at: string | null;
  baseline: boolean;
  playlists: {
    id: string;
    title: string;
    error: string | null;
    added: number;
    removed: number;
    added_items: ChangeItem[];
    removed_items: ChangeItem[];
  }[];
  added_items: ChangeItem[];
  removed_items: ChangeItem[];
};

export type CompareReport = {
  left: { id: string; title: string; count: number };
  right: { id: string; title: string; count: number };
  mode: "song" | "video";
  counts: {
    left: number;
    right: number;
    only_left: number;
    only_right: number;
    both: number;
  };
  only_left: Video[];
  only_right: Video[];
  both: { key: string; left: Video; right: Video }[];
};

export type MobileLibrary = {
  playlists: (Playlist & { videos: Video[]; downloaded_count: number })[];
  loose: Video[];
  videos: Video[];
  count: number;
};

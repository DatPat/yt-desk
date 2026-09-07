import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  AlertTriangle,
  ArrowLeftRight,
  Check,
  Copy,
  Download,
  ExternalLink,
  FolderOpen,
  FolderSearch,
  GitMerge,
  Loader2,
  Play,
  Plus,
  RefreshCw,
  Settings,
  Smartphone,
  Square,
  Trash2,
  Usb,
  Wifi,
  Search,
  Layers,
  Library,
  Sparkles,
} from "lucide-react";
import { api, mediaUrl } from "./api";
import {
  downloadPhase,
  formatBytes,
  formatDuration,
  formatElapsed,
  formatEta,
  formatWhen,
  isAudioFile,
  isDownloaded,
  isDuplicate,
  isUnavailable,
  qualityLabel,
} from "./format";
import type { AndroidStatus, ChangesReport, CompareReport, Download as Dl, DuplicateReport, Health, Playlist, Settings as AppSettings, SongGroup, Stats, SyncInfo, UsbJob, UsbStatus, Video, YoutubeJob, YoutubePreview, YoutubeStatus } from "./types";

type View =
  | { kind: "library" }
  | { kind: "playlist"; id: string }
  | { kind: "duplicates" }
  | { kind: "whatsnew" }
  | { kind: "compare" }
  | { kind: "downloads" }
  | { kind: "sync" }
  | { kind: "merge" }
  | { kind: "settings" };

let toastId = 0;

export function Desktop() {
  const [view, setView] = useState<View>({ kind: "library" });
  const [playlists, setPlaylists] = useState<Playlist[]>([]);
  const [videos, setVideos] = useState<Video[]>([]);
  const [duplicates, setDuplicates] = useState<DuplicateReport>({ across: [], within: [], songs: [] });
  const [changes, setChanges] = useState<ChangesReport | null>(null);
  const [downloads, setDownloads] = useState<Dl[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [sync, setSync] = useState<SyncInfo | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<"all" | "downloaded" | "duplicates" | "missing">("all");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [addOpen, setAddOpen] = useState(false);
  const [addText, setAddText] = useState("");
  const [player, setPlayer] = useState<Video | null>(null);
  const [toasts, setToasts] = useState<{ id: number; msg: string; err?: boolean }[]>([]);
  const [busy, setBusy] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const viewRef = useRef(view);
  const queryRef = useRef(query);
  viewRef.current = view;
  queryRef.current = query;

  const toast = useCallback((msg: string, err = false) => {
    const id = ++toastId;
    setToasts((t) => [...t, { id, msg, err }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4200);
  }, []);

  const refreshLists = useCallback(async () => {
    const [pls, dls, st, hp, ch] = await Promise.all([
      api.playlists(),
      api.downloads(),
      api.stats(),
      api.health(),
      api.playlistChanges().catch(() => null),
    ]);
    setPlaylists(pls);
    setDownloads(dls);
    setStats(st);
    setHealth(hp);
    if (ch) setChanges(ch);
  }, []);

  const loadViewData = useCallback(async (next: View, q: string) => {
    if (next.kind === "library") {
      setVideos(await api.videos(q));
    } else if (next.kind === "playlist") {
      const pl = await api.playlist(next.id);
      setVideos(pl.videos);
    } else if (next.kind === "duplicates") {
      setDuplicates(await api.duplicates());
    } else if (next.kind === "whatsnew") {
      setChanges(await api.playlistChanges());
    } else if (next.kind === "downloads") {
      setDownloads(await api.downloads());
    } else if (next.kind === "sync") {
      setSync(await api.sync());
    } else if (next.kind === "settings") {
      setSettings(await api.settings());
      setHealth(await api.health());
    } else if (next.kind === "merge") {
      setSettings(await api.settings());
    }
  }, []);

  useEffect(() => {
    refreshLists()
      .then(() => loadViewData({ kind: "library" }, ""))
      .catch((e) => toast(String(e.message || e), true));
    const params = new URLSearchParams(window.location.search);
    if (params.get("youtube") === "connected") {
      toast("YouTube account connected");
      window.history.replaceState({}, "", "/");
      setView({ kind: "merge" });
    } else if (params.get("youtube") === "error") {
      toast("YouTube sign-in failed", true);
      window.history.replaceState({}, "", "/");
    }
  }, [loadViewData, refreshLists, toast]);

  useEffect(() => {
    const es = new EventSource("/api/events");
    es.addEventListener("playlists", (ev) => {
      setPlaylists(JSON.parse((ev as MessageEvent).data));
      api.stats().then(setStats).catch(() => undefined);
    });
    es.addEventListener("playlist", (ev) => {
      const pl = JSON.parse((ev as MessageEvent).data);
      api.playlists().then(setPlaylists).catch(() => undefined);
      api.stats().then(setStats).catch(() => undefined);
      const current = viewRef.current;
      if (current.kind === "library") {
        loadViewData(current, queryRef.current).catch(() => undefined);
      } else if (current.kind === "playlist" && pl && (pl.id === current.id || pl.url)) {
        if (pl.id && pl.id !== current.id && current.id.startsWith("pending:")) {
          setView({ kind: "playlist", id: pl.id });
          loadViewData({ kind: "playlist", id: pl.id }, queryRef.current).catch(() => undefined);
        } else if (pl.id === current.id) {
          loadViewData(current, queryRef.current).catch(() => undefined);
        }
      } else if (current.kind === "duplicates") {
        api.duplicates().then(setDuplicates).catch(() => undefined);
      }
    });
    es.addEventListener("download", (ev) => {
      const job = JSON.parse((ev as MessageEvent).data) as Dl;
      setDownloads((cur) => {
        const i = cur.findIndex((d) => d.id === job.id);
        if (i === -1) return [job, ...cur];
        const copy = cur.slice();
        copy[i] = job;
        return copy;
      });
      setVideos((cur) =>
        cur.map((v) => (v.id === job.video_id ? { ...v, download: job } : v)),
      );
    });
    es.addEventListener("duplicates", (ev) => {
      setDuplicates(JSON.parse((ev as MessageEvent).data));
    });
    es.addEventListener("library", () => {
      api.stats().then(setStats).catch(() => undefined);
    });
    es.addEventListener("youtube", () => {
      /* Merge page listens via its own poll + this refresh of playlists after import */
      api.playlists().then(setPlaylists).catch(() => undefined);
    });
    es.addEventListener("changes", (ev) => {
      setChanges(JSON.parse((ev as MessageEvent).data));
    });
    return () => es.close();
  }, []);

  useEffect(() => {
    const t = setTimeout(() => {
      if (view.kind === "library") loadViewData(view, query).catch(() => undefined);
    }, 250);
    return () => clearTimeout(t);
  }, [query, view, loadViewData]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "/" && document.activeElement?.tagName !== "INPUT" && document.activeElement?.tagName !== "TEXTAREA") {
        e.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const go = (next: View) => {
    setView(next);
    setSelected(new Set());
    loadViewData(next, query).catch((e) => toast(String(e.message || e), true));
  };

  const shown = useMemo(() => {
    return videos.filter((v) => {
      if (filter === "downloaded") return isDownloaded(v);
      if (filter === "duplicates") return isDuplicate(v);
      if (filter === "missing") return isUnavailable(v);
      return true;
    });
  }, [videos, filter]);

  const currentPlaylist = view.kind === "playlist" ? playlists.find((p) => p.id === view.id) : null;
  const activeDownloads = downloads.filter((d) => ["queued", "downloading", "processing"].includes(d.status)).length;

  const queue = async (ids: string[], extra?: { playlist_id?: string; unique_only?: boolean; unique_songs?: boolean }) => {
    try {
      setBusy(true);
      await api.startDownloads({ video_ids: ids, ...extra });
      toast("Added to the download queue");
      setSelected(new Set());
      go({ kind: "downloads" });
    } catch (e) {
      toast(String((e as Error).message), true);
    } finally {
      setBusy(false);
    }
  };

  const addPlaylists = async () => {
    try {
      setBusy(true);
      await api.addPlaylists(addText);
      setAddOpen(false);
      setAddText("");
      await refreshLists();
      toast("Fetching playlist contents…");
    } catch (e) {
      toast(String((e as Error).message), true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <img className="brand-mark" src="/favicon.svg" alt="" />
          <div>
            <h1>YT Desk</h1>
            <small>playlist bay</small>
          </div>
        </div>
        <label className="search">
          <Search size={15} />
          <input
            ref={searchRef}
            placeholder="Search titles, channels, IDs — /"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              if (view.kind !== "library") setView({ kind: "library" });
            }}
          />
        </label>
        <div className="top-actions">
          <button className="btn btn-ghost" onClick={() => go({ kind: "sync" })}>
            <Smartphone size={15} /> Phone
          </button>
          <button className="btn btn-primary" onClick={() => setAddOpen(true)}>
            <Plus size={15} /> Add playlists
          </button>
        </div>
      </header>

      <aside className="sidebar">
        <nav className="nav">
          <button className={`nav-btn ${view.kind === "library" ? "active" : ""}`} onClick={() => go({ kind: "library" })}>
            <Library /> Library
            <span className="badge">{stats?.videos ?? 0}</span>
          </button>
          <button className={`nav-btn ${view.kind === "duplicates" ? "active" : ""}`} onClick={() => go({ kind: "duplicates" })}>
            <Layers /> Duplicates
            <span className="badge gold-badge">{stats?.duplicates ?? 0}</span>
          </button>
          <button className={`nav-btn ${view.kind === "whatsnew" ? "active" : ""}`} onClick={() => go({ kind: "whatsnew" })}>
            <Sparkles /> What’s new
            {changes?.added ? <span className="badge gold-badge">{changes.added}</span> : null}
          </button>
          <button className={`nav-btn ${view.kind === "compare" ? "active" : ""}`} onClick={() => go({ kind: "compare" })}>
            <ArrowLeftRight /> Compare lists
          </button>
          <button className={`nav-btn ${view.kind === "downloads" ? "active" : ""}`} onClick={() => go({ kind: "downloads" })}>
            <Download /> Downloads
            {activeDownloads > 0 ? <span className="badge">{activeDownloads}</span> : null}
          </button>
          <button className={`nav-btn ${view.kind === "sync" ? "active" : ""}`} onClick={() => go({ kind: "sync" })}>
            <Wifi /> Phone sync
          </button>
          <button className={`nav-btn ${view.kind === "merge" ? "active" : ""}`} onClick={() => go({ kind: "merge" })}>
            <GitMerge /> Merge on YouTube
          </button>
          <button className={`nav-btn ${view.kind === "settings" ? "active" : ""}`} onClick={() => go({ kind: "settings" })}>
            <Settings /> Settings
          </button>
        </nav>
        <div className="side-label">
          Playlists
          <span style={{ display: "flex", gap: 2 }}>
            <button
              className="icon-btn"
              title="Refetch all from YouTube"
              disabled={playlists.some((p) => p.status === "fetching")}
              onClick={async () => {
                try {
                  const r = await api.refreshAllPlaylists();
                  toast(`Refetching ${r.count} playlists from YouTube…`);
                  await refreshLists();
                } catch (e) {
                  toast(String((e as Error).message), true);
                }
              }}
            >
              <RefreshCw size={14} className={playlists.some((p) => p.status === "fetching") ? "spin" : ""} />
            </button>
            <button className="icon-btn" title="Add" onClick={() => setAddOpen(true)}><Plus size={14} /></button>
          </span>
        </div>
        <div className="pl-list">
          {playlists.map((p) => (
            <button
              key={p.id}
              className={`pl-item ${view.kind === "playlist" && view.id === p.id ? "active" : ""}`}
              onClick={() => go({ kind: "playlist", id: p.id })}
            >
              {p.thumbnail ? <img className="pl-thumb" src={p.thumbnail} alt="" /> : <span className="pl-thumb" />}
              <span className="pl-meta">
                <b>{p.title}</b>
                <span>
                  {p.status === "fetching" ? "Fetching…" : `${p.video_count} videos`}
                  {p.uploader ? ` · ${p.uploader}` : ""}
                </span>
              </span>
              {p.status === "fetching" && <Loader2 className="spin" size={14} />}
              {p.status === "error" && <AlertTriangle size={14} color="#ff6b7d" />}
            </button>
          ))}
        </div>
        <div className="side-foot">
          <button className="btn btn-primary btn-wide" onClick={() => setAddOpen(true)}>
            <Plus size={15} /> Paste playlist URLs
          </button>
        </div>
      </aside>

      <main className="main">
        {view.kind === "library" && (
          <LibraryPage
            title="All videos"
            subtitle={`${shown.length} in view · ${stats?.playlists ?? 0} playlists`}
            videos={shown}
            filter={filter}
            setFilter={setFilter}
            selected={selected}
            setSelected={setSelected}
            onDownload={(ids) => queue(ids)}
            onPlay={setPlayer}
            busy={busy}
            headerActions={
              <>
                <button
                  className="btn"
                  disabled={changes?.status === "running"}
                  onClick={async () => {
                    try {
                      setChanges(await api.checkNewPlaylistItems());
                      go({ kind: "whatsnew" });
                    } catch (e) {
                      toast(String((e as Error).message), true);
                    }
                  }}
                >
                  <Sparkles size={15} /> Check for new songs
                </button>
                <button
                  className="btn"
                  disabled={playlists.some((p) => p.status === "fetching")}
                  onClick={async () => {
                    try {
                      const r = await api.refreshAllPlaylists();
                      toast(`Refetching ${r.count} playlists from YouTube…`);
                      await refreshLists();
                    } catch (e) {
                      toast(String((e as Error).message), true);
                    }
                  }}
                >
                  <RefreshCw size={15} className={playlists.some((p) => p.status === "fetching") ? "spin" : ""} /> Refetch from YouTube
                </button>
              </>
            }
          />
        )}
        {view.kind === "playlist" && currentPlaylist && (
          <LibraryPage
            title={currentPlaylist.title}
            subtitle={`${currentPlaylist.uploader || "YouTube"} · ${shown.length} videos`}
            videos={shown}
            filter={filter}
            setFilter={setFilter}
            selected={selected}
            setSelected={setSelected}
            onDownload={(ids) => queue(ids)}
            onPlay={setPlayer}
            busy={busy}
            headerActions={
              <>
                <button className="btn" onClick={() => api.refreshPlaylist(currentPlaylist.id).then(() => toast("Refetching this playlist from YouTube…"))}>
                  <RefreshCw size={15} /> Refetch
                </button>
                <button className="btn" onClick={() => queue([], { playlist_id: currentPlaylist.id, unique_only: true })}>
                  <Download size={15} /> Download unique songs
                </button>
                <button
                  className="btn btn-danger"
                  onClick={async () => {
                    if (!confirm(`Remove “${currentPlaylist.title}” from YT Desk? Files on disk are kept.`)) return;
                    await api.deletePlaylist(currentPlaylist.id);
                    await refreshLists();
                    go({ kind: "library" });
                  }}
                >
                  <Trash2 size={15} /> Remove
                </button>
              </>
            }
          />
        )}
        {view.kind === "compare" && (
          <ComparePage
            playlists={playlists}
            onDownload={(ids) => queue(ids)}
            onPlay={setPlayer}
            onToast={toast}
          />
        )}
        {view.kind === "whatsnew" && (
          <ChangesPage
            report={changes}
            busy={busy || changes?.status === "running"}
            onCheck={async () => {
              setChanges(await api.checkNewPlaylistItems());
            }}
            onDownload={(ids) => queue(ids)}
            onPlay={(video) => setPlayer(video as Video)}
            onOpenPlaylist={(id) => go({ kind: "playlist", id })}
            onToast={toast}
          />
        )}
        {view.kind === "duplicates" && (
          <DuplicatesPage
            report={duplicates}
            onDownload={(id) => queue([id])}
            onDownloadSong={(group) => queue(group.videos.map((v) => v.id), { unique_songs: true })}
            onPlay={setPlayer}
            onOpenPlaylist={(id) => go({ kind: "playlist", id })}
          />
        )}
        {view.kind === "downloads" && (
          <DownloadsPage
            jobs={downloads}
            onCancel={(id) => api.cancelDownload(id).catch((e) => toast(e.message, true))}
            onRetry={(id) => api.retryDownload(id).catch((e) => toast(e.message, true))}
            onReveal={(id) => api.revealDownload(id).catch((e) => toast(e.message, true))}
            onPlay={(job) => {
              const v = videos.find((x) => x.id === job.video_id);
              setPlayer(v || { id: job.video_id, title: job.title || job.video_id, channel: "", duration: null, thumbnail: null, url: "", upload_date: null, availability: null, download: job });
            }}
          />
        )}
        {view.kind === "merge" && (
          <MergePage
            playlists={playlists}
            settings={settings}
            onToast={toast}
            onSaveSettings={async (patch) => {
              setSettings(await api.saveSettings(patch));
            }}
          />
        )}
        {view.kind === "sync" && sync && (
          <SyncPage
            sync={sync}
            defaultTab={health?.is_mac ? "ios" : "android"}
            onRotate={async () => setSync(await api.rotateSync())}
            onCopy={(text) => {
              navigator.clipboard.writeText(text);
              toast("Copied");
            }}
          />
        )}
        {view.kind === "settings" && settings && (
          <SettingsPage
            settings={settings}
            health={health}
            onSave={async (patch) => {
              setSettings(await api.saveSettings(patch));
              toast("Saved");
            }}
            onPick={async () => {
              const r = await api.pickFolder();
              setSettings(await api.settings());
              toast(r.path);
            }}
            onOpen={() => api.openFolder()}
          />
        )}
      </main>

      {addOpen && (
        <div className="modal-back" onClick={() => setAddOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>Add playlists</h3>
            <p style={{ color: "var(--muted)", margin: 0 }}>
              Paste one or more YouTube playlist URLs. Watch URLs with a list= parameter work too. A single video becomes its own shelf.
            </p>
            <textarea
              autoFocus
              placeholder={"https://www.youtube.com/playlist?list=PLxxxxxxxx\nhttps://www.youtube.com/watch?v=xxxx&list=PLyyyy"}
              value={addText}
              onChange={(e) => setAddText(e.target.value)}
            />
            <div className="modal-actions">
              <button className="btn btn-ghost" onClick={() => setAddOpen(false)}>Cancel</button>
              <button className="btn btn-primary" disabled={busy || !addText.trim()} onClick={addPlaylists}>
                {busy ? <Loader2 className="spin" size={15} /> : <Plus size={15} />} Fetch contents
              </button>
            </div>
          </div>
        </div>
      )}

      {player && (
        <div className="modal-back" onClick={() => setPlayer(null)}>
          <div className="modal player" onClick={(e) => e.stopPropagation()}>
            <h3>{player.title}</h3>
            <p style={{ color: "var(--muted)", margin: 0 }}>{player.channel}</p>
            {isDownloaded(player) ? (
              isAudioFile(player) ? (
                <div>
                  {player.thumbnail && (
                    <img
                      src={player.thumbnail}
                      alt=""
                      style={{ width: "100%", maxHeight: 360, objectFit: "cover", borderRadius: 10, margin: "12px 0" }}
                    />
                  )}
                  <audio className="player" src={mediaUrl(player.id)} controls autoPlay style={{ width: "100%" }} />
                </div>
              ) : (
                <video className="player" src={mediaUrl(player.id)} controls autoPlay />
              )
            ) : (
              <p style={{ color: "var(--muted)" }}>Download this track first to play it locally.</p>
            )}
            <div className="modal-actions">
              <a className="btn" href={player.url || `https://www.youtube.com/watch?v=${player.id}`} target="_blank" rel="noreferrer">
                <ExternalLink size={15} /> YouTube
              </a>
              <button className="btn btn-ghost" onClick={() => setPlayer(null)}>Close</button>
            </div>
          </div>
        </div>
      )}

      {playlists.length === 0 && view.kind === "library" && videos.length === 0 && (
        <></>
      )}

      <div className="toasts">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.err ? "err" : ""}`}>{t.msg}</div>
        ))}
      </div>
    </div>
  );
}

function LibraryPage({
  title,
  subtitle,
  videos,
  filter,
  setFilter,
  selected,
  setSelected,
  onDownload,
  onPlay,
  busy,
  headerActions,
}: {
  title: string;
  subtitle: string;
  videos: Video[];
  filter: "all" | "downloaded" | "duplicates" | "missing";
  setFilter: (f: "all" | "downloaded" | "duplicates" | "missing") => void;
  selected: Set<string>;
  setSelected: (s: Set<string>) => void;
  onDownload: (ids: string[]) => void;
  onPlay: (v: Video) => void;
  busy: boolean;
  headerActions?: ReactNode;
}) {
  const toggle = (id: string) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  };
  const allIds = videos.map((v) => v.id);
  const allOn = allIds.length > 0 && allIds.every((id) => selected.has(id));

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2>{title}</h2>
          <p>{subtitle}</p>
        </div>
        <div className="page-actions">{headerActions}</div>
      </div>
      {videos.length === 0 && title === "All videos" ? (
        <div className="empty">
          <h3>Your shelves are empty</h3>
          <p>Paste a playlist URL to pull in titles, channels, and durations — nothing is downloaded until you queue it.</p>
        </div>
      ) : videos.length === 0 ? (
        <div className="empty">
          <h3>No videos in this view</h3>
          <p>The playlist may still be fetching, or the current filter hides everything.</p>
        </div>
      ) : (
        <>
          <div className="filters">
            {(["all", "downloaded", "duplicates", "missing"] as const).map((f) => (
              <button key={f} className={`chip ${filter === f ? "active" : ""}`} onClick={() => setFilter(f)}>
                {f === "all" ? "All" : f[0].toUpperCase() + f.slice(1)}
              </button>
            ))}
          </div>
          <div className="video-table">
            <div className="video-row" style={{ opacity: 0.6 }}>
              <input
                className="check"
                type="checkbox"
                checked={allOn}
                onChange={() => setSelected(allOn ? new Set() : new Set(allIds))}
              />
              <span />
              <span className="mono">Title</span>
              <span className="mono">Time</span>
              <span className="mono">Playlists</span>
              <span className="mono">Status</span>
              <span />
            </div>
            {videos.map((v) => (
              <VideoRow
                key={`${v.id}-${v.position ?? ""}`}
                video={v}
                checked={selected.has(v.id)}
                onCheck={() => toggle(v.id)}
                onPlay={() => onPlay(v)}
                onDownload={() => onDownload([v.id])}
              />
            ))}
          </div>
          {selected.size > 0 && (
            <div className="selection-bar">
              <span>{selected.size} selected</span>
              <button className="btn btn-ghost" onClick={() => setSelected(new Set())}>Clear</button>
              <button className="btn btn-primary" disabled={busy} onClick={() => onDownload([...selected])}>
                <Download size={15} /> Download
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function VideoRow({
  video,
  checked,
  onCheck,
  onPlay,
  onDownload,
}: {
  video: Video;
  checked: boolean;
  onCheck: () => void;
  onPlay: () => void;
  onDownload: () => void;
}) {
  const missing = isUnavailable(video);
  const dupe = isDuplicate(video);
  const dl = video.download;
  return (
    <div className={`video-row ${missing ? "unavailable" : ""}`}>
      <input className="check" type="checkbox" checked={checked} onChange={onCheck} />
      <div className="thumb-wrap">
        {video.thumbnail && <img src={video.thumbnail} alt="" />}
        <span className="dur">{formatDuration(video.duration)}</span>
        {dupe && (video.song_mates || 0) > 1 && (
          <span className="stamp">same song</span>
        )}
        {dupe && (video.song_mates || 0) <= 1 && (
          <span className="stamp">×{video.playlists?.length}</span>
        )}
      </div>
      <div className="title-block">
        <b>{video.title}</b>
        <span>{video.channel || "Unknown channel"}</span>
      </div>
      <span className="mono">{formatDuration(video.duration)}</span>
      <div className="plist-chips">
        {(video.playlists || []).map((p) => (
          <span key={p.id} className="mini">{p.title}</span>
        ))}
      </div>
      <Status video={video} />
      <div className="row-actions">
        {isDownloaded(video) && (
          <button className="icon-btn" title="Play" onClick={onPlay}><Play size={15} /></button>
        )}
        {dl?.status !== "downloading" && dl?.status !== "queued" && dl?.status !== "done" && (
          <button className="icon-btn" title="Download" onClick={onDownload}><Download size={15} /></button>
        )}
        <a className="icon-btn" title="YouTube" href={video.url || `https://www.youtube.com/watch?v=${video.id}`} target="_blank" rel="noreferrer">
          <ExternalLink size={15} />
        </a>
      </div>
    </div>
  );
}

function Status({ video }: { video: Video }) {
  const dl = video.download;
  if (isUnavailable(video)) return <span className="status-pill err">Unavailable</span>;
  if (dl?.status === "downloading" || dl?.status === "processing") {
    const pct = Math.round(dl.progress || 0);
    const label = dl.status === "processing" ? (dl.phase || "Converting") : `${pct}%`;
    return (
      <span className="status-pill run" title={downloadPhase(dl)}>
        {label}
      </span>
    );
  }
  if (dl?.status === "queued") return <span className="status-pill run">Queued</span>;
  if (dl?.status === "error") return <span className="status-pill err" title={dl.error || ""}>Failed</span>;
  if (isDownloaded(video)) return <span className="status-pill done"><Check size={13} /> Saved</span>;
  if ((video.song_mates || 0) > 1) {
    return <span className="status-pill" style={{ color: "var(--gold)" }}>Same song ×{video.song_mates}</span>;
  }
  if (isDuplicate(video)) return <span className="status-pill" style={{ color: "var(--gold)" }}>In {video.playlists?.length} lists</span>;
  return <span className="status-pill" style={{ color: "var(--faint)" }}>—</span>;
}

function ComparePage({
  playlists,
  onDownload,
  onPlay,
  onToast,
}: {
  playlists: Playlist[];
  onDownload: (ids: string[]) => void;
  onPlay: (v: Video) => void;
  onToast: (msg: string, err?: boolean) => void;
}) {
  const [left, setLeft] = useState(playlists[0]?.id || "");
  const [right, setRight] = useState(playlists[1]?.id || "");
  const [mode, setMode] = useState<"song" | "video">("song");
  const [report, setReport] = useState<CompareReport | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!left && playlists[0]) setLeft(playlists[0].id);
    if (playlists.length >= 2) {
      setRight((current) => {
        if (current && current !== left && playlists.some((p) => p.id === current)) return current;
        const other = playlists.find((p) => p.id !== (left || playlists[0].id));
        return other?.id || "";
      });
    }
  }, [playlists, left]);

  useEffect(() => {
    if (!left || !right || left === right) {
      setReport(null);
      return;
    }
    let cancelled = false;
    setBusy(true);
    api
      .comparePlaylists({ left, right, mode })
      .then((data) => {
        if (!cancelled) setReport(data);
      })
      .catch((e) => {
        if (!cancelled) onToast(String((e as Error).message), true);
      })
      .finally(() => {
        if (!cancelled) setBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [left, right, mode, onToast]);

  const swap = () => {
    setLeft(right);
    setRight(left);
  };

  const leftTitle = playlists.find((p) => p.id === left)?.title || "List A";
  const rightTitle = playlists.find((p) => p.id === right)?.title || "List B";

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2>Compare lists</h2>
          <p>What’s only in one playlist, what’s only in the other, and what they share.</p>
        </div>
      </div>
      {playlists.length < 2 ? (
        <div className="empty">
          <h3>Need two playlists</h3>
          <p>Add at least two shelves, then come back to diff them.</p>
        </div>
      ) : (
        <>
          <div className="compare-controls">
            <label className="field" style={{ padding: 12 }}>
              <span>List A</span>
              <select value={left} onChange={(e) => setLeft(e.target.value)}>
                {playlists.map((p) => (
                  <option key={p.id} value={p.id} disabled={p.id === right}>{p.title}</option>
                ))}
              </select>
            </label>
            <button className="btn" onClick={swap} title="Swap">
              <ArrowLeftRight size={15} />
            </button>
            <label className="field" style={{ padding: 12 }}>
              <span>List B</span>
              <select value={right} onChange={(e) => setRight(e.target.value)}>
                {playlists.map((p) => (
                  <option key={p.id} value={p.id} disabled={p.id === left}>{p.title}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="filters">
            <button className={`chip ${mode === "song" ? "active" : ""}`} onClick={() => setMode("song")}>Same song</button>
            <button className={`chip ${mode === "video" ? "active" : ""}`} onClick={() => setMode("video")}>Same YouTube ID</button>
            {busy && <span style={{ color: "var(--muted)" }}>Comparing…</span>}
          </div>
          {report && (
            <>
              <p style={{ color: "var(--muted)" }}>
                {report.counts.only_left} only in A · {report.counts.both} in both · {report.counts.only_right} only in B
                {mode === "song" ? " (song match)" : " (exact video ID)"}
              </p>
              <div className="compare-grid">
                <CompareColumn
                  title={`Only in ${leftTitle}`}
                  count={report.counts.only_left}
                  videos={report.only_left}
                  onDownload={onDownload}
                  onPlay={onPlay}
                />
                <CompareColumn
                  title="In both"
                  count={report.counts.both}
                  videos={report.both.map((row) => row.left)}
                  pairs={report.both}
                  onDownload={onDownload}
                  onPlay={onPlay}
                />
                <CompareColumn
                  title={`Only in ${rightTitle}`}
                  count={report.counts.only_right}
                  videos={report.only_right}
                  onDownload={onDownload}
                  onPlay={onPlay}
                />
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}

function CompareColumn({
  title,
  count,
  videos,
  pairs,
  onDownload,
  onPlay,
}: {
  title: string;
  count: number;
  videos: Video[];
  pairs?: { key: string; left: Video; right: Video }[];
  onDownload: (ids: string[]) => void;
  onPlay: (v: Video) => void;
}) {
  return (
    <section className="compare-col">
      <header>
        <h3>{title}</h3>
        <span className="badge">{count}</span>
        {videos.length > 0 && (
          <button className="btn btn-sm" onClick={() => onDownload(videos.map((v) => v.id))}>
            <Download size={13} /> Download
          </button>
        )}
      </header>
      <div className="compare-items">
        {count === 0 && <p className="where">None</p>}
        {pairs
          ? pairs.map((row) => (
              <article key={row.key} className="compare-card">
                {row.left.thumbnail && <img src={row.left.thumbnail} alt="" />}
                <div>
                  <b>{row.left.title}</b>
                  <span>{row.left.channel} · {formatDuration(row.left.duration)}</span>
                  {row.right.id !== row.left.id && (
                    <span>Also as “{row.right.title}” · {row.right.channel}</span>
                  )}
                </div>
                <button className="icon-btn" onClick={() => onPlay(isDownloaded(row.left) ? row.left : row.right)} title="Play">
                  <Play size={14} />
                </button>
              </article>
            ))
          : videos.map((v) => (
              <article key={v.id} className="compare-card">
                {v.thumbnail && <img src={v.thumbnail} alt="" />}
                <div>
                  <b>{v.title}</b>
                  <span>{v.channel} · {formatDuration(v.duration)}</span>
                </div>
                <button className="icon-btn" onClick={() => onPlay(v)} title="Play">
                  <Play size={14} />
                </button>
              </article>
            ))}
      </div>
    </section>
  );
}

function ChangesPage({
  report,
  busy,
  onCheck,
  onDownload,
  onPlay,
  onOpenPlaylist,
  onToast,
}: {
  report: ChangesReport | null;
  busy: boolean;
  onCheck: () => Promise<void>;
  onDownload: (ids: string[]) => void;
  onPlay: (v: Video) => void;
  onOpenPlaylist: (id: string) => void;
  onToast: (msg: string, err?: boolean) => void;
}) {
  const running = report?.status === "running";
  const added = report?.added_items || [];
  const removed = report?.removed_items || [];
  const groups = report?.playlists || [];
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2>What’s new</h2>
          <p>
            {running
              ? report?.message || "Checking YouTube…"
              : report?.message || "Compare your playlists to YouTube and list songs added since the last check."}
          </p>
        </div>
        <div className="page-actions">
          {added.length > 0 && (
            <button className="btn btn-primary" disabled={busy} onClick={() => onDownload(added.map((v) => v.id))}>
              <Download size={15} /> Download {added.length} new
            </button>
          )}
          <button
            className="btn"
            disabled={busy || running}
            onClick={() => onCheck().catch((e) => onToast(String((e as Error).message), true))}
          >
            <Sparkles size={15} className={running ? "spin" : ""} /> Check YouTube
          </button>
        </div>
      </div>
      {running && (
        <div className="dl-now">
          <div className="dl-now-phase">{report?.message}</div>
          {report && report.total > 0 && (
            <div className="bar bar-lg">
              <i style={{ width: `${Math.min(100, (report.checked / report.total) * 100)}%` }} />
            </div>
          )}
        </div>
      )}
      {report?.error && <p style={{ color: "var(--danger)" }}>{report.error}</p>}
      {!running && report?.checked_at && (
        <p style={{ color: "var(--faint)", marginTop: 0 }}>
          Last check {formatWhen(report.checked_at)}
          {report.previous_at ? ` · previous ${formatWhen(report.previous_at)}` : " · first baseline"}
        </p>
      )}
      {!running && !report?.checked_at && report?.status === "idle" && (
        <div className="empty">
          <h3>No check yet</h3>
          <p>This looks at YouTube and lists tracks that were not on your playlists last time you ran it.</p>
        </div>
      )}
      {report?.baseline && !running && (
        <div className="empty">
          <h3>Baseline saved</h3>
          <p>Current playlist contents are remembered. Run Check YouTube again after you add songs on YouTube.</p>
        </div>
      )}
      {added.length > 0 && (
        <>
          <div className="section-kicker">Added since last check · {added.length}</div>
          {groups.filter((g) => g.added > 0).map((g) => (
            <div key={g.id} style={{ marginBottom: 18 }}>
              <button className="chip" onClick={() => onOpenPlaylist(g.id)}>{g.title} · {g.added}</button>
              <div className="dupe-grid" style={{ marginTop: 10 }}>
                {g.added_items.map((v) => (
                  <article key={`${g.id}-${v.id}`} className="dupe-card">
                    <div className="thumb-wrap" style={{ width: 168, height: 94 }}>
                      {v.thumbnail && <img src={v.thumbnail} alt="" />}
                    </div>
                    <div>
                      <h3>{v.song_title || v.title}</h3>
                      <div className="where">{v.song_artist || v.channel} · {formatDuration(v.duration)}</div>
                      <div className="row-actions" style={{ justifyContent: "flex-start", marginTop: 8 }}>
                        {isDownloaded(v) ? (
                          <button className="btn btn-sm" onClick={() => onPlay(v as Video)}><Play size={13} /> Play</button>
                        ) : (
                          <button className="btn btn-sm" onClick={() => onDownload([v.id])}><Download size={13} /> Download</button>
                        )}
                      </div>
                    </div>
                  </article>
                ))}
              </div>
            </div>
          ))}
        </>
      )}
      {removed.length > 0 && !running && (
        <>
          <div className="section-kicker">No longer on YouTube · {removed.length}</div>
          <div className="dl-list">
            {removed.map((v) => (
              <div key={`rm-${v.playlist_id}-${v.id}`} className="dl-queue-row">
                <span />
                <div>
                  <b>{v.title}</b>
                  <div style={{ color: "var(--faint)", fontSize: 12 }}>{v.playlist_title} · {v.channel}</div>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function DuplicatesPage({
  report,
  onDownload,
  onDownloadSong,
  onPlay,
  onOpenPlaylist,
}: {
  report: DuplicateReport;
  onDownload: (id: string) => void;
  onDownloadSong: (group: SongGroup) => void;
  onPlay: (v: Video) => void;
  onOpenPlaylist: (id: string) => void;
}) {
  const songs = report.songs || [];
  const empty = songs.length === 0 && report.across.length === 0 && report.within.length === 0;
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2>Duplicates</h2>
          <p>
            {songs.length} same-song groups
            {report.across.length ? ` · ${report.across.length} identical video IDs across playlists` : ""}
            {report.within.length ? ` · ${report.within.length} repeated inside a list` : ""}
          </p>
        </div>
      </div>
      {empty ? (
        <div className="empty">
          <h3>No duplicates in your current playlists</h3>
          <p>
            Same YouTube ID across lists, repeats inside a list, and the same song
            uploaded by different channels all show up here — with titles and which
            playlists they sit in. Refetch from YouTube after you edit lists on the site.
          </p>
        </div>
      ) : (
        <>
          {songs.length > 0 && (
            <>
              <div className="section-kicker">Same song, different uploads</div>
              <div className="dupe-grid">
                {songs.map((group) => (
                  <article key={group.key} className="dupe-card song-card">
                    <div>
                      <h3>{group.artist ? `${group.artist} — ${group.title}` : group.title || group.videos[0]?.title}</h3>
                      <div className="where">
                        {group.count} uploads
                        {group.duration_min != null ? ` · ${formatDuration(group.duration_min)}` : ""}
                        {group.duration_max != null && group.duration_max !== group.duration_min
                          ? `–${formatDuration(group.duration_max)}`
                          : ""}
                        {` · ${group.reason}`}
                      </div>
                      <div className="song-members">
                        {group.videos.map((v) => (
                          <div key={v.id} className="song-member">
                            {v.thumbnail ? <img src={v.thumbnail} alt="" /> : <span className="pl-thumb" />}
                            <div>
                              <b>{v.title}</b>
                              <span>
                                {v.channel || "Unknown channel"} · {formatDuration(v.duration)}
                                {isDownloaded(v) ? " · saved" : ""}
                              </span>
                              <div className="plist-chips" style={{ marginTop: 4 }}>
                                {(v.playlists || []).map((p) => (
                                  <button key={p.id} className="mini" onClick={() => onOpenPlaylist(p.id)}>{p.title}</button>
                                ))}
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                    <div className="page-actions" style={{ flexDirection: "column" }}>
                      {group.videos.some(isDownloaded) ? (
                        <button className="btn" onClick={() => onPlay(group.videos.find(isDownloaded) || group.videos[0])}>
                          <Play size={15} /> Play
                        </button>
                      ) : (
                        <button className="btn btn-primary" onClick={() => onDownloadSong(group)}>
                          <Download size={15} /> One copy
                        </button>
                      )}
                    </div>
                  </article>
                ))}
              </div>
            </>
          )}
          {report.across.length > 0 && (
            <>
              <div className="section-kicker">Same YouTube video in multiple playlists</div>
              <div className="dupe-grid">
                {report.across.map((v) => (
                  <article key={v.id} className="dupe-card">
                    <div className="thumb-wrap" style={{ width: 168, height: 94 }}>
                      {v.thumbnail && <img src={v.thumbnail} alt="" />}
                      <span className="stamp">×{v.playlist_count}</span>
                    </div>
                    <div>
                      <h3>{v.title}</h3>
                      <div className="where">{v.channel} · {formatDuration(v.duration)}</div>
                      <div className="plist-chips" style={{ marginTop: 8 }}>
                        {(v.playlists || []).map((p) => (
                          <button key={p.id} className="mini" onClick={() => onOpenPlaylist(p.id)}>{p.title}</button>
                        ))}
                      </div>
                    </div>
                    <div className="page-actions" style={{ flexDirection: "column" }}>
                      {isDownloaded(v) ? (
                        <button className="btn" onClick={() => onPlay(v)}><Play size={15} /> Play</button>
                      ) : (
                        <button className="btn btn-primary" onClick={() => onDownload(v.id)}><Download size={15} /> One copy</button>
                      )}
                    </div>
                  </article>
                ))}
              </div>
            </>
          )}
          {report.within.length > 0 && (
            <>
              <div className="section-kicker">Repeated inside a playlist</div>
              <div className="dupe-grid">
                {report.within.map((v) => (
                  <article key={`${v.id}-${v.playlist?.id}`} className="dupe-card">
                    <div className="thumb-wrap" style={{ width: 168, height: 94 }}>
                      {v.thumbnail && <img src={v.thumbnail} alt="" />}
                    </div>
                    <div>
                      <h3>{v.title}</h3>
                      <div className="where">
                        Appears {v.copies} times in {v.playlist?.title}
                      </div>
                    </div>
                  </article>
                ))}
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}

function DownloadsPage({
  jobs,
  onCancel,
  onRetry,
  onReveal,
  onPlay,
}: {
  jobs: Dl[];
  onCancel: (id: number) => void;
  onRetry: (id: number) => void;
  onReveal: (id: number) => void;
  onPlay: (job: Dl) => void;
}) {
  const working = jobs.filter((j) => j.status === "downloading" || j.status === "processing");
  const queued = jobs.filter((j) => j.status === "queued");
  const rest = jobs.filter((j) => !["queued", "downloading", "processing"].includes(j.status));
  const summary = working.length
    ? `${working.length} running · ${queued.length} waiting`
    : queued.length
      ? `${queued.length} waiting to start`
      : "Audio is saved as Opus with cover art and tags. One file per song, even if it sits in several playlists.";

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2>Downloads</h2>
          <p>{summary}</p>
        </div>
      </div>
      {jobs.length === 0 ? (
        <div className="empty">
          <h3>Queue is quiet</h3>
          <p>Select videos in a playlist and send them here.</p>
        </div>
      ) : (
        <>
          {working.map((j) => {
            const pct = Math.max(0, Math.min(100, j.progress || 0));
            const unknown = j.status === "downloading" && !j.total_bytes && pct < 1;
            const bits = [
              downloadPhase(j),
              j.total_bytes
                ? `${formatBytes(j.downloaded_bytes || 0)} / ${formatBytes(j.total_bytes)}`
                : pct
                  ? `${Math.round(pct)}%`
                  : "",
              j.speed || "",
              j.eta ? `${formatEta(j.eta)} left` : "",
            ].filter(Boolean);
            return (
              <div key={j.id} className="dl-now">
                <div className="dl-now-top">
                  <div>
                    <b>{j.title || j.video_id}</b>
                    <div className="dl-now-phase">{bits.join(" · ")}</div>
                  </div>
                  <button className="btn btn-sm" onClick={() => onCancel(j.id)}><Square size={13} /> Cancel</button>
                </div>
                <div className={`bar bar-lg ${unknown ? "waiting" : ""}`}>
                  <i style={{ width: `${unknown ? 28 : Math.max(pct, 2)}%` }} />
                </div>
              </div>
            );
          })}
          {queued.length > 0 && (
            <>
              <div className="section-kicker">Up next · {queued.length}</div>
              <div className="dl-list">
                {queued.slice(0, 40).map((j, i) => (
                  <div key={j.id} className="dl-queue-row">
                    <span className="mono">{i + 1}</span>
                    <div>
                      <b>{j.title || j.video_id}</b>
                      <div style={{ color: "var(--faint)", fontSize: 12 }}>Waiting in queue</div>
                    </div>
                    <button className="btn btn-sm" onClick={() => onCancel(j.id)}>Remove</button>
                  </div>
                ))}
              </div>
              {queued.length > 40 && (
                <p style={{ color: "var(--faint)" }}>+{queued.length - 40} more waiting</p>
              )}
            </>
          )}
          {rest.length > 0 && <div className="section-kicker">History</div>}
          <div className="dl-list">
            {rest.map((j) => (
              <div key={j.id} className="dl-row">
                <div>
                  <b>{j.title || j.video_id}</b>
                  <div style={{ color: "var(--muted)", fontSize: 12.5, marginTop: 4 }}>
                    {j.status === "done" ? (j.filename || "Saved") : j.error || downloadPhase(j)}
                    {j.filesize ? ` · ${formatBytes(j.filesize)}` : ""}
                  </div>
                </div>
                <span className="mono">{j.status === "done" ? "Saved" : downloadPhase(j)}</span>
                <div className="row-actions">
                  {j.status === "done" && (
                    <>
                      <button className="icon-btn" onClick={() => onPlay(j)}><Play size={15} /></button>
                      <button className="icon-btn" onClick={() => onReveal(j.id)}><FolderOpen size={15} /></button>
                    </>
                  )}
                  {j.status === "error" && (
                    <button className="btn btn-sm" onClick={() => onRetry(j.id)}><RefreshCw size={13} /> Retry</button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function SyncPage({
  sync,
  defaultTab = "android",
  onRotate,
  onCopy,
}: {
  sync: SyncInfo;
  defaultTab?: "ios" | "android";
  onRotate: () => void;
  onCopy: (t: string) => void;
}) {
  const [tab, setTab] = useState<"ios" | "android">(defaultTab);
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2>Phone sync</h2>
          <p>Plug the phone in over USB to copy tracks onto it, or stay on the same Wi‑Fi to stream.</p>
        </div>
        <div className="page-actions">
          <button className="btn" onClick={onRotate}><RefreshCw size={15} /> New PIN</button>
        </div>
      </div>
      <div className="sync-grid">
        <div className="qr-card">
          <div className="qr" dangerouslySetInnerHTML={{ __html: sync.qr_svg }} />
          <div className="pin">{sync.pin}</div>
          <div style={{ color: "var(--faint)", fontFamily: "var(--mono)", fontSize: 12 }}>PAIRING PIN</div>
          <p style={{ color: "var(--muted)", margin: "12px 0 0" }}>
            {sync.ips.length ? `Listening on ${sync.ips.join(", ")}:${sync.port}` : "No LAN address found — connect Wi‑Fi."}
          </p>
          <button className="btn btn-wide" style={{ marginTop: 14 }} onClick={() => onCopy(sync.mobile_url)}>
            <Copy size={15} /> Copy phone link
          </button>
        </div>
        <div>
          <div className="filters">
            <button className={`chip ${tab === "android" ? "active" : ""}`} onClick={() => setTab("android")}>Android</button>
            <button className={`chip ${tab === "ios" ? "active" : ""}`} onClick={() => setTab("ios")}>iPhone / iPad</button>
          </div>
          {tab === "ios" && (
            <p style={{ color: "var(--gold)", margin: "0 0 12px" }}>
              A USB Android phone will not show on this tab. Switch to <b>Android</b>.
            </p>
          )}
          <div className="steps">
            {tab === "android" ? <AndroidUsbPanel /> : <UsbPanel />}
            <div className="step">
              <b>2. Stream in the phone browser</b>
              <p>
                Scan the QR code. The phone app lists everything already downloaded on this PC.
                Play over Wi‑Fi, or tap download to save a copy on the device.
              </p>
              {tab === "ios" && <p style={{ marginTop: 8 }}>Safari: Share → Add to Home Screen for a standalone player.</p>}
              {tab === "android" && <p style={{ marginTop: 8 }}>Chrome: menu → Install app / Add to Home screen.</p>}
            </div>
            <div className="step">
              <b>3. Sync files over Wi‑Fi (WebDAV)</b>
              {tab === "ios" ? (
                <p>
                  Open Files → browse (•••) → Connect to Server. Use the WebDAV address below,
                  user <b>ytdesk</b>, password = the PIN. Files you copy land in On My iPhone / iCloud Drive.
                </p>
              ) : (
                <p>
                  In Solid Explorer, CX File Explorer, or Material Files, add a WebDAV account with the
                  address below, user <b>ytdesk</b>, password = the PIN. Copy folders onto internal storage.
                </p>
              )}
              <div className="code">{sync.dav_url}</div>
              <button className="btn btn-sm" style={{ marginTop: 8 }} onClick={() => onCopy(sync.dav_url)}>Copy WebDAV URL</button>
            </div>
            <div className="step">
              <b>4. VLC playlist</b>
              <p>Open this M3U on the phone (VLC → Network / Open URL) to play the whole library as a queue.</p>
              <div className="code">{sync.mobile_url.replace("/m?", "/api/mobile/playlist.m3u?")}</div>
            </div>
            <div className="step">
              <b>Library folder on this PC</b>
              <p>{sync.library_dir} — you can also copy this folder onto the phone yourself in Explorer.</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function AndroidUsbPanel() {
  const [info, setInfo] = useState<AndroidStatus | null>(null);
  const [job, setJob] = useState<UsbJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [selectedId, setSelectedId] = useState<string>("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [, setTick] = useState(0);

  const refresh = () => {
    api.androidStatus().then((data) => {
      setLoadError(null);
      setInfo(data);
      setJob(data.job);
      setSelectedId((cur) => {
        if (cur && data.devices.some((d) => d.id === cur)) return cur;
        const ready = data.devices.find((d) => d.ready) || data.devices[0];
        return ready?.id || "";
      });
    }).catch((e) => {
      setLoadError(String((e as Error).message || e));
    });
  };

  useEffect(() => {
    refresh();
    const es = new EventSource("/api/events");
    es.addEventListener("android", (ev) => {
      setJob(JSON.parse((ev as MessageEvent).data));
    });
    const t = setInterval(refresh, 4000);
    return () => {
      es.close();
      clearInterval(t);
    };
  }, []);

  useEffect(() => {
    if (job?.status !== "running") return;
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [job?.status]);

  const device = info?.devices.find((d) => d.id === selectedId) || info?.devices.find((d) => d.ready) || info?.devices[0];
  const destPath = device
    ? `This PC\\${device.name}\\Internal shared storage\\Music`
    : (info?.dest || "This PC\\phone\\Internal shared storage\\Music");
  const running = job?.status === "running";
  const count = info?.library_count ?? 0;
  const doneCount = (job?.copied || 0) + (job?.skipped || 0) + (job?.failed || 0);
  const pct = job && job.total > 0 ? Math.min(100, (doneCount / job.total) * 100) : 0;
  const elapsed = running ? formatElapsed(job?.started_at) : "";

  return (
    <div className="step">
      <b>1. USB copy onto the phone</b>
      <p>
        Plug in <b>any Android phone</b> with a data cable, unlock it, and set USB to <b>File transfer / MTP</b>
        (not Charge only). Tracks copy into <b>{destPath}</b>. Each YT Desk playlist is written to{" "}
        <b>{destPath}\Playlists</b> as a <b>.m3u8</b> file. Names already there are skipped.
        Open the playlists in VLC.
      </p>
      {loadError && (
        <p style={{ color: "var(--danger)", marginTop: 8 }}>
          Could not list USB phones: {loadError}. Open YT Desk at http://127.0.0.1:8765 on this PC (not the phone QR link).
        </p>
      )}
      {info && !info.com && info.is_windows && (
        <p style={{ color: "var(--gold)", marginTop: 8 }}>
          USB copy needs <code>pywin32</code>. In the YT Desk venv: <code>pip install pywin32</code>, then restart.
        </p>
      )}
      {info?.error && !device && <p style={{ color: "var(--danger)", marginTop: 8 }}>{info.error}</p>}
      {device?.error && <p style={{ color: "var(--gold)", marginTop: 8 }}>{device.error}</p>}
      <div className={`phone-card ${device?.ready ? "ready" : ""}`}>
        <div>
          <div className="phone-card-name">
            <i className={`dot ${device?.ready ? "" : "warn"}`} />
            {device ? device.name : "No Android phone on USB"}
          </div>
          <div className="phone-card-meta">
            {device
              ? `${device.kind === "adb" ? "USB debugging" : "File transfer"} · ${count} tracks will copy into ${destPath}`
              : "Unlock the phone, set USB to File transfer / MTP, then Refresh. Use the Android tab, not iPhone / iPad."}
          </div>
        </div>
        <button className="btn btn-sm" onClick={refresh}><RefreshCw size={13} /> Refresh</button>
      </div>
      {info && info.devices.length > 1 && (
        <select value={selectedId} onChange={(e) => setSelectedId(e.target.value)} style={{ marginTop: 10 }}>
          {info.devices.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name} · {d.ready ? (d.kind === "adb" ? "USB debugging" : "File transfer") : (d.error || "not ready")}
            </option>
          ))}
        </select>
      )}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10, alignItems: "center" }}>
        <button
          className="btn btn-primary"
          disabled={busy || running || !device?.ready || count === 0}
          onClick={async () => {
            try {
              setBusy(true);
              setJob(await api.androidSync({ id: device?.id, kind: device?.kind }));
            } catch (e) {
              setJob({
                status: "error",
                copied: 0,
                skipped: 0,
                failed: 0,
                total: 0,
                message: String((e as Error).message),
                device: null,
                error: String((e as Error).message),
              });
            } finally {
              setBusy(false);
            }
          }}
        >
          <Usb size={15} /> Copy library onto this phone
        </button>
        {running && (
          <button className="btn btn-danger" onClick={() => api.androidCancel()}>Cancel</button>
        )}
      </div>
      {job && job.status !== "idle" && (
        <div className="copy-progress">
          <div className="dl-now-phase">
            {job.phase || (running ? "Copying" : job.status)}
            {job.device ? ` · ${job.device}` : ""}
            {elapsed ? ` · ${elapsed} elapsed` : ""}
          </div>
          {job.total > 0 && (
            <>
              <div className="bar bar-lg"><i style={{ width: `${Math.max(pct, running ? 2 : 0)}%` }} /></div>
              <div className="dl-now-phase">
                {doneCount} of {job.total} files
                {typeof job.bytes_total === "number" && job.bytes_total > 0
                  ? ` · ${formatBytes(job.bytes_done || 0)} / ${formatBytes(job.bytes_total)}`
                  : ""}
                {job.copied ? ` · ${job.copied} copied` : ""}
                {job.skipped ? ` · ${job.skipped} already there` : ""}
                {job.failed ? ` · ${job.failed} failed` : ""}
              </div>
            </>
          )}
          {job.current && <p className="copy-current">{job.current}</p>}
          <p style={{ margin: "8px 0 0" }}>{job.message}</p>
          {job.error && <p style={{ color: "var(--danger)", margin: "6px 0 0" }}>{job.error}</p>}
        </div>
      )}
    </div>
  );
}

function UsbPanel() {
  const [usb, setUsb] = useState<UsbStatus | null>(null);
  const [appId, setAppId] = useState("vlc");
  const [job, setJob] = useState<UsbJob | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = () => {
    api.usbStatus().then((data) => {
      setUsb(data);
      setJob(data.job);
    }).catch(() => undefined);
  };

  useEffect(() => {
    refresh();
    const es = new EventSource("/api/events");
    es.addEventListener("usb", (ev) => {
      setJob(JSON.parse((ev as MessageEvent).data));
    });
    const t = setInterval(refresh, 4000);
    return () => {
      es.close();
      clearInterval(t);
    };
  }, []);

  const device = usb?.devices[0];
  const running = job?.status === "running";

  return (
    <div className="step">
      <b>1. USB copy (Mac → iPhone)</b>
      <p>
        Plug the iPhone into this Mac with a data cable, unlock it, tap <b>Trust This Computer</b>.
        Tracks copy into VLC’s Files folder. Already-copied names are skipped.
      </p>
      {!usb?.library && (
        <p style={{ color: "var(--gold)" }}>
          USB copy needs <code>pymobiledevice3</code>. On the Mac: <code>pip install pymobiledevice3</code> in the YT Desk venv, then restart.
        </p>
      )}
      {usb?.error && <p style={{ color: "var(--danger)" }}>{usb.error}</p>}
      <div className="health-row" style={{ marginTop: 10 }}>
        {device ? (
          <span className="tag">
            <i className={`dot ${device.paired ? "" : "warn"}`} />
            {device.name} {device.paired ? "paired" : "tap Trust on the phone"}
          </span>
        ) : (
          <span className="tag"><i className="dot warn" /> No iPhone on USB</span>
        )}
        <button className="btn btn-sm" onClick={refresh}><RefreshCw size={13} /> Refresh</button>
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10, alignItems: "center" }}>
        <select value={appId} onChange={(e) => setAppId(e.target.value)}>
          {(usb?.apps || [{ id: "vlc", label: "VLC" }]).map((a) => (
            <option key={a.id} value={a.id}>{a.label}</option>
          ))}
        </select>
        <button
          className="btn btn-primary"
          disabled={busy || running || !device}
          onClick={async () => {
            try {
              setBusy(true);
              setJob(await api.usbSync({ udid: device?.udid, app: appId }));
            } catch (e) {
              setJob({
                status: "error",
                copied: 0,
                skipped: 0,
                failed: 0,
                total: 0,
                message: String((e as Error).message),
                device: null,
                error: String((e as Error).message),
              });
            } finally {
              setBusy(false);
            }
          }}
        >
          <Usb size={15} /> Copy library to iPhone
        </button>
        {running && (
          <button className="btn btn-danger" onClick={() => api.usbCancel()}>Cancel</button>
        )}
      </div>
      {device && usb && appId === "vlc" && device.apps.length > 0 && !device.apps.includes("vlc") && (
        <p style={{ color: "var(--gold)" }}>VLC is not on this iPhone. Install it from the App Store first.</p>
      )}
      {job && job.status !== "idle" && (
        <div style={{ marginTop: 12 }}>
          <p style={{ margin: "0 0 6px" }}>{job.message}</p>
          {job.total > 0 && (
            <div className="bar">
              <i style={{ width: `${Math.min(100, ((job.copied + job.skipped + job.failed) / job.total) * 100)}%` }} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function SettingsPage({
  settings,
  health,
  onSave,
  onPick,
  onOpen,
}: {
  settings: AppSettings;
  health: Health | null;
  onSave: (patch: Record<string, string>) => void;
  onPick: () => void;
  onOpen: () => void;
}) {
  const [local, setLocal] = useState(settings);
  useEffect(() => setLocal(settings), [settings]);
  const set = (key: string, value: string) => setLocal({ ...local, [key]: value } as AppSettings);
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2>Settings</h2>
          <p>yt-dlp and ffmpeg run on this machine. Cookies help with private or members-only lists.</p>
        </div>
        <button className="btn btn-primary" onClick={() => onSave({
          library_dir: local.library_dir,
          quality: local.quality,
          concurrent: local.concurrent,
          cookies_path: local.cookies_path,
          sponsorblock: local.sponsorblock,
          yt_client_id: local.yt_client_id || "",
          yt_client_secret: local.yt_client_secret || "",
        })}>Save</button>
      </div>
      <div className="health-row">
        <span className="tag"><i className={`dot ${health?.yt_dlp ? "" : "bad"}`} /> yt-dlp {health?.yt_dlp || "missing"}</span>
        <span className="tag"><i className={`dot ${health?.ffmpeg ? "" : "bad"}`} /> ffmpeg {health?.ffmpeg ? "ready" : "missing"}</span>
      </div>
      <div className="settings-grid">
        <div className="field">
          <label>Library folder</label>
          <input value={local.library_dir} onChange={(e) => set("library_dir", e.target.value)} />
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn btn-sm" onClick={onPick}><FolderSearch size={14} /> Browse</button>
            <button className="btn btn-sm" onClick={onOpen}><FolderOpen size={14} /> Open</button>
          </div>
        </div>
        <div className="field">
          <label>Download quality</label>
          <select value={local.quality} onChange={(e) => set("quality", e.target.value)}>
            {(local.qualities || ["audio-opus"]).map((q) => (
              <option key={q} value={q}>{qualityLabel(q)}</option>
            ))}
          </select>
          <span className="hint">
            Default is Opus audio only: thumbnail as cover art, artist/title/album tags. Use m4a if you play in iPhone Safari;
            VLC and Android handle Opus well. Video presets are still here if you need the picture.
          </span>
        </div>
        <div className="field">
          <label>Parallel downloads</label>
          <select value={local.concurrent} onChange={(e) => set("concurrent", e.target.value)}>
            <option value="1">1 (safest)</option>
            <option value="2">2</option>
            <option value="3">3</option>
          </select>
          <span className="hint">YouTube rate-limits aggressive parallel grabs.</span>
        </div>
        <div className="field">
          <label>cookies.txt path</label>
          <input value={local.cookies_path} onChange={(e) => set("cookies_path", e.target.value)} placeholder="Optional — Netscape cookies export" />
          <span className="hint">Needed for private playlists, Watch Later, and some age-gated videos.</span>
        </div>
        <div className="field">
          <label>SponsorBlock</label>
          <select value={local.sponsorblock} onChange={(e) => set("sponsorblock", e.target.value)}>
            <option value="0">Leave chapters as-is</option>
            <option value="1">Strip sponsor / promo / interaction</option>
          </select>
        </div>
        <div className="field">
          <label>Google OAuth client ID</label>
          <input value={local.yt_client_id || ""} onChange={(e) => set("yt_client_id", e.target.value)} placeholder="xxxx.apps.googleusercontent.com" />
          <span className="hint">YouTube Data API v3. Redirect URI must be {window.location.origin}/api/youtube/callback</span>
        </div>
        <div className="field">
          <label>Google OAuth client secret</label>
          <input type="password" value={local.yt_client_secret || ""} onChange={(e) => set("yt_client_secret", e.target.value)} />
          <span className="hint">Stored only on this PC. Used to merge playlists on your YouTube account.</span>
        </div>
      </div>
    </div>
  );
}

function MergePage({
  playlists,
  settings,
  onToast,
  onSaveSettings,
}: {
  playlists: Playlist[];
  settings: AppSettings | null;
  onToast: (msg: string, err?: boolean) => void;
  onSaveSettings: (patch: Record<string, string>) => Promise<void>;
}) {
  const [status, setStatus] = useState<YoutubeStatus | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set(playlists.map((p) => p.id)));
  const [mode, setMode] = useState<"song" | "video">("song");
  const [dest, setDest] = useState<"new" | "existing">("new");
  const [title, setTitle] = useState("Merged playlist");
  const [privacy, setPrivacy] = useState("private");
  const [destinationId, setDestinationId] = useState("");
  const [mine, setMine] = useState<{ id: string; title: string; item_count?: number }[]>([]);
  const [preview, setPreview] = useState<YoutubePreview | null>(null);
  const [job, setJob] = useState<YoutubeJob | null>(null);
  const [clientId, setClientId] = useState(settings?.yt_client_id || "");
  const [clientSecret, setClientSecret] = useState(settings?.yt_client_secret || "");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setPicked(new Set(playlists.map((p) => p.id)));
  }, [playlists]);
  useEffect(() => {
    setClientId(settings?.yt_client_id || "");
    setClientSecret(settings?.yt_client_secret || "");
  }, [settings]);

  useEffect(() => {
    api.youtubeStatus().then(setStatus).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!status?.connected) return;
    api.youtubeMine().then((r) => setMine(r.playlists)).catch(() => undefined);
  }, [status?.connected]);

  useEffect(() => {
    const es = new EventSource("/api/events");
    es.addEventListener("youtube", (ev) => {
      setJob(JSON.parse((ev as MessageEvent).data));
    });
    return () => es.close();
  }, []);

  const toggle = (id: string) => {
    const next = new Set(picked);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setPicked(next);
    setPreview(null);
  };

  const runPreview = async () => {
    try {
      setBusy(true);
      setPreview(await api.youtubePreview({ playlist_ids: [...picked], mode }));
    } catch (e) {
      onToast(String((e as Error).message), true);
    } finally {
      setBusy(false);
    }
  };

  const runMerge = async () => {
    try {
      setBusy(true);
      const started = await api.youtubeMerge({
        playlist_ids: [...picked],
        mode,
        title,
        privacy,
        destination_id: dest === "existing" ? destinationId : null,
        add_to_library: true,
      });
      setJob(started);
    } catch (e) {
      onToast(String((e as Error).message), true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2>Merge on YouTube</h2>
          <p>Build one playlist on your account from the shelves in YT Desk, dropping duplicate videos or the same song.</p>
        </div>
      </div>

      {!status?.connected && (
        <div className="steps" style={{ marginBottom: 22, maxWidth: 720 }}>
          <div className="step">
            <b>1. Enable the YouTube Data API</b>
            <p>
              In Google Cloud Console create a project, enable <b>YouTube Data API v3</b>, set the OAuth consent
              screen (External, add your Google account as a test user), then create an OAuth client.
              Desktop or Web both work. Add this redirect URI:
            </p>
            <div className="code">{status?.redirect_uri || `${window.location.origin}/api/youtube/callback`}</div>
          </div>
          <div className="step">
            <b>2. Paste the client credentials</b>
            <div className="settings-grid" style={{ marginTop: 10 }}>
              <div className="field">
                <label>Client ID</label>
                <input value={clientId} onChange={(e) => setClientId(e.target.value)} />
              </div>
              <div className="field">
                <label>Client secret</label>
                <input type="password" value={clientSecret} onChange={(e) => setClientSecret(e.target.value)} />
              </div>
            </div>
            <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
              <button
                className="btn"
                onClick={() => onSaveSettings({ yt_client_id: clientId, yt_client_secret: clientSecret }).then(() => onToast("Saved credentials"))}
              >
                Save
              </button>
              <button
                className="btn btn-primary"
                onClick={async () => {
                  try {
                    await onSaveSettings({ yt_client_id: clientId, yt_client_secret: clientSecret });
                    await api.youtubeConnect();
                    onToast("Complete sign-in in the browser window");
                  } catch (e) {
                    onToast(String((e as Error).message), true);
                  }
                }}
              >
                Connect YouTube
              </button>
            </div>
            {status?.error && <p style={{ color: "var(--danger)" }}>{status.error}</p>}
          </div>
        </div>
      )}

      {status?.connected && (
        <>
          <div className="health-row">
            <span className="tag"><i className="dot" /> {status.channel}</span>
            <button className="btn btn-sm" onClick={() => api.youtubeDisconnect().then(() => api.youtubeStatus().then(setStatus))}>Disconnect</button>
          </div>

          <div className="section-kicker">Playlists to merge</div>
          <div className="filters">
            <button className="chip" onClick={() => { setPicked(new Set(playlists.map((p) => p.id))); setPreview(null); }}>All</button>
            <button className="chip" onClick={() => { setPicked(new Set()); setPreview(null); }}>None</button>
          </div>
          <div className="merge-list">
            {playlists.map((p) => (
              <label key={p.id} className="merge-item">
                <input className="check" type="checkbox" checked={picked.has(p.id)} onChange={() => toggle(p.id)} />
                <span>
                  <b>{p.title}</b>
                  <span className="faint"> {p.video_count} videos</span>
                </span>
              </label>
            ))}
          </div>

          <div className="section-kicker">Duplicates</div>
          <div className="filters">
            <button className={`chip ${mode === "song" ? "active" : ""}`} onClick={() => { setMode("song"); setPreview(null); }}>Same song</button>
            <button className={`chip ${mode === "video" ? "active" : ""}`} onClick={() => { setMode("video"); setPreview(null); }}>Same YouTube ID only</button>
          </div>
          <p style={{ color: "var(--muted)", marginTop: 0 }}>
            {mode === "song"
              ? "Keeps the first upload of each song (official / lyrics / Topic copies collapse)."
              : "Only skips the exact same video ID listed more than once."}
          </p>

          <div className="section-kicker">Destination on your account</div>
          <div className="filters">
            <button className={`chip ${dest === "new" ? "active" : ""}`} onClick={() => setDest("new")}>New playlist</button>
            <button className={`chip ${dest === "existing" ? "active" : ""}`} onClick={() => setDest("existing")}>Add to one I already have</button>
          </div>
          {dest === "new" ? (
            <div className="settings-grid" style={{ maxWidth: 640 }}>
              <div className="field">
                <label>Title</label>
                <input value={title} onChange={(e) => setTitle(e.target.value)} />
              </div>
              <div className="field">
                <label>Privacy</label>
                <select value={privacy} onChange={(e) => setPrivacy(e.target.value)}>
                  <option value="private">Private</option>
                  <option value="unlisted">Unlisted</option>
                  <option value="public">Public</option>
                </select>
              </div>
            </div>
          ) : (
            <div className="field" style={{ maxWidth: 480 }}>
              <label>Your playlist</label>
              <select value={destinationId} onChange={(e) => setDestinationId(e.target.value)}>
                <option value="">Choose…</option>
                {mine.map((p) => (
                  <option key={p.id} value={p.id}>{p.title}</option>
                ))}
              </select>
            </div>
          )}

          <div className="page-actions" style={{ margin: "18px 0" }}>
            <button className="btn" disabled={busy || picked.size === 0} onClick={runPreview}>Preview unique list</button>
            <button className="btn btn-primary" disabled={busy || !preview || preview.over_limit || (dest === "existing" && !destinationId)} onClick={runMerge}>
              <GitMerge size={15} /> Merge on YouTube
            </button>
            {job?.status === "running" && (
              <button className="btn btn-danger" onClick={() => api.youtubeCancel()}>Cancel</button>
            )}
          </div>

          {preview && (
            <div className="step" style={{ marginBottom: 16 }}>
              <b>{preview.kept} unique · {preview.dropped} dropped from {preview.source_count}</b>
              <p>
                {Object.entries(preview.reasons).map(([k, n]) => `${n} ${k}`).join(" · ") || "Nothing dropped"}
                {` · ~${preview.quota_estimate} API quota`}
              </p>
              {preview.quota_warning && <p style={{ color: "var(--gold)" }}>This is a large merge. YouTube’s daily quota is 10,000 units (50 per video added).</p>}
              {preview.over_limit && <p style={{ color: "var(--danger)" }}>Over 5,000 videos — YouTube will reject the playlist.</p>}
            </div>
          )}

          {job && job.status !== "idle" && (
            <div className="step">
              <b>{job.status === "running" ? "Writing to YouTube…" : job.status}</b>
              <p>{job.message}</p>
              {job.total > 0 && <div className="bar"><i style={{ width: `${Math.min(100, ((job.added + job.skipped + job.failed) / job.total) * 100)}%` }} /></div>}
              {job.playlist_url && (
                <p><a href={job.playlist_url} target="_blank" rel="noreferrer">{job.playlist_url}</a></p>
              )}
            </div>
          )}

          <div className="section-kicker">Drop duplicates on a playlist you already own</div>
          <p style={{ color: "var(--muted)" }}>Removes extra copies in place. First occurrence stays.</p>
          <div className="page-actions">
            <select value={destinationId} onChange={(e) => setDestinationId(e.target.value)}>
              <option value="">Choose a playlist you own…</option>
              {mine.map((p) => (
                <option key={p.id} value={p.id}>{p.title}</option>
              ))}
            </select>
            <button
              className="btn"
              disabled={!destinationId || busy}
              onClick={async () => {
                if (!confirm("Remove duplicate entries from this YouTube playlist? This cannot be undone.")) return;
                try {
                  setJob(await api.youtubeClean({ playlist_id: destinationId, mode }));
                } catch (e) {
                  onToast(String((e as Error).message), true);
                }
              }}
            >
              Clean duplicates
            </button>
          </div>
        </>
      )}
    </div>
  );
}

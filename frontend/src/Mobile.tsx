import { useEffect, useMemo, useState } from "react";
import { ChevronDown, Download, Pause, Play, SkipBack, SkipForward } from "lucide-react";
import { api, m3uUrl, mediaUrl } from "./api";
import { formatDuration } from "./format";
import type { MobileLibrary, Video } from "./types";

function readToken(): string {
  const params = new URLSearchParams(window.location.search);
  return params.get("t") || sessionStorage.getItem("ytdesk-token") || "";
}

export function Mobile() {
  const [token, setToken] = useState(readToken);
  const [pin, setPin] = useState("");
  const [error, setError] = useState("");
  const [lib, setLib] = useState<MobileLibrary | null>(null);
  const [tab, setTab] = useState<string>("all");
  const [current, setCurrent] = useState<Video | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [playing, setPlaying] = useState(false);

  useEffect(() => {
    if (!token) return;
    sessionStorage.setItem("ytdesk-token", token);
    const url = new URL(window.location.href);
    if (!url.searchParams.get("t")) {
      url.searchParams.set("t", token);
      window.history.replaceState(null, "", url.toString());
    }
    api
      .mobileLibrary(token)
      .then(setLib)
      .catch((e) => {
        setError(String(e.message || e));
        setToken("");
        sessionStorage.removeItem("ytdesk-token");
      });
  }, [token]);

  const queue = useMemo(() => {
    if (!lib) return [];
    if (tab === "all" || tab === "loose") {
      if (tab === "loose") return lib.loose;
      return lib.videos;
    }
    const pl = lib.playlists.find((p) => p.id === tab);
    return pl?.videos || [];
  }, [lib, tab]);

  const playAt = (video: Video) => {
    setCurrent(video);
    setPlaying(true);
    setExpanded(true);
  };

  const skip = (dir: number) => {
    if (!current) return;
    const i = queue.findIndex((v) => v.id === current.id);
    const next = queue[i + dir];
    if (next) setCurrent(next);
  };

  if (!token) {
    return (
      <div className="pair">
        <img src="/favicon.svg" width={48} height={48} alt="" />
        <h1>YT Desk</h1>
        <p style={{ color: "var(--muted)", maxWidth: 320 }}>
          On the same Wi‑Fi as your PC. Enter the 6-digit PIN from the Phone sync screen, or scan the QR code.
        </p>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setError("");
            try {
              const r = await api.pair(pin.trim());
              setToken(r.token);
            } catch (err) {
              setError(String((err as Error).message || "Wrong PIN"));
            }
          }}
        >
          <input
            className="pin-input"
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={6}
            placeholder="000000"
            value={pin}
            onChange={(e) => setPin(e.target.value.replace(/\D/g, "").slice(0, 6))}
          />
          <div>
            <button className="btn btn-primary" type="submit" disabled={pin.length !== 6}>
              Connect
            </button>
          </div>
        </form>
        {error && <p style={{ color: "var(--danger)" }}>{error}</p>}
      </div>
    );
  }

  return (
    <div className="mobile">
      <div className="mobile-top">
        <div>
          <h1>Library</h1>
          <div style={{ color: "var(--muted)" }}>{lib ? `${lib.count} downloaded` : "Loading…"}</div>
        </div>
        {token && (
          <a className="btn btn-sm" href={m3uUrl(token, tab !== "all" && tab !== "loose" ? tab : undefined)}>
            M3U
          </a>
        )}
      </div>
      <div className="m-tabs">
        <button className={`chip ${tab === "all" ? "active" : ""}`} onClick={() => setTab("all")}>All</button>
        {lib?.playlists.map((p) => (
          <button key={p.id} className={`chip ${tab === p.id ? "active" : ""}`} onClick={() => setTab(p.id)}>
            {p.title}
          </button>
        ))}
      </div>
      <div className="m-list">
        {queue.map((v) => (
          <button key={v.id} className="m-card" onClick={() => playAt(v)}>
            <img src={v.thumbnail || ""} alt="" />
            <div>
              <b>{v.title}</b>
              <span>
                {v.channel} · {formatDuration(v.duration)}
              </span>
            </div>
          </button>
        ))}
        {lib && queue.length === 0 && (
          <div className="empty">
            <h3>Nothing downloaded yet</h3>
            <p>Download videos on the PC, then pull to refresh this page.</p>
          </div>
        )}
      </div>

      {current && !expanded && (
        <button className="nowplay" onClick={() => setExpanded(true)}>
          <img src={current.thumbnail || ""} alt="" />
          <div style={{ textAlign: "left" }}>
            <b style={{ display: "block", fontSize: 13 }}>{current.title}</b>
            <span style={{ color: "var(--muted)", fontSize: 12 }}>{current.channel}</span>
          </div>
          <span className="icon-btn">{playing ? <Pause size={18} /> : <Play size={18} />}</span>
        </button>
      )}

      {current && expanded && (
        <div className="expand-player">
          <button className="btn btn-ghost" onClick={() => setExpanded(false)} style={{ alignSelf: "flex-start" }}>
            <ChevronDown size={18} /> Down
          </button>
          {current.thumbnail && (
            <img
              src={current.thumbnail}
              alt=""
              style={{ width: "100%", borderRadius: 12, margin: "12px 0", objectFit: "cover", maxHeight: 320 }}
            />
          )}
          <audio
            key={current.id}
            src={mediaUrl(current.id, token)}
            controls
            autoPlay
            playsInline
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onEnded={() => skip(1)}
            style={{ width: "100%" }}
          />
          <h2 style={{ fontFamily: "var(--display)", margin: "8px 0 4px" }}>{current.title}</h2>
          <p style={{ color: "var(--muted)", margin: 0 }}>{current.channel}</p>
          <div style={{ display: "flex", gap: 8, marginTop: 16, justifyContent: "center" }}>
            <button className="icon-btn" onClick={() => skip(-1)}><SkipBack /></button>
            <a className="btn" href={mediaUrl(current.id, token)} download>
              <Download size={15} /> Save on phone
            </a>
            <button className="icon-btn" onClick={() => skip(1)}><SkipForward /></button>
          </div>
        </div>
      )}
    </div>
  );
}

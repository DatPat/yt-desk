# YT Desk

A local desktop app for YouTube playlists: paste URLs, browse every video, spot duplicates across lists, download with **yt-dlp**, and sync the library to an **Android** or **iOS** phone on the same Wi‑Fi.

## What it does

- Add one or many playlist URLs (watch URLs with `list=` work; a single video becomes its own shelf)
- Browse titles, channels, durations, and thumbnails
- Highlight videos that appear in more than one playlist, plus repeats inside a single list
- Queue downloads through yt-dlp. Default is **Opus audio** with embedded cover art and tags (m4a / mp3 / video still available)
- One file per video even when it lives in several playlists
- Phone sync:
  - USB copy onto Android (Windows File transfer) or into VLC on iPhone (Mac)
  - QR code → mobile player (stream or save) on the same Wi‑Fi
  - WebDAV folder for Files / Solid Explorer / CX
  - M3U for VLC or Infuse

Nothing is uploaded. The PC is the library.

## Requirements

Already expected on this machine:

- Python 3.11+
- Node.js 18+ (to build the UI once)
- `yt-dlp` and `ffmpeg` on PATH

## Run

From `D:\yt-desk` in PowerShell:

```powershell
.\start.ps1
```

That creates a virtualenv, installs Python deps, builds the UI if needed, and opens a native window.

To open in the browser instead:

```powershell
$env:YTDESK_BROWSER = "1"
.\.venv\Scripts\python.exe run.py
```

UI: [http://127.0.0.1:8765](http://127.0.0.1:8765)

### Dev (hot reload)

```powershell
.\.venv\Scripts\python.exe run.py
# other terminal
cd frontend
npm install
npm run dev
```

Then set `YTDESK_DEV=1` before `run.py` if you want the desktop window pointed at Vite (`http://127.0.0.1:5173`).

## Phone sync

1. Download the tracks you want on the computer.
2. Open **Phone sync**.

### Android over USB (Windows)

Plug the phone in with a data cable, unlock it, and set USB to **File transfer / MTP**. Open **Phone sync → Android** and click **Copy library onto this phone**. Files land in `This PC\<phone>\Internal shared storage\Music`. Each YT Desk playlist is written to `Music\Playlists` as a `.m3u8` file (relative paths to the tracks in Music). Names already there are skipped. Play them in **VLC** or Files.

If USB debugging is on, the same button uses `adb push` instead.

### iPhone over USB (Mac)

On a Mac, plug the iPhone in with a data cable, unlock it, tap **Trust This Computer**, install **VLC** on the phone, then click **Copy library to iPhone**. Files land in VLC → Files as `YT Desk/`. Already-copied names are skipped. Opus plays in VLC, not in Apple Music.

### Wi‑Fi

Stay on the same network as the phone. Scan the QR code, or type the PIN on `http://<pc-ip>:8765/m`.

**Stream:** play in the phone browser / home-screen app while the PC is on.

**Keep files on the phone:**

| Device | How |
| --- | --- |
| iPhone / iPad | Files → Connect to Server → WebDAV URL. User `ytdesk`, password = PIN. Copy into On My iPhone. Or tap **Save on phone** in the player (Safari). |
| Android | Solid Explorer / CX / Material Files → WebDAV, same credentials. Or Chrome download from the player. |

VLC: open the M3U URL from the sync page.

## Merge playlists on YouTube

**Merge on YouTube** signs into your Google account (YouTube Data API) and writes a new playlist — or appends to one you own — with duplicate videos/songs dropped. You need a Google Cloud OAuth client; the screen walks through the redirect URI.

Quota: each video added costs 50 units (default daily cap 10,000). Preview shows the estimate before anything is written.

Windows Firewall may ask to allow Python on private networks the first time — allow it, or phones will not connect.

## Library location

Defaults to `Videos\YT Desk` (Windows). Database and PIN live in `%LOCALAPPDATA%\YTDesk`.

Private / members playlists: export a Netscape `cookies.txt` and set its path in Settings.

## Note

Use this for content you are allowed to download. YouTube’s terms may restrict ripping streams.

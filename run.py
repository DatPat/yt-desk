from __future__ import annotations

import os
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import HOST, PORT  # noqa: E402


def wait_until_up(timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{PORT}/api/health"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.15)
    return False


def start_server() -> None:
    import uvicorn

    uvicorn.run(
        "backend.app:app",
        host=HOST,
        port=PORT,
        log_level="info",
        reload=False,
    )


def main() -> None:
    os.chdir(ROOT)
    thread = threading.Thread(target=start_server, daemon=True)
    thread.start()
    if not wait_until_up():
        print(f"Server failed to start on port {PORT}", file=sys.stderr)
        sys.exit(1)

    url = os.environ.get("YTDESK_URL")
    if not url:
        url = (
            f"http://127.0.0.1:5173"
            if os.environ.get("YTDESK_DEV")
            else f"http://127.0.0.1:{PORT}"
        )

    if os.environ.get("YTDESK_BROWSER"):
        webbrowser.open(url)
        thread.join()
        return

    try:
        import webview

        webview.create_window(
            "YT Desk",
            url,
            width=1440,
            height=920,
            min_size=(960, 640),
            background_color="#0b0b0d",
        )
        webview.start()
    except Exception as exc:
        print(f"Native window unavailable ({exc}); opening the browser instead.")
        webbrowser.open(url)
        thread.join()


if __name__ == "__main__":
    main()

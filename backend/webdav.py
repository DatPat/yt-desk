from __future__ import annotations

import os
from pathlib import Path

from .db import db


def build_dav_app():
    try:
        from wsgidav.fs_dav_provider import FilesystemProvider
        from wsgidav.wsgidav_app import WsgiDAVApp
    except ImportError:
        return None

    class PinDomainController:
        def __init__(self, wsgidav_app=None, config=None):
            self.wsgidav_app = wsgidav_app
            self.config = config

        def get_domain_realm(self, path_info, environ):
            return "/dav"

        def require_authentication(self, realm, environ):
            return True

        def basic_auth_user(self, realm, user_name, password, environ):
            pin = db.get_setting("sync_pin")
            return user_name == "ytdesk" and password == pin and bool(pin)

        def supports_http_digest_auth(self):
            return False

        def is_share_anonymous(self, path_info):
            return False

    class LiveFilesystemProvider(FilesystemProvider):
        def _root(self) -> str:
            root = str(Path(db.get_setting("library_dir") or ".").expanduser().resolve())
            Path(root).mkdir(parents=True, exist_ok=True)
            return root

        def _loc_to_file_path(self, path, environ=None):
            root_path = os.path.abspath(self._root())
            rel = (path or "/").replace("\\", "/").lstrip("/")
            abs_path = os.path.abspath(os.path.join(root_path, rel))
            if os.path.commonpath([abs_path, root_path]) != root_path:
                raise RuntimeError("Blocked path outside the library folder")
            return abs_path

    library = Path(db.get_setting("library_dir") or ".")
    library.mkdir(parents=True, exist_ok=True)
    config = {
        "provider_mapping": {"/": LiveFilesystemProvider(str(library))},
        "http_authenticator": {
            "domain_controller": PinDomainController,
            "accept_basic": True,
            "accept_digest": False,
            "default_to_digest": False,
            "trusted_auth_header": None,
        },
        "simple_dc": {"user_mapping": {"*": True}},
        "verbose": 0,
        "dir_browser": {"enable": True},
        "mount_path": "/dav",
    }
    return WsgiDAVApp(config)

from __future__ import annotations

# Production behavior must normally be installed explicitly by the bot, monitor,
# report, discovery and system-check entry points.  The one startup compatibility
# shim below is enabled only inside the isolated runtime-state publish steps.

import base64
import json
import os


if os.getenv("BBVG_RUNTIME_STATE_BRANCH", "").strip():
    import requests

    _original_session_request = requests.sessions.Session.request

    def _runtime_state_large_file_request(self, method, url, *args, **kwargs):
        response = _original_session_request(self, method, url, *args, **kwargs)
        if method.upper() != "GET" or "/contents/state.json" not in str(url):
            return response
        if response.status_code != 200:
            return response
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError):
            return response
        if not isinstance(payload, dict) or payload.get("content"):
            return response
        download_url = str(payload.get("download_url") or "").strip()
        if not download_url:
            return response
        raw_response = _original_session_request(
            self,
            "GET",
            download_url,
            timeout=kwargs.get("timeout", 20),
        )
        if raw_response.status_code != 200 or not raw_response.content:
            return response
        payload["content"] = base64.b64encode(raw_response.content).decode("ascii")
        payload["encoding"] = "base64"
        response._content = json.dumps(payload).encode("utf-8")
        response.encoding = "utf-8"
        return response

    requests.sessions.Session.request = _runtime_state_large_file_request

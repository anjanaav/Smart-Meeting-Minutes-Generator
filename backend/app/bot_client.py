"""Backend -> bot server requests (the bot receives join requests from the backend)."""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger(__name__)


class BotUnreachable(RuntimeError):
    pass


class BotRejected(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class BotClient:
    def __init__(self, shared_secret: str = "", timeout: int = 15, urlopen=None):
        self._secret, self._timeout = shared_secret, timeout
        self._urlopen = urlopen or urllib.request.urlopen

    def _post(self, base_url: str, path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            base_url.rstrip("/") + path, data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "X-Bot-Secret": self._secret},
        )
        try:
            with self._urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            raise BotRejected(exc.code, f"bot server answered HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise BotUnreachable(base_url) from None

    def join(self, base_url: str, payload: dict) -> dict:
        return self._post(base_url, "/api/join", payload)

    def leave(self, base_url: str, meeting_id: str) -> dict:
        return self._post(base_url, "/api/leave", {"meeting_id": meeting_id})

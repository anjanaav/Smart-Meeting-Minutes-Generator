"""Authentication.

* Users: Firebase ID tokens (``Authorization: Bearer <id_token>``). Passwords are handled by Firebase
  Authentication; this backend never sees them.
* Bot: a random per-meeting token (``Authorization: Bot <token>``). Only its SHA-256 hash is stored.
  The bot never receives a user's credentials.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from functools import wraps

from flask import current_app, g, request

from .errors import ApiError

log = logging.getLogger(__name__)


class TokenVerifier:
    def verify(self, token: str) -> dict:  # -> {"uid", "email", "name"}
        raise NotImplementedError


class FirebaseVerifier(TokenVerifier):
    def __init__(self, project_id: str = ""):
        self._project_id = project_id

    def verify(self, token: str) -> dict:
        from firebase_admin import auth

        from .firebase_app import ensure_firebase_app

        ensure_firebase_app(self._project_id)
        try:
            decoded = auth.verify_id_token(token)
        except Exception as exc:  # expired, revoked, malformed, wrong project...
            log.info("ID token rejected: %s", type(exc).__name__)
            raise ApiError(401, "INVALID_TOKEN", "Your session has expired. Please sign in again.") from exc
        return {"uid": decoded["uid"], "email": decoded.get("email", ""), "name": decoded.get("name", "")}


class DevVerifier(TokenVerifier):
    """Development only (AUTH_MODE=dev). Token format: ``dev:<uid>[:<email>]``."""

    def verify(self, token: str) -> dict:
        parts = token.split(":")
        if len(parts) < 2 or parts[0] != "dev" or not parts[1]:
            raise ApiError(401, "INVALID_TOKEN", "Invalid development token.")
        return {"uid": parts[1], "email": parts[2] if len(parts) > 2 else "", "name": ""}


def _bearer(scheme: str) -> str:
    header = request.headers.get("Authorization", "")
    prefix = scheme + " "
    if not header.startswith(prefix):
        return ""
    return header[len(prefix):].strip()


def require_user(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = _bearer("Bearer")
        if not token:
            raise ApiError(401, "AUTH_REQUIRED", "Sign in to continue.")
        g.user = current_app.extensions["smmg"].verifier.verify(token)
        return fn(*args, **kwargs)

    return wrapper


def new_bot_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def check_bot_token(meeting: dict) -> None:
    """Raise unless the request carries the bot token issued for this meeting."""
    token = _bearer("Bot")
    stored = meeting.get("bot_token_hash") or ""
    if not token or not stored or not hmac.compare_digest(hash_token(token), stored):
        raise ApiError(401, "INVALID_BOT_TOKEN", "Bot authentication failed.")

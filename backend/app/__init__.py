"""Smart Meeting Minutes Generator - Flask backend (API, ASR, NLP, minutes generation, storage)."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

from .asr import WhisperEngine
from .auth import DevVerifier, FirebaseVerifier
from .bot_client import BotClient
from .config import Config
from .errors import ApiError
from .generator import MinutesGenerator, build_provider
from .jitsi import default_resolver
from .pipeline import Pipeline, probe_mean_db
from .store import FirestoreStore, MemoryStore

log = logging.getLogger(__name__)
_DEFAULT: Any = object()


@dataclass
class Services:
    cfg: Config
    store: Any
    verifier: Any
    pipeline: Pipeline
    bot_client: Any
    resolver: Any


def _error(status: int, code: str, message: str):
    return jsonify({"error": {"code": code, "message": message}}), status


def create_app(cfg: Config = None, *, store=None, verifier=None, asr=None, provider=_DEFAULT, bot_client=None,
               resolver=None, probe=probe_mean_db, executor=None, start_background: bool = True) -> Flask:
    cfg = cfg or Config.from_env()
    cfg.validate()
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = cfg.max_audio_chunk_bytes + 1024 * 1024
    if cfg.auth_mode == "dev":
        log.warning("AUTH_MODE=dev: tokens are NOT verified against Firebase. Never use this in production.")

    store = store or (MemoryStore() if cfg.store_backend == "memory" else FirestoreStore(cfg.firebase_project_id))
    verifier = verifier or (DevVerifier() if cfg.auth_mode == "dev" else FirebaseVerifier(cfg.firebase_project_id))
    provider = build_provider(cfg) if provider is _DEFAULT else provider
    generator = MinutesGenerator(provider, cfg.llm_window_chars, cfg.llm_overlap_segments)
    pipeline = Pipeline(cfg, store, asr or WhisperEngine(cfg.whisper_model), generator, executor=executor, probe=probe)
    app.extensions["smmg"] = Services(cfg, store, verifier, pipeline, bot_client or BotClient(cfg.bot_shared_secret),
                                      resolver or default_resolver)

    from .routes import bp
    app.register_blueprint(bp)

    @app.errorhandler(ApiError)
    def _api_error(e):
        return _error(e.status, e.code, e.message)

    @app.errorhandler(404)
    def _404(_e):
        return _error(404, "NOT_FOUND", "Not found.")

    @app.errorhandler(405)
    def _405(_e):
        return _error(405, "METHOD_NOT_ALLOWED", "Method not allowed.")

    @app.errorhandler(413)
    def _413(_e):
        return _error(413, "PAYLOAD_TOO_LARGE", "The upload is too large.")

    @app.errorhandler(HTTPException)
    def _http_error(e):
        # Werkzeug's own errors (400 malformed upload, 415, 408, ...) keep their status instead of becoming a 500.
        code = (e.name or "ERROR").upper().replace(" ", "_")
        return _error(e.code or 500, code, e.description or "The request could not be processed.")

    @app.errorhandler(Exception)
    def _unexpected(e):
        if type(e).__module__.startswith(("google.", "grpc")):  # Firestore / network failures
            log.error("database error: %s", type(e).__name__)
            return _error(503, "DATABASE_UNAVAILABLE", "The database is temporarily unavailable. Please try again.")
        log.exception("unhandled error")
        return _error(500, "INTERNAL_ERROR", "Something went wrong. Please try again.")

    if start_background:
        def loop():
            stop = threading.Event()
            while not stop.wait(cfg.sweep_interval_seconds):
                try:
                    pipeline.sweep()
                except Exception:
                    log.exception("sweep failed")
        threading.Thread(target=loop, name="smmg-sweeper", daemon=True).start()
    return app

"""Runtime configuration. Everything comes from environment variables (see .env.example).

Secrets (OPENAI_API_KEY, Firebase credentials, BOT_SHARED_SECRET) live only on the server.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name, default).strip()
    # A value that is only a comment ("KEY=   # explanation" copied from a template and parsed badly) means "not set".
    return default if value.startswith("#") else value


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name) or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(_env(name) or default)
    except ValueError:
        return default


def _list(name: str) -> tuple:
    return tuple(x.strip() for x in _env(name).split(",") if x.strip())


@dataclass(frozen=True)
class Config:
    env: str = "development"                 # development | production
    auth_mode: str = "firebase"              # firebase | dev (dev is refused in production)
    firebase_project_id: str = ""
    store_backend: str = "firestore"         # firestore | memory

    # Temporary processing data (audio chunks). Never kept permanently.
    temp_dir: str = "tmp_audio"
    temp_retention_minutes: int = 60         # failed jobs may be retried within this window
    bot_idle_timeout_minutes: int = 10       # auto-finalize if the bot goes silent
    max_audio_chunk_bytes: int = 30 * 1024 * 1024
    min_audio_db: float = -55.0              # mean volume above this counts as real audio

    # LLM (minutes generation)
    llm_provider: str = "openai"             # openai | rules
    openai_api_key: str = field(default="", repr=False)
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    llm_window_chars: int = 24000
    llm_overlap_segments: int = 6
    llm_timeout_seconds: int = 120

    # ASR
    whisper_model: str = "base"
    whisper_language: str = ""               # empty = auto-detect
    min_transcript_words: int = 5

    # Bot server (Node.js)
    bot_server_url: str = ""
    bot_server_allowlist: tuple = ()
    bot_shared_secret: str = field(default="", repr=False)
    public_backend_url: str = ""             # URL the bot uses to reach this backend
    bot_display_name: str = "Smart Meeting Minutes Bot"

    # Jitsi
    default_jitsi_server: str = "https://meet.jit.si"
    jitsi_allowed_hosts: tuple = ()          # empty = any public host

    worker_threads: int = 2
    sweep_interval_seconds: int = 60

    @classmethod
    def from_env(cls) -> "Config":
        cfg = cls(
            env=_env("APP_ENV", "development"),
            auth_mode=_env("AUTH_MODE", "firebase"),
            firebase_project_id=_env("FIREBASE_PROJECT_ID"),
            store_backend=_env("STORE_BACKEND", "firestore"),
            temp_dir=_env("TEMP_DIR", "tmp_audio"),
            temp_retention_minutes=_int("TEMP_RETENTION_MINUTES", 60),
            bot_idle_timeout_minutes=_int("BOT_IDLE_TIMEOUT_MINUTES", 10),
            max_audio_chunk_bytes=_int("MAX_AUDIO_CHUNK_BYTES", 30 * 1024 * 1024),
            min_audio_db=_float("MIN_AUDIO_DB", -55.0),
            llm_provider=_env("LLM_PROVIDER", "openai"),
            openai_api_key=_env("OPENAI_API_KEY"),
            openai_model=_env("OPENAI_MODEL", "gpt-4o-mini"),
            openai_base_url=_env("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            llm_window_chars=_int("LLM_WINDOW_CHARS", 24000),
            llm_overlap_segments=_int("LLM_OVERLAP_SEGMENTS", 6),
            llm_timeout_seconds=_int("LLM_TIMEOUT_SECONDS", 120),
            whisper_model=_env("WHISPER_MODEL", "base"),
            whisper_language=_env("WHISPER_LANGUAGE"),
            min_transcript_words=_int("MIN_TRANSCRIPT_WORDS", 5),
            bot_server_url=_env("BOT_SERVER_URL"),
            bot_server_allowlist=_list("BOT_SERVER_ALLOWLIST"),
            bot_shared_secret=_env("BOT_SHARED_SECRET"),
            public_backend_url=_env("PUBLIC_BACKEND_URL"),
            bot_display_name=_env("BOT_DISPLAY_NAME", "Smart Meeting Minutes Bot"),
            default_jitsi_server=_env("DEFAULT_JITSI_SERVER", "https://meet.jit.si"),
            jitsi_allowed_hosts=_list("JITSI_ALLOWED_HOSTS"),
            worker_threads=_int("WORKER_THREADS", 2),
            sweep_interval_seconds=_int("SWEEP_INTERVAL_SECONDS", 60),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if self.env == "production" and self.auth_mode == "dev":
            raise ValueError("AUTH_MODE=dev is not allowed when APP_ENV=production")
        if self.env == "production" and self.store_backend == "memory":
            raise ValueError("STORE_BACKEND=memory is not allowed when APP_ENV=production")
        if self.auth_mode not in ("firebase", "dev"):
            raise ValueError("AUTH_MODE must be 'firebase' or 'dev'")
        if self.store_backend not in ("firestore", "memory"):
            raise ValueError("STORE_BACKEND must be 'firestore' or 'memory'")

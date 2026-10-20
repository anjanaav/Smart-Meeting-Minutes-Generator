"""Lazy, shared initialisation of the Firebase Admin SDK (server-side credentials only)."""
from __future__ import annotations

import threading

_lock = threading.Lock()


def ensure_firebase_app(project_id: str = ""):
    import firebase_admin  # imported lazily so the backend runs without it in dev/test

    with _lock:
        if not firebase_admin._apps:
            options = {"projectId": project_id} if project_id else None
            # Credentials come from GOOGLE_APPLICATION_CREDENTIALS (service-account JSON on the server).
            firebase_admin.initialize_app(options=options)
        return firebase_admin.get_app()

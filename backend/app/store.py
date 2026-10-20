"""Persistence. Only final outputs and essential meeting metadata are stored.

Raw audio and intermediate processing data never reach the store (they live in a temp directory that is
deleted after processing, see pipeline.py).

Layout (Firestore):
  users/{uid}                              profile
  meetings/{meeting_id}                    metadata, status, summary, key points, decisions, participants
  meetings/{meeting_id}/segments/chunk_NNNN     {"items": [transcript segments]}   (200 per doc)
  meetings/{meeting_id}/action_items/ai_NNNN    one action item per doc
"""
from __future__ import annotations

import copy
import threading
from datetime import datetime, timezone
from typing import Callable, Optional

ACTIVE_STATUSES = ("bot_joining", "waiting", "joined", "capturing", "disconnected", "processing")
LIVE_STATUSES = ("bot_joining", "waiting", "joined", "capturing", "disconnected")
OPEN_STATUSES = ("created",) + ACTIVE_STATUSES   # a meeting a user can still be "in"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    # -- users
    def upsert_user(self, uid: str, data: dict) -> dict: raise NotImplementedError
    def get_user(self, uid: str) -> Optional[dict]: raise NotImplementedError

    # -- meetings
    def create_meeting(self, meeting: dict) -> dict: raise NotImplementedError
    def get_meeting(self, meeting_id: str) -> Optional[dict]: raise NotImplementedError

    def mutate_meeting(self, meeting_id: str, fn: Callable[[dict], Optional[dict]]):
        """Atomically apply ``fn(meeting) -> fields_to_update | None``. Returns (applied, meeting).

        This is the single primitive used for status transitions, so "finalize exactly once" is a
        compare-and-set performed inside the store's transaction.
        """
        raise NotImplementedError

    def create_or_get_open(self, meeting: dict):
        """Atomically return (existing_open_meeting, False) for the same owner+room, else create and return (meeting, True)."""
        raise NotImplementedError
    def list_meetings(self, owner_uid: str, limit: int = 50, status: Optional[str] = None) -> list: raise NotImplementedError
    def list_meetings_by_status(self, statuses: tuple) -> list: raise NotImplementedError

    # -- outputs
    def save_output(self, meeting_id: str, output: dict, meeting_fields: dict) -> None: raise NotImplementedError
    def get_output(self, meeting_id: str) -> Optional[dict]: raise NotImplementedError


class MemoryStore(Store):
    """In-process store for tests and local development without Firebase."""

    def __init__(self):
        self._lock = threading.RLock()
        self._users: dict = {}
        self._meetings: dict = {}
        self._outputs: dict = {}

    def upsert_user(self, uid, data):
        with self._lock:
            user = self._users.setdefault(uid, {"uid": uid, "created_at": utcnow_iso()})
            user.update(data)
            return copy.deepcopy(user)

    def get_user(self, uid):
        with self._lock:
            return copy.deepcopy(self._users.get(uid))

    def create_meeting(self, meeting):
        with self._lock:
            self._meetings[meeting["id"]] = copy.deepcopy(meeting)
            return copy.deepcopy(meeting)

    def get_meeting(self, meeting_id):
        with self._lock:
            m = self._meetings.get(meeting_id)
            return copy.deepcopy(m) if m else None

    def mutate_meeting(self, meeting_id, fn):
        with self._lock:
            current = self._meetings.get(meeting_id)
            if current is None:
                raise KeyError(meeting_id)
            snapshot = copy.deepcopy(current)
            fields = fn(snapshot)
            if fields is None:
                return False, snapshot
            current.update(copy.deepcopy(fields))
            return True, copy.deepcopy(current)

    def create_or_get_open(self, meeting):
        with self._lock:
            for m in self._meetings.values():
                if m["owner_uid"] == meeting["owner_uid"] and m.get("room_key") == meeting["room_key"] and m["status"] in OPEN_STATUSES:
                    return copy.deepcopy(m), False
            self._meetings[meeting["id"]] = copy.deepcopy(meeting)
            return copy.deepcopy(meeting), True

    def list_meetings(self, owner_uid, limit=50, status=None):
        with self._lock:
            rows = [m for m in self._meetings.values() if m["owner_uid"] == owner_uid and (status is None or m["status"] == status)]
            rows.sort(key=lambda m: m["created_at"], reverse=True)
            return copy.deepcopy(rows[:limit])

    def list_meetings_by_status(self, statuses):
        with self._lock:
            return copy.deepcopy([m for m in self._meetings.values() if m["status"] in statuses])

    def save_output(self, meeting_id, output, meeting_fields):
        with self._lock:
            if meeting_id not in self._meetings:
                raise KeyError(meeting_id)
            self._outputs[meeting_id] = copy.deepcopy(output)
            self._meetings[meeting_id].update(copy.deepcopy(meeting_fields))

    def get_output(self, meeting_id):
        with self._lock:
            out = self._outputs.get(meeting_id)
            return copy.deepcopy(out) if out else None


def _where(query, field: str, op: str, value):
    """Add a filter using the current ``FieldFilter`` API (positional arguments are deprecated in the client library)."""
    try:
        from google.cloud.firestore_v1.base_query import FieldFilter
    except ImportError:  # very old client library
        return query.where(field, op, value)
    return query.where(filter=FieldFilter(field, op, value))


class FirestoreStore(Store):
    SEGMENTS_PER_DOC = 200
    BATCH_LIMIT = 400   # Firestore allows 500 writes per batch; stay below it

    def __init__(self, project_id: str = "", client=None, transactional=None):
        """``client`` and ``transactional`` exist so tests can inject a fake; in production both come from Firebase."""
        if client is None:
            from firebase_admin import firestore

            from .firebase_app import ensure_firebase_app

            ensure_firebase_app(project_id)
            client = firestore.client()
        if transactional is None:
            from google.cloud import firestore as gcf

            transactional = gcf.transactional
        self._db = client
        self._transactional = transactional

    # users
    def upsert_user(self, uid, data):
        ref = self._db.collection("users").document(uid)
        ref.set({**data, "uid": uid}, merge=True)
        return ref.get().to_dict()

    def get_user(self, uid):
        snap = self._db.collection("users").document(uid).get()
        return snap.to_dict() if snap.exists else None

    # meetings
    def create_meeting(self, meeting):
        self._db.collection("meetings").document(meeting["id"]).set(meeting)
        return meeting

    def get_meeting(self, meeting_id):
        snap = self._db.collection("meetings").document(meeting_id).get()
        return snap.to_dict() if snap.exists else None

    def mutate_meeting(self, meeting_id, fn):
        ref = self._db.collection("meetings").document(meeting_id)

        @self._transactional
        def txn_body(txn):
            snap = ref.get(transaction=txn)
            if not snap.exists:
                raise KeyError(meeting_id)
            data = snap.to_dict()
            fields = fn(dict(data))   # may run more than once if the transaction is retried, so ``fn`` must be pure
            if fields is None:
                return False, data
            txn.update(ref, fields)
            data.update(fields)
            return True, data

        return txn_body(self._db.transaction())

    def create_or_get_open(self, meeting):
        coll = self._db.collection("meetings")

        @self._transactional
        def txn_body(txn):
            q = _where(_where(coll, "owner_uid", "==", meeting["owner_uid"]), "room_key", "==", meeting["room_key"])
            for snap in q.stream(transaction=txn):
                data = snap.to_dict()
                if data["status"] in OPEN_STATUSES:
                    return data, False
            txn.set(coll.document(meeting["id"]), meeting)
            return meeting, True

        return txn_body(self._db.transaction())

    def list_meetings(self, owner_uid, limit=50, status=None):
        q = _where(self._db.collection("meetings"), "owner_uid", "==", owner_uid)
        if status:
            q = _where(q, "status", "==", status)
        q = q.order_by("created_at", direction="DESCENDING").limit(limit)   # needs the composite indexes in firestore.indexes.json
        return [s.to_dict() for s in q.stream()]

    def list_meetings_by_status(self, statuses):
        q = _where(self._db.collection("meetings"), "status", "in", list(statuses))
        return [s.to_dict() for s in q.stream()]

    # outputs
    def _commit_in_batches(self, operations):
        """operations: [("set", ref, data) | ("delete", ref, None)], applied in batches below Firestore's limit."""
        for i in range(0, len(operations), self.BATCH_LIMIT):
            batch = self._db.batch()
            for kind, ref, data in operations[i:i + self.BATCH_LIMIT]:
                batch.set(ref, data) if kind == "set" else batch.delete(ref)
            batch.commit()

    def save_output(self, meeting_id, output, meeting_fields):
        ref = self._db.collection("meetings").document(meeting_id)
        seg_coll, ai_coll = ref.collection("segments"), ref.collection("action_items")
        # Idempotent re-save after a retry: remove whatever an earlier attempt wrote first.
        operations = [("delete", s.reference, None) for coll in (seg_coll, ai_coll) for s in coll.stream()]
        segs = output["transcript"]
        for i in range(0, len(segs), self.SEGMENTS_PER_DOC):
            operations.append(("set", seg_coll.document(f"chunk_{i // self.SEGMENTS_PER_DOC:04d}"), {"items": segs[i:i + self.SEGMENTS_PER_DOC]}))
        for n, item in enumerate(output["action_items"]):
            operations.append(("set", ai_coll.document(f"ai_{n:04d}"), item))
        self._commit_in_batches(operations)
        head = {k: output[k] for k in ("summary", "key_points", "decisions", "generated_by", "warnings", "schema_version")}
        head["participants_final"] = output["participants"]   # live join/leave records stay in `participants`
        # Written last: the meeting only becomes "completed" (and readable) once every part above is in place.
        ref.update({**head, **meeting_fields})

    def get_output(self, meeting_id):
        ref = self._db.collection("meetings").document(meeting_id)
        snap = ref.get()
        if not snap.exists:
            return None
        m = snap.to_dict()
        if m.get("status") != "completed":
            return None
        transcript = []
        for s in ref.collection("segments").order_by("__name__").stream():
            transcript.extend(s.to_dict()["items"])
        items = [s.to_dict() for s in ref.collection("action_items").order_by("__name__").stream()]
        return {
            "schema_version": m.get("schema_version", 1),
            "meeting_id": m["id"],
            "meeting_title": m.get("title") or m.get("room_name"),
            "meeting_date": m.get("started_at") or m.get("created_at"),
            "duration_seconds": m.get("duration_seconds", 0),
            "summary": m.get("summary", ""),
            "key_points": m.get("key_points", []),
            "decisions": m.get("decisions", []),
            "action_items": items,
            "participants": m.get("participants_final", m.get("participants", [])),
            "transcript": transcript,
            "generated_by": m.get("generated_by", "llm"),
            "warnings": m.get("warnings", []),
        }

"""FirestoreStore, run against the strict in-memory fake (see fake_firestore.py) and against the real client library's API.

The same contract tests also run on MemoryStore, so the two stores are held to identical behaviour.
"""
import unittest
from dataclasses import replace

from app.generator import MinutesGenerator
from app.store import FirestoreStore, MemoryStore, OPEN_STATUSES

from .fake_firestore import FakeClient, FakeFirestoreError, fake_transactional
from .helpers import AppCase, audio, seg

try:
    from google.cloud.firestore_v1 import _helpers as fs_helpers
except ImportError:  # pragma: no cover
    fs_helpers = None


def meeting(mid="m1", owner="alice", room_key="meet.jit.si/standup", status="created", created="2026-10-19T10:00:00+00:00",
            started="2026-10-19T10:00:05+00:00"):
    return {"id": mid, "owner_uid": owner, "title": "Standup", "room_name": "standup", "room_key": room_key, "status": status,
            "created_at": created, "started_at": started, "participants": [], "error": None}


def make_output(mid="m1", segments=3, actions=2):
    transcript = [{"id": i, "speaker": "Alice" if i % 2 else "Bob", "text": f"Line {i} about the release plan today",
                   "start_time": float(i * 5), "end_time": float(i * 5 + 4), "uncertain": False} for i in range(1, segments + 1)]
    doc = MinutesGenerator(None).generate({"id": mid, "title": "Standup", "room_name": "standup", "started_at": "2026-10-19T10:00:05+00:00",
                                           "created_at": "2026-10-19T10:00:00+00:00", "duration_seconds": segments * 5}, transcript,
                                          [{"name": "Alice"}, {"name": "Bob"}])
    doc["action_items"] = [{"task": f"Task {n}", "assignee": None, "deadline": None, "status": "pending", "evidence": "e", "segment_ids": [1]}
                           for n in range(actions)]
    return doc


COMPLETED = {"status": "completed", "status_detail": "", "error": None, "duration_seconds": 15, "completed_at": "2026-10-19T11:00:00+00:00",
             "summary_preview": "x", "generated_by": "rule_based", "bot_token_hash": None}


class StoreContract:
    """Behaviour every store must have. Subclasses provide make_store()."""

    def setUp(self):
        self.store = self.make_store()

    def test_mutate_applies_only_when_the_function_returns_fields(self):
        self.store.create_meeting(meeting())
        applied, m = self.store.mutate_meeting("m1", lambda x: {"status": "bot_joining"} if x["status"] == "created" else None)
        self.assertTrue(applied)
        self.assertEqual(m["status"], "bot_joining")
        applied, m = self.store.mutate_meeting("m1", lambda x: {"status": "bot_joining"} if x["status"] == "created" else None)
        self.assertFalse(applied)                                   # compare-and-set: the second claim loses
        self.assertEqual(self.store.get_meeting("m1")["status"], "bot_joining")

    def test_mutating_a_missing_meeting_raises(self):
        with self.assertRaises(KeyError):
            self.store.mutate_meeting("nope", lambda x: {"status": "x"})

    def test_create_or_get_open_does_not_duplicate(self):
        first, created = self.store.create_or_get_open(meeting("m1"))
        again, created2 = self.store.create_or_get_open(meeting("m2"))
        self.assertTrue(created)
        self.assertFalse(created2)
        self.assertEqual(again["id"], "m1")
        self.assertIsNone(self.store.get_meeting("m2"))

    def test_create_or_get_open_reuses_only_open_meetings_of_the_same_owner_and_room(self):
        self.store.create_or_get_open(meeting("m1"))
        _, other_owner = self.store.create_or_get_open(meeting("m2", owner="bob"))
        _, other_room = self.store.create_or_get_open(meeting("m3", room_key="meet.jit.si/other"))
        self.assertTrue(other_owner and other_room)
        self.store.mutate_meeting("m1", lambda x: {"status": "completed"})
        _, after_close = self.store.create_or_get_open(meeting("m4"))
        self.assertTrue(after_close)                                # a finished meeting does not block a new one

    def test_listing_is_per_owner_newest_first_limited_and_filterable(self):
        for i in range(5):
            self.store.create_meeting(meeting(f"a{i}", created=f"2026-10-19T10:0{i}:00+00:00", room_key=f"r{i}"))
        self.store.create_meeting(meeting("b0", owner="bob", room_key="rb"))
        self.store.mutate_meeting("a1", lambda x: {"status": "completed"})
        ids = [m["id"] for m in self.store.list_meetings("alice")]
        self.assertEqual(ids, ["a4", "a3", "a2", "a1", "a0"])
        self.assertEqual([m["id"] for m in self.store.list_meetings("alice", limit=2)], ["a4", "a3"])
        self.assertEqual([m["id"] for m in self.store.list_meetings("alice", status="completed")], ["a1"])
        self.assertEqual([m["id"] for m in self.store.list_meetings("bob")], ["b0"])

    def test_list_by_status_across_owners(self):
        self.store.create_meeting(meeting("a", status="capturing", room_key="r1"))
        self.store.create_meeting(meeting("b", owner="bob", status="joined", room_key="r2"))
        self.store.create_meeting(meeting("c", status="completed", room_key="r3"))
        self.assertEqual({m["id"] for m in self.store.list_meetings_by_status(("capturing", "joined"))}, {"a", "b"})

    def test_saved_output_reads_back_identically(self):
        self.store.create_meeting(meeting())
        out = make_output(segments=7, actions=3)
        # as in the pipeline: the meeting fields saved with the output carry the same duration the document was generated with
        self.store.save_output("m1", out, {**COMPLETED, "duration_seconds": out["duration_seconds"]})
        self.assertEqual(self.store.get_output("m1"), out)
        self.assertEqual(self.store.get_meeting("m1")["status"], "completed")

    def test_saving_again_replaces_the_previous_output(self):
        self.store.create_meeting(meeting())
        self.store.save_output("m1", make_output(segments=450, actions=5), dict(COMPLETED))
        smaller = make_output(segments=3, actions=1)
        self.store.save_output("m1", smaller, dict(COMPLETED))
        got = self.store.get_output("m1")
        self.assertEqual(len(got["transcript"]), 3)                 # no stale segments left from the first save
        self.assertEqual(len(got["action_items"]), 1)

    def test_users(self):
        self.store.upsert_user("u1", {"email": "a@b.c"})
        self.store.upsert_user("u1", {"display_name": "Anjana"})
        user = self.store.get_user("u1")
        self.assertEqual((user["email"], user["display_name"], user["uid"]), ("a@b.c", "Anjana", "u1"))
        self.assertIsNone(self.store.get_user("missing"))


class MemoryStoreContract(StoreContract, unittest.TestCase):
    def make_store(self):
        return MemoryStore()


class FirestoreStoreContract(StoreContract, unittest.TestCase):
    def make_store(self):
        self.client = FakeClient()
        return FirestoreStore(client=self.client, transactional=fake_transactional)

    # ---- Firestore-specific behaviour
    def test_output_is_not_readable_until_the_meeting_is_completed(self):
        self.store.create_meeting(meeting())
        self.assertIsNone(self.store.get_output("m1"))
        self.store.save_output("m1", make_output(), {"status": "processing"})
        self.assertIsNone(self.store.get_output("m1"))             # parts are written, but the head says "not completed"

    def test_large_output_is_split_into_chunks_and_batches_under_the_limit(self):
        self.store.create_meeting(meeting())
        out = make_output(segments=5000, actions=30)
        self.store.save_output("m1", out, dict(COMPLETED))         # the fake raises if any batch exceeds 500 writes
        segment_docs = [p for p in self.client.docs if p[:3] == ("meetings", "m1", "segments")]
        self.assertEqual(len(segment_docs), 25)                    # 5000 / 200
        self.assertEqual(self.store.get_output("m1")["transcript"], out["transcript"])

    def test_every_stored_document_is_accepted_by_the_real_firestore_encoder(self):
        if fs_helpers is None:
            self.skipTest("google-cloud-firestore is not installed")
        self.store.upsert_user("u1", {"email": "a@b.c"})
        self.store.create_meeting(meeting())
        self.store.save_output("m1", make_output(segments=30, actions=3), dict(COMPLETED))
        for path, data in self.client.docs.items():
            try:
                fs_helpers.encode_dict(data)
            except Exception as exc:                               # pragma: no cover
                self.fail(f"{'/'.join(path)} cannot be written to Firestore: {exc}")

    def test_a_query_without_a_declared_index_is_rejected_by_the_fake(self):
        # Guards the fake itself: ordering by created_at with an equality filter on another field needs an index entry.
        q = self.client.collection("meetings").where("room_key", "==", "x").order_by("created_at", direction="DESCENDING")
        with self.assertRaises(FakeFirestoreError):
            list(q.stream())

    def test_reads_after_writes_in_a_transaction_are_rejected_by_the_fake(self):
        txn = self.client.transaction()
        ref = self.client.collection("meetings").document("m1")
        txn.set(ref, {"a": 1})
        with self.assertRaises(FakeFirestoreError):
            ref.get(transaction=txn)


class RealClientApiTests(unittest.TestCase):
    """The fake is only as good as my model of the library, so check the calls the store makes exist in the real one."""

    def setUp(self):
        try:
            from google.cloud import firestore as gcf
            from google.cloud.firestore_v1 import (Client, CollectionReference, DocumentReference, DocumentSnapshot,
                                                   Transaction, WriteBatch)
            from google.cloud.firestore_v1.base_query import FieldFilter
        except ImportError:
            self.skipTest("google-cloud-firestore is not installed")
        self.gcf, self.FieldFilter = gcf, FieldFilter
        self.surface = {Client: ["collection", "transaction", "batch"], CollectionReference: ["where", "order_by", "stream", "document", "limit"],
                        DocumentReference: ["collection", "get", "set", "update", "delete"], DocumentSnapshot: ["to_dict", "exists", "reference"],
                        Transaction: ["set", "update", "get"], WriteBatch: ["set", "delete", "commit"]}

    def test_methods_used_by_the_store_exist(self):
        missing = [f"{cls.__name__}.{name}" for cls, names in self.surface.items() for name in names if not hasattr(cls, name)]
        self.assertEqual(missing, [])

    def test_transactional_decorator_and_descending_constant(self):
        self.assertTrue(callable(self.gcf.transactional))
        self.assertEqual(self.gcf.Query.DESCENDING, "DESCENDING")   # the store passes the literal string

    def test_field_filter_exposes_what_the_fake_reads(self):
        f = self.FieldFilter("status", "in", ["a", "b"])
        self.assertEqual((f.field_path, f.op_string, f.value), ("status", "in", ["a", "b"]))


class FirestorePathEndToEnd(AppCase):
    """The real API, pipeline and exactly-once finalize, but persisting through FirestoreStore on the fake."""

    def setUp(self):
        super().setUp()
        self.store = FirestoreStore(client=FakeClient(), transactional=fake_transactional)
        self.build()

    def test_full_flow_duplicate_complete_and_isolation(self):
        mid, token = self.start()
        self.upload(mid, token, audio(seg(0, 4, "We will ship version two on the fifteenth."), seg(4, 8, "I will send the release notes by Friday.")))
        self.assertEqual(self.status(mid)["status"], "capturing")
        self.assertEqual(self.complete(mid, token).status_code, 202)
        again = self.complete(mid, token)                                      # duplicate: the token was revoked when processing was claimed
        self.assertEqual((again.status_code, again.get_json()["error"]["code"]), (401, "INVALID_BOT_TOKEN"))
        self.assertEqual(self.status(mid)["status"], "completed")
        self.assertEqual(len(self.asr.calls), 1)
        minutes = self.client.get(f"/api/meetings/{mid}/minutes", headers=self.hdr()).get_json()
        self.assertGreaterEqual(len(minutes["transcript"]), 1)
        self.assertEqual(self.client.get(f"/api/meetings/{mid}/minutes", headers=self.hdr("mallory")).status_code, 404)
        self.assertEqual(self.client.get(f"/api/meetings/{mid}/export?format=txt", headers=self.hdr("mallory")).status_code, 404)
        self.assertEqual(self.client.get(f"/api/meetings/{mid}/export?format=txt", headers=self.hdr()).status_code, 200)
        self.assertEqual([m["id"] for m in self.client.get("/api/meetings", headers=self.hdr()).get_json()["meetings"]], [mid])
        self.assertEqual(self.client.get("/api/meetings", headers=self.hdr("mallory")).get_json()["meetings"], [])

    def test_creating_the_same_room_twice_reuses_the_record(self):
        first = self.create().get_json()["meeting"]["id"]
        second = self.create()
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.get_json()["meeting"]["id"], first)
        self.assertTrue(second.get_json()["reused"])

    def test_open_statuses_are_what_the_dedupe_query_expects(self):
        self.assertIn("created", OPEN_STATUSES)
        self.assertNotIn("completed", OPEN_STATUSES)
        self.assertNotIn("failed", OPEN_STATUSES)


if __name__ == "__main__":
    unittest.main()

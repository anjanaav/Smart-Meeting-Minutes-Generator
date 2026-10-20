import json
import os
import unittest
from dataclasses import replace

from tests.helpers import AppCase, audio, seg

STANDUP = audio(seg(0, 4, "We decided to ship version two on the fifteenth."), seg(5, 9, "Thanks everyone, that is all for today."))
BOB = audio(seg(0, 4, "I will send the release notes by Friday."))


class AuthAndValidationTests(AppCase):
    def test_every_user_endpoint_requires_a_valid_token(self):
        for method, path in [("get", "/api/meetings"), ("post", "/api/meetings"), ("get", "/api/meetings/x"), ("get", "/api/meetings/x/status"),
                             ("get", "/api/meetings/x/minutes"), ("get", "/api/meetings/x/export"), ("post", "/api/meetings/x/stop"),
                             ("post", "/api/meetings/x/retry"), ("post", "/api/meetings/x/bot/join"), ("get", "/api/users/me")]:
            self.assertEqual(getattr(self.client, method)(path).status_code, 401, path)
        bad = self.client.get("/api/meetings", headers={"Authorization": "Bearer nonsense"})
        self.assertEqual((bad.status_code, bad.get_json()["error"]["code"]), (401, "INVALID_TOKEN"))

    def test_health_is_public_and_leaks_no_secrets(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("key", json.dumps(r.get_json()).lower())

    def test_consent_and_link_validation(self):
        r = self.client.post("/api/meetings", json={"meeting_url": "https://meet.jit.si/abc"}, headers=self.hdr())
        self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (400, "CONSENT_REQUIRED"))
        r = self.create(url="https://localhost/room")
        self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (400, "INVALID_MEETING_LINK"))
        r = self.client.post("/api/meetings", data="not json", headers=self.hdr(), content_type="application/json")
        self.assertEqual(r.get_json()["error"]["code"], "INVALID_JSON")

    def test_profile_upsert(self):
        r = self.client.post("/api/users/profile", json={"display_name": "  Alice\x00 "}, headers={"Authorization": "Bearer dev:alice:a@x.com"})
        self.assertEqual((r.get_json()["display_name"], r.get_json()["email"]), ("Alice", "a@x.com"))
        self.assertEqual(self.client.get("/api/users/me", headers=self.hdr()).get_json()["display_name"], "Alice")

    def test_duplicate_meeting_records_are_not_created(self):
        a = self.create().get_json()
        b = self.create(url="https://MEET.jit.si/Standup-Room").get_json()
        self.assertEqual((a["reused"], b["reused"], a["meeting"]["id"] == b["meeting"]["id"]), (False, True, True))
        self.assertEqual(len(self.client.get("/api/meetings", headers=self.hdr()).get_json()["meetings"]), 1)
        other = self.create("bob").get_json()
        self.assertNotEqual(other["meeting"]["id"], a["meeting"]["id"])  # another user gets their own record


class UserIsolationTests(AppCase):
    def test_other_users_cannot_see_or_touch_a_meeting(self):
        mid, token = self.start("alice")
        self.upload(mid, token, STANDUP)
        self.complete(mid, token)
        for method, path in [("get", f"/api/meetings/{mid}"), ("get", f"/api/meetings/{mid}/status"), ("get", f"/api/meetings/{mid}/minutes"),
                             ("get", f"/api/meetings/{mid}/export"), ("post", f"/api/meetings/{mid}/stop"), ("post", f"/api/meetings/{mid}/retry"),
                             ("post", f"/api/meetings/{mid}/bot/join")]:
            r = getattr(self.client, method)(path, headers=self.hdr("mallory"))
            self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (404, "NOT_FOUND"), path)
        self.assertEqual(self.client.get("/api/meetings", headers=self.hdr("mallory")).get_json()["meetings"], [])
        self.assertEqual(self.client.get(f"/api/meetings/{mid}/minutes", headers=self.hdr("alice")).status_code, 200)

    def test_history_lists_only_own_meetings_newest_first(self):
        a1, _ = self.start("alice", "https://meet.jit.si/room-one")
        a2, _ = self.start("alice", "https://meet.jit.si/room-two")
        b1, _ = self.start("bob", "https://meet.jit.si/room-one")
        ids = [m["id"] for m in self.client.get("/api/meetings", headers=self.hdr("alice")).get_json()["meetings"]]
        self.assertEqual(ids, [a2, a1])
        self.assertEqual([m["id"] for m in self.client.get("/api/meetings", headers=self.hdr("bob")).get_json()["meetings"]], [b1])

    def test_public_view_never_exposes_secrets(self):
        mid, token = self.start()
        text = json.dumps(self.client.get(f"/api/meetings/{mid}", headers=self.hdr()).get_json())
        for secret in (token, "bot_token_hash", "owner_uid", "alice"):
            self.assertNotIn(secret, text)


class BotAuthTests(AppCase):
    def test_bot_endpoints_reject_missing_wrong_and_user_tokens(self):
        mid, token = self.start()
        for headers in ({}, self.bot_h("wrong"), self.hdr(), {"Authorization": f"Bearer {token}"}):
            r = self.client.post(f"/api/bot/meetings/{mid}/events", json={"type": "heartbeat"}, headers=headers)
            self.assertEqual(r.status_code, 401, headers)
        self.assertEqual(self.client.post("/api/bot/meetings/unknown/events", json={"type": "heartbeat"}, headers=self.bot_h(token)).status_code, 401)

    def test_token_is_scoped_to_its_own_meeting(self):
        m1, t1 = self.start("alice", "https://meet.jit.si/room-one")
        m2, t2 = self.start("alice", "https://meet.jit.si/room-two")
        self.assertEqual(self.event(m2, t1, type="heartbeat").status_code, 401)
        self.assertEqual(self.event(m2, t2, type="heartbeat").status_code, 200)

    def test_token_stored_only_as_hash_and_revoked_after_completion(self):
        mid, token = self.start()
        stored = self.store.get_meeting(mid)["bot_token_hash"]
        self.assertNotEqual(stored, token)
        self.upload(mid, token, STANDUP)
        self.complete(mid, token)
        self.assertEqual(self.event(mid, token, type="heartbeat").status_code, 401)


class BotJoinTests(AppCase):
    def test_backend_sends_scoped_join_request_to_bot(self):
        self.build(cfg=replace(self.cfg, bot_server_url="http://192.168.1.5:3000"))
        mid, token = self.start()
        url, payload = self.bot.joins[0]
        self.assertEqual(url, "http://192.168.1.5:3000")
        self.assertEqual((payload["meeting_id"], payload["server"], payload["room"]), (mid, "https://meet.jit.si", "standup-room"))
        self.assertTrue(payload["backend_url"].startswith("http"))
        self.assertNotIn("alice", json.dumps(payload))   # no user credentials or ids are forwarded to the bot

    def test_bot_server_allowlist_blocks_arbitrary_urls(self):
        self.build(cfg=replace(self.cfg, bot_server_url="http://192.168.1.5:3000"))
        mid = self.create().get_json()["meeting"]["id"]
        r = self.client.post(f"/api/meetings/{mid}/bot/join", json={"bot_server_url": "http://169.254.169.254"}, headers=self.hdr())
        self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (400, "BOT_SERVER_NOT_ALLOWED"))
        self.assertEqual(self.bot.joins, [])

    def test_production_requires_configured_bot_server(self):
        self.build(cfg=replace(self.cfg, env="staging", bot_server_url=""))
        mid = self.create().get_json()["meeting"]["id"]
        r = self.client.post(f"/api/meetings/{mid}/bot/join", json={"bot_server_url": "http://x:3000"}, headers=self.hdr())
        self.assertEqual(r.get_json()["error"]["code"], "BOT_SERVER_NOT_CONFIGURED")

    def test_unreachable_bot_is_reported_and_never_shown_as_connected(self):
        self.bot.unreachable = True
        mid = self.create().get_json()["meeting"]["id"]
        r = self.client.post(f"/api/meetings/{mid}/bot/join", json={"bot_server_url": "http://10.0.2.2:3000"}, headers=self.hdr())
        self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (502, "BOT_UNREACHABLE"))
        s = self.status(mid)
        self.assertEqual((s["status"], s["error"]["code"], s["audio_confirmed"]), ("failed", "BOT_UNREACHABLE", False))

    def test_rejected_join_and_idempotent_rejoin(self):
        self.bot.reject = True
        mid = self.create().get_json()["meeting"]["id"]
        self.assertEqual(self.client.post(f"/api/meetings/{mid}/bot/join", json={}, headers=self.hdr()).get_json()["error"]["code"], "BOT_REJECTED")
        self.bot.reject = False
        mid2, _ = self.start("alice", "https://meet.jit.si/other-room")
        again = self.client.post(f"/api/meetings/{mid2}/bot/join", json={}, headers=self.hdr())
        self.assertEqual(again.status_code, 200)
        self.assertEqual(len(self.bot.joins), 1)   # the bot was not sent twice


class LifecycleTests(AppCase):
    def test_states_are_only_reported_when_confirmed(self):
        mid, token = self.start()
        self.assertEqual(self.status(mid)["status"], "bot_joining")
        self.event(mid, token, type="waiting")
        self.assertEqual(self.status(mid)["status"], "waiting")
        self.event(mid, token, type="joined")
        s = self.status(mid)
        self.assertEqual((s["status"], s["audio_confirmed"]), ("joined", False))   # joined, but audio NOT confirmed
        r = self.upload(mid, token, STANDUP)
        self.assertEqual((r.status_code, r.get_json()["audio_confirmed"]), (201, True))
        s = self.status(mid)
        self.assertEqual((s["status"], s["audio_confirmed"]), ("capturing", True))

    def test_silent_audio_never_confirms_capture(self):
        mid, token = self.start()
        self.event(mid, token, type="joined")
        r = self.upload(mid, token, b"SILENT" + b"\x00" * 100)
        self.assertEqual(r.get_json(), {"stored": False, "reason": "silent"})
        s = self.status(mid)
        self.assertEqual((s["status"], s["audio_confirmed"]), ("joined", False))
        self.assertFalse(self.pipeline.has_audio(mid))

    def test_audio_upload_is_idempotent_and_validated(self):
        mid, token = self.start()
        self.assertEqual(self.upload(mid, token, STANDUP, seq=3).status_code, 201)
        self.assertEqual(self.upload(mid, token, STANDUP, seq=3).get_json()["duplicate"], True)   # safe bot retry
        self.assertEqual(len(self.pipeline._chunks(mid)), 1)
        self.assertEqual(self.upload(mid, token, STANDUP, pid="../evil").status_code, 400)       # path traversal blocked
        self.assertEqual(self.upload(mid, token, b"").status_code, 400)

    def test_participants_tracked_with_join_and_leave_and_bot_hidden(self):
        mid, token = self.start()
        self.event(mid, token, type="participants", participants=[{"id": "p1", "name": "Alice"}, {"id": "bot", "name": self.cfg.bot_display_name}])
        self.event(mid, token, type="participant_joined", id="p2", name="Bob")
        self.assertEqual([p["name"] for p in self.status(mid)["participants"]], ["Alice", "Bob"])
        self.event(mid, token, type="participant_left", id="p2")
        left = {p["name"]: p["left_at"] for p in self.status(mid)["participants"]}
        self.assertIsNone(left["Alice"])
        self.assertIsNotNone(left["Bob"])

    def test_bot_errors_show_clear_server_chosen_messages(self):
        for code in ("PASSWORD_REQUIRED", "LOBBY_REJECTED", "AUDIO_CAPTURE_UNAVAILABLE"):
            mid, token = self.start("alice", f"https://meet.jit.si/room-{code.lower()}")
            self.event(mid, token, type="error", code=code, message="<script>alert(1)</script>")
            s = self.status(mid)
            self.assertEqual((s["status"], s["error"]["code"]), ("failed", code))
            self.assertNotIn("script", s["error"]["message"])

    def test_disconnect_then_rejoin_keeps_captured_audio(self):
        mid, token = self.start()
        self.event(mid, token, type="joined")
        self.upload(mid, token, STANDUP)
        self.event(mid, token, type="disconnected")
        self.assertEqual(self.status(mid)["status"], "disconnected")
        self.event(mid, token, type="joined")
        self.assertEqual(self.status(mid)["status"], "capturing")

    def test_unknown_event_and_closed_meeting(self):
        mid, token = self.start()
        self.assertEqual(self.event(mid, token, type="made_up").status_code, 400)
        self.upload(mid, token, STANDUP)
        self.complete(mid, token)
        mid2, t2 = self.start("alice", "https://meet.jit.si/second")
        self.upload(mid2, t2, STANDUP)
        self.store.mutate_meeting(mid2, lambda m: {"status": "completed"})
        self.assertEqual(self.upload(mid2, t2, STANDUP, seq=9).status_code, 409)


class EndToEndTests(AppCase):
    def run_meeting(self):
        mid, token = self.start()
        self.event(mid, token, type="joined")
        self.event(mid, token, type="participants", participants=[{"id": "p1", "name": "Alice"}, {"id": "p2", "name": "Bob"}])
        self.upload(mid, token, STANDUP, pid="p1", name="Alice", seq=0, offset_ms=0)
        self.upload(mid, token, BOB, pid="p2", name="Bob", seq=0, offset_ms=12000)
        return mid, token

    def test_full_flow_to_saved_exportable_minutes_and_audio_deleted(self):
        mid, token = self.run_meeting()
        self.assertTrue(os.path.isdir(self.pipeline.meeting_dir(mid)))
        r = self.complete(mid, token)
        self.assertEqual(r.status_code, 202)
        self.assertEqual(self.status(mid)["status"], "completed")

        doc = self.client.get(f"/api/meetings/{mid}/minutes", headers=self.hdr()).get_json()
        self.assertEqual([t["speaker"] for t in doc["transcript"]], ["Alice", "Bob"])          # speaker names from Jitsi metadata
        self.assertEqual(doc["transcript"][1]["start_time"], 12.0)                              # chunk offset + ASR time
        self.assertEqual(doc["decisions"][0]["evidence"], "Alice: We decided to ship version two on the fifteenth. Thanks everyone, that is all for today.")
        self.assertEqual(doc["meeting_title"], "standup-room")
        self.assertEqual(doc["generated_by"], "llm")

        txt = self.client.get(f"/api/meetings/{mid}/export?format=txt", headers=self.hdr())
        self.assertEqual(txt.status_code, 200)
        self.assertIn("Decisions Made", txt.get_data(as_text=True))
        pdf = self.client.get(f"/api/meetings/{mid}/export", headers=self.hdr())
        self.assertTrue(pdf.data.startswith(b"%PDF") and "attachment" in pdf.headers["Content-Disposition"])
        self.assertEqual(self.client.get(f"/api/meetings/{mid}/export?format=docx", headers=self.hdr()).status_code, 400)

        self.assertFalse(os.path.exists(self.pipeline.meeting_dir(mid)))                        # raw audio not retained
        stored = json.dumps(self.store.get_meeting(mid))
        self.assertNotIn("audio", stored.replace("audio_confirmed", ""))                        # no audio or intermediates in the record

    def test_finalization_runs_exactly_once(self):
        mid, token = self.run_meeting()
        first = self.complete(mid, token)
        self.assertEqual(first.status_code, 202)
        calls_after_first = len(self.asr.calls)
        self.complete(mid, token, reason="bot_left")                       # duplicate completion events
        self.client.post(f"/api/meetings/{mid}/stop", headers=self.hdr())  # user taps stop afterwards
        self.assertEqual(len(self.asr.calls), calls_after_first)           # nothing was processed twice
        self.assertEqual(len(self.provider.calls), 1)

    def test_minutes_not_available_while_processing_or_before_start(self):
        mid, token = self.start()
        r = self.client.get(f"/api/meetings/{mid}/minutes", headers=self.hdr())
        self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (409, "MINUTES_NOT_READY"))

    def test_stop_asks_bot_to_leave_or_finalizes_if_bot_is_gone(self):
        mid, token = self.run_meeting()
        r = self.client.post(f"/api/meetings/{mid}/stop", headers=self.hdr())
        self.assertEqual((r.get_json()["status"], self.bot.leaves), ("stopping", [mid]))
        self.assertTrue(self.status(mid)["stop_requested"])
        self.assertEqual(self.status(mid)["status"], "capturing")           # bot finishes its upload, then calls /complete
        self.bot.unreachable = True
        r = self.client.post(f"/api/meetings/{mid}/stop", headers=self.hdr())
        self.assertEqual(self.status(mid)["status"], "completed")


if __name__ == "__main__":
    unittest.main()

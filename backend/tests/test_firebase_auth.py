"""FirebaseVerifier and the API running in AUTH_MODE=firebase.

The Firebase Admin SDK is real; only its network-bound call (verify_id_token) is replaced where a valid token would be
needed, because minting a Google-signed token is impossible offline. What this proves: every failure path fails CLOSED
(401), tokens are never logged, development tokens are not accepted, and users are isolated. What it cannot prove:
that a genuine Firebase ID token from your project verifies. That needs your project and a real sign-in.
"""
import unittest
from unittest import mock

try:
    from firebase_admin import auth as fb_auth
except ImportError:  # pragma: no cover
    fb_auth = None

from app import create_app
from app.auth import FirebaseVerifier
from app.config import Config
from app.errors import ApiError
from app.pipeline import SyncExecutor
from app.store import MemoryStore

from .helpers import FakeASR, FakeBot, fake_probe

GOOD = {"alice-token": {"uid": "alice", "email": "alice@example.org", "name": "Alice"},
        "bob-token": {"uid": "bob", "email": "bob@example.org"}}


def fake_verify(token, app=None, check_revoked=False, clock_skew_seconds=0):
    if token in GOOD:
        return GOOD[token]
    if token == "expired-token":
        raise fb_auth.ExpiredIdTokenError("expired", cause=None)
    if token == "revoked-token":
        raise fb_auth.RevokedIdTokenError("revoked")
    raise fb_auth.InvalidIdTokenError("bad token")


@unittest.skipIf(fb_auth is None, "firebase-admin is not installed")
class VerifierUnitTests(unittest.TestCase):
    def setUp(self):
        patches = [mock.patch("app.firebase_app.ensure_firebase_app"), mock.patch("firebase_admin.auth.verify_id_token", side_effect=fake_verify)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.v = FirebaseVerifier("demo-project")

    def test_valid_token_yields_uid_email_name(self):
        self.assertEqual(self.v.verify("alice-token"), {"uid": "alice", "email": "alice@example.org", "name": "Alice"})

    def test_missing_email_and_name_default_to_empty(self):
        self.assertEqual(self.v.verify("bob-token"), {"uid": "bob", "email": "bob@example.org", "name": ""})

    def test_expired_revoked_and_garbage_tokens_are_401_invalid_token(self):
        for token in ("expired-token", "revoked-token", "garbage"):
            with self.assertRaises(ApiError) as ctx:
                self.v.verify(token)
            self.assertEqual((ctx.exception.status, ctx.exception.code), (401, "INVALID_TOKEN"), token)

    def test_unexpected_library_errors_also_fail_closed(self):
        with mock.patch("firebase_admin.auth.verify_id_token", side_effect=ConnectionError("certs unreachable")):
            with self.assertRaises(ApiError) as ctx:
                self.v.verify("alice-token")
        self.assertEqual(ctx.exception.status, 401)

    def test_the_token_itself_is_never_logged(self):
        secret = "eyJ.super-secret-token.value"
        with self.assertLogs("app.auth", level="INFO") as logs:
            with self.assertRaises(ApiError):
                self.v.verify(secret)
        self.assertNotIn(secret, "\n".join(logs.output))


@unittest.skipIf(fb_auth is None, "firebase-admin is not installed")
class RealSdkTests(unittest.TestCase):
    """No mocks for the token check: the real SDK code runs and must reject anything that is not a real ID token."""

    def test_a_development_token_is_rejected_in_firebase_mode(self):
        with self.assertRaises(ApiError) as ctx:
            FirebaseVerifier("demo-project").verify("dev:alice:alice@example.org")
        self.assertEqual((ctx.exception.status, ctx.exception.code), (401, "INVALID_TOKEN"))

    def test_a_random_string_is_rejected(self):
        with self.assertRaises(ApiError) as ctx:
            FirebaseVerifier("demo-project").verify("not-a-jwt")
        self.assertEqual(ctx.exception.status, 401)


@unittest.skipIf(fb_auth is None, "firebase-admin is not installed")
class ApiInFirebaseModeTests(unittest.TestCase):
    def setUp(self):
        for p in (mock.patch("app.firebase_app.ensure_firebase_app"), mock.patch("firebase_admin.auth.verify_id_token", side_effect=fake_verify)):
            p.start()
            self.addCleanup(p.stop)
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = Config(env="test", auth_mode="firebase", store_backend="memory", temp_dir=tmp.name, firebase_project_id="demo-project",
                     bot_server_url="http://10.0.2.2:3000")
        self.app = create_app(cfg, store=MemoryStore(), asr=FakeASR(), provider=None, bot_client=FakeBot(), resolver=lambda h: ["8.8.8.8"],
                              probe=fake_probe, executor=SyncExecutor(), start_background=False)
        self.client = self.app.test_client()

    def h(self, token):
        return {"Authorization": f"Bearer {token}"}

    def test_no_header_and_bad_tokens_are_401(self):
        self.assertEqual(self.client.get("/api/meetings").get_json()["error"]["code"], "AUTH_REQUIRED")
        for token in ("expired-token", "dev:alice", "nonsense"):
            r = self.client.get("/api/meetings", headers=self.h(token))
            self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (401, "INVALID_TOKEN"), token)

    def test_a_bot_token_is_not_accepted_as_a_user_token(self):
        r = self.client.get("/api/meetings", headers={"Authorization": "Bot alice-token"})
        self.assertEqual(r.status_code, 401)

    def test_health_is_public(self):
        self.assertEqual(self.client.get("/api/health").status_code, 200)

    def test_two_firebase_users_cannot_see_each_others_meetings(self):
        r = self.client.post("/api/meetings", json={"meeting_url": "https://meet.jit.si/private-room", "consent": True}, headers=self.h("alice-token"))
        self.assertEqual(r.status_code, 201)
        mid = r.get_json()["meeting"]["id"]
        self.assertEqual([m["id"] for m in self.client.get("/api/meetings", headers=self.h("alice-token")).get_json()["meetings"]], [mid])
        self.assertEqual(self.client.get("/api/meetings", headers=self.h("bob-token")).get_json()["meetings"], [])
        for path in (f"/api/meetings/{mid}", f"/api/meetings/{mid}/status", f"/api/meetings/{mid}/minutes", f"/api/meetings/{mid}/export?format=txt"):
            self.assertEqual(self.client.get(path, headers=self.h("bob-token")).status_code, 404, path)
        for path in (f"/api/meetings/{mid}/stop", f"/api/meetings/{mid}/retry", f"/api/meetings/{mid}/bot/join"):
            self.assertEqual(self.client.post(path, json={}, headers=self.h("bob-token")).status_code, 404, path)

    def test_profile_email_comes_from_the_verified_token_not_the_request(self):
        r = self.client.post("/api/users/profile", json={"display_name": "Alice", "email": "attacker@evil.test"}, headers=self.h("alice-token"))
        self.assertEqual(r.get_json()["email"], "alice@example.org")


if __name__ == "__main__":
    unittest.main()

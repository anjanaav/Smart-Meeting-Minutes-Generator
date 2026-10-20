"""Checks on the deployment files (compose, Dockerfiles, Caddyfile, .env templates) and on the production server (gunicorn).

Run from the repo root:   python3 -m unittest e2e.test_deploy -v        (needs: pyyaml, python-dotenv, flask; node; optional: docker, gunicorn)

What this CAN show: the files parse, the settings they pass are names the code actually reads, the values they set are accepted by the
code's own config validation, only the HTTPS proxy is published, secrets are not hard-coded, and the backend really starts and answers
under gunicorn. What it CANNOT show: that the images build, that Chromium runs inside the bot container, or that certificates are issued.
No Docker daemon was available when this was written, so `docker compose build/up` has never been run.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
import urllib.error
import urllib.request
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

COMPOSE = ROOT / "docker-compose.yml"
SECRET = "s" * 48


def backend_var_names() -> set:
    names = set(re.findall(r'_(?:env|int|float|list)\("([A-Z0-9_]+)"', (ROOT / "backend/app/config.py").read_text()))
    wsgi = (ROOT / "backend/wsgi.py").read_text()
    names |= set(re.findall(r'environ\.get\("([A-Z0-9_]+)"', wsgi))
    return names | {"GOOGLE_APPLICATION_CREDENTIALS"}  # read by the Google client libraries


def bot_var_names() -> set:
    text = (ROOT / "bot/src/config.js").read_text() + (ROOT / "bot/src/index.js").read_text()
    return set(re.findall(r"\bBOT_[A-Z0-9_]+\b", text)) | {"NODE_ENV"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def substitute(value, env: dict) -> str:
    """Resolve ${VAR}, ${VAR:-default} and ${VAR:?message} the way compose does."""
    def one(m):
        name, op, rest = m.group(1), m.group(2), m.group(3)
        if env.get(name):
            return env[name]
        if op == ":-":
            return rest
        if op == ":?":
            raise KeyError(f"{name} is required: {rest}")
        return ""
    return re.sub(r"\$\{([A-Z0-9_]+)(?:(:-|:\?)([^}]*))?\}", one, str(value))


@unittest.skipIf(yaml is None, "pyyaml is not installed")
class ComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = COMPOSE.read_text()
        cls.compose = yaml.safe_load(cls.raw)
        cls.services = cls.compose["services"]
        cls.env = {"SMMG_DOMAIN": "minutes.example.org", "FIREBASE_PROJECT_ID": "demo-project", "BOT_SHARED_SECRET": SECRET}

    def resolved(self, service):
        return {k: substitute(v, self.env) for k, v in self.services[service]["environment"].items()}

    def test_only_the_https_proxy_publishes_ports(self):
        self.assertEqual({n for n, s in self.services.items() if "ports" in s}, {"caddy"})
        self.assertEqual(set(self.services["caddy"]["ports"]), {"80:80", "443:443"})
        for name in ("backend", "bot"):
            self.assertIn("expose", self.services[name])

    def test_required_secrets_must_be_set_and_are_never_hard_coded(self):
        for var in ("SMMG_DOMAIN", "FIREBASE_PROJECT_ID", "BOT_SHARED_SECRET"):
            self.assertRegex(self.raw, rf"\$\{{{var}:\?")
        for service in ("backend", "bot"):
            for key, value in self.services[service]["environment"].items():
                if re.search(r"KEY|SECRET|TOKEN|PASSWORD", key):
                    self.assertTrue(str(value).startswith("${") or value == "", f"{service}.{key} looks hard-coded")
        with self.assertRaises(KeyError):
            substitute("${BOT_SHARED_SECRET:?needed}", {})

    def test_the_firebase_credentials_are_a_mounted_secret_file(self):
        self.assertIn("firebase_service_account", self.compose["secrets"])
        self.assertEqual(self.resolved("backend")["GOOGLE_APPLICATION_CREDENTIALS"], "/run/secrets/firebase_service_account")
        self.assertIn("firebase_service_account", self.services["backend"]["secrets"])
        self.assertNotIn("firebase_service_account", self.services["bot"].get("secrets", []))

    def test_every_variable_passed_is_one_the_code_reads(self):
        self.assertEqual(set(self.services["backend"]["environment"]) - backend_var_names(), set())
        self.assertEqual(set(self.services["bot"]["environment"]) - bot_var_names(), set())

    def test_the_backend_accepts_its_compose_settings_in_production(self):
        from app.config import Config
        with unittest.mock.patch.dict(os.environ, self.resolved("backend"), clear=True):
            cfg = Config.from_env()               # runs the production guards (no dev auth, no memory store)
        self.assertEqual((cfg.env, cfg.auth_mode, cfg.store_backend), ("production", "firebase", "firestore"))
        self.assertEqual(cfg.bot_server_url, "http://bot:3000")
        self.assertEqual(cfg.public_backend_url, "http://backend:5000")

    def test_the_bot_accepts_its_compose_settings(self):
        env = {**self.resolved("bot"), "NODE_ENV": "production"}
        code = "const {loadConfig}=require('./src/config');const c=loadConfig(JSON.parse(process.argv[1]));console.log(JSON.stringify(c))"
        r = subprocess.run(["node", "-e", code, json.dumps(env)], cwd=ROOT / "bot", capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = json.loads(r.stdout)
        self.assertEqual((cfg["driver"], cfg["host"], cfg["port"]), ("playwright", "0.0.0.0", 3000))

    def test_backend_and_bot_point_at_each_other_and_at_nothing_else(self):
        backend, bot = self.resolved("backend"), self.resolved("bot")
        self.assertEqual(backend["BOT_SERVER_URL"], "http://bot:3000")
        self.assertIn("3000", self.services["bot"]["expose"])
        self.assertEqual(bot["BOT_BACKEND_ALLOWLIST"], backend["PUBLIC_BACKEND_URL"])           # the bot only accepts this backend
        self.assertIn("5000", self.services["backend"]["expose"])
        self.assertEqual(backend["BOT_SHARED_SECRET"], bot["BOT_SHARED_SECRET"])                # one value, same on both sides

    def test_the_bot_accepts_the_backend_address_the_backend_hands_it(self):
        code = ("const {parseBackendUrl}=require('./src/security');const cfg=JSON.parse(process.argv[1]);"
                "console.log(parseBackendUrl(process.argv[2],cfg))")
        cfg = {"allowHttpBackend": True, "backendAllowlist": [self.resolved("bot")["BOT_BACKEND_ALLOWLIST"]]}
        r = subprocess.run(["node", "-e", code, json.dumps(cfg), self.resolved("backend")["PUBLIC_BACKEND_URL"]], cwd=ROOT / "bot", capture_output=True, text=True, timeout=30)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "http://backend:5000"), r.stderr)

    def test_raw_audio_is_memory_only(self):
        tmpfs = self.services["backend"]["tmpfs"]
        self.assertTrue(any(t.startswith("/data/tmp_audio") for t in tmpfs))
        self.assertEqual(self.resolved("backend")["TEMP_DIR"], "/data/tmp_audio")
        mounted = [v if isinstance(v, str) else v.get("target") for v in self.services["backend"].get("volumes", [])]
        self.assertFalse(any("tmp_audio" in str(m) for m in mounted), "raw audio must not be on a persistent volume")

    def test_caddy_proxies_to_the_backend_and_gets_the_domain(self):
        caddyfile = (ROOT / "deploy/Caddyfile").read_text()
        self.assertIn("{$SMMG_DOMAIN}", caddyfile)
        self.assertIn("reverse_proxy backend:5000", caddyfile)
        self.assertIn("SMMG_DOMAIN", self.services["caddy"]["environment"])
        self.assertIn("backend", self.services["caddy"]["depends_on"])
        targets = re.findall(r"(?m)^\s*reverse_proxy\s+(\S+)", caddyfile)
        self.assertEqual(targets, ["backend:5000"])              # the bot is never reachable through the proxy

    @unittest.skipUnless(shutil.which("docker"), "docker is not installed")
    def test_docker_compose_accepts_the_file_and_rejects_missing_required_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for item in ("docker-compose.yml", "deploy", "backend", "bot"):
                src = ROOT / item
                shutil.copytree(src, tmp / item, ignore=shutil.ignore_patterns("node_modules", "__pycache__", "tests")) if src.is_dir() else shutil.copy(src, tmp / item)
            (tmp / "deploy/secrets").mkdir(exist_ok=True)
            (tmp / "deploy/secrets/firebase-service-account.json").write_text("{}")
            clean = {k: v for k, v in os.environ.items() if k not in ("SMMG_DOMAIN", "FIREBASE_PROJECT_ID", "BOT_SHARED_SECRET")}
            bad = subprocess.run(["docker", "compose", "config", "--quiet"], cwd=tmp, env=clean, capture_output=True, text=True, timeout=60)
            self.assertNotEqual(bad.returncode, 0, "compose must refuse to start while required values are missing")
            self.assertRegex(bad.stderr, r"SMMG_DOMAIN|FIREBASE_PROJECT_ID|BOT_SHARED_SECRET")
            good = subprocess.run(["docker", "compose", "config", "--quiet"], cwd=tmp, capture_output=True, text=True, timeout=60,
                                  env={**clean, "SMMG_DOMAIN": "minutes.example.org", "FIREBASE_PROJECT_ID": "demo", "BOT_SHARED_SECRET": SECRET})
            self.assertEqual(good.returncode, 0, good.stderr)


class DockerfileTests(unittest.TestCase):
    def test_backend_image(self):
        text = (ROOT / "backend/Dockerfile").read_text()
        self.assertRegex(text, r"(?m)^USER smmg")                      # not root
        self.assertIn("ffmpeg", text)
        self.assertIn("HEALTHCHECK", text)
        self.assertRegex(text, r'"--workers", "1"')                    # one process: audio is on this container's disk
        self.assertIn("wsgi:app", text)
        for copied in ("requirements.txt", "app", "wsgi.py"):
            self.assertTrue((ROOT / "backend" / copied).exists(), copied)
        self.assertRegex((ROOT / "backend/requirements.txt").read_text(), r"(?m)^gunicorn")

    def test_bot_image(self):
        text = (ROOT / "bot/Dockerfile").read_text()
        self.assertRegex(text, r"(?m)^USER smmg")
        self.assertIn("playwright install --with-deps chromium", text)
        self.assertIn("HEALTHCHECK", text)
        for copied in ("package.json", "src"):
            self.assertTrue((ROOT / "bot" / copied).exists(), copied)
        self.assertIn(".env", (ROOT / "bot/.dockerignore").read_text())

    def test_secrets_never_enter_an_image_build_context(self):
        for svc in ("backend", "bot"):
            ignore = (ROOT / svc / ".dockerignore").read_text()
            self.assertIn(".env", ignore)
        self.assertIn("service-account", (ROOT / "backend/.dockerignore").read_text())
        self.assertIn("deploy/secrets", (ROOT / ".gitignore").read_text())


class EnvTemplateTests(unittest.TestCase):
    FILES = ("backend/.env.example", "bot/.env.example", "deploy/.env.example")

    def test_no_value_is_a_comment_in_disguise(self):
        for name in self.FILES:
            for key, value in dotenv_values(ROOT / name).items():
                self.assertNotIn("#", value or "", f"{name}: {key} would be read with the comment as its value")

    def test_every_variable_is_one_the_code_reads(self):
        known = {"backend/.env.example": backend_var_names(), "bot/.env.example": bot_var_names()}
        compose_names = set(re.findall(r"\$\{([A-Z0-9_]+)", COMPOSE.read_text()))
        known["deploy/.env.example"] = compose_names | {"TEMP_TMPFS_SIZE"}
        for name, allowed in known.items():
            self.assertEqual(set(dotenv_values(ROOT / name)) - allowed, set(), f"{name} defines variables nothing reads (typo?)")

    def test_every_variable_compose_requires_or_uses_is_in_the_template(self):
        names = set(re.findall(r"\$\{([A-Z0-9_]+)", COMPOSE.read_text()))
        self.assertEqual(names - set(dotenv_values(ROOT / "deploy/.env.example")), set())

    def test_an_untouched_backend_template_gives_defaults_not_comment_text(self):
        from app.config import Config
        values = {k: v for k, v in dotenv_values(ROOT / "backend/.env.example").items() if v is not None}
        values.update(AUTH_MODE="dev", STORE_BACKEND="memory", BOT_SHARED_SECRET=SECRET)   # only what a trial needs
        with unittest.mock.patch.dict(os.environ, values, clear=True):
            cfg = Config.from_env()
        self.assertEqual((cfg.whisper_language, cfg.jitsi_allowed_hosts, cfg.openai_api_key, cfg.public_backend_url), ("", (), "", ""))

    def test_a_pasted_comment_as_a_value_is_treated_as_unset(self):
        from app.config import Config
        env = {"AUTH_MODE": "dev", "STORE_BACKEND": "memory", "WHISPER_LANGUAGE": "# empty = auto-detect", "OPENAI_API_KEY": "# paste your key",
               "JITSI_ALLOWED_HOSTS": "# empty = any", "WHISPER_MODEL": "# tiny | base"}
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            cfg = Config.from_env()
        self.assertEqual((cfg.whisper_language, cfg.openai_api_key, cfg.jitsi_allowed_hosts, cfg.whisper_model), ("", "", (), "base"))


@unittest.skipUnless(shutil.which("gunicorn"), "gunicorn is not installed")
class GunicornTests(unittest.TestCase):
    """The same command the backend image runs, on a free port, with in-memory dev settings."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.port = free_port()
        env = {**os.environ, "APP_ENV": "development", "AUTH_MODE": "dev", "STORE_BACKEND": "memory", "LLM_PROVIDER": "rules",
               "BOT_SHARED_SECRET": SECRET, "BOT_SERVER_URL": "http://127.0.0.1:1", "TEMP_DIR": cls.tmp.name}
        cls.proc = subprocess.Popen(["gunicorn", "--workers", "1", "--threads", "8", "--timeout", "180", "--bind", f"127.0.0.1:{cls.port}", "wsgi:app"],
                                    cwd=ROOT / "backend", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        deadline = time.time() + 40
        while time.time() < deadline:
            if cls.proc.poll() is not None:
                raise RuntimeError("gunicorn exited: " + cls.proc.stderr.read()[-800:])
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{cls.port}/api/health", timeout=2)
                return
            except OSError:
                time.sleep(0.3)
        cls.proc.kill()
        raise RuntimeError("gunicorn did not become ready")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            cls.proc.kill()
        cls.tmp.cleanup()

    def call(self, path, headers=None, data=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers=headers or {}, data=data)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode() or "{}"), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}"), dict(e.headers)

    def test_health_answers_the_way_the_apps_test_button_expects(self):
        status, body, _ = self.call("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(set(body), {"ok", "service", "version", "asr", "llm", "store"})

    def test_protected_routes_refuse_anonymous_callers_and_unknown_routes_are_json(self):
        status, body, _ = self.call("/api/meetings")
        self.assertEqual((status, body["error"]["code"]), (401, "AUTH_REQUIRED"))
        status, body, _ = self.call("/api/nope")
        self.assertEqual((status, body["error"]["code"]), (404, "NOT_FOUND"))

    def test_unicode_titles_and_per_user_isolation_work_through_a_real_server(self):
        """Real HTTP through gunicorn. (An IP-literal Jitsi host is used so no DNS lookup is needed.)"""
        h = {"Authorization": "Bearer dev:alice", "Content-Type": "application/json"}
        body = json.dumps({"meeting_url": "https://8.8.8.8/deploy-check-room", "title": "മലയാളം യോഗം", "consent": True}).encode()
        status, created, _ = self.call("/api/meetings", h, body)
        self.assertEqual(status, 201, created)
        mid = created["meeting"]["id"]
        self.assertEqual(created["meeting"]["title"], "മലയാളം യോഗം")
        status, listing, _ = self.call("/api/meetings", h)
        self.assertEqual([m["id"] for m in listing["meetings"]], [mid])
        status, other, _ = self.call("/api/meetings", {"Authorization": "Bearer dev:mallory"})
        self.assertEqual(other["meetings"], [])


if __name__ == "__main__":
    unittest.main()

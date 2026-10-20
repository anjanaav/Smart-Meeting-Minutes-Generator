'use strict';

// Runtime configuration. Everything comes from environment variables (see .env.example).
// The shared secret must match BOT_SHARED_SECRET on the Flask backend.

const SAMPLE_RATE = 16000; // what the ASR engine and the backend expect (mono, 16-bit PCM)

function num(env, name, fallback) {
  const raw = (env[name] || '').trim();
  if (raw === '') return fallback;
  const value = Number(raw);
  if (!Number.isFinite(value)) throw new Error(`${name} must be a number`);
  return value;
}

function bool(env, name, fallback) {
  const raw = (env[name] || '').trim().toLowerCase();
  if (raw === '') return fallback;
  return ['1', 'true', 'yes', 'on'].includes(raw);
}

function list(env, name) {
  return (env[name] || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
}

function loadConfig(env = process.env) {
  const cfg = {
    host: (env.BOT_HOST || '127.0.0.1').trim(),
    port: num(env, 'BOT_PORT', 3000),
    sharedSecret: (env.BOT_SHARED_SECRET || '').trim(),
    displayName: (env.BOT_DISPLAY_NAME || 'Smart Meeting Minutes Bot').trim().slice(0, 60),

    // "playwright" joins real Jitsi rooms. "fake" simulates a meeting; for tests and the contract check only.
    driver: (env.BOT_DRIVER || 'playwright').trim(),
    headless: bool(env, 'BOT_HEADLESS', true),
    chromiumPath: (env.BOT_CHROMIUM_PATH || '').trim(),
    chromiumNoSandbox: bool(env, 'BOT_CHROMIUM_NO_SANDBOX', false), // needed in some containers only

    maxSessions: num(env, 'BOT_MAX_SESSIONS', 2),
    jitsiAllowedHosts: list(env, 'BOT_JITSI_ALLOWED_HOSTS'),
    backendAllowlist: list(env, 'BOT_BACKEND_ALLOWLIST'), // origins, e.g. https://api.example.com
    allowHttpBackend: bool(env, 'BOT_ALLOW_HTTP_BACKEND', false), // plain http is always allowed for localhost only

    sampleRate: SAMPLE_RATE,
    chunkMs: num(env, 'BOT_CHUNK_MS', 10000),
    heartbeatMs: num(env, 'BOT_HEARTBEAT_MS', 30000),
    pollMs: num(env, 'BOT_POLL_MS', 1000),
    joinTimeoutMs: num(env, 'BOT_JOIN_TIMEOUT_MS', 120000),
    lobbyTimeoutMs: num(env, 'BOT_LOBBY_TIMEOUT_MS', 300000),
    audioTimeoutMs: num(env, 'BOT_AUDIO_TIMEOUT_MS', 90000), // participants present but no audio at all
    aloneTimeoutMs: num(env, 'BOT_ALONE_TIMEOUT_MS', 300000), // bot alone this long after joining: it leaves
    rejoinGraceMs: num(env, 'BOT_REJOIN_GRACE_MS', 30000),
    maxRejoins: num(env, 'BOT_MAX_REJOINS', 2),

    backendTimeoutMs: num(env, 'BOT_BACKEND_TIMEOUT_MS', 20000),
    uploadRetries: num(env, 'BOT_UPLOAD_RETRIES', 5),
    uploadRetryBaseMs: num(env, 'BOT_UPLOAD_RETRY_BASE_MS', 1000),
    uploadRetryMaxMs: num(env, 'BOT_UPLOAD_RETRY_MAX_MS', 16000),
    uploadConcurrency: num(env, 'BOT_UPLOAD_CONCURRENCY', 2),
    uploadMaxPending: num(env, 'BOT_UPLOAD_MAX_PENDING', 300),
    drainTimeoutMs: num(env, 'BOT_DRAIN_TIMEOUT_MS', 120000),
  };
  validateConfig(cfg, env);
  return cfg;
}

function validateConfig(cfg, env) {
  if (cfg.sharedSecret.length < 32) {
    throw new Error('BOT_SHARED_SECRET must be at least 32 characters (same value as on the backend).');
  }
  if (!['playwright', 'fake'].includes(cfg.driver)) {
    throw new Error('BOT_DRIVER must be "playwright" or "fake".');
  }
  if (cfg.driver === 'fake' && env.NODE_ENV === 'production') {
    throw new Error('BOT_DRIVER=fake is for tests only and is refused when NODE_ENV=production.');
  }
  if (!(cfg.port >= 1 && cfg.port <= 65535)) throw new Error('BOT_PORT must be between 1 and 65535.');
  if (!(cfg.chunkMs >= 1000 && cfg.chunkMs <= 60000)) throw new Error('BOT_CHUNK_MS must be between 1000 and 60000.');
  if (!(cfg.maxSessions >= 1)) throw new Error('BOT_MAX_SESSIONS must be at least 1.');
  if (!(cfg.uploadConcurrency >= 1)) throw new Error('BOT_UPLOAD_CONCURRENCY must be at least 1.');
}

module.exports = { loadConfig, validateConfig, SAMPLE_RATE };

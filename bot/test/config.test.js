'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadConfig } = require('../src/config');
const { sanitize, createLogger } = require('../src/logger');
const { SECRET } = require('./helpers');

test('config loads with defaults when only the secret is set', () => {
  const cfg = loadConfig({ BOT_SHARED_SECRET: SECRET });
  assert.equal(cfg.port, 3000);
  assert.equal(cfg.driver, 'playwright');
  assert.equal(cfg.chunkMs, 10000);
  assert.equal(cfg.sampleRate, 16000);
});

test('a missing or short shared secret stops the bot from starting', () => {
  assert.throws(() => loadConfig({}), /BOT_SHARED_SECRET/);
  assert.throws(() => loadConfig({ BOT_SHARED_SECRET: 'short' }), /BOT_SHARED_SECRET/);
});

test('the fake driver is refused in production', () => {
  assert.throws(() => loadConfig({ BOT_SHARED_SECRET: SECRET, BOT_DRIVER: 'fake', NODE_ENV: 'production' }), /tests only/);
  assert.equal(loadConfig({ BOT_SHARED_SECRET: SECRET, BOT_DRIVER: 'fake' }).driver, 'fake');
});

test('invalid numbers and chunk sizes are rejected with a clear message', () => {
  assert.throws(() => loadConfig({ BOT_SHARED_SECRET: SECRET, BOT_PORT: 'abc' }), /BOT_PORT/);
  assert.throws(() => loadConfig({ BOT_SHARED_SECRET: SECRET, BOT_CHUNK_MS: '50' }), /BOT_CHUNK_MS/);
});

test('list settings are split and trimmed', () => {
  const cfg = loadConfig({ BOT_SHARED_SECRET: SECRET, BOT_JITSI_ALLOWED_HOSTS: ' meet.a.org , meet.b.org,', BOT_BACKEND_ALLOWLIST: 'https://api.example.com' });
  assert.deepEqual(cfg.jitsiAllowedHosts, ['meet.a.org', 'meet.b.org']);
  assert.deepEqual(cfg.backendAllowlist, ['https://api.example.com']);
});

test('log lines redact URLs and bearer tokens', () => {
  const out = sanitize('joined https://meet.example.org/secret-room with Authorization: Bot abc123token');
  assert.ok(!out.includes('secret-room'));
  assert.ok(!out.includes('abc123token'));
  assert.equal(sanitize('smmg-bot 2.0.0 listening'), 'smmg-bot 2.0.0 listening', 'the service name is not a token');
  assert.ok(!sanitize('Bearer xyz987').includes('xyz987'));
  let written = '';
  const log = createLogger('info', { write: (s) => { written += s; } });
  log.info('uses https://api.example.com/x');
  assert.match(written, /INFO uses \[url\]/);
});

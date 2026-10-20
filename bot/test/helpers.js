'use strict';

const { loadConfig } = require('../src/config');
const { createLogger } = require('../src/logger');

const SECRET = 'test-secret-'.padEnd(40, 'x');

// Time moves only when the code sleeps, so backoff and polling run instantly and deterministically.
class FakeClock {
  constructor(start = 1_000_000) {
    this.t = start;
  }

  now() {
    return this.t;
  }

  sleep(ms) {
    this.t += ms;
    return Promise.resolve();
  }
}

function testConfig(overrides = {}) {
  const cfg = loadConfig({ BOT_SHARED_SECRET: SECRET, BOT_HEARTBEAT_MS: '1000000000' });
  return {
    ...cfg,
    pollMs: 1000,
    chunkMs: 1000,
    uploadRetryBaseMs: 0,
    uploadRetryMaxMs: 0,
    joinTimeoutMs: 60_000,
    lobbyTimeoutMs: 60_000,
    audioTimeoutMs: 5_000,
    aloneTimeoutMs: 600_000,
    rejoinGraceMs: 2_000,
    drainTimeoutMs: 60_000,
    backendTimeoutMs: 1000,
    ...overrides,
  };
}

function silentLog() {
  return createLogger('silent');
}

async function formToObject(form) {
  const out = {};
  for (const [key, value] of form.entries()) {
    if (typeof value === 'string') out[key] = value;
    else out[key] = Buffer.from(await value.arrayBuffer());
  }
  return out;
}

// A fetch stand-in. handler(call, allCalls) returns { status, body } and may throw to simulate
// a network failure. Every request is recorded so tests can check order and content.
function fakeBackend(handler) {
  const calls = [];
  const fetchImpl = async (url, init = {}) => {
    const call = {
      url,
      method: init.method,
      auth: (init.headers || {}).Authorization,
      json: null,
      form: null,
    };
    if (init.body && typeof init.body === 'object' && typeof init.body.entries === 'function') {
      call.form = await formToObject(init.body);
    } else if (typeof init.body === 'string') {
      call.json = JSON.parse(init.body);
    }
    calls.push(call);
    const res = await handler(call, calls);
    return {
      status: res.status,
      text: async () => (res.body === undefined ? '' : JSON.stringify(res.body)),
    };
  };
  return { fetchImpl, calls };
}

// Backend that accepts everything: events, audio and completion.
function acceptingBackend() {
  return fakeBackend((call) => {
    if (call.url.endsWith('/audio')) return { status: 201, body: { stored: true, audio_confirmed: true } };
    return { status: 202, body: { status: 'ok' } };
  });
}

function meetingSpec(overrides = {}) {
  return {
    meetingId: 'meeting-1',
    botToken: 'bot-token-'.padEnd(32, 'z'),
    server: 'https://meet.example.org',
    room: 'team-sync',
    url: 'https://meet.example.org/team-sync',
    backendUrl: 'http://127.0.0.1:5000',
    displayName: 'Smart Meeting Minutes Bot',
    ...overrides,
  };
}

module.exports = {
  SECRET,
  FakeClock,
  testConfig,
  silentLog,
  fakeBackend,
  acceptingBackend,
  meetingSpec,
};

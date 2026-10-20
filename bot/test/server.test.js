'use strict';

const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const { createBotServer } = require('../src/server');
const { silentLog, testConfig, SECRET } = require('./helpers');

// The HTTP layer is tested with a stub session factory, so no browser is involved.
let server;
let base;
let started;
let stopCalls;

function startStub() {
  started = [];
  stopCalls = 0;
  const cfg = testConfig({ maxSessions: 1 });
  const startSession = (meeting, onDone) => {
    started.push(meeting);
    void onDone; // stub sessions stay registered for the whole test run
    return {
      requestStop: () => {
        stopCalls += 1;
      },
      snapshot: () => ({ meeting_id: meeting.meetingId, state: 'joined', failure: null, stop_requested: false, participants: 0, uploads: {} }),
    };
  };
  return createBotServer({ cfg, startSession, log: silentLog() });
}

before(async () => {
  server = startStub().server;
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${server.address().port}`;
});

after(async () => {
  await new Promise((resolve) => server.close(resolve));
});

async function call(method, path, { secret = SECRET, body, raw } = {}) {
  const headers = {};
  if (secret !== null) headers['X-Bot-Secret'] = secret;
  if (body !== undefined || raw !== undefined) headers['Content-Type'] = 'application/json';
  const res = await fetch(base + path, { method, headers, body: raw !== undefined ? raw : body === undefined ? undefined : JSON.stringify(body) });
  const text = await res.text();
  return { status: res.status, json: text ? JSON.parse(text) : null };
}

const joinBody = (over = {}) => ({
  meeting_id: 'meeting-1',
  bot_token: 'bot-token-'.padEnd(32, 'q'),
  server: 'https://meet.example.org',
  room: 'team-sync',
  backend_url: 'http://127.0.0.1:5000',
  bot_display_name: 'Minutes Bot',
  ...over,
});

test('health is public and reveals no configuration', async () => {
  const r = await call('GET', '/api/health', { secret: null });
  assert.equal(r.status, 200);
  assert.equal(r.json.ok, true);
  assert.equal(Object.keys(r.json).includes('driver'), false);
});

test('join without the shared secret is refused', async () => {
  const wrong = await call('POST', '/api/join', { secret: 'nope', body: joinBody() });
  assert.equal(wrong.status, 401);
  const missing = await call('POST', '/api/join', { secret: null, body: joinBody() });
  assert.equal(missing.status, 401);
  assert.equal(started.length, 0);
});

test('invalid join payloads return a coded 400 and start nothing', async () => {
  const r = await call('POST', '/api/join', { body: joinBody({ server: 'http://meet.example.org' }) });
  assert.equal(r.status, 400);
  assert.equal(r.json.error.code, 'INVALID_SERVER');
  const bad = await call('POST', '/api/join', { raw: '{not json' });
  assert.equal(bad.status, 400);
  assert.equal(bad.json.error.code, 'INVALID_JSON');
  assert.equal(started.length, 0);
});

test('a valid join returns 202 and starts exactly one session', async () => {
  const r = await call('POST', '/api/join', { body: joinBody() });
  assert.equal(r.status, 202);
  assert.equal(started.length, 1);
  assert.equal(started[0].url, 'https://meet.example.org/team-sync');
  assert.equal(started[0].displayName, 'Minutes Bot');
});

test('a second join for the same meeting is refused', async () => {
  const r = await call('POST', '/api/join', { body: joinBody() });
  assert.equal(r.status, 409);
  assert.equal(r.json.error.code, 'ALREADY_JOINED');
});

test('the session limit is enforced', async () => {
  const r = await call('POST', '/api/join', { body: joinBody({ meeting_id: 'meeting-2' }) });
  assert.equal(r.status, 503);
  assert.equal(r.json.error.code, 'BOT_BUSY');
});

test('status returns the session snapshot, with the secret required', async () => {
  const ok = await call('GET', '/api/meetings/meeting-1/status');
  assert.equal(ok.status, 200);
  assert.equal(ok.json.state, 'joined');
  const denied = await call('GET', '/api/meetings/meeting-1/status', { secret: 'bad' });
  assert.equal(denied.status, 401);
  const missing = await call('GET', '/api/meetings/unknown/status');
  assert.equal(missing.status, 404);
});

test('leave asks the session to stop; an unknown meeting is 404 so the backend finalizes directly', async () => {
  const known = await call('POST', '/api/leave', { body: { meeting_id: 'meeting-1' } });
  assert.equal(known.status, 202);
  assert.equal(stopCalls, 1);
  const unknown = await call('POST', '/api/leave', { body: { meeting_id: 'meeting-9' } });
  assert.equal(unknown.status, 404);
  assert.equal(unknown.json.error.code, 'MEETING_NOT_ACTIVE');
  const bad = await call('POST', '/api/leave', { body: { meeting_id: '../x' } });
  assert.equal(bad.status, 400);
});

test('unknown routes are 404 and never leak details', async () => {
  const r = await call('GET', '/api/nope');
  assert.equal(r.status, 404);
  assert.equal(r.json.error.code, 'NOT_FOUND');
});

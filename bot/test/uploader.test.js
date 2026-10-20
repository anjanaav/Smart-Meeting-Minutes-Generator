'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { AudioUploader } = require('../src/uploader');
const { BackendClient } = require('../src/backendClient');
const { FakeClock, fakeBackend, silentLog } = require('./helpers');

const RETRY = { attempts: 4, baseMs: 0, maxMs: 0 };
const chunk = (seq) => ({ participantId: 'alice', participantName: 'Alice', seq, offsetMs: seq * 1000, durationMs: 1000, rms: 0.2, wav: Buffer.alloc(44) });

function uploaderWith(sendFn, opts = {}) {
  const clock = new FakeClock();
  const closed = [];
  const up = new AudioUploader({
    send: sendFn,
    clock,
    log: silentLog(),
    retry: RETRY,
    concurrency: 2,
    maxPending: opts.maxPending || 300,
    onClosed: () => closed.push(true),
  });
  return { up, clock, closed };
}

test('BackendClient classifies answers into closed, server, rejected and network', async () => {
  const cases = [
    [{ status: 409, body: { error: { code: 'MEETING_CLOSED' } } }, 'closed'],
    [{ status: 401, body: { error: { code: 'INVALID_BOT_TOKEN' } } }, 'closed'],
    [{ status: 503, body: {} }, 'server'],
    [{ status: 429, body: {} }, 'server'],
    [{ status: 400, body: { error: { code: 'AUDIO_REQUIRED' } } }, 'rejected'],
  ];
  for (const [answer, kindExpected] of cases) {
    const { fetchImpl } = fakeBackend(() => answer);
    const client = new BackendClient({ fetchImpl });
    await assert.rejects(client.request('POST', 'http://x/api', 't', { json: {} }), (e) => e.kind === kindExpected);
  }
  const broken = new BackendClient({ fetchImpl: async () => { throw new TypeError('fetch failed'); } });
  await assert.rejects(broken.request('POST', 'http://x', 't', {}), (e) => e.kind === 'network');
});

test('a transient server error is retried and the chunk is counted once', async () => {
  let calls = 0;
  const { up, clock } = uploaderWith(async () => {
    calls += 1;
    if (calls === 1) throw Object.assign(new Error('x'), { kind: 'server' });
  });
  up.enqueue(chunk(0));
  const result = await up.drain(10_000);
  assert.equal(calls, 2);
  assert.equal(result.sent, 1);
  assert.equal(result.drained, true);
  assert.ok(clock.now() >= 1_000_000);
});

test('a rejection is not retried and is counted as failed', async () => {
  let calls = 0;
  const { up } = uploaderWith(async () => {
    calls += 1;
    throw Object.assign(new Error('bad'), { kind: 'rejected' });
  });
  up.enqueue(chunk(0));
  const result = await up.drain(10_000);
  assert.equal(calls, 1);
  assert.equal(result.failed, 1);
});

test('a closed answer stops the uploader and discards what is queued', async () => {
  const sent = [];
  const { up, closed } = uploaderWith(async (c) => {
    sent.push(c.seq);
    throw Object.assign(new Error('closed'), { kind: 'closed' });
  });
  up.enqueue(chunk(0));
  up.enqueue(chunk(1));
  up.enqueue(chunk(2));
  const result = await up.drain(10_000);
  assert.equal(result.closed, true);
  assert.equal(closed.length, 1);
  assert.equal(up.enqueue(chunk(3)), false, 'nothing is accepted after closing');
  assert.ok(sent.length <= 2, 'at most the in-flight chunks were attempted');
});

test('a full queue counts dropped chunks instead of losing them silently', () => {
  const { up } = uploaderWith(() => new Promise(() => {}), { maxPending: 2 }); // never resolves
  up.concurrency = 0; // hold everything in the queue
  assert.equal(up.enqueue(chunk(0)), true);
  assert.equal(up.enqueue(chunk(1)), true);
  assert.equal(up.enqueue(chunk(2)), false);
  assert.equal(up.stats().dropped, 1);
});

test('drain reports not drained when the timeout passes first', async () => {
  const { up } = uploaderWith(() => new Promise(() => {})); // never resolves
  up.enqueue(chunk(0));
  const result = await up.drain(100);
  assert.equal(result.drained, false);
  assert.equal(result.inflight, 1);
});

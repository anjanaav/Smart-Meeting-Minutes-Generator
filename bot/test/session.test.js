'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { createSession } = require('../src/session');
const { createPageAgent } = require('../src/pageAgent');
const { BackendClient } = require('../src/backendClient');
const { FakePage, tone } = require('../src/browser/fakeDriver');
const { FakeClock, testConfig, silentLog, fakeBackend, acceptingBackend, meetingSpec } = require('./helpers');

const bodyText = (text) => ({ bodyText: text, hasPasswordInput: false, inConference: false, remoteAudioTracks: 0, participants: [] });
const joinedSnap = (participants = [], remoteAudioTracks = 1) => ({
  bodyText: '',
  hasPasswordInput: false,
  inConference: true,
  remoteAudioTracks,
  participants,
});
const ALICE = [{ id: 'alice', name: 'Alice Example' }];
let session; // the session under test, referenced from scripted page steps

// Builds a session wired to a fake page and a fake backend. Returns handles for assertions.
function setup({ script, backend, cfg = {}, meeting = {}, openBrowserFails = false }) {
  const clock = new FakeClock();
  const config = testConfig(cfg);
  const page = new FakePage({ script });
  const { fetchImpl, calls } = backend || acceptingBackend();
  const client = new BackendClient({ fetchImpl });
  const handles = { closes: 0 };
  const openBrowser = async () => {
    if (openBrowserFails) throw new Error('chromium missing at https://example.invalid');
    return {
      page,
      close: async () => {
        handles.closes += 1;
        page.closed = true;
      },
    };
  };
  const session = createSession({
    meeting: meetingSpec(meeting),
    cfg: config,
    backend: client,
    openBrowser,
    agent: createPageAgent({ cfg: config, clock }),
    log: silentLog(),
    clock,
  });
  return { session, page, calls, handles, clock };
}

const types = (calls) => calls.filter((c) => c.url.endsWith('/events')).map((c) => c.json.type);
const audioCalls = (calls) => calls.filter((c) => c.url.endsWith('/audio'));
const completeCalls = (calls) => calls.filter((c) => c.url.endsWith('/complete'));

test('happy path: lobby, join, speech captured in timed chunks, stop, complete last', async () => {
  let stopped = false;
  const script = (step, page) => {
    if (step === 0) return bodyText('Waiting for the host to let you in');
    if (step === 1) {
      page.emitAudio({ participantId: 'alice', participantName: 'Alice Example', samples: tone(2.5) }).catch(() => {});
      return joinedSnap(ALICE);
    }
    if (step === 4 && !stopped) {
      stopped = true;
      session.requestStop();
    }
    return joinedSnap(ALICE);
  };
  const { session: s, calls, handles } = setup({ script });
  session = s;
  const reason = await session.run();

  assert.equal(reason, 'requested');
  assert.deepEqual(types(calls), ['joining', 'waiting', 'joined', 'participants']);
  assert.deepEqual(calls.find((c) => c.url.endsWith('/events') && c.json.type === 'participants').json.participants, ALICE);

  const audio = audioCalls(calls);
  assert.deepEqual(audio.map((c) => c.form.seq), ['0', '1', '2'], 'two full chunks plus the flushed remainder');
  assert.deepEqual(audio.map((c) => c.form.duration_ms), ['1000', '1000', '500']);
  assert.ok(audio.every((c) => Buffer.from(c.form.file).toString('ascii', 0, 4) === 'RIFF'), 'every chunk is a WAV file');
  assert.ok(audio.every((c) => c.form.participant_name === 'Alice Example'));
  assert.ok(audio.every((c) => Number(c.form.rms) > 0.1), 'the tone is real signal');
  assert.ok(audio.every((c) => c.auth === `Bot ${meetingSpec().botToken}`), 'per-meeting bot token is used');

  const complete = completeCalls(calls);
  assert.equal(complete.length, 1, 'completion is sent exactly once');
  assert.deepEqual(complete[0].json, { reason: 'requested' });
  assert.ok(calls.indexOf(complete[0]) > calls.lastIndexOf(audio[audio.length - 1]), 'complete is sent after all audio');
  assert.equal(handles.closes, 1, 'the browser is closed');
  assert.equal(session.state, 'done');
});

test('a password-protected room fails with a clear code and completes with reason error', async () => {
  const script = () => ({ bodyText: '', hasPasswordInput: true, inConference: false, remoteAudioTracks: 0, participants: [] });
  const { session: s, calls } = setup({ script });
  const reason = await s.run();
  assert.equal(reason, 'error');
  assert.deepEqual(types(calls), ['joining', 'error']);
  assert.equal(calls.find((c) => c.json && c.json.type === 'error').json.code, 'PASSWORD_REQUIRED');
  assert.deepEqual(completeCalls(calls)[0].json, { reason: 'error' });
});

test('participants present but no audio at all reports AUDIO_CAPTURE_UNAVAILABLE', async () => {
  const script = () => joinedSnap([{ id: 'bob', name: 'Bob' }], 0);
  const { session: s, calls } = setup({ script, cfg: { audioTimeoutMs: 5000 } });
  const reason = await s.run();
  assert.equal(reason, 'error');
  const err = calls.find((c) => c.json && c.json.type === 'error');
  assert.equal(err.json.code, 'AUDIO_CAPTURE_UNAVAILABLE');
  assert.equal(audioCalls(calls).length, 0);
});

test('a bot left alone past the timeout leaves and completes as meeting_ended', async () => {
  const script = () => joinedSnap([]);
  const { session: s, calls } = setup({ script, cfg: { aloneTimeoutMs: 3000 } });
  assert.equal(await s.run(), 'meeting_ended');
  assert.deepEqual(completeCalls(calls)[0].json, { reason: 'meeting_ended' });
});

test('a dropped connection is reported, rejoined once, and the bot reports joined again', async () => {
  const steps = [joinedSnap(), bodyText('Connecting'), bodyText('Connecting'), bodyText('Connecting'), bodyText('Connecting'), joinedSnap(), joinedSnap()];
  let stop = false;
  const script = (step) => {
    if (step >= steps.length) {
      if (!stop) {
        stop = true;
        session.requestStop();
      }
      return joinedSnap();
    }
    return steps[step];
  };
  const { session: s, calls, page } = setup({ script, cfg: { rejoinGraceMs: 2000 } });
  session = s;
  await s.run();
  assert.ok(types(calls).includes('disconnected'));
  assert.equal(types(calls).filter((t) => t === 'joined').length, 2, 'joined is reported again after the rejoin');
  assert.equal(page.urls.length, 2, 'the page was reloaded once');
});

test('the browser crashing is reported as BOT_CRASHED', async () => {
  const script = (step, page) => {
    if (step === 1) page.closed = true;
    return joinedSnap(ALICE);
  };
  const { session: s, calls } = setup({ script });
  assert.equal(await s.run(), 'error');
  assert.equal(calls.find((c) => c.json && c.json.type === 'error').json.code, 'BOT_CRASHED');
});

test('the browser failing to start is reported as BOT_CRASHED', async () => {
  const { session: s, calls } = setup({ script: () => undefined, openBrowserFails: true });
  assert.equal(await s.run(), 'error');
  assert.equal(calls.find((c) => c.json && c.json.type === 'error').json.code, 'BOT_CRASHED');
});

test('a stop requested before the browser starts never opens one', async () => {
  const { session: s, calls, handles } = setup({ script: () => joinedSnap() });
  s.requestStop();
  assert.equal(await s.run(), 'requested');
  assert.equal(handles.closes, 0);
  assert.deepEqual(completeCalls(calls)[0].json, { reason: 'requested' });
});

test('a backend that closes the meeting stops uploads and completion', async () => {
  const backend = fakeBackend((call) => {
    if (call.url.endsWith('/audio')) return { status: 409, body: { error: { code: 'MEETING_CLOSED' } } };
    return { status: 202, body: {} };
  });
  let step = 0;
  const script = (n, page) => {
    step = n;
    if (n === 1) page.emitAudio({ participantId: 'alice', participantName: 'Alice', samples: tone(2) }).catch(() => {});
    if (n === 3) session.requestStop();
    return joinedSnap(ALICE);
  };
  const { session: s, calls } = setup({ script, backend });
  session = s;
  await s.run();
  assert.ok(step >= 1);
  assert.ok(audioCalls(calls).length <= 2, 'at most the two chunks already in flight were attempted');
  assert.equal(audioCalls(calls).filter((c) => c.form.seq === '0').length, 1, 'a closed answer is never retried');
  assert.equal(completeCalls(calls).length, 0, 'nothing is sent after the backend closed the meeting');
});

test('transient event failures are retried, then the session still finishes', async () => {
  const backend = fakeBackend((call) => {
    if (call.url.endsWith('/events')) throw new TypeError('connection refused');
    if (call.url.endsWith('/complete')) throw new TypeError('connection refused');
    return { status: 201, body: {} };
  });
  let n = 0;
  const script = () => {
    n += 1;
    if (n === 2) session.requestStop();
    return joinedSnap(ALICE);
  };
  const { session: s, calls } = setup({ script, backend });
  session = s;
  const reason = await s.run();
  assert.equal(reason, 'requested');
  const joiningAttempts = calls.filter((c) => c.url.endsWith('/events') && c.json.type === 'joining');
  assert.equal(joiningAttempts.length, 3, 'event delivery is retried three times');
  assert.equal(completeCalls(calls).length, 5, 'completion is retried five times and then given up');
});

test('participant updates are de-duplicated, drop the bot itself, and only send changes', async () => {
  const { session: s, calls } = setup({ script: () => undefined });
  s.updateParticipants([
    { id: 'alice', name: 'Alice' },
    { id: 'alice', name: 'duplicate' },
    { id: 'bot', name: 'Smart Meeting Minutes Bot' },
  ]);
  s.updateParticipants([{ id: 'alice', name: 'Alice' }]); // unchanged: no event
  await s.eventChain;
  const sent = calls.filter((c) => c.url.endsWith('/events'));
  assert.equal(sent.length, 1);
  assert.deepEqual(sent[0].json.participants, [{ id: 'alice', name: 'Alice' }]);
});

test("audio from the bot's own tile is ignored, and malformed payloads are dropped", async () => {
  const { session: s, calls } = setup({ script: () => undefined });
  s.state = 'joined';
  s.onBrowserAudio({ participantId: 'x', participantName: 'Smart Meeting Minutes Bot', samples: 'AAAA', sampleRate: 16000 });
  s.onBrowserAudio({ participantId: 'x', participantName: 'Ann', samples: 'AAAA', sampleRate: 1 });
  s.onBrowserAudio({ participantId: 'x', participantName: 'Ann', samples: 42, sampleRate: 16000 });
  s.onBrowserAudio(null);
  s.flushAudio();
  await s.eventChain;
  assert.equal(audioCalls(calls).length, 0);
});

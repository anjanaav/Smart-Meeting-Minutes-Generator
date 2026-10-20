'use strict';

// A stand-in for a Jitsi browser page. It replays a scripted sequence of page snapshots and can
// emit audio through the same binding the real capture script uses. It exists so the bot's logic
// (state machine, uploads, HTTP API) can be tested without Chromium or a live meeting.
// BOT_DRIVER=fake is refused when NODE_ENV=production (see config.js).

const { encodeInt16Base64 } = require('../audio');

class FakePage {
  // script(step, page) -> snapshot object (see pageState.js). Called once per poll.
  constructor({ script, gotoError = null } = {}) {
    this.script = script || (() => undefined);
    this.gotoError = gotoError;
    this.bindings = {};
    this.initScripts = [];
    this.urls = [];
    this.closed = false;
    this.step = 0;
  }

  async exposeFunction(name, fn) {
    this.bindings[name] = fn;
  }

  async addInitScript(source) {
    this.initScripts.push(source);
  }

  async goto(url) {
    this.urls.push(url);
    if (this.gotoError) throw this.gotoError;
  }

  async evaluate() {
    const snap = this.script(this.step, this);
    this.step += 1;
    if (snap === undefined) return { bodyText: '', hasPasswordInput: false, inConference: false, remoteAudioTracks: 0, participants: [] };
    return snap;
  }

  isClosed() {
    return this.closed;
  }

  async close() {
    this.closed = true;
  }

  // Emits one block of audio, as the browser script does (sine tone by default, so it is real signal).
  async emitAudio({ participantId, participantName, samples, sampleRate = 16000 }) {
    const payload = {
      participantId,
      participantName,
      samples: typeof samples === 'string' ? samples : encodeInt16Base64(samples),
      sampleRate,
    };
    if (!this.bindings.__smmgAudio) throw new Error('audio binding is not installed');
    return this.bindings.__smmgAudio(payload);
  }
}

// Sine tone: a real, audible signal that passes the backend's "is there sound" check.
function tone(seconds, freq = 220, amplitude = 0.3, sampleRate = 16000) {
  const out = new Int16Array(Math.round(seconds * sampleRate));
  for (let i = 0; i < out.length; i += 1) {
    out[i] = Math.round(Math.sin((2 * Math.PI * freq * i) / sampleRate) * amplitude * 32767);
  }
  return out;
}

// Default timeline: loading for two polls, then joined with one participant who speaks.
function demoScript({ participantId = 'alice', participantName = 'Alice Example', joinAfterPolls = 2 } = {}) {
  return (step, page) => {
    if (step < joinAfterPolls) return undefined;
    const snap = {
      bodyText: '',
      hasPasswordInput: false,
      inConference: true,
      remoteAudioTracks: 1,
      participants: [{ id: participantId, name: participantName }],
    };
    page.emitAudio({ participantId, participantName, samples: tone(1) }).catch(() => {});
    return snap;
  };
}

async function openFakeBrowser(meeting, { script } = {}) {
  void meeting;
  const page = new FakePage({ script: script || demoScript() });
  return { page, close: () => page.close() };
}

module.exports = { FakePage, openFakeBrowser, demoScript, tone };

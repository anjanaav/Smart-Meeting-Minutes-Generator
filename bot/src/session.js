'use strict';

// One bot session per meeting. It owns:
//   - the ordered event stream to the backend (joining, waiting, joined, participants, error, ...)
//   - participant tracking (only changes are sent)
//   - audio intake: per-participant segmenting into timestamped WAV chunks, then upload
//   - the finish sequence, which runs exactly once:
//       close the browser -> flush partial chunks -> drain uploads -> send "complete"
//
// "complete" is sent last, so the backend never starts minutes generation before all audio has arrived.

const { withRetry } = require('./retry');
const { AudioUploader } = require('./uploader');
const { Segmenter, decodeInt16Base64, resampleLinear, rmsLinear, pcm16ToWav, int16ToBuffer, durationMs } = require('./audio');
const { cleanName, safeId } = require('./security');

const MAX_B64_CHARS = 2 * 1024 * 1024; // one browser block is about 32 KB; anything huge is refused
const MIN_FINAL_CHUNK_MS = 300; // shorter leftovers at the end of a meeting are not worth uploading
const VALID_REASONS = new Set(['meeting_ended', 'bot_left', 'error', 'requested']);
const EVENT_RETRY = { attempts: 3, baseMs: 500, maxMs: 4000 };
const COMPLETE_RETRY = { attempts: 5, baseMs: 1000, maxMs: 8000 };

class MeetingSession {
  constructor({ meeting, cfg, backend, openBrowser, agent, log, clock }) {
    this.meeting = meeting; // { meetingId, botToken, backendUrl, url, displayName, ... }
    this.cfg = cfg;
    this.backend = backend;
    this.openBrowser = openBrowser; // async (meeting) => { page, close }
    this.agent = agent; // { join(session) } -> reason
    this.log = log;
    this.clock = clock;

    this.state = 'created';
    this.failure = null;
    this.stopRequested = false;
    this.backendClosed = false;
    this.startEpoch = null;
    this.browser = null;
    this.page = null;
    this.participants = [];
    this.lastParticipantsKey = '';
    this.segmenters = new Map();
    this.seqs = new Map();
    this.eventChain = Promise.resolve();
    this.heartbeatTimer = null;
    this.uploader = new AudioUploader({
      send: (chunk) => this.sendChunk(chunk),
      clock,
      log,
      retry: { attempts: cfg.uploadRetries, baseMs: cfg.uploadRetryBaseMs, maxMs: cfg.uploadRetryMaxMs },
      concurrency: cfg.uploadConcurrency,
      maxPending: cfg.uploadMaxPending,
      onClosed: () => {
        this.backendClosed = true;
        this.log.warn(`meeting ${this.id} is closed on the backend; stopping uploads`);
      },
    });
  }

  get id() {
    return this.meeting.meetingId;
  }

  url(suffix) {
    return `${this.meeting.backendUrl}/api/bot/meetings/${encodeURIComponent(this.id)}${suffix}`;
  }

  // ------------------------------------------------------------------ run / finish
  async run() {
    this.startEpoch = this.clock.now();
    this.state = 'starting';
    this.startHeartbeat();
    let reason = 'error';
    try {
      if (this.stopRequested) {
        reason = 'requested';
      } else {
        this.browser = await this.openBrowser(this.meeting);
        this.page = this.browser.page;
        reason = await this.agent.join(this);
      }
    } catch (err) {
      this.log.error(`meeting ${this.id}: browser session failed (${(err && err.name) || 'Error'})`);
      this.fail('BOT_CRASHED');
      reason = 'error';
    }
    return this.finish(reason);
  }

  async finish(reason) {
    this.stopHeartbeat();
    this.state = 'finishing';
    await this.closeBrowser();
    this.flushAudio();
    const drain = await this.uploader.drain(this.cfg.drainTimeoutMs);
    if (!drain.drained && !drain.closed) {
      this.log.warn(`meeting ${this.id}: ${drain.pending + drain.inflight} audio chunk(s) were not uploaded before the timeout`);
    }
    await this.eventChain;
    const finalReason = this.failure ? 'error' : VALID_REASONS.has(reason) ? reason : 'error';
    if (!this.backendClosed) await this.sendComplete(finalReason);
    this.state = 'done';
    this.log.info(`meeting ${this.id}: finished (${finalReason}), uploads sent=${this.uploader.sent} failed=${this.uploader.failed}`);
    return finalReason;
  }

  async closeBrowser() {
    const handle = this.browser;
    this.browser = null;
    this.page = null;
    if (!handle) return;
    try {
      await handle.close();
    } catch {
      // the browser may already be gone; nothing more to do
    }
  }

  async sendComplete(reason) {
    try {
      await withRetry(
        () => this.backend.request('POST', this.url('/complete'), this.meeting.botToken, { json: { reason } }),
        { ...COMPLETE_RETRY, sleep: (ms) => this.clock.sleep(ms) },
      );
    } catch (err) {
      if (err && err.kind === 'closed') {
        this.backendClosed = true;
      } else {
        // The backend's idle timeout still finalizes the meeting, so this is logged, not fatal.
        this.log.error(`meeting ${this.id}: could not send completion (${(err && err.kind) || 'error'})`);
      }
    }
  }

  // ------------------------------------------------------------------ state reports
  report(type, extra = {}) {
    if (this.backendClosed) return this.eventChain;
    this.eventChain = this.eventChain.then(() => this.sendEvent(type, extra)).catch(() => {});
    return this.eventChain;
  }

  async sendEvent(type, extra) {
    if (this.backendClosed) return;
    try {
      await withRetry(
        () => this.backend.request('POST', this.url('/events'), this.meeting.botToken, { json: { type, ...extra } }),
        { ...EVENT_RETRY, sleep: (ms) => this.clock.sleep(ms) },
      );
    } catch (err) {
      if (err && err.kind === 'closed') {
        this.backendClosed = true;
      } else {
        this.log.warn(`meeting ${this.id}: could not report "${type}" (${(err && err.kind) || 'error'})`);
      }
    }
  }

  setJoining() {
    this.state = 'joining';
    this.report('joining');
  }

  setWaiting() {
    this.state = 'waiting';
    this.report('waiting');
  }

  markJoined() {
    this.state = 'joined';
    this.report('joined');
  }

  reportDisconnected() {
    this.state = 'disconnected';
    this.report('disconnected');
  }

  // Records a terminal failure once and tells the backend. The finish sequence still runs, so any
  // audio already captured is uploaded and the backend decides what to keep.
  fail(code) {
    if (this.failure) return;
    this.failure = code;
    this.state = 'failed';
    this.log.warn(`meeting ${this.id}: ${code}`);
    this.report('error', { code });
  }

  requestStop() {
    this.stopRequested = true;
  }

  // Full participant list from the page (the bot is excluded). Only changes are sent.
  updateParticipants(list) {
    if (this.backendClosed || this.state === 'finishing' || this.state === 'done') return;
    const seen = new Set();
    const clean = [];
    for (const p of Array.isArray(list) ? list : []) {
      const id = safeId(p && p.id);
      const name = cleanName(p && p.name, 80) || 'Participant';
      if (!id || seen.has(id) || name === this.meeting.displayName) continue;
      seen.add(id);
      clean.push({ id, name });
    }
    const key = JSON.stringify(clean.map((p) => [p.id, p.name]).sort());
    if (key === this.lastParticipantsKey) return;
    this.lastParticipantsKey = key;
    this.participants = clean;
    this.report('participants', { participants: clean });
  }

  otherParticipantCount() {
    return this.participants.length;
  }

  // ------------------------------------------------------------------ audio
  // Called by the browser script (through the page binding) with about one second of audio
  // for one participant. The chunk is timestamped from arrival time, which is close enough to the
  // capture time for a one-second block.
  onBrowserAudio(payload) {
    if (this.backendClosed || this.state === 'finishing' || this.state === 'done') return;
    if (!payload || typeof payload.samples !== 'string' || payload.samples.length > MAX_B64_CHARS) return;
    const rate = Number(payload.sampleRate);
    if (!(rate >= 8000 && rate <= 48000)) return;

    const pid = safeId(payload.participantId) || 'unknown';
    const name = cleanName(payload.participantName, 80) || 'Participant';
    if (name === this.meeting.displayName) return; // never transcribe the bot's own (silent) audio

    let pcm = decodeInt16Base64(payload.samples);
    if (rate !== this.cfg.sampleRate) pcm = resampleLinear(pcm, rate, this.cfg.sampleRate);
    if (pcm.length === 0) return;
    const blockMs = durationMs(pcm.length, this.cfg.sampleRate);
    const startMs = Math.max(0, this.clock.now() - this.startEpoch - blockMs);

    let segmenter = this.segmenters.get(pid);
    if (!segmenter) {
      segmenter = new Segmenter({ sampleRate: this.cfg.sampleRate, chunkMs: this.cfg.chunkMs });
      this.segmenters.set(pid, segmenter);
    }
    for (const seg of segmenter.push(pcm, startMs)) this.enqueueChunk(pid, name, seg);
  }

  flushAudio() {
    for (const [pid, segmenter] of this.segmenters) {
      const seg = segmenter.flush();
      if (seg && durationMs(seg.samples.length, this.cfg.sampleRate) >= MIN_FINAL_CHUNK_MS) {
        this.enqueueChunk(pid, this.lastNameFor(pid), seg);
      }
    }
    this.segmenters.clear();
  }

  lastNameFor(pid) {
    const found = this.participants.find((p) => p.id === pid);
    return found ? found.name : 'Participant';
  }

  enqueueChunk(pid, name, seg) {
    const seq = this.seqs.get(pid) || 0;
    this.seqs.set(pid, seq + 1);
    const samples = seg.samples;
    const duration = durationMs(samples.length, this.cfg.sampleRate);
    if (duration <= 0) return;
    const wav = pcm16ToWav(int16ToBuffer(samples), this.cfg.sampleRate);
    this.uploader.enqueue({
      participantId: pid,
      participantName: name,
      seq,
      offsetMs: seg.offsetMs,
      durationMs: duration,
      rms: rmsLinear(samples),
      wav,
    });
  }

  // POST one chunk. Throws BackendError on failure so the uploader can decide whether to retry.
  async sendChunk(chunk) {
    const form = new FormData();
    form.append('file', new Blob([chunk.wav], { type: 'audio/wav' }), `chunk-${chunk.seq}.wav`);
    form.append('participant_id', chunk.participantId);
    form.append('participant_name', chunk.participantName);
    form.append('seq', String(chunk.seq));
    form.append('offset_ms', String(chunk.offsetMs));
    form.append('duration_ms', String(chunk.durationMs));
    form.append('rms', String(chunk.rms));
    await this.backend.request('POST', this.url('/audio'), this.meeting.botToken, { form });
  }

  // ------------------------------------------------------------------ heartbeat and status
  startHeartbeat() {
    this.heartbeatTimer = setInterval(() => this.report('heartbeat'), this.cfg.heartbeatMs);
    if (this.heartbeatTimer.unref) this.heartbeatTimer.unref();
  }

  stopHeartbeat() {
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = null;
  }

  snapshot() {
    return {
      meeting_id: this.id,
      state: this.state,
      failure: this.failure,
      stop_requested: this.stopRequested,
      participants: this.participants.length,
      uploads: this.uploader.stats(),
    };
  }
}

function createSession(options) {
  return new MeetingSession(options);
}

module.exports = { createSession, MeetingSession };

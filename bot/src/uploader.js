'use strict';

// Uploads audio chunks to the backend in the background.
//
// - Bounded queue: when full, new chunks are counted as dropped and reported, never silently lost.
// - Transient failures are retried with exponential backoff; the backend treats a repeated
//   (participant, seq) as a duplicate, so a retry after a timeout cannot double-store audio.
// - A "closed" answer stops the uploader and discards the queue (the backend has finished the meeting).
// - drain() waits for the queue to empty before the bot reports completion.

const { withRetry } = require('./retry');

class AudioUploader {
  constructor({ send, clock, log, retry, concurrency = 2, maxPending = 300, onClosed = () => {} }) {
    this.send = send;
    this.clock = clock;
    this.log = log;
    this.retry = retry; // { attempts, baseMs, maxMs }
    this.concurrency = concurrency;
    this.maxPending = maxPending;
    this.onClosed = onClosed;
    this.queue = [];
    this.inflight = 0;
    this.closed = false;
    this.sent = 0;
    this.failed = 0;
    this.dropped = 0;
  }

  enqueue(chunk) {
    if (this.closed) return false;
    if (this.queue.length >= this.maxPending) {
      this.dropped += 1;
      this.log.warn(`audio upload queue is full; dropped a chunk (participant ${chunk.participantId})`);
      return false;
    }
    this.queue.push(chunk);
    this.pump();
    return true;
  }

  pump() {
    while (!this.closed && this.inflight < this.concurrency && this.queue.length > 0) {
      const chunk = this.queue.shift();
      this.inflight += 1;
      this.run(chunk).then(() => {
        this.inflight -= 1;
        this.pump();
      });
    }
  }

  async run(chunk) {
    try {
      await withRetry((attempt) => this.send(chunk, attempt), {
        attempts: this.retry.attempts,
        baseMs: this.retry.baseMs,
        maxMs: this.retry.maxMs,
        sleep: (ms) => this.clock.sleep(ms),
      });
      this.sent += 1;
    } catch (err) {
      if (err && err.kind === 'closed') {
        this.close();
        return;
      }
      this.failed += 1;
      this.log.warn(`audio chunk ${chunk.seq} for participant ${chunk.participantId} could not be uploaded (${(err && err.kind) || 'error'})`);
    }
  }

  close() {
    if (this.closed) return;
    this.closed = true;
    this.queue = [];
    this.onClosed();
  }

  stats() {
    return {
      pending: this.queue.length,
      inflight: this.inflight,
      sent: this.sent,
      failed: this.failed,
      dropped: this.dropped,
      closed: this.closed,
    };
  }

  // Resolves when everything queued has been sent, failed, or the timeout passes.
  async drain(timeoutMs) {
    const deadline = this.clock.now() + timeoutMs;
    while (!this.closed && (this.queue.length > 0 || this.inflight > 0)) {
      if (this.clock.now() >= deadline) break;
      await this.clock.sleep(25);
    }
    const stats = this.stats();
    return { ...stats, drained: !this.closed && stats.pending === 0 && stats.inflight === 0 };
  }
}

module.exports = { AudioUploader };

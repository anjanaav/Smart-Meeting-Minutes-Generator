'use strict';

// Single-attempt HTTP client for bot -> backend calls. Retries are added by callers (see retry.js),
// so each caller chooses its own policy.
//
// Error kinds:
//   network  - no answer (connection refused, timeout). Retryable.
//   server   - 408/425/429/5xx. Retryable.
//   closed   - the backend no longer accepts this meeting's bot (MEETING_CLOSED, INVALID_BOT_TOKEN).
//              Stop sending.
//   rejected - any other 4xx. Not retryable.

class BackendError extends Error {
  constructor(kind, status, code, message) {
    super(message);
    this.name = 'BackendError';
    this.kind = kind;
    this.status = status;
    this.code = code;
  }
}

const RETRYABLE_STATUS = new Set([408, 425, 429]);

class BackendClient {
  constructor({ fetchImpl = globalThis.fetch, timeoutMs = 20000 } = {}) {
    this.fetch = fetchImpl;
    this.timeoutMs = timeoutMs;
  }

  // opts: { json } or { form } (FormData). Authorization is "Bot <per-meeting token>".
  async request(method, url, token, opts = {}) {
    const headers = { Authorization: `Bot ${token}` };
    let body;
    if (opts.form) {
      body = opts.form; // fetch sets the multipart boundary itself
    } else if (opts.json !== undefined) {
      headers['Content-Type'] = 'application/json';
      body = JSON.stringify(opts.json);
    }

    let res;
    try {
      res = await this.fetch(url, { method, headers, body, signal: AbortSignal.timeout(this.timeoutMs) });
    } catch {
      throw new BackendError('network', 0, 'NETWORK', 'The backend could not be reached.');
    }

    let text = '';
    try {
      text = await res.text();
    } catch {
      text = '';
    }
    let data = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      data = {};
    }

    if (res.status >= 200 && res.status < 300) return { status: res.status, body: data };

    const code = (data && data.error && data.error.code) || `HTTP_${res.status}`;
    if ((res.status === 409 && code === 'MEETING_CLOSED') || (res.status === 401 && code === 'INVALID_BOT_TOKEN')) {
      throw new BackendError('closed', res.status, code, 'The backend no longer accepts this meeting.');
    }
    if (RETRYABLE_STATUS.has(res.status) || res.status >= 500) {
      throw new BackendError('server', res.status, code, `The backend answered HTTP ${res.status}.`);
    }
    throw new BackendError('rejected', res.status, code, `The backend rejected the request (${code}).`);
  }
}

module.exports = { BackendClient, BackendError };

'use strict';

// Retries only transient failures (network errors and server-side 5xx/408/425/429). Rejections
// (4xx) and "meeting closed" answers are never retried, so a retry can never duplicate work.

function isTransient(err) {
  return Boolean(err) && (err.kind === 'network' || err.kind === 'server');
}

async function withRetry(fn, { attempts, baseMs, maxMs, sleep, shouldRetry = isTransient }) {
  let last;
  for (let attempt = 1; attempt <= Math.max(1, attempts); attempt += 1) {
    try {
      return await fn(attempt);
    } catch (err) {
      last = err;
      if (attempt >= attempts || !shouldRetry(err)) throw err;
      await sleep(Math.min(maxMs, baseMs * 2 ** (attempt - 1)));
    }
  }
  throw last;
}

module.exports = { withRetry, isTransient };

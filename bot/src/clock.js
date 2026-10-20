'use strict';

// Time source. Tests inject a fake clock so polling and backoff run instantly.
const systemClock = {
  now: () => Date.now(),
  sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
};

module.exports = { systemClock };

'use strict';

// Logs ids, states and error names only. URLs and bearer-style tokens are redacted, and transcript
// or audio content is never passed to the logger.

const LEVELS = { debug: 10, info: 20, warn: 30, error: 40, silent: 100 };

function sanitize(message) {
  return String(message)
    .replace(/https?:\/\/\S+/gi, '[url]')
    .replace(/(Authorization:?\s*)(Bot|Bearer)\s+\S+/gi, '$1$2 [redacted]')
    .replace(/\bBearer\s+\S+/gi, 'Bearer [redacted]')
    .slice(0, 500);
}

function createLogger(level = 'info', sink = process.stderr) {
  const min = LEVELS[level] ?? LEVELS.info;
  const write = (name, message) => {
    if (LEVELS[name] < min) return;
    sink.write(`${new Date().toISOString()} ${name.toUpperCase()} ${sanitize(message)}\n`);
  };
  return {
    debug: (m) => write('debug', m),
    info: (m) => write('info', m),
    warn: (m) => write('warn', m),
    error: (m) => write('error', m),
  };
}

module.exports = { createLogger, sanitize };

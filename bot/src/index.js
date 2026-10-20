#!/usr/bin/env node
'use strict';

const { loadConfig } = require('./config');
const { createLogger } = require('./logger');
const { createBotServer, VERSION } = require('./server');
const { createSession } = require('./session');
const { createPageAgent } = require('./pageAgent');
const { BackendClient } = require('./backendClient');
const { systemClock } = require('./clock');

function main() {
  let cfg;
  try {
    cfg = loadConfig();
  } catch (err) {
    process.stderr.write(`Configuration error: ${err.message}\n`);
    process.exit(1);
  }
  const log = createLogger(process.env.BOT_LOG_LEVEL || 'info');

  const openBrowser =
    cfg.driver === 'fake'
      ? require('./browser/fakeDriver').openFakeBrowser
      : (meeting) => require('./browser/playwrightDriver').openBrowser(cfg, meeting);

  const backend = new BackendClient({ timeoutMs: cfg.backendTimeoutMs });
  const agent = createPageAgent({ cfg, clock: systemClock });

  const startSession = (meeting, onDone) => {
    const session = createSession({ meeting, cfg, backend, openBrowser, agent, log, clock: systemClock });
    session
      .run()
      .catch((err) => log.error(`meeting ${meeting.meetingId}: unexpected failure (${(err && err.name) || 'Error'})`))
      .then(() => onDone());
    return session;
  };

  const { server, sessions } = createBotServer({ cfg, startSession, log });
  server.listen(cfg.port, cfg.host, () => {
    log.info(`smmg-bot ${VERSION} listening on ${cfg.host}:${cfg.port} (driver: ${cfg.driver})`);
  });

  // On shutdown, ask every session to leave and wait (bounded) so audio already captured is uploaded.
  let stopping = false;
  const shutdown = () => {
    if (stopping) return;
    stopping = true;
    log.info(`shutting down: asking ${sessions.size} session(s) to finish`);
    for (const session of sessions.values()) session.requestStop();
    const deadline = Date.now() + 60000;
    const timer = setInterval(() => {
      if (sessions.size === 0 || Date.now() > deadline) {
        clearInterval(timer);
        server.close();
        process.exit(0);
      }
    }, 250);
  };
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);
}

if (require.main === module) main();

module.exports = { main };

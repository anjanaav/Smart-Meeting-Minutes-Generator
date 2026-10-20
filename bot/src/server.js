'use strict';

// HTTP API the Flask backend calls. Join and leave require the shared secret (X-Bot-Secret).
// Meeting audio and events go the other way, from the bot to the backend, with the per-meeting token.
//
//   GET  /api/health                      public liveness check (no details)
//   POST /api/join     X-Bot-Secret       start a session for a meeting -> 202
//   POST /api/leave    X-Bot-Secret       ask a session to leave -> 202 (or 404 if none)
//   GET  /api/meetings/{id}/status        X-Bot-Secret; state, counts, failure code (no content)

const http = require('node:http');
const { ValidationError, MEETING_ID, secretMatches, validateJoinPayload } = require('./security');

const VERSION = '2.0.0';
const MAX_BODY_BYTES = 64 * 1024;

function createBotServer({ cfg, startSession, log }) {
  const sessions = new Map();

  function send(res, status, body) {
    const data = JSON.stringify(body);
    res.writeHead(status, {
      'Content-Type': 'application/json; charset=utf-8',
      'Content-Length': Buffer.byteLength(data),
      'Cache-Control': 'no-store',
    });
    res.end(data);
  }

  function fail(res, status, code, message) {
    send(res, status, { error: { code, message } });
  }

  function authorized(req) {
    return secretMatches(req.headers['x-bot-secret'], cfg.sharedSecret);
  }

  function readJson(req) {
    return new Promise((resolve, reject) => {
      let size = 0;
      const chunks = [];
      let tooLarge = false;
      req.on('data', (chunk) => {
        if (tooLarge) return;
        size += chunk.length;
        if (size > MAX_BODY_BYTES) {
          tooLarge = true;
          reject(new ValidationError('BODY_TOO_LARGE', 'The request is too large.', 413));
          return;
        }
        chunks.push(chunk);
      });
      req.on('end', () => {
        if (tooLarge) return;
        try {
          resolve(chunks.length ? JSON.parse(Buffer.concat(chunks).toString('utf8')) : {});
        } catch {
          reject(new ValidationError('INVALID_JSON', 'The request body must be JSON.'));
        }
      });
      req.on('error', reject);
    });
  }

  async function join(req, res) {
    if (!authorized(req)) return fail(res, 401, 'UNAUTHORIZED', 'The bot secret is missing or wrong.');
    let meeting;
    try {
      meeting = validateJoinPayload(await readJson(req), cfg);
    } catch (err) {
      if (err instanceof ValidationError) return fail(res, err.status, err.code, err.message);
      throw err;
    }
    if (sessions.has(meeting.meetingId)) return fail(res, 409, 'ALREADY_JOINED', 'This meeting already has a bot.');
    if (sessions.size >= cfg.maxSessions) return fail(res, 503, 'BOT_BUSY', 'The bot is busy with other meetings.');

    const session = startSession(meeting, () => sessions.delete(meeting.meetingId));
    sessions.set(meeting.meetingId, session);
    log.info(`meeting ${meeting.meetingId}: join accepted (${sessions.size} active)`);
    return send(res, 202, { meeting_id: meeting.meetingId, status: 'accepted' });
  }

  async function leave(req, res) {
    if (!authorized(req)) return fail(res, 401, 'UNAUTHORIZED', 'The bot secret is missing or wrong.');
    let body;
    try {
      body = await readJson(req);
    } catch (err) {
      if (err instanceof ValidationError) return fail(res, err.status, err.code, err.message);
      throw err;
    }
    const id = body && body.meeting_id;
    if (typeof id !== 'string' || !MEETING_ID.test(id)) return fail(res, 400, 'INVALID_MEETING', 'The meeting id is invalid.');
    const session = sessions.get(id);
    if (!session) return fail(res, 404, 'MEETING_NOT_ACTIVE', 'No bot is active for this meeting.');
    session.requestStop();
    return send(res, 202, { status: 'stopping' });
  }

  function status(req, res, id) {
    if (!authorized(req)) return fail(res, 401, 'UNAUTHORIZED', 'The bot secret is missing or wrong.');
    const session = sessions.get(id);
    if (!session) return fail(res, 404, 'MEETING_NOT_ACTIVE', 'No bot is active for this meeting.');
    return send(res, 200, session.snapshot());
  }

  async function route(req, res) {
    const url = new URL(req.url || '/', 'http://bot.local');
    const path = url.pathname;
    if (req.method === 'GET' && path === '/api/health') {
      return send(res, 200, { ok: true, service: 'smmg-bot', version: VERSION });
    }
    if (req.method === 'POST' && path === '/api/join') return join(req, res);
    if (req.method === 'POST' && path === '/api/leave') return leave(req, res);
    const match = /^\/api\/meetings\/([^/]+)\/status$/.exec(path);
    if (req.method === 'GET' && match) return status(req, res, decodeURIComponent(match[1]));
    return fail(res, 404, 'NOT_FOUND', 'Unknown endpoint.');
  }

  const server = http.createServer((req, res) => {
    route(req, res).catch((err) => {
      log.error(`request failed (${(err && err.name) || 'Error'})`);
      if (!res.headersSent) fail(res, 500, 'INTERNAL', 'Internal error.');
    });
  });

  return { server, sessions };
}

module.exports = { createBotServer, VERSION };

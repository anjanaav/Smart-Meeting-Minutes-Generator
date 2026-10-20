'use strict';

// Validation for everything the bot accepts from the backend. The bot opens a browser at whatever
// Jitsi host it is told to, so private and loopback addresses are refused (no network probing).
// The backend already validates meeting links; these checks are defence in depth.

const crypto = require('node:crypto');
const net = require('node:net');

class ValidationError extends Error {
  constructor(code, message, status = 400) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

const MEETING_ID = /^[A-Za-z0-9_-]{1,64}$/;
const ROOM_PART = /^[\p{L}\p{N}_.\-~]{1,200}$/u;
const BLOCKED_SUFFIXES = ['.local', '.localhost', '.internal', '.lan', '.home', '.corp'];
const LOCAL_HOSTS = ['localhost', '127.0.0.1', '::1'];

// Constant-time comparison of the shared secret (both sides hashed so lengths do not leak).
function secretMatches(provided, expected) {
  if (!expected || typeof provided !== 'string') return false;
  const a = crypto.createHash('sha256').update(provided, 'utf8').digest();
  const b = crypto.createHash('sha256').update(expected, 'utf8').digest();
  return crypto.timingSafeEqual(a, b) && provided === expected;
}

function isPublicIPv4(ip) {
  const p = ip.split('.').map(Number);
  const [a, b, c] = p;
  if (a === 0 || a === 10 || a === 127) return false;
  if (a === 169 && b === 254) return false;
  if (a === 172 && b >= 16 && b <= 31) return false;
  if (a === 192 && b === 168) return false;
  if (a === 192 && b === 0 && c === 0) return false;
  if (a === 100 && b >= 64 && b <= 127) return false; // carrier-grade NAT
  if (a >= 224) return false; // multicast, reserved, broadcast
  return true;
}

function isPublicIPv6(ip) {
  const lower = ip.toLowerCase();
  if (lower === '::' || lower === '::1') return false;
  if (lower.startsWith('::ffff:')) {
    const v4 = lower.slice(7);
    return net.isIPv4(v4) ? isPublicIPv4(v4) : false;
  }
  if (/^fe[89ab]/.test(lower)) return false; // link-local fe80::/10
  if (/^f[cd]/.test(lower)) return false; // unique local fc00::/7
  if (lower.startsWith('ff')) return false; // multicast
  if (lower.startsWith('2001:db8')) return false; // documentation
  return true;
}

// True for a host the bot may open. DNS is not resolved here: the backend has already resolved it.
function isPublicHost(raw) {
  const host = String(raw || '').toLowerCase().replace(/^\[|\]$/g, '').replace(/\.$/, '');
  if (!host || host === 'localhost') return false;
  if (BLOCKED_SUFFIXES.some((s) => host.endsWith(s))) return false;
  const version = net.isIP(host);
  if (version === 4) return isPublicIPv4(host);
  if (version === 6) return isPublicIPv6(host);
  return host.includes('.'); // single-label names can point at internal machines
}

function parseServer(raw, cfg) {
  let u;
  try {
    u = new URL(String(raw || ''));
  } catch {
    throw new ValidationError('INVALID_SERVER', 'The Jitsi server address is invalid.');
  }
  if (u.protocol !== 'https:' || u.username || u.password || (u.pathname !== '/' && u.pathname !== '')) {
    throw new ValidationError('INVALID_SERVER', 'The Jitsi server must be an https address without a path.');
  }
  if (!isPublicHost(u.hostname)) {
    throw new ValidationError('INVALID_SERVER', 'The Jitsi server must be a public host.');
  }
  if (cfg.jitsiAllowedHosts.length && !cfg.jitsiAllowedHosts.map((h) => h.toLowerCase()).includes(u.hostname.toLowerCase())) {
    throw new ValidationError('SERVER_NOT_ALLOWED', 'This Jitsi server is not allowed by the bot configuration.');
  }
  return u.origin;
}

function parseRoom(raw) {
  const parts = String(raw || '').split('/').filter(Boolean);
  if (parts.length < 1 || parts.length > 2 || !parts.every((p) => ROOM_PART.test(p) && p !== '.' && p !== '..')) {
    throw new ValidationError('INVALID_ROOM', 'The meeting room name is invalid.');
  }
  return parts.join('/');
}

function parseBackendUrl(raw, cfg) {
  let u;
  try {
    u = new URL(String(raw || '').trim());
  } catch {
    throw new ValidationError('INVALID_BACKEND_URL', 'The backend address is invalid.');
  }
  if (u.username || u.password) {
    throw new ValidationError('INVALID_BACKEND_URL', 'The backend address must not contain credentials.');
  }
  const local = LOCAL_HOSTS.includes(u.hostname.replace(/^\[|\]$/g, ''));
  if (u.protocol === 'http:') {
    if (!local && !cfg.allowHttpBackend) {
      throw new ValidationError('INVALID_BACKEND_URL', 'The backend must use https (plain http is allowed for localhost only).');
    }
  } else if (u.protocol !== 'https:') {
    throw new ValidationError('INVALID_BACKEND_URL', 'The backend must use https.');
  }
  if (cfg.backendAllowlist.length && !cfg.backendAllowlist.includes(u.origin)) {
    throw new ValidationError('BACKEND_NOT_ALLOWED', 'This backend is not allowed by the bot configuration.', 403);
  }
  return `${u.origin}${u.pathname.replace(/\/+$/, '')}`;
}

function cleanName(value, limit = 60) {
  return String(value || '')
    .replace(/[\x00-\x1f\x7f]/g, '')
    .trim()
    .slice(0, limit);
}

// Participant ids come from Jitsi; keep only characters the backend accepts.
function safeId(value) {
  return String(value || '').replace(/[^A-Za-z0-9_-]/g, '_').slice(0, 64);
}

// Validates the body of POST /api/join and returns the normalized meeting spec.
function validateJoinPayload(body, cfg) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    throw new ValidationError('INVALID_JOIN', 'The join request must be a JSON object.');
  }
  if (typeof body.meeting_id !== 'string' || !MEETING_ID.test(body.meeting_id)) {
    throw new ValidationError('INVALID_MEETING', 'The meeting id is invalid.');
  }
  if (typeof body.bot_token !== 'string' || body.bot_token.length < 20 || body.bot_token.length > 200) {
    throw new ValidationError('INVALID_BOT_TOKEN', 'The bot token is invalid.', 401);
  }
  const server = parseServer(body.server, cfg);
  const room = parseRoom(body.room);
  const backendUrl = parseBackendUrl(body.backend_url, cfg);
  const displayName = cleanName(body.bot_display_name) || cfg.displayName;
  const url = `${server}/${room.split('/').map(encodeURIComponent).join('/')}`;
  return {
    meetingId: body.meeting_id,
    botToken: body.bot_token,
    server,
    room,
    url,
    backendUrl,
    displayName,
  };
}

module.exports = {
  ValidationError,
  MEETING_ID,
  secretMatches,
  isPublicHost,
  parseServer,
  parseRoom,
  parseBackendUrl,
  validateJoinPayload,
  cleanName,
  safeId,
};

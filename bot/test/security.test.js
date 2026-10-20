'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { SECRET, testConfig } = require('./helpers');
const { ValidationError, secretMatches, isPublicHost, validateJoinPayload, safeId, cleanName } = require('../src/security');

const TOKEN = 'a'.repeat(32);

function join(overrides = {}) {
  return {
    meeting_id: 'abc-123',
    bot_token: TOKEN,
    server: 'https://meet.example.org',
    room: 'team-sync',
    backend_url: 'http://127.0.0.1:5000/',
    bot_display_name: 'Bot',
    ...overrides,
  };
}

function expectCode(fn, code) {
  assert.throws(fn, (err) => err instanceof ValidationError && err.code === code, `expected ${code}`);
}

test('secretMatches accepts only the exact secret', () => {
  assert.equal(secretMatches(SECRET, SECRET), true);
  assert.equal(secretMatches(SECRET + 'x', SECRET), false);
  assert.equal(secretMatches('', SECRET), false);
  assert.equal(secretMatches(undefined, SECRET), false);
  assert.equal(secretMatches(SECRET, ''), false, 'an unset secret never matches');
});

test('valid join payload is normalized', () => {
  const spec = validateJoinPayload(join(), testConfig());
  assert.equal(spec.meetingId, 'abc-123');
  assert.equal(spec.url, 'https://meet.example.org/team-sync');
  assert.equal(spec.backendUrl, 'http://127.0.0.1:5000', 'trailing slash removed, localhost http allowed');
  assert.equal(spec.displayName, 'Bot');
});

test('two-part room names are accepted and encoded per part', () => {
  const spec = validateJoinPayload(join({ room: 'org/team-sync' }), testConfig());
  assert.equal(spec.room, 'org/team-sync');
  assert.equal(spec.url, 'https://meet.example.org/org/team-sync');
});

test('unicode room names are accepted (the backend accepts them too)', () => {
  const spec = validateJoinPayload(join({ room: 'réunion-ß' }), testConfig());
  assert.equal(spec.room, 'réunion-ß');
});

test('invalid meeting ids and tokens are refused', () => {
  expectCode(() => validateJoinPayload(join({ meeting_id: '../etc' }), testConfig()), 'INVALID_MEETING');
  expectCode(() => validateJoinPayload(join({ meeting_id: '' }), testConfig()), 'INVALID_MEETING');
  expectCode(() => validateJoinPayload(join({ bot_token: 'short' }), testConfig()), 'INVALID_BOT_TOKEN');
});

test('servers must be public https origins without a path', () => {
  const cfg = testConfig();
  expectCode(() => validateJoinPayload(join({ server: 'http://meet.example.org' }), cfg), 'INVALID_SERVER');
  expectCode(() => validateJoinPayload(join({ server: 'https://meet.example.org/path' }), cfg), 'INVALID_SERVER');
  expectCode(() => validateJoinPayload(join({ server: 'https://user:pw@meet.example.org' }), cfg), 'INVALID_SERVER');
  expectCode(() => validateJoinPayload(join({ server: 'not a url' }), cfg), 'INVALID_SERVER');
});

test('private, loopback and metadata addresses are refused as Jitsi servers', () => {
  const cfg = testConfig();
  for (const server of [
    'https://localhost',
    'https://127.0.0.1',
    'https://10.1.2.3',
    'https://192.168.1.10',
    'https://172.20.0.5',
    'https://169.254.169.254',
    'https://[::1]',
    'https://[fd00::5]',
    'https://jitsi',
    'https://intranet.corp',
  ]) {
    expectCode(() => validateJoinPayload(join({ server }), cfg), 'INVALID_SERVER');
  }
});

test('room names with path tricks are refused', () => {
  const cfg = testConfig();
  for (const room of ['', '../secret', 'a/b/c', 'with space!', '?x=1', '..', '.']) {
    expectCode(() => validateJoinPayload(join({ room }), cfg), 'INVALID_ROOM');
  }
});

test('backend must be https, or http only for localhost', () => {
  const cfg = testConfig();
  expectCode(() => validateJoinPayload(join({ backend_url: 'http://api.example.com' }), cfg), 'INVALID_BACKEND_URL');
  expectCode(() => validateJoinPayload(join({ backend_url: 'ftp://127.0.0.1' }), cfg), 'INVALID_BACKEND_URL');
  expectCode(() => validateJoinPayload(join({ backend_url: 'https://u:p@api.example.com' }), cfg), 'INVALID_BACKEND_URL');
  const ok = validateJoinPayload(join({ backend_url: 'https://api.example.com/smmg/' }), cfg);
  assert.equal(ok.backendUrl, 'https://api.example.com/smmg');
});

test('plain http backend is allowed only when explicitly configured', () => {
  const cfg = testConfig({ allowHttpBackend: true });
  const spec = validateJoinPayload(join({ backend_url: 'http://api.internal-lan.example' }), cfg);
  assert.equal(spec.backendUrl, 'http://api.internal-lan.example');
});

test('allow-lists restrict Jitsi servers and backends when configured', () => {
  const cfg = testConfig({
    jitsiAllowedHosts: ['meet.example.org'],
    backendAllowlist: ['https://api.example.com'],
  });
  expectCode(() => validateJoinPayload(join({ server: 'https://other.example.net' }), cfg), 'SERVER_NOT_ALLOWED');
  expectCode(() => validateJoinPayload(join({ backend_url: 'https://evil.example.com' }), cfg), 'BACKEND_NOT_ALLOWED');
  validateJoinPayload(join({ backend_url: 'https://api.example.com' }), cfg);
});

test('isPublicHost classifies addresses', () => {
  assert.equal(isPublicHost('meet.jit.si'), true);
  assert.equal(isPublicHost('8.8.8.8'), true);
  assert.equal(isPublicHost('::ffff:10.0.0.1'), false, 'mapped private IPv4');
  assert.equal(isPublicHost('2606:4700:4700::1111'), true);
  assert.equal(isPublicHost('fe80::1'), false);
  assert.equal(isPublicHost('100.64.0.1'), false, 'carrier-grade NAT');
  assert.equal(isPublicHost(''), false);
});

test('safeId and cleanName drop unsafe characters', () => {
  assert.equal(safeId('ab/c d"e'), 'ab_c_d_e');
  assert.equal(safeId(''), '');
  assert.equal(cleanName('  Ann\u0000 Lee  '), 'Ann Lee');
  assert.equal(cleanName('x'.repeat(200), 10).length, 10);
});

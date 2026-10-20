'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { classifyPageState } = require('../src/pageState');

const base = { bodyText: '', hasPasswordInput: false, inConference: false, remoteAudioTracks: 0, participants: [] };
const kind = (over) => classifyPageState({ ...base, ...over }).kind;

test('no snapshot or an empty page is loading', () => {
  assert.equal(classifyPageState(null).kind, 'loading');
  assert.equal(kind({}), 'loading');
});

test('a password field means the room is protected', () => {
  assert.equal(kind({ hasPasswordInput: true }), 'password');
});

test('the conference view means joined, even if chat contains lobby-like words', () => {
  assert.equal(kind({ inConference: true, bodyText: 'Waiting for the host to let you in' }), 'joined');
  assert.equal(kind({ inConference: true, remoteAudioTracks: 2 }), 'joined');
});

test('lobby, rejection, removal, end and not-found are recognised outside the conference', () => {
  assert.equal(kind({ bodyText: 'Asking to join. Waiting for the host.' }), 'lobby');
  assert.equal(kind({ bodyText: 'Your request to join was denied.' }), 'rejected');
  assert.equal(kind({ bodyText: 'You were removed from the meeting' }), 'removed');
  assert.equal(kind({ bodyText: 'The conference has ended.' }), 'ended');
  assert.equal(kind({ bodyText: 'This meeting does not exist.' }), 'not_found');
});

test('a dropped connection is distinguished from a loading page', () => {
  assert.equal(kind({ bodyText: 'You have been disconnected. Trying to reconnect' }), 'disconnected');
  assert.equal(kind({ bodyText: 'Connecting to the meeting' }), 'loading');
});

test('password check wins over other text; rejection wins over lobby', () => {
  assert.equal(kind({ hasPasswordInput: true, bodyText: 'waiting for the host' }), 'password');
  assert.equal(kind({ bodyText: 'waiting for the host. your request to join was denied' }), 'rejected');
});

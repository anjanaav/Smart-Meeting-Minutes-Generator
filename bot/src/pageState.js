'use strict';

// Decides what the Jitsi page is showing from a snapshot taken by the browser script.
// Pure function: no browser needed, so every branch is unit-tested.
//
// Text patterns are heuristics against Jitsi's English UI. They are only consulted OUTSIDE the
// conference view, because in-call chat messages can contain the same words. Inside the
// conference, the page is "joined".

const PATTERNS = {
  rejected: /\b(your request to join was denied|was denied entry|not admitted|declined your request)\b/i,
  removed: /\b(you were removed|you have been removed|you were kicked|you have been kicked)\b/i,
  ended: /\b(the conference has ended|meeting has ended|the meeting is over|this meeting has ended)\b/i,
  notFound: /\b(meeting|room|conference)\b[^.\n]{0,40}\b(does not exist|doesn't exist|not found|can't be found)\b/i,
  lobby: /\b(waiting for the host|asking to join|you('| a)re in the lobby|waiting in the lobby|someone will let you in)\b/i,
  disconnected: /\b(you have been disconnected|connection (was )?lost|reconnecting|trying to reconnect)\b/i,
};

// snapshot: { bodyText, hasPasswordInput, inConference, remoteAudioTracks, participants }
// returns: { kind: 'loading'|'password'|'lobby'|'rejected'|'removed'|'ended'|'not_found'|'disconnected'|'joined' }
function classifyPageState(snapshot) {
  if (!snapshot) return { kind: 'loading' };
  if (snapshot.hasPasswordInput) return { kind: 'password' };
  if (snapshot.inConference) return { kind: 'joined' };

  const text = String(snapshot.bodyText || '');
  if (PATTERNS.rejected.test(text)) return { kind: 'rejected' };
  if (PATTERNS.removed.test(text)) return { kind: 'removed' };
  if (PATTERNS.ended.test(text)) return { kind: 'ended' };
  if (PATTERNS.notFound.test(text)) return { kind: 'not_found' };
  if (PATTERNS.lobby.test(text)) return { kind: 'lobby' };
  if (PATTERNS.disconnected.test(text)) return { kind: 'disconnected' };
  return { kind: 'loading' };
}

module.exports = { classifyPageState, PATTERNS };

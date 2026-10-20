'use strict';

// Drives one join. The page is an injected object with the Playwright page surface used here:
//   exposeFunction, addInitScript, goto, evaluate, isClosed.
// Tests pass a fake page; production passes a real Playwright page.
//
// The agent never reports "joined" or "capturing" on its own. "joined" is reported when the page
// shows the conference. The backend sets "capturing" only after it measures real audio.

const { classifyPageState } = require('./pageState');
const { collectSnapshot, initScriptSource } = require('./browser/captureScript');

// Hash parameters read by Jitsi when the page loads: start muted, skip the prejoin screen, and
// set the bot's display name. The bot never turns on its own microphone or camera.
function roomUrl(url, displayName) {
  const params = [
    ['config.startWithAudioMuted', 'true'],
    ['config.startWithVideoMuted', 'true'],
    ['config.prejoinPageEnabled', 'false'],
    ['config.disableDeepLinking', 'true'],
    ['userInfo.displayName', displayName],
  ];
  return `${url}#${params.map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&')}`;
}

async function readSnapshot(page) {
  try {
    return await page.evaluate(collectSnapshot);
  } catch {
    return null; // page mid-navigation; the next poll tries again
  }
}

async function gotoRoom(page, session, cfg) {
  await page.goto(roomUrl(session.meeting.url, session.meeting.displayName), {
    waitUntil: 'domcontentloaded',
    timeout: cfg.joinTimeoutMs,
  });
}

async function joinAndWatch(session, cfg, clock) {
  const page = session.page;
  await page.exposeFunction('__smmgAudio', (payload) => {
    session.onBrowserAudio(payload);
  });
  await page.addInitScript(initScriptSource());

  session.setJoining();
  const startedAt = clock.now();
  let joinedOnce = false;
  let lobbySince = null;
  let offlineSince = null;
  let reconnecting = false; // true from the first off-conference poll until the conference is back
  let rejoins = 0;
  let audioMissingSince = null;
  let aloneSince = null;

  try {
    await gotoRoom(page, session, cfg);
  } catch {
    session.fail('JOIN_FAILED');
    return 'error';
  }

  for (;;) {
    if (session.stopRequested) return 'requested';
    if (typeof page.isClosed === 'function' && page.isClosed()) {
      session.fail('BOT_CRASHED');
      return 'error';
    }

    const snap = await readSnapshot(page);
    const state = classifyPageState(snap);
    const now = clock.now();

    switch (state.kind) {
      case 'password':
        session.fail('PASSWORD_REQUIRED');
        return 'error';
      case 'not_found':
        session.fail('MEETING_NOT_FOUND');
        return 'error';
      case 'rejected':
        session.fail('LOBBY_REJECTED');
        return 'error';
      case 'removed':
      case 'ended':
        if (!joinedOnce) {
          session.fail('JOIN_FAILED'); // the meeting closed before the bot got in
          return 'error';
        }
        return state.kind === 'removed' ? 'bot_left' : 'meeting_ended';

      case 'lobby':
        if (lobbySince === null) {
          lobbySince = now;
          session.setWaiting();
        }
        if (now - lobbySince > cfg.lobbyTimeoutMs) {
          session.fail('LOBBY_TIMEOUT');
          return 'error';
        }
        break;

      case 'loading':
      case 'disconnected': {
        if (!joinedOnce) {
          if (now - startedAt > cfg.joinTimeoutMs) {
            session.fail('JOIN_FAILED');
            return 'error';
          }
          break;
        }
        // Joined earlier, now off the conference view: give the page a grace period, then rejoin.
        if (offlineSince === null) {
          offlineSince = now;
          reconnecting = true;
          session.reportDisconnected();
        }
        if (now - offlineSince > cfg.rejoinGraceMs) {
          if (rejoins >= cfg.maxRejoins) {
            session.fail('BOT_CRASHED');
            return 'error';
          }
          rejoins += 1;
          offlineSince = null;
          try {
            await gotoRoom(page, session, cfg);
          } catch {
            // the next poll decides what happens
          }
        }
        break;
      }

      case 'joined': {
        if (!joinedOnce) {
          joinedOnce = true;
          session.markJoined();
        } else if (reconnecting) {
          reconnecting = false;
          offlineSince = null;
          session.markJoined(); // back in the conference after a reconnect
        }
        session.updateParticipants(snap.participants);

        const others = session.otherParticipantCount();
        if (others > 0 && snap.remoteAudioTracks === 0) {
          if (audioMissingSince === null) audioMissingSince = now;
          if (now - audioMissingSince > cfg.audioTimeoutMs) {
            session.fail('AUDIO_CAPTURE_UNAVAILABLE');
            return 'error';
          }
        } else {
          audioMissingSince = null;
        }

        if (others === 0) {
          if (aloneSince === null) aloneSince = now;
          if (now - aloneSince > cfg.aloneTimeoutMs) return 'meeting_ended';
        } else {
          aloneSince = null;
        }
        break;
      }
      default:
        break;
    }

    await clock.sleep(cfg.pollMs);
  }
}

function createPageAgent({ cfg, clock }) {
  return {
    join: (session) => joinAndWatch(session, cfg, clock),
  };
}

module.exports = { createPageAgent, roomUrl };

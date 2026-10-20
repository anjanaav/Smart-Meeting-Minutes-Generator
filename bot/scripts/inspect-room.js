#!/usr/bin/env node
'use strict';

// Diagnostic for a REAL Jitsi room: joins it exactly like the bot (muted, no prejoin) and prints what the capture
// script can see. Use it when the bot reports AUDIO_CAPTURE_UNAVAILABLE or wrong/missing participant names.
// It sends nothing to the backend and keeps no audio.
//
//   node scripts/inspect-room.js https://meet.jit.si/YourRoomName [--seconds 40] [--headed]
//
// Have at least one other person in the room who speaks during the run. The report answers:
//   1. Did the page attach remote audio with srcObject?  (audio elements with a stream)
//   2. Does window.APP.store exist, and which of its features does it have?
//   3. Which speakers did the script hear, under which names, and how much audio each?
// It does not read chat, and it prints only participant display names and counts, never audio.

const { openBrowser } = require('../src/browser/playwrightDriver');
const { initScriptSource, collectSnapshot } = require('../src/browser/captureScript');
const { roomUrl } = require('../src/pageAgent');

function parseArgs(argv) {
  const out = { url: '', seconds: 40, headed: false };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--seconds') out.seconds = Number(argv[++i]);
    else if (argv[i] === '--headed') out.headed = true;
    else if (!out.url) out.url = argv[i];
  }
  return out;
}

// Runs inside the page: facts about how this Jitsi build wires its audio.
function pageFacts() {
  var media = Array.prototype.slice.call(document.querySelectorAll('audio, video'));
  var withStream = media.filter(function (el) { return el.srcObject; });
  var state = null;
  try { state = window.APP && window.APP.store && window.APP.store.getState(); } catch (e) { state = null; }
  var tracks = state && state['features/base/tracks'];
  return {
    mediaElements: media.length,
    elementsWithSrcObject: withStream.length,
    elementsWithAudioTrack: withStream.filter(function (el) { return el.srcObject.getAudioTracks && el.srcObject.getAudioTracks().length > 0; }).length,
    storeAvailable: !!state,
    storeHasParticipants: !!(state && state['features/base/participants']),
    storeHasTracks: Array.isArray(tracks),
    storeAudioTracks: Array.isArray(tracks) ? tracks.filter(function (t) { return t && t.mediaType === 'audio'; }).length : 0,
    tilesWithDataParticipantId: document.querySelectorAll('[data-participant-id]').length,
    tilesWithParticipantIdAttr: document.querySelectorAll('[id^="participant_"]').length,
    inConferenceView: !!document.querySelector('#videoconference_page, #largeVideoContainer'),
  };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!/^https:\/\//i.test(args.url)) {
    console.error('Usage: node scripts/inspect-room.js https://meet.jit.si/YourRoom [--seconds 40] [--headed]');
    process.exit(2);
  }
  const heard = new Map(); // participantId -> { name, blocks }
  const { page, close } = await openBrowser({
    headless: !args.headed,
    chromiumPath: process.env.BOT_CHROMIUM_PATH || '',
    chromiumNoSandbox: /^(1|true)$/i.test(process.env.BOT_CHROMIUM_NO_SANDBOX || ''),
  });
  try {
    await page.exposeFunction('__smmgAudio', (p) => {
      const entry = heard.get(p.participantId) || { name: p.participantName, blocks: 0 };
      entry.name = p.participantName;
      entry.blocks += 1;
      heard.set(p.participantId, entry);
    });
    await page.addInitScript(initScriptSource());
    await page.goto(roomUrl(args.url, 'Smart Meeting Minutes Inspector'), { waitUntil: 'domcontentloaded', timeout: 60000 });
    console.log(`Joined ${new URL(args.url).host}; listening for ${args.seconds}s. Ask someone in the room to speak.`);
    await new Promise((r) => setTimeout(r, args.seconds * 1000));

    const snap = await page.evaluate(collectSnapshot);
    const facts = await page.evaluate(pageFacts);
    console.log('\n--- what the page does ---');
    console.log(facts);
    console.log('\n--- what the bot would report ---');
    console.log({ inConference: snap.inConference, remoteAudioStreams: snap.remoteAudioTracks, participantSource: snap.participantSource, participants: snap.participants.map((p) => p.name) });
    console.log('\n--- audio heard (about 1 block = 1 second) ---');
    if (heard.size === 0) console.log('none');
    for (const [id, e] of heard) console.log(`${e.name} [${id.slice(0, 8)}]: ${e.blocks} block(s)`);

    console.log('\n--- verdict ---');
    if (!snap.inConference) console.log('The conference view was not detected (password, lobby, or a different page layout). The bot would not report "joined".');
    else if (facts.elementsWithAudioTrack === 0 && heard.size === 0) console.log('No remote audio stream was attached through srcObject. This Jitsi build needs a different capture method; the bot would report AUDIO_CAPTURE_UNAVAILABLE.');
    else if (heard.size === 0) console.log('Streams exist but no audio was captured. Was anyone speaking, and unmuted?');
    else if (snap.participantSource === 'none') console.log('Audio is captured, but no participant list was found: speakers will be labelled "Participant N".');
    else console.log(`Audio is captured and speakers are identified (participant list source: ${snap.participantSource}).`);
  } finally {
    await close().catch(() => {});
  }
}

main().catch((err) => { console.error(`inspect failed: ${err.message}`); process.exit(1); });

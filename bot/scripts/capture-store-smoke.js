#!/usr/bin/env node
'use strict';

// Browser smoke test for the "Jitsi state" paths of the capture script. Needs Playwright + Chromium, NOT a Jitsi room.
// The local page imitates the parts of the Jitsi web app the script reads: window.APP.store with
// 'features/base/tracks' and 'features/base/participants' (participants.remote is a Map), and a track object
// exposing getOriginalStream() / getParticipantId().
//
// It checks: speaker and name come from the store (the audio element carries no hint at all), a change of owner on a
// stream that stays attached is followed, the bot's own (local) audio is never sent, and a stream whose element
// leaves the page is dropped. It does NOT prove a real Jitsi build exposes the same structure: see README.
//
//   node scripts/capture-store-smoke.js   (BOT_CHROMIUM_PATH / BOT_CHROMIUM_NO_SANDBOX are honoured)

const http = require('node:http');
const { openBrowser } = require('../src/browser/playwrightDriver');
const { initScriptSource, collectSnapshot } = require('../src/browser/captureScript');

const PAGE = `<!doctype html><html><body>
<div id="videoconference_page"><div id="stage"></div></div>
<script>
  const ctx = new AudioContext();
  function toneStream(freq) {
    const osc = ctx.createOscillator(); osc.frequency.value = freq;
    const gain = ctx.createGain(); gain.gain.value = 0.4;
    const dest = ctx.createMediaStreamDestination();
    osc.connect(gain); gain.connect(dest); osc.start();
    return dest.stream;
  }
  const remoteStream = toneStream(330);
  const localStream = toneStream(220);
  let owner = 'p-bob';
  const remote = new Map([
    ['p-bob', { id: 'p-bob', name: 'Bob Builder' }],
    ['p-carol', { id: 'p-carol', name: 'Carol Singer' }],
  ]);
  window.APP = { store: { getState: () => ({
    'features/base/participants': { local: { id: 'me', name: 'Smart Meeting Minutes Bot', local: true }, remote },
    'features/base/tracks': [
      { mediaType: 'audio', local: false, participantId: 'p-bob', jitsiTrack: { getOriginalStream: () => remoteStream, getParticipantId: () => owner } },
      { mediaType: 'audio', local: true, jitsiTrack: { getOriginalStream: () => localStream, getParticipantId: () => 'me' } },
    ],
  }) } };
  window.switchOwner = () => { owner = 'p-carol'; };
  window.removeRemote = () => { const el = document.getElementById('audio-slot-1'); if (el) el.remove(); };

  // No data-participant-id, no helpful element id: only the store can say who this is.
  const a = document.createElement('audio'); a.id = 'audio-slot-1'; a.autoplay = true;
  const v = document.createElement('video'); v.id = 'preview'; v.muted = true;
  const stage = document.getElementById('stage');
  stage.appendChild(a); stage.appendChild(v);
  setTimeout(() => { a.srcObject = remoteStream; v.srcObject = localStream; }, 300);
</script></body></html>`;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const energy = (b64) => {
  const buf = Buffer.from(b64, 'base64');
  let sum = 0;
  for (let i = 0; i + 1 < buf.length; i += 2) { const v = buf.readInt16LE(i) / 32768; sum += v * v; }
  return Math.sqrt(sum / (buf.length / 2));
};

async function main() {
  const server = http.createServer((_req, res) => { res.writeHead(200, { 'Content-Type': 'text/html' }); res.end(PAGE); });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const url = `http://127.0.0.1:${server.address().port}/`;
  const cfg = {
    headless: true,
    chromiumPath: process.env.BOT_CHROMIUM_PATH || '',
    chromiumNoSandbox: /^(1|true)$/i.test(process.env.BOT_CHROMIUM_NO_SANDBOX || ''),
  };
  const { page, close } = await openBrowser(cfg);
  const blocks = [];
  let ok = false;
  try {
    await page.exposeFunction('__smmgAudio', (p) => { blocks.push({ ...p, at: Date.now() }); });
    await page.addInitScript(initScriptSource());
    await page.goto(url);

    const waitFor = async (cond, ms) => { const end = Date.now() + ms; while (Date.now() < end && !cond()) await sleep(200); return cond(); };
    await waitFor(() => blocks.length >= 3, 15000);
    const before = blocks.slice();
    const snapBefore = await page.evaluate(collectSnapshot);

    await page.evaluate(() => window.switchOwner());
    const mark = blocks.length;
    await waitFor(() => blocks.length >= mark + 3, 15000);
    const after = blocks.slice(mark + 1); // skip a block that may have been mid-flight at the switch

    await page.evaluate(() => window.removeRemote());
    await sleep(3500); // the page's own sweep runs every 3 s
    const snapGone = await page.evaluate(collectSnapshot);
    const countAfterRemoval = blocks.length;
    await sleep(2500);

    const checks = [
      ['received audio from the remote stream', before.length >= 3],
      ['speaker id comes from the store, not the element', before.every((b) => b.participantId === 'p-bob')],
      ['display name comes from the store', before.every((b) => b.participantName === 'Bob Builder')],
      ['audio has real signal', energy(before[before.length - 1].samples) > 0.05],
      ["the bot's own audio is never sent (only one speaker seen)", new Set(blocks.map((b) => b.participantId)).size === 2 && !blocks.some((b) => b.participantId === 'me')],
      ['no block is labelled with the bot display name', !blocks.some((b) => /Smart Meeting Minutes Bot/.test(b.participantName))],
      ['snapshot lists remote participants from the store', snapBefore.participantSource === 'store' && snapBefore.participants.length === 2],
      ['snapshot excludes the local participant', !snapBefore.participants.some((p) => p.id === 'me')],
      ['a change of owner on the same stream is followed', after.length >= 2 && after.every((b) => b.participantId === 'p-carol' && b.participantName === 'Carol Singer')],
      ['a removed element stops being counted', snapGone.remoteAudioTracks === 0],
      ['no audio is sent after the element is removed', blocks.length - countAfterRemoval <= 1],
    ];
    for (const [name, pass] of checks) console.log(`${pass ? 'PASS' : 'FAIL'} ${name}`);
    ok = checks.every(([, pass]) => pass);
    if (!ok) console.log('blocks seen:', JSON.stringify(blocks.map((b) => [b.participantId, b.participantName])));
  } finally {
    await close().catch(() => {});
    server.close();
  }
  process.exit(ok ? 0 : 1);
}

main().catch((err) => { console.error(`smoke test error: ${err.message}`); process.exit(1); });

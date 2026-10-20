#!/usr/bin/env node
'use strict';

// Second browser smoke test for the capture script: the Jitsi-state paths. Needs Playwright + Chromium, NOT a Jitsi room.
// The page imitates the SHAPE of Jitsi's web app state (window.APP.store with 'features/base/participants' as a Map of
// remote participants, and 'features/base/tracks' with jitsiTrack objects). That shape is from my knowledge of
// jitsi-meet, not from a live room, so a pass here proves the script's logic, not that a given Jitsi build looks like this.
//
// Checks: speakers are named from the store (no data-participant-id on the page at all); the bot's own (local) audio is
// never sent; when the store re-maps the same stream to another speaker the next blocks carry the new speaker; a removed
// element stops being counted; a page without the store still works (covered by capture-smoke.js).

const http = require('node:http');
const { openBrowser } = require('../src/browser/playwrightDriver');
const { initScriptSource, collectSnapshot } = require('../src/browser/captureScript');

const PAGE = `<!doctype html><html><body>
<div id="videoconference_page">
  <audio id="remoteAudio1" autoplay></audio>
  <audio id="localPreview" autoplay></audio>
</div>
<script>
  const ctx = new AudioContext();
  function toneStream(freq) {
    const osc = ctx.createOscillator(); osc.frequency.value = freq;
    const gain = ctx.createGain(); gain.gain.value = 0.4;
    const dest = ctx.createMediaStreamDestination();
    osc.connect(gain); gain.connect(dest); osc.start();
    return dest.stream;
  }
  const remote = toneStream(330), local = toneStream(440);
  let owner = 'p1';
  window.__setOwner = (id) => { owner = id; };
  window.APP = { store: { getState: () => ({
    'features/base/participants': { local: { id: 'me', name: 'Smart Meeting Minutes Bot', local: true },
      remote: new Map([['p1', { id: 'p1', name: 'Alice Example' }], ['p2', { id: 'p2', name: 'Bob Builder' }]]) },
    'features/base/tracks': [
      { mediaType: 'audio', local: false, participantId: 'p1', jitsiTrack: { getOriginalStream: () => remote, getParticipantId: () => owner } },
      { mediaType: 'audio', local: true, jitsiTrack: { getOriginalStream: () => local } },
    ] }) } };
  setTimeout(() => {
    document.getElementById('remoteAudio1').srcObject = remote;
    document.getElementById('localPreview').srcObject = local;
  }, 300);
</script></body></html>`;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const server = http.createServer((_req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/html' });
    res.end(PAGE);
  });
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
    await page.exposeFunction('__smmgAudio', (p) => { blocks.push(p); });
    await page.addInitScript(initScriptSource());
    await page.goto(url);

    const until = async (cond, ms = 15000) => {
      const end = Date.now() + ms;
      while (Date.now() < end && !cond()) await sleep(200);
    };
    await until(() => blocks.length >= 3);
    const beforeRemap = blocks.length;
    const snapBefore = await page.evaluate(collectSnapshot);

    await page.evaluate(() => window.__setOwner('p2'));
    await until(() => blocks.length >= beforeRemap + 3);
    const afterRemap = blocks.slice(beforeRemap + 1); // skip one block that may have been mid-flight

    await page.evaluate(() => document.getElementById('remoteAudio1').remove());
    await sleep(3500); // the pruning interval runs every 3 s
    const snapAfterRemoval = await page.evaluate(collectSnapshot);

    const checks = [
      ['received audio blocks', beforeRemap >= 3],
      ['speaker named from the Jitsi store (no data-participant-id on the page)', blocks.slice(0, beforeRemap).every((b) => b.participantId === 'p1' && b.participantName === 'Alice Example')],
      ["the bot's own (local) audio is never sent", blocks.every((b) => b.participantId !== 'me' && b.participantName !== 'Smart Meeting Minutes Bot')],
      ['only the remote stream is counted as active', snapBefore.remoteAudioTracks === 1],
      ['participant list comes from the store and excludes the bot', snapBefore.participantSource === 'store' && snapBefore.participants.length === 2 && snapBefore.participants.every((p) => p.id !== 'me')],
      ['participant names are read from the store', snapBefore.participants.some((p) => p.id === 'p2' && p.name === 'Bob Builder')],
      ['a re-mapped stream is attributed to the new speaker', afterRemap.length >= 2 && afterRemap.every((b) => b.participantId === 'p2' && b.participantName === 'Bob Builder')],
      ['a removed audio element is no longer counted', snapAfterRemoval.remoteAudioTracks === 0],
    ];
    for (const [name, pass] of checks) console.log(`${pass ? 'PASS' : 'FAIL'} ${name}`);
    ok = checks.every(([, pass]) => pass);
  } finally {
    await close().catch(() => {});
    server.close();
  }
  process.exit(ok ? 0 : 1);
}

main().catch((err) => { console.error(`smoke test error: ${err.message}`); process.exit(1); });

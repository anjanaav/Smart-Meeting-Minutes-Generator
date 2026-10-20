#!/usr/bin/env node
'use strict';

// Browser smoke test for the capture script. Needs Playwright + Chromium (see README), NOT a Jitsi room.
// It serves a tiny local page that behaves like Jitsi's media wiring (a participant tile with
// data-participant-id, and an <audio> element whose srcObject is a MediaStream carrying a tone),
// then checks that the real Playwright driver + capture script see the stream and emit PCM audio.
// It proves the srcObject hook works in Chromium; it does not prove a given Jitsi build uses srcObject.
//
//   node scripts/capture-smoke.js          (BOT_CHROMIUM_PATH / BOT_CHROMIUM_NO_SANDBOX are honoured)

const http = require('node:http');
const { openBrowser } = require('../src/browser/playwrightDriver');
const { initScriptSource, collectSnapshot } = require('../src/browser/captureScript');

const PAGE = `<!doctype html><html><body>
<div id="videoconference_page">
  <div data-participant-id="bob-1"><span class="display-name">Bob Builder</span><audio id="a" autoplay></audio></div>
</div>
<script>
  const ctx = new AudioContext();
  const osc = ctx.createOscillator(); osc.frequency.value = 330;
  const gain = ctx.createGain(); gain.gain.value = 0.4;
  const dest = ctx.createMediaStreamDestination();
  osc.connect(gain); gain.connect(dest); osc.start();
  setTimeout(() => { document.getElementById('a').srcObject = dest.stream; }, 300);
</script></body></html>`;

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
    const deadline = Date.now() + 15000;
    while (Date.now() < deadline && blocks.length < 3) await new Promise((r) => setTimeout(r, 250));
    const snap = await page.evaluate(collectSnapshot);

    const energy = (b64) => {
      const buf = Buffer.from(b64, 'base64');
      let sum = 0;
      for (let i = 0; i + 1 < buf.length; i += 2) { const v = buf.readInt16LE(i) / 32768; sum += v * v; }
      return Math.sqrt(sum / (buf.length / 2));
    };
    const checks = [
      ['received audio blocks', blocks.length >= 3],
      ['blocks are tagged with the participant id', blocks.every((b) => b.participantId === 'bob-1')],
      ['display name read from the tile', blocks.every((b) => b.participantName === 'Bob Builder')],
      ['sample rate is 16 kHz', blocks.every((b) => b.sampleRate === 16000)],
      ['audio has real signal (RMS > 0.05)', blocks.length > 0 && blocks.slice(-1).every((b) => energy(b.samples) > 0.05)],
      ['snapshot sees an active stream', snap.remoteAudioTracks === 1],
      ['snapshot sees the participant', snap.participants.some((p) => p.id === 'bob-1' && p.name === 'Bob Builder')],
      ['snapshot sees the conference view', snap.inConference === true],
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

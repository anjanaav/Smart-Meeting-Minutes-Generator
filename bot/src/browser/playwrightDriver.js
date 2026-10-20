'use strict';

// The only file that needs Chromium and Playwright on the machine running the bot.
// Everything else in the bot is tested without a browser.
//
// Setup (once per machine):   npm install && npx playwright install chromium
//
// Each meeting gets its own browser context, so cookies and storage never carry over between
// meetings. The bot's microphone and camera are never turned on: Jitsi starts muted, and the
// fake device flags only stop Chromium from asking for permission.

function loadPlaywright() {
  try {
    return require('playwright');
  } catch {
    throw new Error('Playwright is not installed. Run "npm install" and then "npx playwright install chromium" in the bot folder.');
  }
}

async function openBrowser(cfg) {
  const { chromium } = loadPlaywright();

  const args = [
    '--autoplay-policy=no-user-gesture-required', // remote audio must play without a click
    '--use-fake-ui-for-media-stream', // auto-accept device prompts (the bot never sends its own audio)
    '--use-fake-device-for-media-stream', // container machines have no microphone
  ];
  if (cfg.chromiumNoSandbox) args.push('--no-sandbox', '--disable-setuid-sandbox');

  const browser = await chromium.launch({
    headless: cfg.headless,
    args,
    executablePath: cfg.chromiumPath || undefined,
  });

  try {
    const context = await browser.newContext({
      permissions: ['microphone', 'camera'],
    });
    const page = await context.newPage();
    return {
      page,
      close: async () => {
        await browser.close();
      },
    };
  } catch (err) {
    await browser.close().catch(() => {});
    throw err;
  }
}

module.exports = { openBrowser };

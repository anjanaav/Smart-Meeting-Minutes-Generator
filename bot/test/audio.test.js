'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const {
  decodeInt16Base64,
  encodeInt16Base64,
  resampleLinear,
  rmsLinear,
  pcm16ToWav,
  int16ToBuffer,
  Segmenter,
} = require('../src/audio');
const { tone } = require('../src/browser/fakeDriver');

test('base64 int16 encoding round-trips (little-endian)', () => {
  const samples = Int16Array.from([0, 1, -1, 32767, -32768, 1234]);
  assert.deepEqual(Array.from(decodeInt16Base64(encodeInt16Base64(samples))), Array.from(samples));
  // Explicit byte check: 0x0102 must be stored as 02 01.
  assert.equal(encodeInt16Base64(Int16Array.from([0x0102])), Buffer.from([0x02, 0x01]).toString('base64'));
});

test('WAV wrapper has a valid 44-byte PCM header', () => {
  const pcm = int16ToBuffer(Int16Array.from([1, 2, 3, 4]));
  const wav = pcm16ToWav(pcm, 16000);
  assert.equal(wav.length, 44 + pcm.length);
  assert.equal(wav.toString('ascii', 0, 4), 'RIFF');
  assert.equal(wav.readUInt32LE(4), 36 + pcm.length);
  assert.equal(wav.toString('ascii', 8, 12), 'WAVE');
  assert.equal(wav.toString('ascii', 12, 16), 'fmt ');
  assert.equal(wav.readUInt16LE(20), 1, 'PCM format');
  assert.equal(wav.readUInt16LE(22), 1, 'mono');
  assert.equal(wav.readUInt32LE(24), 16000, 'sample rate');
  assert.equal(wav.readUInt32LE(28), 32000, 'byte rate');
  assert.equal(wav.readUInt16LE(34), 16, 'bits per sample');
  assert.equal(wav.toString('ascii', 36, 40), 'data');
  assert.equal(wav.readUInt32LE(40), pcm.length);
});

test('RMS of a full-scale sine is about 0.707 and of silence is 0', () => {
  const sine = tone(1, 220, 1.0);
  assert.ok(Math.abs(rmsLinear(sine) - 0.707) < 0.01);
  assert.equal(rmsLinear(new Int16Array(160)), 0);
  assert.equal(rmsLinear(new Int16Array(0)), 0);
});

test('resampling changes length by the rate ratio', () => {
  const src = tone(1, 440, 0.5, 16000);
  const down = resampleLinear(src, 16000, 8000);
  assert.equal(down.length, 8000);
  assert.equal(resampleLinear(src, 16000, 16000), src, 'same rate returns the input');
  assert.equal(resampleLinear(new Int16Array(0), 48000, 16000).length, 0);
});

test('segmenter cuts full chunks with increasing offsets and keeps the remainder', () => {
  const seg = new Segmenter({ sampleRate: 1000, chunkMs: 1000 }); // 1 kHz keeps the numbers readable
  const out1 = seg.push(new Int16Array(1500), 0);
  assert.equal(out1.length, 1);
  assert.equal(out1[0].samples.length, 1000);
  assert.equal(out1[0].offsetMs, 0);

  const out2 = seg.push(new Int16Array(1000), 1500); // arrives right after, no gap
  assert.equal(out2.length, 1);
  assert.equal(out2[0].offsetMs, 1000);
  assert.equal(out2[0].samples.length, 1000);

  const rest = seg.flush();
  assert.equal(rest.samples.length, 500);
  assert.equal(rest.offsetMs, 2000);
  assert.equal(seg.flush(), null, 'nothing left after flush');
});

test('a gap in arrival time starts a new chunk so offsets stay honest', () => {
  const seg = new Segmenter({ sampleRate: 1000, chunkMs: 5000, maxGapMs: 1500 });
  assert.deepEqual(seg.push(new Int16Array(1000), 0), []);
  const out = seg.push(new Int16Array(1000), 10_000); // ten seconds later
  assert.equal(out.length, 1, 'the earlier audio is flushed as its own chunk');
  assert.equal(out[0].offsetMs, 0);
  const tail = seg.flush();
  assert.equal(tail.offsetMs, 10_000);
});

test('a block larger than one chunk yields several chunks in order', () => {
  const seg = new Segmenter({ sampleRate: 1000, chunkMs: 1000 });
  const out = seg.push(new Int16Array(3000), 500);
  assert.deepEqual(out.map((s) => s.offsetMs), [500, 1500, 2500]);
  assert.equal(out.every((s) => s.samples.length === 1000), true);
});

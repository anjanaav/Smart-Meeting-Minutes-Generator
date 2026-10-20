'use strict';

// Pure audio helpers. Samples are mono signed 16-bit PCM (Int16Array).

// Base64 little-endian int16 -> Int16Array (endianness-safe, copies the data).
function decodeInt16Base64(b64) {
  const bytes = Buffer.from(b64, 'base64');
  const out = new Int16Array(Math.floor(bytes.length / 2));
  for (let i = 0; i < out.length; i += 1) out[i] = bytes.readInt16LE(i * 2);
  return out;
}

// Inverse of decodeInt16Base64 (used by the fake browser and tests).
function encodeInt16Base64(samples) {
  const buf = Buffer.alloc(samples.length * 2);
  for (let i = 0; i < samples.length; i += 1) buf.writeInt16LE(samples[i], i * 2);
  return buf.toString('base64');
}

function concatInt16(a, b) {
  const out = new Int16Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return out;
}

// Linear resampling (adequate for speech before ASR).
function resampleLinear(samples, from, to) {
  if (from === to || samples.length === 0) return samples;
  const ratio = from / to;
  const outLength = Math.floor(samples.length / ratio);
  const out = new Int16Array(outLength);
  for (let i = 0; i < outLength; i += 1) {
    const pos = i * ratio;
    const i0 = Math.floor(pos);
    const i1 = Math.min(i0 + 1, samples.length - 1);
    const frac = pos - i0;
    out[i] = Math.round(samples[i0] * (1 - frac) + samples[i1] * frac);
  }
  return out;
}

// Root-mean-square level on a 0..1 scale. The backend expects this linear value (it converts to dB).
function rmsLinear(samples) {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < samples.length; i += 1) {
    const v = samples[i] / 32768;
    sum += v * v;
  }
  return Math.sqrt(sum / samples.length);
}

// Wraps raw little-endian PCM16 mono in a 44-byte WAV header.
function pcm16ToWav(pcm, sampleRate) {
  const header = Buffer.alloc(44);
  header.write('RIFF', 0, 'ascii');
  header.writeUInt32LE(36 + pcm.length, 4);
  header.write('WAVE', 8, 'ascii');
  header.write('fmt ', 12, 'ascii');
  header.writeUInt32LE(16, 16); // fmt chunk size
  header.writeUInt16LE(1, 20); // PCM
  header.writeUInt16LE(1, 22); // mono
  header.writeUInt32LE(sampleRate, 24);
  header.writeUInt32LE(sampleRate * 2, 28); // byte rate
  header.writeUInt16LE(2, 32); // block align
  header.writeUInt16LE(16, 34); // bits per sample
  header.write('data', 36, 'ascii');
  header.writeUInt32LE(pcm.length, 40);
  return Buffer.concat([header, pcm]);
}

function int16ToBuffer(samples) {
  const buf = Buffer.alloc(samples.length * 2);
  for (let i = 0; i < samples.length; i += 1) buf.writeInt16LE(samples[i], i * 2);
  return buf;
}

function durationMs(sampleCount, sampleRate) {
  return Math.round((sampleCount * 1000) / sampleRate);
}

// Cuts one participant's continuous stream into fixed-length chunks with timestamps.
// A gap larger than maxGapMs (silence in the capture, a stalled page) starts a new chunk, so
// offsets stay honest. Offsets are milliseconds from the start of the meeting.
class Segmenter {
  constructor({ sampleRate, chunkMs, maxGapMs = 1500 }) {
    this.sr = sampleRate;
    this.chunkSamples = Math.round((sampleRate * chunkMs) / 1000);
    this.maxGapMs = maxGapMs;
    this.buf = new Int16Array(0);
    this.startMs = 0;
    this.expectedNextMs = null;
  }

  // Adds samples whose first sample happened at atMs. Returns completed chunks.
  push(samples, atMs) {
    const out = [];
    if (this.buf.length > 0 && this.expectedNextMs !== null && Math.abs(atMs - this.expectedNextMs) > this.maxGapMs) {
      const gapped = this.flush();
      if (gapped) out.push(gapped);
    }
    if (this.buf.length === 0) this.startMs = atMs;
    this.buf = concatInt16(this.buf, samples);
    this.expectedNextMs = this.startMs + durationMs(this.buf.length, this.sr);
    while (this.buf.length >= this.chunkSamples) out.push(this.cut(this.chunkSamples));
    return out;
  }

  cut(n) {
    const samples = this.buf.slice(0, n);
    const offsetMs = Math.round(this.startMs);
    this.buf = this.buf.slice(n);
    this.startMs += durationMs(n, this.sr);
    this.expectedNextMs = this.startMs + durationMs(this.buf.length, this.sr);
    return { samples, offsetMs };
  }

  // Returns whatever is buffered (may be shorter than a full chunk), or null when empty.
  flush() {
    if (this.buf.length === 0) return null;
    const seg = { samples: this.buf, offsetMs: Math.round(this.startMs) };
    this.buf = new Int16Array(0);
    this.expectedNextMs = null;
    return seg;
  }
}

module.exports = {
  decodeInt16Base64,
  encodeInt16Base64,
  concatInt16,
  resampleLinear,
  rmsLinear,
  pcm16ToWav,
  int16ToBuffer,
  durationMs,
  Segmenter,
};

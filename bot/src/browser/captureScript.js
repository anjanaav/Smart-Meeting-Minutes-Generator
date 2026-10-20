'use strict';

// Code that runs INSIDE the Jitsi page (through Playwright). It is written as plain functions
// and serialized with Function.prototype.toString, so it must not reference anything from Node.
//
// How capture works:
//   Jitsi attaches each remote participant's audio MediaStream to a media element via
//   element.srcObject. We wrap that setter, so every remote stream is seen as it is attached.
//   Each stream goes through a Web Audio graph that downsamples to 16 kHz and emits ~1 s blocks of
//   16-bit PCM. Those blocks go to Node through the "__smmgAudio" binding.
//
// Who is speaking (in order of preference):
//   1. Jitsi's own state, window.APP.store (the Redux store of the web app). It lists every audio
//      track with its participant and every participant with a display name. It is looked up again for
//      EVERY block, so if Jitsi re-maps a shared audio slot to another speaker (newer servers do this),
//      the next block carries the new speaker. The bot's own (local) audio is dropped.
//   2. The page itself: an element id that contains a known participant id, then the nearest ancestor
//      with data-participant-id or an id of the form participant_<id>.
//   3. A generic label ("Participant N").
//
// Assumptions that only a real room can confirm (see README, "Troubleshooting Jitsi capture"):
//   - Remote audio is attached with srcObject (true for current jitsi-meet builds). If Jitsi routes
//     audio through Web Audio only, no stream is seen and the bot reports AUDIO_CAPTURE_UNAVAILABLE.
//   - window.APP.store has 'features/base/tracks' and 'features/base/participants'. This is an
//     internal API of the Jitsi web app and can change; when it is missing, the page heuristics are used.
//   - The conference view has #videoconference_page or #largeVideoContainer.

// Installed before any page script runs (addInitScript), so it hooks the very first attachment.
function captureInit() {
  if (window.__smmgInstalled) return;
  window.__smmgInstalled = true;

  var TARGET_RATE = 16000;
  var BLOCK_SECONDS = 1;
  var ctx = null;
  var records = new WeakMap(); // MediaStream -> record (never keeps a stream alive once it is dropped)
  var live = new Set();
  var ordinal = 0;
  var elementIds = new WeakMap();

  function audioContext() {
    if (!ctx || ctx.state === 'closed') {
      try {
        ctx = new AudioContext({ sampleRate: TARGET_RATE });
      } catch (e) {
        ctx = new AudioContext();
      }
    }
    if (ctx.state === 'suspended') ctx.resume().catch(function () {});
    return ctx;
  }

  // ---- Jitsi's own state ----------------------------------------------------------------
  function appState() {
    try {
      var app = window.APP;
      if (app && app.store && typeof app.store.getState === 'function') return app.store.getState();
    } catch (e) {
      // the store is not ready or not exposed
    }
    return null;
  }

  // { id: { id, name } } for every remote, visible participant, or null when the state has no participant list.
  function storeParticipants(state) {
    var part = state && state['features/base/participants'];
    if (!part) return null;
    var out = {};
    function add(p, key) {
      if (!p || p.local || p.fakeParticipant || p.isHidden || p.hidden) return;
      var id = p.id || key;
      if (id === undefined || id === null || id === '') return;
      out[String(id)] = { id: String(id), name: String(p.name || p.displayName || '').trim() };
    }
    var remote = part.remote;
    if (remote && typeof remote.forEach === 'function') {
      remote.forEach(function (p, key) {
        add(p, typeof key === 'string' ? key : undefined); // Map: (value, key). Array: (value, index)
      });
    } else if (remote && typeof remote === 'object') {
      Object.keys(remote).forEach(function (k) {
        add(remote[k], k);
      });
    } else if (Array.isArray(part)) {
      part.forEach(function (p) {
        add(p);
      });
    }
    return out;
  }

  // { id, local } for the audio track that owns this stream, or null when the state does not know it.
  function ownerFromStore(stream) {
    var state = appState();
    var tracks = state && state['features/base/tracks'];
    if (!stream || !Array.isArray(tracks)) return null;
    for (var i = 0; i < tracks.length; i++) {
      var t = tracks[i];
      if (!t || t.mediaType !== 'audio' || !t.jitsiTrack) continue;
      var jt = t.jitsiTrack;
      var s = null;
      try {
        s = typeof jt.getOriginalStream === 'function' ? jt.getOriginalStream() : jt.stream;
      } catch (e) {
        s = null;
      }
      if (!s || !(s === stream || (s.id && s.id === stream.id))) continue;
      if (t.local) return { id: null, local: true };
      var id = null;
      try {
        id = typeof jt.getParticipantId === 'function' ? jt.getParticipantId() : null; // current mapping
      } catch (e) {
        id = null;
      }
      id = id || t.participantId;
      return id ? { id: String(id), local: false } : null;
    }
    return null;
  }

  // ---- the page itself (fallbacks) ----------------------------------------------------------
  function tileFor(id) {
    var safe = String(id).replace(/["\\]/g, '');
    return document.querySelector('[data-participant-id="' + safe + '"]') || document.getElementById('participant_' + safe);
  }

  function domName(id) {
    var tile = tileFor(id);
    if (!tile) return '';
    var label = tile.querySelector('[data-displayname], .display-name, .displayname');
    var text = label ? label.getAttribute('data-displayname') || label.textContent || '' : '';
    return text.replace(/\s+/g, ' ').trim().slice(0, 80);
  }

  function domTileIds() {
    var ids = [];
    var seen = {};
    function push(id) {
      if (!id || seen[id] || /local/i.test(id)) return;
      seen[id] = true;
      ids.push(id);
    }
    document.querySelectorAll('[data-participant-id]').forEach(function (tile) {
      push(tile.getAttribute('data-participant-id'));
    });
    document.querySelectorAll('[id^="participant_"]').forEach(function (tile) {
      push(String(tile.id).slice('participant_'.length));
    });
    return ids;
  }

  function knownParticipantIds() {
    var map = storeParticipants(appState());
    return map ? Object.keys(map) : domTileIds();
  }

  // Attach-time guess for streams the state does not know about.
  function participantIdFor(el) {
    var elId = el && el.id ? String(el.id) : '';
    if (elId) {
      var known = knownParticipantIds();
      for (var k = 0; k < known.length; k++) {
        if (known[k].length >= 4 && elId.indexOf(known[k]) !== -1) return known[k];
      }
    }
    var node = el;
    while (node && node.nodeType === 1) {
      var attr = node.getAttribute('data-participant-id');
      if (attr) return attr;
      var nid = node.id ? String(node.id) : '';
      if (nid.indexOf('participant_') === 0 && !/local/i.test(nid)) return nid.slice('participant_'.length);
      node = node.parentElement;
    }
    if (!elementIds.has(el)) {
      ordinal += 1;
      elementIds.set(el, 'remote' + ordinal);
    }
    return elementIds.get(el);
  }

  function nameFor(id, fallbackOrdinal) {
    var map = storeParticipants(appState());
    var name = map && map[id] ? map[id].name : '';
    if (!name) name = domName(id);
    if (name) return name.slice(0, 80);
    return /^remote\d+$/.test(String(id)) ? 'Participant ' + fallbackOrdinal : 'Participant ' + String(id).slice(0, 4);
  }

  // ---- audio ----------------------------------------------------------------------------------
  function toBase64(bytes) {
    var s = '';
    for (var i = 0; i < bytes.length; i += 0x8000) {
      s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return btoa(s);
  }

  function emit(rec, c) {
    var total = rec.count;
    var merged = new Float32Array(total);
    var pos = 0;
    for (var k = 0; k < rec.blocks.length; k++) {
      merged.set(rec.blocks[k], pos);
      pos += rec.blocks[k].length;
    }
    rec.blocks = [];
    rec.count = 0;

    // Who owns this audio RIGHT NOW (the mapping can change while the stream stays attached).
    var owner = ownerFromStore(rec.stream);
    if (owner && owner.local) return; // never send the bot's own audio
    var id = owner && owner.id ? owner.id : rec.id;

    var pcm = new Int16Array(total);
    for (var i = 0; i < total; i++) {
      var s = Math.max(-1, Math.min(1, merged[i]));
      pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    if (window.__smmgAudio) {
      Promise.resolve(
        window.__smmgAudio({
          participantId: id,
          participantName: nameFor(id, rec.ordinal),
          samples: toBase64(new Uint8Array(pcm.buffer)),
          sampleRate: c.sampleRate,
        }),
      ).catch(function () {});
    }
  }

  // A stream counts only while it is really in use: a live track, on an element that is still in the page
  // and still showing this stream. (Jitsi removes the element when someone leaves; the track may never fire "ended".)
  function isActive(rec) {
    var tracks = rec.stream.getAudioTracks();
    var alive = false;
    for (var i = 0; i < tracks.length; i++) {
      if (tracks[i].readyState === undefined || tracks[i].readyState === 'live') alive = true;
    }
    return alive && !!rec.el && rec.el.isConnected !== false && rec.el.srcObject === rec.stream;
  }

  function prune() {
    live.forEach(function (rec) {
      if (!isActive(rec)) rec.stop();
    });
  }

  function track(stream, el) {
    if (!stream || typeof stream.getAudioTracks !== 'function') return;
    if (stream.getAudioTracks().length === 0 || records.has(stream)) return;
    // A muted <video> is the bot's own preview, not a remote participant.
    if (el && el.tagName === 'VIDEO' && el.muted) return;
    var owner = ownerFromStore(stream);
    if (owner && owner.local) return;
    var c = audioContext();
    var rec = { id: owner && owner.id ? owner.id : participantIdFor(el), ordinal: 0, blocks: [], count: 0, stop: null, stream: stream, el: el };
    rec.ordinal = live.size + 1;
    records.set(stream, rec);

    var source = c.createMediaStreamSource(stream);
    var processor = c.createScriptProcessor(4096, 1, 1);
    var mute = c.createGain();
    mute.gain.value = 0;
    processor.onaudioprocess = function (ev) {
      var input = ev.inputBuffer.getChannelData(0);
      rec.blocks.push(new Float32Array(input));
      rec.count += input.length;
      if (rec.count >= c.sampleRate * BLOCK_SECONDS) emit(rec, c);
    };
    source.connect(processor);
    processor.connect(mute);
    mute.connect(c.destination);

    rec.stop = function () {
      try {
        source.disconnect();
        processor.disconnect();
        mute.disconnect();
      } catch (e) {
        // already disconnected
      }
      processor.onaudioprocess = null;
      live.delete(rec);
      records.delete(stream); // a stream attached again later is picked up again
    };
    live.add(rec);
    stream.getAudioTracks().forEach(function (t) {
      t.addEventListener('ended', function () {
        rec.stop();
      });
    });
  }

  function participants() {
    var map = storeParticipants(appState());
    var out = [];
    if (map) {
      Object.keys(map).forEach(function (id) {
        out.push({ id: id, name: map[id].name || domName(id) || 'Participant ' + id.slice(0, 4) });
      });
      return { list: out, source: 'store' };
    }
    domTileIds().forEach(function (id) {
      out.push({ id: id, name: domName(id) || 'Participant ' + (out.length + 1) });
    });
    return { list: out, source: out.length ? 'dom' : 'none' };
  }

  // Wrap the srcObject setter so every stream attached by the page is seen.
  var desc = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'srcObject');
  if (desc && desc.get && desc.set) {
    Object.defineProperty(HTMLMediaElement.prototype, 'srcObject', {
      configurable: true,
      enumerable: desc.enumerable,
      get: function () {
        return desc.get.call(this);
      },
      set: function (value) {
        desc.set.call(this, value);
        try {
          track(value, this);
        } catch (e) {
          // never break the page's own media handling
        }
      },
    });
  }
  // Also pick up streams attached before this script ran, or through a path the setter missed,
  // and drop the ones that are gone.
  setInterval(function () {
    try {
      prune();
    } catch (e) {
      // ignore
    }
    document.querySelectorAll('audio, video').forEach(function (el) {
      if (el.srcObject) {
        try {
          track(el.srcObject, el);
        } catch (e) {
          // ignore
        }
      }
    });
  }, 3000);

  window.__smmg = {
    activeCount: function () {
      prune();
      return live.size;
    },
    participants: participants,
  };
}

// Run by Node after navigation to read the page state.
function collectSnapshot() {
  var text = (document.body && document.body.innerText) || '';
  var cap = window.__smmg;
  var found = cap ? cap.participants() : { list: [], source: 'none' };
  return {
    bodyText: text.slice(0, 4000),
    hasPasswordInput: !!document.querySelector('input[type="password"]'),
    inConference: !!document.querySelector('#videoconference_page, #largeVideoContainer'),
    remoteAudioTracks: cap ? cap.activeCount() : 0,
    participants: found.list,
    participantSource: found.source, // 'store' | 'dom' | 'none': how trustworthy the list is
  };
}

function initScriptSource() {
  return '(' + captureInit.toString() + ')();';
}

module.exports = { captureInit, collectSnapshot, initScriptSource };

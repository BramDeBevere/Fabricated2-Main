/*
  p2p.js
  ──────
  The "room" client shared by EVERY device — pure peer-to-peer, no server.

  Uses WebRTC (via the PeerJS library, loaded from a CDN) so one device can be
  the ROOM HOST and every other device connects DIRECTLY to it. There is no
  python, no terminal, and no backend — the whole installation is static files
  that can be hosted anywhere (e.g. Netlify). The only thing the devices need
  beyond the code is an internet connection (used for the one-time handshake /
  NAT via PeerJS's free cloud + STUN; the actual data is device-to-device).

  How it maps to "person A gets a code, person B enters it, they share a room":
    • The Projector-2 · Hub page calls  P2P.host({ code })   → it becomes the
      room whose ID is that code and accepts every device that connects.
    • A tablet / Projector 1 calls      P2P.connect({ code }) → it dials the
      host by code. Wrong / not-open code  =>  onDenied.

  Exposes window.P2P:
    P2P.code()          -> the room code (?code, else localStorage)
    P2P.rememberCode()  -> persist the code
    P2P.connect(opts)   -> client. returns { send(obj), close(), socket }
    P2P.host(opts)      -> host.   returns { code, broadcast(obj), count(), close() }

  Client opts : { id, kind, onOpen, onMessage(d, socket), onDenied(d), onClose() }
  Host opts   : { code, onOpen, onJoin(rec), onMessage(d, rec), onLeave(rec), onError(e) }
  Both sides exchange JSON. A device's first message is { type:'join', id, kind }.
*/
(function (global) {
  'use strict';

  var qs = new URLSearchParams(location.search);
  var NAMESPACE = 'fabricated2-';   // prefix so our room ids never collide with others

  // The full PeerJS peer id for a room code. The code itself is what people
  // type; the prefix is invisible plumbing that avoids cross-app collisions.
  function peerIdFor(c) {
    return NAMESPACE + String(c || '').toUpperCase();
  }

  function code() {
    var c = qs.get('code');
    if (c) return c.trim().toUpperCase();
    try { return (localStorage.getItem('fabricated_room_code') || '').trim().toUpperCase(); }
    catch (e) { return ''; }
  }

  function rememberCode(c) {
    try { localStorage.setItem('fabricated_room_code', (c || '').trim().toUpperCase()); }
    catch (e) {}
  }

  function parse(raw) {
    if (raw == null) return null;
    if (typeof raw === 'string') { try { return JSON.parse(raw); } catch (e) { return null; } }
    return raw;
  }

  function lib() { return (typeof global.Peer !== 'undefined') ? global.Peer : null; }
  function noop() {}

  // ── CLIENT: dial the room host by code ────────────────────────────────────
  function connect(opts) {
    opts = opts || {};
    var PeerCtor = lib();
    var socket = null;
    var closedByUs = false;
    var denied = false;

    if (!PeerCtor) {
      if (opts.onDenied) opts.onDenied({ type: 'denied', reason: 'lib' });
      return { send: noop, close: noop, socket: null };
    }

    var peer = new PeerCtor();

    function doConnect() {
      if (closedByUs || denied || peer.destroyed) return;
      try { socket = peer.connect(peerIdFor(code()), { reliable: true }); }
      catch (e) { return; }
      socket.on('open', function () {
        socket.send(JSON.stringify({ type: 'join', id: opts.id, kind: opts.kind || 'tablet' }));
        if (opts.onOpen) opts.onOpen(socket);
      });
      socket.on('data', function (raw) {
        var d = parse(raw);
        if (!d) return;
        if (d.type === 'denied') { denied = true; if (opts.onDenied) opts.onDenied(d); return; }
        if (opts.onMessage) opts.onMessage(d, socket);
      });
      socket.on('close', function () {
        if (closedByUs) return;
        if (opts.onClose) opts.onClose();
        scheduleReconnect();
      });
    }

    function scheduleReconnect() {
      if (closedByUs || denied) return;
      setTimeout(function () { if (!closedByUs && !denied) doConnect(); }, 2000);
    }

    peer.on('open', function () { doConnect(); });
    peer.on('error', function (err) {
      var t = err && err.type;
      // The host for this code isn't open (wrong code, or host not started):
      // stop retrying and surface a clear error, matching the old behaviour.
      if (t === 'peer-unavailable') {
        denied = true;
        if (opts.onDenied) opts.onDenied({ type: 'denied', reason: 'bad-code' });
      }
      // other error types are transient (network blips); PeerJS may auto-recover
    });
    peer.on('disconnected', function () {
      // Lost the signalling cloud (NOT the data channels). Re-attach so new
      // connections can form again; existing links keep working.
      if (!closedByUs && !denied) { try { peer.reconnect(); } catch (e) {} }
    });

    return {
      send: function (obj) { if (socket && socket.open) socket.send(JSON.stringify(obj)); },
      close: function () {
        closedByUs = true;
        try { if (socket) socket.close(); } catch (e) {}
        try { if (!peer.destroyed) peer.destroy(); } catch (e) {}
      },
      get socket() { return socket; },
    };
  }

  // ── HOST: BECOME the room identified by `code` ────────────────────────────
  function host(opts) {
    opts = opts || {};
    var PeerCtor = lib();
    var roomCode = (opts.code || code());
    var clients = {};   // conn.peer -> { id, kind, conn }

    if (!PeerCtor) {
      if (opts.onError) opts.onError({ reason: 'lib' });
      return { code: roomCode, broadcast: noop, count: function () { return 0; }, close: noop };
    }

    var peer = new PeerCtor(peerIdFor(roomCode));

    peer.on('open', function () { if (opts.onOpen) opts.onOpen(); });
    peer.on('error', function (err) {
      var t = err && err.type;
      if (t === 'unavailable-id') { if (opts.onError) opts.onError({ reason: 'code-in-use' }); }
      else if (opts.onError) opts.onError({ reason: t || 'error' });
    });
    peer.on('disconnected', function () { try { peer.reconnect(); } catch (e) {} });
    peer.on('connection', function (conn) {
      var rec = { id: null, kind: null, conn: conn };
      clients[conn.peer] = rec;
      conn.on('data', function (raw) {
        var d = parse(raw);
        if (!d) return;
        if (d.type === 'join') { rec.id = d.id; rec.kind = d.kind; if (opts.onJoin) opts.onJoin(rec); }
        else if (opts.onMessage) opts.onMessage(d, rec);
      });
      conn.on('close', function () {
        delete clients[conn.peer];
        if (opts.onLeave) opts.onLeave(rec);
      });
    });

    function broadcast(obj) {
      var s = JSON.stringify(obj);
      for (var k in clients) {
        var c = clients[k];
        if (c && c.conn && c.conn.open) { try { c.conn.send(s); } catch (e) {} }
      }
    }

    return {
      code: roomCode,
      clients: clients,
      count: function () { return Object.keys(clients).length; },
      broadcast: broadcast,
      close: function () { try { if (!peer.destroyed) peer.destroy(); } catch (e) {} },
    };
  }

  global.P2P = {
    code: code,
    rememberCode: rememberCode,
    connect: connect,
    host: host,
    _peerIdFor: peerIdFor,
  };
})(typeof window !== 'undefined' ? window : this);

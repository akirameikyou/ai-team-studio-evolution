// 3-B (RECORD_UI_3B.md 0.6): the page's side of the record API of 3-A (RECORD_API_3A.md 0.6.2).
// The relay's record is the one that counts. This module keeps, per Case and for the settings, the version read from
// the relay, the edits not yet sent (drafts), and at most one write in flight (Pending). It never decides by comparing
// revisions what the relay can answer itself (section 4.3-5), never sends a Pending that is not in the cache first
// (D-9), and never sends anything while canWrite is false (section 2.3).
// Everything it touches from outside is passed in (fetch, storage, timers, sha256, carry), so it can be tested without
// a browser page or a relay. app.js is the only caller in the Studio.
(function (root) {
  'use strict';
  const CACHE_FORMAT = 'ats-ui-cache-1';
  const SETTINGS = '_settings';
  const copy = v => v === undefined ? undefined : JSON.parse(JSON.stringify(v));
  // Archive file names: <Case>--<YYYYMMDDThhmmssmmmZ>[-<k>].json. Newest first = time, then k as a number (no k = 1). D-10 (1)
  const ARCHIVE_NAME = /^(CASE-\d{8}-\d{3}(?:-[a-z0-9]{6})?)--(\d{8}T\d{9})Z(?:-(\d+))?\.json$/;
  function archiveKey(name) { const m = ARCHIVE_NAME.exec(name || ''); return m ? [m[2], m[3] ? +m[3] : 1] : null; }
  function newerArchive(a, b) {           // > 0 when a is newer than b
    const x = archiveKey(a), y = archiveKey(b); if (!x || !y) return x ? 1 : y ? -1 : 0;
    return x[0] !== y[0] ? (x[0] > y[0] ? 1 : -1) : x[1] - y[1];
  }
  // One row per Case ID, the newest file of each, newest Cases first.
  function archiveRows(list) {
    const best = new Map();
    for (const a of list || []) { const m = ARCHIVE_NAME.exec(a && a.name || ''); if (!m) continue; const id = m[1];
      if (!best.has(id) || newerArchive(a.name, best.get(id).name) > 0) best.set(id, { ...a, id }); }
    return [...best.values()].sort((a, b) => newerArchive(b.name, a.name));
  }

  function create(opt) {
    const o = Object.assign({ key: 'ats-operational-001-3b', timeoutMs: 20000, flushDelay: 1000, relayRetryMs: 15000, cacheRetryMs: 30000,
      now: () => new Date().toISOString(), setTimer: (f, ms) => setTimeout(f, ms), clearTimer: t => clearTimeout(t),
      random: b => crypto.getRandomValues(b), computeCarry: null, onChange: () => {}, log: () => {} }, opt);
    const ui = { load: 'loading', relay: 'online', cache: 'ok', notSent: {}, checkedAt: null, why: {} };
    // id -> { kind: 'case'|'settings', value, rev, sha256, pending, dirty, flag: null|'conflict'|'corrupt'|'refused', inflight, timer,
    //         mine: own edits kept after "最新を読み直す" of a conflict, until "控えを捨てる" (section 4.4) }
    const items = {};
    let order = [], relay = '', currentId = null;
    // Which version of an item each object handed out belongs to: a re-read hands out a new object of a new generation.
    const issued = new WeakMap(); let generation = 0;
    let resumeRun = null, resumeAgain = false, relayTimer = null, cacheTimer = null;

    const canWrite = () => ui.load === 'loaded' && ui.relay === 'online' && ui.cache === 'ok';
    const changed = () => { try { o.onChange(); } catch (_) { } };
    const item = id => items[id] || null;
    function stateOf(it) {
      if (!it) return null;
      if (it.flag) return it.flag;
      if (it.pending) return it.inflight ? 'sending' : 'unanswered';
      return it.dirty ? 'dirty' : 'clean';
    }
    const base = () => relay.replace(/\/$/, '');

    // ---------- the cache (browser storage). Pending is written and read back before it is sent (D-9). ----------
    function cacheValue() {
      const pending = {}, drafts = {};
      for (const [id, it] of Object.entries(items)) {
        if (it.pending) pending[id] = it.pending;
        if (it.dirty || it.flag === 'conflict') drafts[id] = { baseRev: it.pending ? it.pending.baseRev + 1 : it.rev, value: it.value };
        else if (it.mine) drafts[id] = { kept: true, value: it.mine };
      }
      return { format: CACHE_FORMAT, relay, currentId, pending, drafts, notSent: ui.notSent };
    }
    function writeCache() {
      try {
        const text = JSON.stringify(cacheValue());
        o.storage.setItem(o.key, text);
        if (o.storage.getItem(o.key) !== text) throw new Error('read back differs');
        if (ui.cache !== 'ok') { ui.cache = 'ok'; delete ui.why.cache; }
        return true;
      } catch (e) {
        ui.cache = 'failed'; ui.why.cache = e && e.name || String(e);
        if (!cacheTimer) cacheTimer = o.setTimer(retryCache, o.cacheRetryMs);
        changed(); return false;
      }
    }
    function retryCache() { cacheTimer = null; if (writeCache()) resume(); else changed(); }
    function readCache() {
      let raw = null; try { raw = o.storage.getItem(o.key); } catch (_) { return null; }
      if (raw == null) return null;
      try { const c = JSON.parse(raw); return c && c.format === CACHE_FORMAT ? c : null; } catch (_) { return null; }
    }

    // ---------- talking to the relay ----------
    async function call(method, path, body) {
      const ac = typeof AbortController === 'function' ? new AbortController() : null;
      const t = o.setTimer(() => ac && ac.abort(), o.timeoutMs);
      try {
        const r = await o.fetch(base() + path, method === 'GET' ? { signal: ac && ac.signal } :
          { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: ac && ac.signal });
        let j = null; try { j = await r.json(); } catch (_) { }
        return { status: r.status, body: j || {} };
      } catch (e) { return { status: 0, body: { error: e && e.message || 'network' } }; }
      finally { o.clearTimer(t); }
    }
    const unreachable = r => r.status === 0 || r.status >= 500;    // 503 busy included (section 2.2)
    function goOffline(why) {
      if (ui.relay !== 'offline') { ui.relay = 'offline'; ui.why.relay = why; }
      if (!relayTimer) relayTimer = o.setTimer(probeRelay, o.relayRetryMs);
      changed();
    }
    async function probeRelay() {
      relayTimer = null;
      const r = await call('GET', '/record');
      ui.checkedAt = o.now();
      if (r.status === 200) { ui.relay = 'online'; delete ui.why.relay; resume(); }
      else goOffline(r.body.error || 'HTTP ' + r.status);
      changed();
      return r.status === 200;
    }
    // Manual "読み直す" (0.6 section 2.3): try now, do not wait for the 15 s check; also try the cache once.
    async function manualReload() {
      if (ui.cache === 'failed') retryCache();
      if (ui.relay === 'offline') return probeRelay();
      resume(); return true;
    }

    // ---------- reading (section 4.3) ----------
    function put(id, kind, value, rev, sha256) {
      const it = items[id] || (items[id] = { kind, value: null, rev: 0, sha256: null, pending: null, dirty: false, flag: null, inflight: false, timer: null });
      it.value = value; it.rev = rev; it.sha256 = sha256;
      if (value && typeof value === 'object') { it.gen = ++generation; issued.set(value, it.gen); }
      return it;
    }
    async function loadAll() {
      ui.load = 'loading'; changed();
      const list = await call('GET', '/record');
      if (unreachable(list)) { ui.load = 'loadFailed'; goOffline(list.body.error || 'HTTP ' + list.status); return; }
      if (list.status !== 200 || list.body.format !== 'ats-record-list-1') { ui.load = 'loadFailed'; ui.why.load = '記録の口のない中継です'; goOffline(ui.why.load); return; }
      const L = list.body;
      // settings
      const st = items[SETTINGS], srev = L.settings ? L.settings.rev || 0 : 0;
      if (L.settings && L.settings.problem) put(SETTINGS, 'settings', st ? st.value : null, srev, null).flag = 'corrupt';
      else if (st && (st.dirty || st.pending || st.flag === 'conflict')) {
        if (st.dirty && !st.pending && !st.flag && st.rev !== srev) st.flag = 'conflict';     // section 4.3-6
      } else if (!st || st.rev !== srev || st.value == null) {
        const s = await call('GET', '/record/settings');
        if (unreachable(s)) { ui.load = 'loadFailed'; goOffline(s.body.error || 'HTTP ' + s.status); return; }
        if (s.status === 409) put(SETTINGS, 'settings', st ? st.value : null, srev, null).flag = 'corrupt';
        else if (s.status === 200) put(SETTINGS, 'settings', s.body.settings || {}, s.body.rev || 0, s.body.sha256 || null);
      }
      order = L.cases.map(x => x.id);
      for (const row of L.cases) {
        const it = items[row.id];
        if (row.problem) { put(row.id, 'case', it ? it.value : null, row.rev || 0, null).flag = 'corrupt'; continue; }
        if (it && it.dirty && !it.pending && !it.flag && it.rev !== row.rev) { it.flag = 'conflict'; continue; }   // drafts only, written over (4.3-6)
        if (it && (it.dirty || it.pending || it.flag === 'conflict')) continue;           // own edits are never replaced (section 2.4)
        if (it && it.rev === row.rev && it.sha256 === row.sha256 && it.value) continue;  // already read, same version
        const r = await call('GET', '/record/case?' + new URLSearchParams({ id: row.id }));
        if (r.status === 409) { put(row.id, 'case', it ? it.value : null, row.rev, null).flag = 'corrupt'; continue; }
        if (r.status === 200) { put(row.id, 'case', r.body.case, r.body.rev, r.body.sha256); changed(); continue; }
        ui.load = 'loadFailed';
        if (unreachable(r)) goOffline(r.body.error || 'HTTP ' + r.status); else { ui.why.load = '一覧にある Case を読めません（' + row.id + '）'; changed(); }
        return;
      }
      // Cases only in the cache (new Cases whose first write did not answer) stay, after the listed ones.
      for (const id of Object.keys(items)) if (id !== SETTINGS && !order.includes(id)) order.push(id);
      ui.load = 'loaded'; delete ui.why.load; changed();
    }

    // ---------- writing (sections 2.1, 2.4, 4.1) ----------
    function newRequestId() {
      const b = new Uint8Array(16); o.random(b); return [...b].map(x => x.toString(16).padStart(2, '0')).join('');
    }
    function bodyFor(id, it, baseRev) {
      const v = copy(it.value);
      if (it.kind === 'case' && o.computeCarry) v.carry = Object.assign(o.computeCarry(v) || {}, { format: 'ats-carry-1', at: o.now(), rev: baseRev + 1 });
      return v;
    }
    // Make a Pending from the drafts and send it, if allowed. Returns a promise of the item's state afterwards.
    async function flush(id) {
      const it = item(id); if (!it) return null;
      if (it.timer) { o.clearTimer(it.timer); it.timer = null; }
      if (it.inflight) { await it.sendP; return stateOf(it); }      // wait for the write in flight (Q-1 after a new Case, 11/8)
      if (!canWrite() || it.flag) return stateOf(it);
      if (!it.pending) {
        if (!it.dirty) return stateOf(it);
        const p = { requestId: newRequestId(), baseRev: it.rev, body: bodyFor(id, it, it.rev), since: o.now(), tries: 0 };
        it.pending = p; it.dirty = false;              // it.value stays the page's own object (the page keeps editing it)
        if (!writeCache()) { it.pending = null; it.dirty = true; return stateOf(it); }   // D-9: not in the cache, not sent
      }
      return send(id);
    }
    // One write in flight per item; a second caller waits for the same one.
    function send(id) {
      const it = item(id);
      if (it.inflight) return it.sendP;
      if (!canWrite()) return Promise.resolve(stateOf(it));
      it.inflight = true;
      it.sendP = sendOnce(id, it).finally(() => { it.sendP = null; });
      return it.sendP;
    }
    async function sendOnce(id, it) {
      const p = it.pending; p.tries++; changed();
      const r = it.kind === 'settings'
        ? await call('POST', '/record/settings', { baseRev: p.baseRev, requestId: p.requestId, settings: p.body })
        : await call('POST', '/record/case', { id, baseRev: p.baseRev, requestId: p.requestId, case: p.body });
      it.inflight = false;
      if (unreachable(r)) { goOffline(r.body.error || 'HTTP ' + r.status); return stateOf(it); }
      if (r.status === 200) return answered(id, it, r.body);
      if (r.status === 409 && r.body.error === 'record_corrupt') { it.pending = null; it.flag = 'corrupt'; }
      else if (r.status === 409) { it.pending = null; it.dirty = true; it.flag = 'conflict'; o.log(id + ': ' + (r.body.error || 'conflict')); }
      else { it.pending = null; it.dirty = true; it.flag = 'refused'; ui.why[id] = r.body.error || 'HTTP ' + r.status; }
      writeCache(); changed(); return stateOf(it);
    }
    async function answered(id, it, j) {
      const later = j.replayed && typeof j.currentRev === 'number' && j.currentRev > j.rev;   // someone wrote after this write
      it.pending = null;
      if (!it.dirty) {
        it.rev = j.rev; it.sha256 = j.sha256;
        writeCache();
        if (later) await reread(id);
      } else if (later) {
        it.rev = j.rev; it.flag = 'conflict';          // drafts sit on an old version: never raise baseRev to currentRev
        writeCache();
      } else {
        it.rev = j.rev; it.sha256 = j.sha256;
        writeCache();
        changed(); return flush(id);                    // the drafts become the next write (new requestId)
      }
      changed(); return stateOf(it);
    }
    async function reread(id) {
      const it = item(id);
      const r = id === SETTINGS ? await call('GET', '/record/settings') : await call('GET', '/record/case?' + new URLSearchParams({ id }));
      if (r.status === 200) {
        if (it.flag === 'conflict' || it.dirty) it.mine = copy(it.value);             // keep own edits (section 4.4)
        put(id, it.kind, id === SETTINGS ? r.body.settings : r.body.case, r.body.rev, r.body.sha256); it.flag = null; it.dirty = false; writeCache();
      }
      else if (r.status === 409) it.flag = 'corrupt';
      else if (unreachable(r)) goOffline(r.body.error || 'HTTP ' + r.status);
      changed(); return r.status === 200;
    }

    // ---------- resume (section 2.3): one at a time; a call while running asks for one more run afterwards ----------
    function resume() {
      if (resumeRun) { resumeAgain = true; return resumeRun; }
      resumeRun = (async () => {
        // Start after resumeRun is assigned: with nothing to send the body would finish synchronously, clear resumeRun
        // in finally, and then the assignment would set it again for ever (found by U-25, 11/8).
        await Promise.resolve();
        try {
          do {
            resumeAgain = false;
            if (ui.relay === 'offline') break;
            if (ui.load !== 'loaded') { await loadAll(); if (ui.load !== 'loaded') break; }
            if (ui.cache === 'failed') break;
            for (const id of Object.keys(items)) {                    // Pending first (sent as they are), then drafts
              const it = items[id];
              if (it.pending && !it.inflight && !it.flag) await send(id);
              if (!canWrite()) break;
            }
            for (const id of Object.keys(items)) {
              const it = items[id];
              if (it.dirty && !it.pending && !it.flag) await flush(id);
              if (!canWrite()) break;
            }
          } while (resumeAgain);
        } finally { resumeRun = null; changed(); }
      })();
      return resumeRun;
    }

    // ---------- start: the cache first, then the relay ----------
    function start(relayUrl) {
      relay = relayUrl || ''; const c = readCache();
      if (c) {
        currentId = c.currentId || null; ui.notSent = c.notSent || {};
        for (const [id, d] of Object.entries(c.drafts || {})) {
          if (d.kept) { const it = put(id, id === SETTINGS ? 'settings' : 'case', null, 0, null); it.mine = d.value; continue; }
          const it = put(id, id === SETTINGS ? 'settings' : 'case', d.value, d.baseRev, null); it.dirty = true;
        }
        for (const [id, p] of Object.entries(c.pending || {})) {
          const it = items[id] || put(id, id === SETTINGS ? 'settings' : 'case', copy(p.body), p.baseRev, null);
          it.pending = p; it.rev = p.baseRev;
          if (!(c.drafts || {})[id]) it.value = copy(p.body);
        }
      }
      return resume();
    }

    // ---------- what the page calls ----------
    function edit(id, value, replace) {   // value: the whole Case (or settings) as the page now holds it
      if (!canWrite()) return false;
      const it = item(id); if (!it || it.flag) return false;
      // A Case object that is no longer this module's (it was re-read while the page worked on the old one, e.g. a
      // delivery or a source check in flight) is never written over the newer version: it is kept as own edits (11/8).
      if (!replace && it.kind === 'case' && value !== it.value && issued.has(value) && issued.get(value) !== it.gen) {
        it.mine = copy(value); ui.why[id] = 'この Case は作業の途中で読み直されたため、古い内容では保存しません'; writeCache(); changed(); return false;
      }
      it.value = value; it.dirty = true;
      if (value && typeof value === 'object' && !issued.has(value)) issued.set(value, it.gen);
      if (!writeCache()) return false;
      if (it.timer) o.clearTimer(it.timer);
      it.timer = o.setTimer(() => { it.timer = null; flush(id); }, o.flushDelay);
      changed(); return true;
    }
    // Q-1 (section 4.2): resolves true only when this Case's current content has a 200 from the relay.
    async function saveNow(id) {
      if (!canWrite()) return { ok: false, why: whyNot() };
      const st = items[SETTINGS];
      if (st && stateOf(st) !== 'clean') { await flush(SETTINGS); if (stateOf(items[SETTINGS]) !== 'clean') return { ok: false, why: '設定を中継に保存できていません' }; }
      const it = item(id); if (!it) return { ok: false, why: 'Case がありません' };
      for (let i = 0; i < 5 && (it.dirty || it.pending || it.inflight); i++) { await flush(id); if (it.flag || !canWrite()) break; }
      return stateOf(it) === 'clean' ? { ok: true } : { ok: false, why: stateOf(it) === 'conflict' ? '別の画面がこの Case を更新しました' : whyNot() || stateOf(it) };
    }
    function whyNot() {
      return ui.relay === 'offline' ? '中継に届きません' : ui.load !== 'loaded' ? '記録を読み込み中です' : ui.cache !== 'ok' ? '控えに書けません' : '';
    }
    function addCase(c) {            // a new Case (or a JSON import of a Case the relay does not have): baseRev 0
      if (!canWrite() || items[c.id]) return false;
      const it = put(c.id, 'case', c, 0, null); it.dirty = true;
      if (!writeCache()) { delete items[c.id]; return false; }
      order.unshift(c.id); flush(c.id); changed(); return true;
    }
    function replaceCase(c) {         // a JSON import over an existing Case: baseRev = its rev, the history keeps the old one
      const it = item(c.id); if (!canWrite() || !it || it.flag || it.pending) return false;
      return edit(c.id, c, true);
    }
    function noteNotSent(id, entry) {
      const list = ui.notSent[id] || (ui.notSent[id] = []);
      if (!list.some(x => x.task === entry.task)) list.push(entry);
      writeCache(); changed();
    }
    function clearNotSent(id, task) {
      if (!ui.notSent[id]) return; ui.notSent[id] = ui.notSent[id].filter(x => x.task !== task);
      if (!ui.notSent[id].length) delete ui.notSent[id]; writeCache(); changed();
    }
    // "保存していない編集を JSON で書き出す" (section 3, 0.6): the Case export plus notSent outside the Case.
    function unsavedExports() {
      return Object.entries(items).filter(([id, it]) => id !== SETTINGS && it.value && (it.dirty || it.pending || it.flag === 'conflict'))
        .map(([id, it]) => ({ id, text: JSON.stringify({ format: 'ats-operational-case', version: 1, exportedAt: o.now(), case: it.value, ...(ui.notSent[id] ? { notSent: ui.notSent[id] } : {}) }, null, 2) }));
    }
    function restoreNotSent(id, list) { for (const e of list || []) if (e && e.task) noteNotSent(id, e); }
    async function rereadLatest(id) { if (!canWrite()) return false; const ok = await reread(id); if (ok) changed(); return ok; }
    // "控えを捨てる": forget own kept edits (and drafts of a conflict), then read the relay's version.
    function dropDrafts(id) { const it = item(id); if (!it || it.pending) return false; it.mine = null; it.dirty = false; if (it.flag === 'conflict') it.flag = null; writeCache(); return reread(id); }
    // Section 4.7: every 60 s (the page's timer), re-read clean items whose version changed on the relay.
    async function refreshList() {
      if (ui.load !== 'loaded' || ui.relay !== 'online') return false;
      const r = await call('GET', '/record');
      if (unreachable(r)) { goOffline(r.body.error || 'HTTP ' + r.status); return false; }
      if (r.status !== 200) return false;
      const updated = [];
      for (const row of r.body.cases || []) {
        const it = item(row.id);
        if (!it) { if (!row.problem && await reread2(row.id)) updated.push(row.id); continue; }
        if (stateOf(it) === 'clean' && (it.rev !== row.rev || it.sha256 !== row.sha256)) { if (await reread(row.id)) updated.push(row.id); }
      }
      order = (r.body.cases || []).map(x => x.id).concat(order.filter(id => !(r.body.cases || []).some(x => x.id === id)));
      const st = item(SETTINGS);
      if (st && stateOf(st) === 'clean' && r.body.settings && st.rev !== r.body.settings.rev) await reread(SETTINGS);
      changed(); return updated;
    }
    async function reread2(id) { put(id, 'case', null, 0, null); return reread(id); }
    const mine = id => item(id) ? item(id).mine || null : null;
    async function archives() {
      const r = await call('GET', '/archives');
      if (r.status !== 200 || !r.body || (r.body.folder !== 'present' && r.body.folder !== 'absent')) return { ok: false, why: r.body && r.body.error || (r.status === 200 ? '書庫の中継が古い（folder がない）' : 'HTTP ' + r.status) };
      return { ok: true, folder: r.body.folder, rows: archiveRows(r.body.archives) };
    }
    async function takenIds() {       // section 7.1 / D-11: avoid both, as read just before; null when the archive cannot be read
      const a = await archives(); if (!a.ok) return null;
      return [...order, ...a.rows.map(x => x.id)];
    }

    return {
      start, resume, manualReload, edit, saveNow, addCase, replaceCase, flush, rereadLatest, dropDrafts, refreshList, mine,
      noteNotSent, clearNotSent, restoreNotSent, unsavedExports, archives, takenIds, canWrite, whyNot,
      ui, get order() { return order.slice(); }, get currentId() { return currentId; }, set currentId(v) { currentId = v; writeCache(); },
      value: id => item(id) ? item(id).value : null, state: id => stateOf(item(id)), rev: id => item(id) ? item(id).rev : null,
      ids: () => Object.keys(items).filter(id => id !== SETTINGS), SETTINGS,
      _items: items, _running: () => !!resumeRun,
    };
  }

  const api = { create, archiveRows, newerArchive, ARCHIVE_NAME, SETTINGS };
  if (typeof module === 'object' && module.exports) module.exports = api; else root.StudioRecord = api;
})(typeof window !== 'undefined' ? window : globalThis);

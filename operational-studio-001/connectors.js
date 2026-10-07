'use strict';
// Connectors: how a packet reaches an AI and how its result comes back.
// Each connector is replaceable. No API key ever lives in the browser; API calls go through the local proxy,
// which reads keys from its own environment (see README.md).
//
// Interface:
//   send(ctx)    -> Promise<{ status: 'copied'|'shown'|'queued'|'answered', text?: string, note?: string }>
//   collect(ctx) -> Promise<string|null>   (null = nothing yet; manual connectors have no collect)
// ctx = { caseId, taskId, observer, packet, proxyUrl }

const Connectors = {
  manual: {
    label: '手動（コピーして貼る）',
    async send(ctx) {
      try { await navigator.clipboard.writeText(ctx.packet); return { status: 'copied' }; }
      catch (e) {
        const ta = document.createElement('textarea'); ta.value = ctx.packet; document.body.appendChild(ta); ta.select();
        let ok = false; try { ok = document.execCommand('copy'); } catch (_) { } ta.remove();
        return ok ? { status: 'copied' } : { status: 'shown', note: 'クリップボードが使えません。表示された依頼文を手でコピーしてください。' };
      }
    },
    collect: null
  },

  proxy: {
    label: 'ローカル中継',
    async send(ctx) {
      const r = await fetch(ctx.proxyUrl.replace(/\/$/, '') + '/task', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ case: ctx.caseId, task: ctx.taskId, to: ctx.observer.name, route: ctx.observer.route || 'agent', packet: ctx.packet })
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
      return j.text ? { status: 'answered', text: j.text, note: j.note } : { status: 'queued', note: j.note };
    },
    async collect(ctx) {
      const u = ctx.proxyUrl.replace(/\/$/, '') + '/result?' + new URLSearchParams({ case: ctx.caseId, task: ctx.taskId, to: ctx.observer.name });
      const r = await fetch(u); if (r.status === 404) return null;
      const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
      return j.text || null;
    }
  }
};

async function proxyHealth(proxyUrl) {
  const r = await fetch(proxyUrl.replace(/\/$/, '') + '/health');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

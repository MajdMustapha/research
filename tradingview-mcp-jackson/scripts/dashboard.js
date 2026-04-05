#!/usr/bin/env node
// TradingView MCP — Bloomberg-Style Cockpit
// Port 3847 — Pure Node.js, zero dependencies

import http from 'http';
import { execSync, exec } from 'child_process';
import fs from 'fs';
import path from 'path';

const PORT = 3847;
const P = '/home/ubuntu/tradingview-mcp-jackson';
const HOME = process.env.HOME || '/home/ubuntu';

function x(cmd, t = 10000) { try { return execSync(cmd, { encoding: 'utf8', timeout: t, stdio: ['pipe', 'pipe', 'pipe'] }).trim(); } catch (e) { return e.stdout?.trim() || ''; } }
function rj(f) { try { return JSON.parse(fs.readFileSync(f, 'utf8')); } catch { return null; } }
function rf(f) { try { return fs.readFileSync(f, 'utf8'); } catch { return ''; } }
function tail(f, n = 15) { const c = rf(f); return c ? c.split('\n').slice(-n).join('\n') : ''; }
function e(s) { return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

function getData() {
  const health = x(`bash "${P}/scripts/tv_health.sh" 2>&1`);
  const rules = rj(`${P}/rules.json`);
  const strats = [];
  try {
    for (const f of fs.readdirSync(`${P}/strategies`).filter(f => f.endsWith('.json') && !f.includes('backtest_results'))) {
      strats.push(rj(`${P}/strategies/${f}`));
    }
  } catch {}

  const pines = [];
  try {
    for (const f of fs.readdirSync(`${P}/pine`).filter(f => f.endsWith('.pine'))) {
      const c = rf(`${P}/pine/${f}`);
      const m = c.match(/(?:indicator|strategy)\s*\(\s*["']([^"']+)/);
      pines.push({ file: f, title: m?.[1] || f, lines: c.split('\n').length, isStrategy: c.includes('strategy(') });
    }
  } catch {}

  const journal = rj(`${HOME}/.tradingview-mcp/journal/trades.json`) || { trades: [] };
  const signalLog = rj(`${HOME}/.tradingview-mcp/signals/signals.json`) || [];
  const signalStats = rj(`${HOME}/.tradingview-mcp/signals/stats.json`) || null;
  const multiState = rj(`${P}/.multi_strategy_state.json`) || {};
  const alertState = rj(`${P}/.last_alert_state.json`) || {};
  const equityScanHistory = rj(`${HOME}/.tradingview-mcp/equity-signals/scan_history.json`) || [];
  const bounceBacktest = rj(`${P}/strategies/bounce_backtest_results.json`) || null;

  const svcs = ['tv-chrome', 'tv-telegram-bot', 'tv-dashboard'].map(s => {
    const r = x(`systemctl is-active ${s} 2>/dev/null`);
    return { name: s, up: r === 'active' };
  });

  return { health, rules, strats, pines, journal, signalLog, signalStats, multiState, alertState, equityScanHistory, bounceBacktest, svcs,
    briefLog: tail('/tmp/tv-telegram-brief.log'), alertLog: tail('/tmp/tv-telegram-alert.log'),
    equitiesLog: tail('/tmp/tv-equities-scan.log'),
    scanLog: tail('/tmp/tv-multi-scan.log'), backtestLog: tail('/tmp/backtest-all.log', 25),
    healthy: health.includes('ALL SYSTEMS GO'),
    cron: x('crontab -l 2>/dev/null').split('\n').filter(l => l.includes('tradingview')),
  };
}

function html(d) {
  const now = new Date().toLocaleString('en-GB', { timeZone: 'UTC' });
  const trades = d.journal.trades || [];
  const open = trades.filter(t => t.status === 'open');
  const closed = trades.filter(t => t.status === 'closed');
  const wins = closed.filter(t => t.pnl > 0);
  const pnl = closed.reduce((s, t) => s + (t.pnl || 0), 0);
  const wr = closed.length ? (wins.length / closed.length * 100).toFixed(0) : '-';

  // Strategy signal counts
  const activeSignals = Object.entries(d.multiState).filter(([, v]) => v !== null);
  const zoneActive = Object.entries(d.alertState).filter(([, v]) => v);

  // Build strategy rows with backtest data
  const stratRows = [
    // VDP from rules.json
    (() => {
      const bt = d.rules?.backtest_results || {};
      const tests = Object.entries(bt).filter(([k]) => !k.startsWith('_'));
      return { name: 'VDP + Tone Vase', author: 'LewisWJackson', tf: 'W', type: 'Swing',
        watchlist: d.rules?.watchlist || [], tests,
        aggregate: bt._aggregate, recs: bt._recommendations };
    })(),
    ...d.strats.map(s => ({
      name: s.name, author: s.author, tf: s.timeframe === '60' ? '1H' : s.timeframe === '240' ? '4H' : s.timeframe === '15' ? '15M' : s.timeframe,
      type: s.type?.split('/')[0]?.trim() || '', watchlist: s.watchlist || [],
      tests: Object.entries(s.backtest_results || s.tv_strategy_tester || {}).filter(([k]) => !k.startsWith('_')),
      aggregate: (s.backtest_results || s.tv_strategy_tester)?._aggregate, recs: (s.backtest_results || s.tv_strategy_tester)?._recommendations,
      stats: s.reported_stats,
    })),
  ];

  return `<!DOCTYPE html><html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>Alpha Terminal — Cockpit</title>
<style>
:root{--bg:#0a0c10;--card:#0d1117;--border:#1b1f27;--text:#c9d1d9;--muted:#484f58;--green:#00e676;--red:#ff1744;--amber:#ffc107;--blue:#58a6ff;--cyan:#39d4e8}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font:13px/1.5 'SF Mono','Fira Code','Cascadia Code',monospace}
a{color:var(--blue);text-decoration:none}

/* Top bar — Bloomberg style */
.top{background:#000;border-bottom:2px solid var(--amber);padding:6px 16px;display:flex;justify-content:space-between;align-items:center}
.top-left{display:flex;align-items:center;gap:16px}
.logo{color:var(--amber);font-weight:900;font-size:16px;letter-spacing:1px}
.top-status{display:flex;gap:12px;align-items:center}
.pill{padding:2px 10px;border-radius:3px;font-size:10px;font-weight:700;letter-spacing:.5px}
.pill-ok{background:#002b00;color:var(--green);border:1px solid #004d00}
.pill-err{background:#2b0000;color:var(--red);border:1px solid #4d0000}
.pill-warn{background:#2b2300;color:var(--amber);border:1px solid #4d3d00}
.svc-dots{display:flex;gap:4px}
.svc-dot{width:8px;height:8px;border-radius:50%}
.clock{color:var(--muted);font-size:11px}
.btn{padding:4px 12px;background:#1c1c1c;border:1px solid #333;color:var(--text);font:11px/1 monospace;cursor:pointer;border-radius:2px}
.btn:hover{background:#333}.btn-go{border-color:var(--green);color:var(--green)}.btn-go:hover{background:#001a00}

/* Grid */
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--border);margin:1px}
.cell{background:var(--card);padding:10px 12px;overflow:hidden}
.span2{grid-column:span 2}.span3{grid-column:span 3}.span4{grid-column:span 4}
.cell-t{color:var(--amber);font-size:10px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;margin-bottom:8px;border-bottom:1px solid var(--border);padding-bottom:4px}

/* Ticker tape */
.tape{background:#000;border-bottom:1px solid var(--border);padding:4px 16px;display:flex;gap:20px;overflow-x:auto;white-space:nowrap}
.tape-item{font-size:11px}.tape-sym{color:var(--cyan);font-weight:700}.tape-val{color:var(--text)}.tape-sig{color:var(--amber);font-weight:700}

/* Strategy cards */
.strat{background:#080b10;border:1px solid var(--border);border-radius:4px;padding:10px;margin-bottom:8px}
.strat-head{display:flex;justify-content:space-between;align-items:center}
.strat-name{font-weight:700;color:#fff;font-size:13px}
.strat-tf{color:var(--cyan);font-size:11px;font-weight:700}
.strat-meta{color:var(--muted);font-size:10px;margin:2px 0 6px}
.strat-chips{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:6px}
.chip{background:#1a1a2e;color:var(--blue);padding:1px 8px;border-radius:2px;font-size:10px;font-weight:600}
.bt-row{display:flex;gap:8px;margin-top:6px;flex-wrap:wrap}
.bt-tag{font-size:10px;padding:2px 8px;border-radius:2px}
.bt-win{background:#001a00;color:var(--green);border:1px solid #003300}
.bt-loss{background:#1a0000;color:var(--red);border:1px solid #330000}
.bt-na{background:#1a1a00;color:var(--muted);border:1px solid #333300}

/* Signals */
.sig-row{display:flex;align-items:center;gap:8px;padding:4px 0;border-bottom:1px solid #111}
.sig-row:last-child{border:none}
.sig-sym{color:var(--cyan);font-weight:700;width:70px}
.sig-strat{color:var(--muted);font-size:10px;flex:1}
.sig-val{font-weight:700;font-size:11px}
.sig-long{color:var(--green)}.sig-short{color:var(--red)}.sig-none{color:#333}

/* Stats */
.stat-row{display:flex;gap:1px;margin-bottom:1px}
.stat{flex:1;background:#080b10;padding:10px;text-align:center}
.stat-v{font-size:20px;font-weight:900;color:#fff}
.stat-l{font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:1px;margin-top:2px}
.stat-g{color:var(--green)}.stat-r{color:var(--red)}

/* Log */
.log{background:#000;font-size:10px;line-height:1.6;padding:8px;max-height:220px;overflow-y:auto;color:var(--muted);white-space:pre-wrap;border:1px solid #111}

/* Health */
.h-ok{color:var(--green)}.h-fail{color:var(--red)}.h-warn{color:var(--amber)}

@media(max-width:900px){.grid{grid-template-columns:1fr 1fr}.span3,.span4{grid-column:1/-1}}
@media(max-width:600px){.grid{grid-template-columns:1fr}.span2,.span3,.span4{grid-column:1}}
</style></head><body>

<!-- TOP BAR -->
<div class="top">
  <div class="top-left">
    <div class="logo">ALPHA TERMINAL</div>
    <span class="pill ${d.healthy ? 'pill-ok' : 'pill-err'}">${d.healthy ? 'SYSTEMS GO' : 'DEGRADED'}</span>
    <span class="pill pill-warn">${activeSignals.length} SIGNALS</span>
    <span class="pill ${zoneActive.length > 0 ? 'pill-ok' : 'pill-warn'}">${zoneActive.length} ZONES</span>
    <div class="svc-dots">${d.svcs.map(s => `<span class="svc-dot" style="background:${s.up ? 'var(--green)' : 'var(--red)'}" title="${e(s.name)}"></span>`).join('')}</div>
  </div>
  <div class="top-status">
    <button class="btn btn-go" onclick="api('/api/scan',this)">SCAN ALL</button>
    <button class="btn btn-go" onclick="api('/api/brief',this)">BRIEF</button>
    <button class="btn" onclick="location.reload()">↻</button>
    <span class="clock">${e(now)} UTC</span>
  </div>
</div>

<!-- SIGNAL TAPE -->
<div class="tape">
${Object.entries(d.multiState).map(([key, val]) => {
  const [strat, sym] = key.split('|');
  if (!val) return '';
  return `<div class="tape-item"><span class="tape-sym">${e(sym)}</span> <span class="tape-sig">${e(val)}</span> <span class="tape-val">${e(strat)}</span></div>`;
}).filter(Boolean).join('') || '<div class="tape-item" style="color:var(--muted)">No active signals — run SCAN ALL to check</div>'}
</div>

<div class="grid">

<!-- OVERVIEW STATS -->
<div class="cell span4" style="padding:0">
  <div class="stat-row">
    <div class="stat"><div class="stat-v">${stratRows.length}</div><div class="stat-l">Strategies</div></div>
    <div class="stat"><div class="stat-v">${trades.length}</div><div class="stat-l">Trades</div></div>
    <div class="stat"><div class="stat-v ${parseInt(wr) > 50 ? 'stat-g' : parseInt(wr) < 40 ? 'stat-r' : ''}">${wr}%</div><div class="stat-l">Win Rate</div></div>
    <div class="stat"><div class="stat-v ${pnl >= 0 ? 'stat-g' : 'stat-r'}">$${pnl.toFixed(2)}</div><div class="stat-l">P&L</div></div>
    <div class="stat"><div class="stat-v">${open.length}</div><div class="stat-l">Open</div></div>
    <div class="stat"><div class="stat-v">${activeSignals.length}</div><div class="stat-l">Signals</div></div>
    <div class="stat"><div class="stat-v">${zoneActive.length}</div><div class="stat-l">Buy Zones</div></div>
    <div class="stat"><div class="stat-v">${d.pines.length}</div><div class="stat-l">Scripts</div></div>
  </div>
</div>

<!-- STRATEGIES -->
<div class="cell span2">
  <div class="cell-t">Strategies & Backtest Results</div>
  ${stratRows.map(s => `<div class="strat">
    <div class="strat-head"><span class="strat-name">${e(s.name)}</span><span class="strat-tf">${e(s.tf)}</span></div>
    <div class="strat-meta">${e(s.author)} · ${e(s.type)}</div>
    <div class="strat-chips">${s.watchlist.map(w => `<span class="chip">${e(w)}</span>`).join('')}</div>
    ${s.aggregate ? `<div style="font-size:10px;color:var(--text)">Agg: WR ${e(s.aggregate.overall_win_rate)} · ${e(s.aggregate.total_trades)} trades · Avg Ret ${e(s.aggregate.avg_return_per_period)}</div>` : ''}
    ${s.tests?.length > 0 ? `<div class="bt-row">${s.tests.map(([name, r]) => {
      if (r.error) return `<span class="bt-tag bt-na">${e(name.substring(0,20))}: ERR</span>`;
      const wr = r.win_rate || r.wr || '';
      const pf = r.profit_factor || r.pf || '';
      const tr = r.trades || r.total_trades || '';
      const pfNum = parseFloat(pf);
      return `<span class="bt-tag ${pfNum >= 1.5 ? 'bt-win' : pfNum >= 1.0 ? 'bt-win' : pfNum > 0 ? 'bt-loss' : 'bt-na'}">${e(name.substring(0,20))}: WR ${e(wr)} · PF ${e(pf)}${tr ? ' · ' + tr + 't' : ''}</span>`;
    }).join('')}</div>` : '<div style="font-size:10px;color:var(--muted)">No backtest data yet</div>'}
    ${s.recs?.length > 0 ? `<div style="font-size:10px;color:var(--amber);margin-top:4px">${s.recs.map(r => '→ ' + e(r)).join('<br>')}</div>` : ''}
  </div>`).join('')}
</div>

<!-- SIGNALS -->
<div class="cell span2">
  <div class="cell-t">Live Signal State</div>
  ${Object.entries(d.multiState).length > 0 ? Object.entries(d.multiState).map(([key, val]) => {
    const [strat, sym] = key.split('|');
    return `<div class="sig-row">
      <span class="sig-sym">${e(sym)}</span>
      <span class="sig-strat">${e(strat)}</span>
      <span class="sig-val ${val ? (val.includes('LONG') || val.includes('BULL') || val.includes('BUY') ? 'sig-long' : 'sig-short') : 'sig-none'}">${val ? e(val) : '—'}</span>
    </div>`;
  }).join('') : '<div style="color:var(--muted);font-size:11px">Run SCAN ALL to populate signals</div>'}

  <div class="cell-t" style="margin-top:16px">Buy Zone Monitor</div>
  ${Object.entries(d.alertState).map(([sym, zone]) =>
    `<div class="sig-row"><span class="sig-sym">${e(sym)}</span><span class="sig-val ${zone ? 'sig-long' : 'sig-none'}">${zone ? 'IN ZONE' : '—'}</span></div>`
  ).join('') || '<div style="color:var(--muted);font-size:11px">No data</div>'}
</div>

<!-- JOURNAL -->
<div class="cell">
  <div class="cell-t">Trade Journal</div>
  ${open.length > 0 ? open.map(t => `<div class="sig-row"><span class="sig-sym">${e(t.symbol)}</span><span style="color:var(--cyan)">${t.side} $${t.entry_price}</span><span class="sig-strat">${t.entry_date?.split('T')[0]}</span></div>`).join('') : '<div style="color:var(--muted)">No open positions</div>'}
  ${closed.slice(-5).reverse().map(t => {
    const w = t.pnl > 0;
    return `<div class="sig-row"><span class="sig-sym">${e(t.symbol)}</span><span style="color:${w ? 'var(--green)' : 'var(--red)'}">${t.pnl_pct}%</span><span class="sig-strat">$${t.entry_price}→$${t.exit_price}</span></div>`;
  }).join('')}
</div>

<!-- HEALTH -->
<div class="cell">
  <div class="cell-t">System Health</div>
  <div style="font-size:11px;line-height:1.6">
  ${(d.health || '').split('\n').map(l => {
    let c = l.includes('OK') ? 'h-ok' : l.includes('DOWN') || l.includes('FAIL') ? 'h-fail' : l.includes('WARN') ? 'h-warn' : '';
    return `<div class="${c}">${e(l)}</div>`;
  }).join('')}
  </div>
</div>

<!-- SIGNAL TRACKER -->
<div class="cell span2">
  <div class="cell-t">Signal Tracker (Forward Testing)</div>
  ${(() => {
    const sigs = d.signalLog || [];
    const stats = d.signalStats;
    const pending = sigs.filter(s => !s.outcome);
    const scored = sigs.filter(s => s.outcome && s.outcome !== 'pending');
    const wins = scored.filter(s => s.outcome === 'win');
    const losses = scored.filter(s => s.outcome === 'loss');

    let html = '<div class="stat-row" style="margin-bottom:8px">';
    html += '<div class="stat"><div class="stat-v">' + sigs.length + '</div><div class="stat-l">Total Signals</div></div>';
    html += '<div class="stat"><div class="stat-v">' + pending.length + '</div><div class="stat-l">Pending</div></div>';
    html += '<div class="stat"><div class="stat-v stat-g">' + wins.length + '</div><div class="stat-l">Wins</div></div>';
    html += '<div class="stat"><div class="stat-v stat-r">' + losses.length + '</div><div class="stat-l">Losses</div></div>';
    html += '</div>';

    if (stats?.by_strategy) {
      html += '<div style="margin-bottom:8px">';
      for (const [name, s] of Object.entries(stats.by_strategy)) {
        html += '<div class="sig-row"><span style="flex:1;font-weight:700">' + e(name) + '</span><span>' + e(s.win_rate) + ' WR</span><span class="muted" style="margin-left:8px">' + s.total + ' signals, avg ' + e(s.avg_pct) + '</span></div>';
      }
      html += '</div>';
    }

    // Recent signals
    const recent = sigs.slice(-5).reverse();
    for (const s of recent) {
      const emoji = s.outcome === 'win' ? '🟢' : s.outcome === 'loss' ? '🔴' : '⏳';
      const outText = s.outcome ? s.outcome.toUpperCase() + (s.outcome_pct ? ' (' + s.outcome_pct + '%)' : '') : 'PENDING';
      html += '<div class="sig-row"><span>' + emoji + '</span><span class="sig-sym">' + e(s.symbol) + '</span><span style="flex:1">' + e(s.strategy) + '</span><span style="color:' + (s.outcome === 'win' ? 'var(--green)' : s.outcome === 'loss' ? 'var(--red)' : 'var(--muted)') + '">' + outText + '</span></div>';
    }

    if (sigs.length === 0) html += '<div class="muted">No signals recorded yet. Signals are logged automatically when the scanner runs.</div>';

    return html;
  })()}
</div>

<!-- EQUITIES BOUNCE SCANNER -->
<div class="cell span2">
  <div class="cell-t">Equities Bounce Scanner (50 Stocks)</div>
  ${(() => {
    const scans = d.equityScanHistory || [];
    const lastScan = scans[scans.length - 1];
    const bt = d.bounceBacktest;
    let html = '';

    if (lastScan) {
      html += '<div style="margin-bottom:8px;font-size:11px;color:var(--muted)">Last scan: ' + e(lastScan.timestamp?.split('T')[0]) + ' | ' + lastScan.tickers_scanned + ' checked | ' + lastScan.signals_found + ' signals</div>';
      if (lastScan.signals?.length > 0) {
        for (const s of lastScan.signals) {
          html += '<div class="sig-row"><span class="sig-sym">' + e(s.ticker) + '</span><span class="sig-val sig-long">' + e(s.signal) + '</span><span class="muted">' + e(s.tf) + ' | $' + s.price + ' | +' + s.plus + '</span></div>';
        }
      } else {
        html += '<div class="muted">No bounce signals in last scan</div>';
      }
    } else {
      html += '<div class="muted">No scans yet. Runs daily at 9:30 PM UTC or /equities</div>';
    }

    // Top performers from backtest
    if (bt?.top_performers?.length > 0) {
      html += '<div style="margin-top:10px"><div class="cell-t" style="padding:4px 0;border:none">Backtest Top Performers</div>';
      for (const t of bt.top_performers.slice(0, 6)) {
        html += '<div class="sig-row"><span class="sig-sym">' + e(t.ticker) + '</span><span class="muted">' + e(t.tf) + '</span><span style="color:var(--green)">PF ' + t.pf + '</span><span class="muted">WR ' + t.wr + '% | ' + t.trades + ' trades</span></div>';
      }
      html += '</div>';
    }
    return html;
  })()}
</div>

<!-- SYSTEM -->
<div class="cell span2">
  <div class="cell-t">System</div>
  <div style="display:flex;gap:16px;flex-wrap:wrap">
    <div>
      <div style="font-size:10px;color:var(--muted);margin-bottom:4px">SERVICES</div>
      ${d.svcs.map(s => `<div class="sig-row"><span class="svc-dot" style="background:${s.up ? 'var(--green)' : 'var(--red)'}"></span><span>${e(s.name)}</span></div>`).join('')}
    </div>
    <div style="flex:1">
      <div style="font-size:10px;color:var(--muted);margin-bottom:4px">HEALTH</div>
      <div style="font-size:11px;line-height:1.5">${(d.health || '').split('\\n').slice(2, -2).map(l => {
        let c = l.includes('OK') ? 'h-ok' : l.includes('DOWN') || l.includes('FAIL') ? 'h-fail' : l.includes('WARN') ? 'h-warn' : '';
        return '<div class="' + c + '">' + e(l) + '</div>';
      }).join('')}</div>
    </div>
  </div>
  <div style="margin-top:8px">
    <div style="font-size:10px;color:var(--muted);margin-bottom:4px">CRON (${d.cron.length} jobs)</div>
    <div style="font-size:9px;color:var(--muted);font-family:monospace">${d.cron.map(c => e(c)).join('<br>')}</div>
  </div>
</div>

<!-- LOGS -->
<div class="cell span2">
  <div class="cell-t">Activity Log</div>
  <div class="log">${e(d.scanLog) || e(d.alertLog) || 'No activity yet'}</div>
</div>
<div class="cell span2">
  <div class="cell-t">Equities Scan Log</div>
  <div class="log">${e(d.equitiesLog) || 'No equities scans yet'}</div>
</div>

</div>

<script>
function api(u,btn){const t=btn.textContent;btn.textContent='...';btn.disabled=true;fetch(u).then(r=>r.json()).then(()=>{btn.textContent='✓';setTimeout(()=>{btn.textContent=t;btn.disabled=false},2000)}).catch(()=>{btn.textContent='ERR';setTimeout(()=>{btn.textContent=t;btn.disabled=false},2000)})}
</script>
</body></html>`;
}

http.createServer((req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  res.setHeader('Access-Control-Allow-Origin', '*');
  if (url.pathname === '/') { res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' }); res.end(html(getData())); }
  else if (url.pathname === '/api/health') { const h = x(`bash "${P}/scripts/tv_health.sh" 2>&1`); res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ status: h.includes('ALL SYSTEMS GO') ? 'healthy' : 'degraded', raw: h })); }
  else if (url.pathname === '/api/brief') { exec(`cd "${P}" && node scripts/telegram_brief.js >> /tmp/tv-telegram-brief.log 2>&1`, { timeout: 150000 }); res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ status: 'triggered' })); }
  else if (url.pathname === '/api/scan') { exec(`cd "${P}" && node scripts/multi_strategy_scan.js >> /tmp/tv-multi-scan.log 2>&1`, { timeout: 300000 }); res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ status: 'triggered' })); }
  else if (url.pathname === '/api/status') { res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(getData(), null, 2)); }
  else { res.writeHead(404); res.end('Not found'); }
}).listen(PORT, '0.0.0.0', () => console.log(`[Alpha Terminal] http://0.0.0.0:${PORT}`));

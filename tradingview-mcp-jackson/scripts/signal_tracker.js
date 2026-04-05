#!/usr/bin/env node
/**
 * Signal Tracker — logs every signal from the multi-strategy scanner,
 * then periodically checks what happened after each signal to score it.
 *
 * Two modes:
 *   node signal_tracker.js record   — run after each scan to log new signals
 *   node signal_tracker.js evaluate — check outcomes of past signals
 *
 * Data stored in ~/.tradingview-mcp/signals/
 */

import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'fs';
import { resolve, dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { homedir } from 'os';
import { evaluate } from '../src/connection.js';
import { setSymbol, setTimeframe } from '../src/core/chart.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const envPath = resolve(__dirname, '../.env');
const SIGNALS_DIR = join(homedir(), '.tradingview-mcp', 'signals');
const SIGNALS_FILE = join(SIGNALS_DIR, 'signals.json');
const STATS_FILE = join(SIGNALS_DIR, 'stats.json');

mkdirSync(SIGNALS_DIR, { recursive: true });

// Load .env
for (const line of readFileSync(envPath, 'utf8').split('\n')) {
  const match = line.match(/^([^#=]+)=(.*)$/);
  if (match) process.env[match[1].trim()] = match[2].trim();
}

const BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const CHAT_ID = process.env.TELEGRAM_CHAT_ID;

async function sendTG(text) {
  const maxLen = 4000;
  const chunks = text.length <= maxLen ? [text] : (() => {
    const c = []; let cur = '';
    for (const line of text.split('\n')) {
      if ((cur + '\n' + line).length > maxLen) { c.push(cur); cur = line; }
      else cur += (cur ? '\n' : '') + line;
    }
    if (cur) c.push(cur);
    return c;
  })();
  for (const chunk of chunks) {
    await fetch(`https://api.telegram.org/bot${BOT_TOKEN}/sendMessage`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: CHAT_ID, text: chunk, parse_mode: 'Markdown', disable_web_page_preview: true }),
    });
  }
}

function loadSignals() {
  if (existsSync(SIGNALS_FILE)) {
    try { return JSON.parse(readFileSync(SIGNALS_FILE, 'utf8')); } catch {}
  }
  return [];
}

function saveSignals(signals) {
  writeFileSync(SIGNALS_FILE, JSON.stringify(signals, null, 2));
}

async function getCurrentPrice(symbol) {
  try {
    await setSymbol({ symbol });
    await new Promise(r => setTimeout(r, 2000));
    const price = await evaluate(`
      (function() {
        var chart = window.TradingViewApi._activeChartWidgetWV.value();
        var bars = chart._chartWidget.model().mainSeries().bars();
        var v = bars.valueAt(bars.lastIndex());
        return v ? v[4] : null;
      })()
    `);
    return price;
  } catch { return null; }
}

// ═══════════════════════════════════════════════════════════════
// RECORD MODE — called after each multi-strategy scan
// ═══════════════════════════════════════════════════════════════

async function recordSignals() {
  const stateFile = resolve(__dirname, '../.multi_strategy_state.json');
  if (!existsSync(stateFile)) {
    console.log('No scan state file. Run /scan first.');
    return;
  }

  const state = JSON.parse(readFileSync(stateFile, 'utf8'));
  const signals = loadSignals();
  const now = new Date().toISOString();
  let newCount = 0;

  for (const [key, signal] of Object.entries(state)) {
    if (!signal) continue;

    const [strategy, symbol] = key.split('|');

    // Check if we already logged this signal recently (within 1 hour)
    const recent = signals.find(s =>
      s.strategy === strategy &&
      s.symbol === symbol &&
      s.signal === signal &&
      (new Date(now) - new Date(s.timestamp)) < 3600000
    );
    if (recent) continue;

    // Get current price for the signal
    const price = await getCurrentPrice(symbol);

    const entry = {
      id: `S${Date.now()}-${newCount}`,
      timestamp: now,
      strategy,
      symbol,
      signal,
      direction: signal.toLowerCase().includes('long') || signal.toLowerCase().includes('bull') || signal.toLowerCase().includes('buy') ? 'long' : 'short',
      entry_price: price,
      // Outcome tracking — filled by evaluate mode
      price_1h: null,
      price_4h: null,
      price_24h: null,
      price_1w: null,
      max_favorable: null,    // best price in trade direction
      max_adverse: null,      // worst price against trade direction
      outcome: null,          // 'win', 'loss', 'breakeven', 'pending'
      outcome_pct: null,
      evaluated_at: null,
      traded: false,          // did user actually trade this?
      trade_id: null,         // link to journal trade if traded
    };

    signals.push(entry);
    newCount++;
    console.log(`Recorded: ${strategy} | ${symbol} | ${signal} @ $${price}`);
  }

  saveSignals(signals);

  if (newCount > 0) {
    let msg = `📡 *${newCount} New Signal(s) Logged*\n\n`;
    const newSignals = signals.slice(-newCount);
    for (const s of newSignals) {
      const emoji = s.direction === 'long' ? '🟢' : '🔴';
      msg += `${emoji} *${s.symbol}* — ${s.signal}\n`;
      msg += `   Strategy: ${s.strategy}\n`;
      msg += `   Price: $${s.entry_price?.toLocaleString() || '?'}\n`;
      msg += `   ID: \`${s.id}\`\n`;
      msg += `   To trade: \`/entry ${s.symbol} ${s.entry_price || '?'} 0.1\`\n\n`;
    }
    msg += '_Outcome will be evaluated at 1H, 4H, 24H, 1W intervals._';
    await sendTG(msg);
  }

  console.log(`${newCount} new signals recorded. Total: ${signals.length}`);
}

// ═══════════════════════════════════════════════════════════════
// EVALUATE MODE — check outcomes of past signals
// ═══════════════════════════════════════════════════════════════

async function evaluateSignals() {
  const signals = loadSignals();
  const now = new Date();
  let updated = 0;
  const newOutcomes = [];

  for (const s of signals) {
    if (!s.entry_price || s.outcome === 'evaluated') continue;

    const age = now - new Date(s.timestamp);
    const ageHours = age / 3600000;

    // Get current price if we need any check
    let needsCheck = false;
    if (!s.price_1h && ageHours >= 1) needsCheck = true;
    if (!s.price_4h && ageHours >= 4) needsCheck = true;
    if (!s.price_24h && ageHours >= 24) needsCheck = true;
    if (!s.price_1w && ageHours >= 168) needsCheck = true;

    if (!needsCheck) continue;

    const price = await getCurrentPrice(s.symbol);
    if (!price) continue;

    // Update time-based price checks
    if (!s.price_1h && ageHours >= 1) {
      s.price_1h = price;
      updated++;
    }
    if (!s.price_4h && ageHours >= 4) {
      s.price_4h = price;
      updated++;
    }
    if (!s.price_24h && ageHours >= 24) {
      s.price_24h = price;
      updated++;
    }
    if (!s.price_1w && ageHours >= 168) {
      s.price_1w = price;
      updated++;
    }

    // Track max favorable/adverse excursion
    if (s.direction === 'long') {
      if (!s.max_favorable || price > s.max_favorable) s.max_favorable = price;
      if (!s.max_adverse || price < s.max_adverse) s.max_adverse = price;
    } else {
      if (!s.max_favorable || price < s.max_favorable) s.max_favorable = price;
      if (!s.max_adverse || price > s.max_adverse) s.max_adverse = price;
    }

    // Score the signal after 24H
    if (s.price_24h && !s.outcome) {
      const pctChange = s.direction === 'long'
        ? (s.price_24h - s.entry_price) / s.entry_price * 100
        : (s.entry_price - s.price_24h) / s.entry_price * 100;

      s.outcome_pct = pctChange.toFixed(2);
      s.outcome = pctChange > 0.5 ? 'win' : pctChange < -0.5 ? 'loss' : 'breakeven';
      s.evaluated_at = now.toISOString();

      newOutcomes.push(s);
    }

    // Final evaluation after 1 week
    if (s.price_1w && s.outcome !== 'evaluated') {
      s.outcome = 'evaluated';
      s.evaluated_at = now.toISOString();
    }
  }

  saveSignals(signals);

  // Send outcome notifications
  if (newOutcomes.length > 0) {
    let msg = `📊 *Signal Outcomes (24H Review)*\n\n`;
    for (const s of newOutcomes) {
      const emoji = s.outcome === 'win' ? '🟢' : s.outcome === 'loss' ? '🔴' : '⚪';
      msg += `${emoji} *${s.symbol}* — ${s.outcome.toUpperCase()} (${s.outcome_pct}%)\n`;
      msg += `   ${s.strategy} | ${s.signal}\n`;
      msg += `   Entry: $${s.entry_price.toLocaleString()} → 24H: $${s.price_24h.toLocaleString()}\n`;
      msg += `   Max favorable: $${s.max_favorable?.toLocaleString() || '?'}\n`;
      msg += `   Max adverse: $${s.max_adverse?.toLocaleString() || '?'}\n\n`;
    }
    await sendTG(msg);
  }

  // Generate stats
  await generateStats(signals);

  console.log(`Evaluated ${updated} price checks. ${newOutcomes.length} new outcomes.`);
}

// ═══════════════════════════════════════════════════════════════
// STATS — aggregate performance by strategy, symbol, session
// ═══════════════════════════════════════════════════════════════

async function generateStats(signals) {
  const scored = signals.filter(s => s.outcome && s.outcome !== 'pending');
  if (scored.length === 0) return;

  const stats = {
    generated_at: new Date().toISOString(),
    total_signals: signals.length,
    scored_signals: scored.length,
    pending: signals.filter(s => !s.outcome).length,

    by_strategy: {},
    by_symbol: {},
    by_direction: { long: { wins: 0, losses: 0, total: 0 }, short: { wins: 0, losses: 0, total: 0 } },
    by_hour: {},

    overall: {
      wins: scored.filter(s => s.outcome === 'win').length,
      losses: scored.filter(s => s.outcome === 'loss').length,
      breakeven: scored.filter(s => s.outcome === 'breakeven').length,
      avg_outcome_pct: 0,
      best_signal: null,
      worst_signal: null,
    },
  };

  let totalPct = 0;
  let bestPct = -Infinity;
  let worstPct = Infinity;

  for (const s of scored) {
    const pct = parseFloat(s.outcome_pct) || 0;
    totalPct += pct;
    if (pct > bestPct) { bestPct = pct; stats.overall.best_signal = { id: s.id, symbol: s.symbol, strategy: s.strategy, pct }; }
    if (pct < worstPct) { worstPct = pct; stats.overall.worst_signal = { id: s.id, symbol: s.symbol, strategy: s.strategy, pct }; }

    // By strategy
    if (!stats.by_strategy[s.strategy]) stats.by_strategy[s.strategy] = { wins: 0, losses: 0, breakeven: 0, total: 0, total_pct: 0 };
    const strat = stats.by_strategy[s.strategy];
    strat.total++;
    strat.total_pct += pct;
    if (s.outcome === 'win') strat.wins++;
    else if (s.outcome === 'loss') strat.losses++;
    else strat.breakeven++;

    // By symbol
    if (!stats.by_symbol[s.symbol]) stats.by_symbol[s.symbol] = { wins: 0, losses: 0, total: 0, total_pct: 0 };
    const sym = stats.by_symbol[s.symbol];
    sym.total++;
    sym.total_pct += pct;
    if (s.outcome === 'win') sym.wins++;
    else if (s.outcome === 'loss') sym.losses++;

    // By direction
    const dir = stats.by_direction[s.direction];
    dir.total++;
    if (s.outcome === 'win') dir.wins++;
    else if (s.outcome === 'loss') dir.losses++;

    // By hour
    const hour = new Date(s.timestamp).getUTCHours();
    if (!stats.by_hour[hour]) stats.by_hour[hour] = { wins: 0, losses: 0, total: 0 };
    stats.by_hour[hour].total++;
    if (s.outcome === 'win') stats.by_hour[hour].wins++;
    else if (s.outcome === 'loss') stats.by_hour[hour].losses++;
  }

  stats.overall.avg_outcome_pct = (totalPct / scored.length).toFixed(2);

  // Compute win rates
  for (const key of Object.keys(stats.by_strategy)) {
    const s = stats.by_strategy[key];
    s.win_rate = s.total > 0 ? (s.wins / s.total * 100).toFixed(0) + '%' : '-';
    s.avg_pct = s.total > 0 ? (s.total_pct / s.total).toFixed(2) + '%' : '-';
  }
  for (const key of Object.keys(stats.by_symbol)) {
    const s = stats.by_symbol[key];
    s.win_rate = s.total > 0 ? (s.wins / s.total * 100).toFixed(0) + '%' : '-';
  }

  writeFileSync(STATS_FILE, JSON.stringify(stats, null, 2));
}

// ═══════════════════════════════════════════════════════════════
// REPORT — send current stats to Telegram
// ═══════════════════════════════════════════════════════════════

async function sendReport() {
  const signals = loadSignals();
  const stats = existsSync(STATS_FILE) ? JSON.parse(readFileSync(STATS_FILE, 'utf8')) : null;

  let msg = `📡 *Signal Tracker Report*\n\n`;
  msg += `Total signals: ${signals.length}\n`;
  msg += `Pending evaluation: ${signals.filter(s => !s.outcome).length}\n`;

  if (stats && stats.scored_signals > 0) {
    msg += `Scored: ${stats.scored_signals}\n`;
    msg += `Overall: ${stats.overall.wins}W / ${stats.overall.losses}L / ${stats.overall.breakeven}BE\n`;
    msg += `Avg outcome: ${stats.overall.avg_outcome_pct}%\n\n`;

    msg += `*By Strategy:*\n`;
    for (const [name, s] of Object.entries(stats.by_strategy)) {
      msg += `  ${name}: ${s.win_rate} WR (${s.total} signals, avg ${s.avg_pct})\n`;
    }

    msg += `\n*By Symbol:*\n`;
    for (const [sym, s] of Object.entries(stats.by_symbol)) {
      msg += `  ${sym}: ${s.win_rate} WR (${s.total} signals)\n`;
    }

    msg += `\n*By Direction:*\n`;
    msg += `  Long: ${stats.by_direction.long.wins}W/${stats.by_direction.long.losses}L (${stats.by_direction.long.total})\n`;
    msg += `  Short: ${stats.by_direction.short.wins}W/${stats.by_direction.short.losses}L (${stats.by_direction.short.total})\n`;
  } else {
    msg += `\n_No scored signals yet. Outcomes are evaluated after 24H._`;
  }

  // Recent signals
  const recent = signals.slice(-5).reverse();
  if (recent.length > 0) {
    msg += `\n*Recent Signals:*\n`;
    for (const s of recent) {
      const emoji = s.outcome === 'win' ? '🟢' : s.outcome === 'loss' ? '🔴' : '⏳';
      msg += `  ${emoji} ${s.symbol} ${s.direction} — ${s.strategy}\n`;
      msg += `     $${s.entry_price?.toLocaleString() || '?'} | ${s.outcome || 'pending'} ${s.outcome_pct ? `(${s.outcome_pct}%)` : ''}\n`;
    }
  }

  await sendTG(msg);
}

// ═══════════════════════════════════════════════════════════════
// MAIN
// ═══════════════════════════════════════════════════════════════

const mode = process.argv[2] || 'record';

if (mode === 'record') {
  await recordSignals();
} else if (mode === 'evaluate') {
  await evaluateSignals();
} else if (mode === 'report') {
  await sendReport();
} else {
  console.log('Usage: node signal_tracker.js [record|evaluate|report]');
}

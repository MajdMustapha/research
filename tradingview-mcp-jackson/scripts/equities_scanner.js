#!/usr/bin/env node
/**
 * Equities Bounce Scanner — cycles through S&P 500 top 50 (by volume),
 * loads the Bounce Setup strategy on each, reads signals, sends Telegram.
 *
 * Runs daily after US market close (9:30 PM UTC).
 * Scans both 4H and Daily timeframes.
 *
 * Excludes: penny stocks, meme stocks, low volume (<200K)
 */

import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'fs';
import { resolve, dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { homedir } from 'os';
import { setSymbol, setTimeframe, getState } from '../src/core/chart.js';
import { getStudyValues } from '../src/core/data.js';
import { evaluate } from '../src/connection.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const envPath = resolve(__dirname, '../.env');
const SIGNALS_DIR = join(homedir(), '.tradingview-mcp', 'equity-signals');
const SCAN_LOG = join(SIGNALS_DIR, 'scan_history.json');

mkdirSync(SIGNALS_DIR, { recursive: true });

for (const line of readFileSync(envPath, 'utf8').split('\n')) {
  const match = line.match(/^([^#=]+)=(.*)$/);
  if (match) process.env[match[1].trim()] = match[2].trim();
}

const BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const CHAT_ID = process.env.TELEGRAM_CHAT_ID;

async function sendTG(text) {
  const maxLen = 4000;
  const chunks = [];
  if (text.length <= maxLen) chunks.push(text);
  else {
    let cur = '';
    for (const line of text.split('\n')) {
      if ((cur + '\n' + line).length > maxLen) { chunks.push(cur); cur = line; }
      else cur += (cur ? '\n' : '') + line;
    }
    if (cur) chunks.push(cur);
  }
  for (const chunk of chunks) {
    await fetch(`https://api.telegram.org/bot${BOT_TOKEN}/sendMessage`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: CHAT_ID, text: chunk, parse_mode: 'Markdown', disable_web_page_preview: true }),
    });
  }
}

// ═══════════════════════════════════════════════════════════════
// S&P 500 Top 50 by Volume — No penny stocks, no meme stocks
// ═══════════════════════════════════════════════════════════════

const TICKERS = [
  // Tech mega caps
  'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA',
  // Semis
  'AMD', 'INTC', 'AVGO', 'QCOM', 'MU',
  // Finance
  'JPM', 'BAC', 'GS', 'MS', 'V', 'MA', 'BRK.B',
  // Healthcare
  'UNH', 'JNJ', 'PFE', 'ABBV', 'MRK', 'LLY',
  // Consumer
  'HD', 'WMT', 'COST', 'PG', 'KO', 'PEP', 'MCD',
  // Energy
  'XOM', 'CVX', 'COP',
  // Industrial
  'CAT', 'BA', 'GE', 'UPS', 'HON',
  // Comms
  'DIS', 'NFLX', 'CMCSA',
  // Indices / ETFs
  'SPY', 'QQQ', 'IWM', 'DIA',
  // Other large caps
  'CRM', 'ORCL', 'ADBE', 'NOW', 'PYPL',
];

// ═══════════════════════════════════════════════════════════════
// SCAN LOGIC — read Bounce Setup indicators from chart
// ═══════════════════════════════════════════════════════════════

async function getQuote(symbol) {
  try {
    return await evaluate(`
      (function() {
        var chart = window.TradingViewApi._activeChartWidgetWV.value();
        var bars = chart._chartWidget.model().mainSeries().bars();
        var last = bars.lastIndex();
        var v = bars.valueAt(last);
        if (!v) return null;
        return { symbol: chart.symbol(), time: v[0], open: v[1], high: v[2], low: v[3], close: v[4], volume: v[5] };
      })()
    `);
  } catch { return null; }
}

async function scanTicker(symbol, timeframe) {
  try {
    await setSymbol({ symbol });
    await setTimeframe({ timeframe });
    await new Promise(r => setTimeout(r, 4000)); // wait for data + strategy computation

    const quote = await getQuote(symbol);
    if (!quote || !quote.close) return null;

    // Volume filter — skip illiquid stocks
    if (quote.volume < 200000) {
      return { symbol, timeframe, skipped: true, reason: 'Low volume: ' + quote.volume };
    }

    let indicators;
    try { indicators = await getStudyValues(); } catch { indicators = null; }

    const study = indicators?.studies?.find(s => s.name?.includes('Bounce'));
    if (!study) return { symbol, timeframe, skipped: true, reason: 'Bounce indicator not loaded' };

    const parseNum = (v) => {
      if (typeof v === 'number') return v;
      if (typeof v === 'string') return parseFloat(v.replace(/,/g, ''));
      return NaN;
    };

    const signal = parseNum(study.values?.['Signal']);
    const longScore = parseNum(study.values?.['Long Score']); // this is actually the "Signal" plot
    const plusScore = parseNum(study.values?.['Plus Score']);
    const stochK = parseNum(study.values?.['Stoch %K']);
    const stochD = parseNum(study.values?.['Stoch %D']);
    const macdHist = parseNum(study.values?.['MACD Hist']);

    return {
      symbol: quote.symbol,
      ticker: symbol,
      timeframe,
      price: quote.close,
      volume: quote.volume,
      signal: signal === 1 ? 'LONG' : signal === -1 ? 'SHORT' : 'NONE',
      plusScore: plusScore || 0,
      stochK,
      stochD,
      macdHist,
      stochOversold: stochK < 30 && stochD < 30,
      timestamp: new Date().toISOString(),
    };
  } catch (err) {
    return { symbol, timeframe, skipped: true, reason: err.message };
  }
}

// ═══════════════════════════════════════════════════════════════
// MAIN
// ═══════════════════════════════════════════════════════════════

const timeframes = [
  { tf: 'D', label: 'Daily' },
  { tf: '240', label: '4H' },
];

const now = new Date();
console.log(`[${now.toISOString()}] Equities Bounce Scanner starting...`);
console.log(`Scanning ${TICKERS.length} tickers on ${timeframes.length} timeframes`);

await sendTG(`🔍 *Equities Bounce Scanner*\n\nScanning ${TICKERS.length} stocks on Daily + 4H...\nThis takes ~${Math.ceil(TICKERS.length * timeframes.length * 5 / 60)} minutes.`);

const results = [];
const signals = [];
let scanned = 0;
let skipped = 0;

for (const { tf, label } of timeframes) {
  console.log(`\n--- ${label} scan ---`);

  for (const ticker of TICKERS) {
    const result = await scanTicker(ticker, tf);
    scanned++;

    if (!result || result.skipped) {
      skipped++;
      if (result) console.log(`  ${ticker} ${label}: SKIP (${result.reason})`);
      continue;
    }

    results.push(result);

    if (result.signal === 'LONG' || result.signal === 'SHORT') {
      signals.push(result);
      console.log(`  🟢 ${ticker} ${label}: ${result.signal} | Price: $${result.price} | Plus: ${result.plusScore}/6 | Stoch: ${result.stochK?.toFixed(0)}/${result.stochD?.toFixed(0)}`);
    } else if (result.stochOversold) {
      console.log(`  ⚡ ${ticker} ${label}: Stoch oversold (${result.stochK?.toFixed(0)}/${result.stochD?.toFixed(0)}) — watching for pattern`);
    } else {
      // Periodic progress
      if (scanned % 10 === 0) console.log(`  Scanned ${scanned}/${TICKERS.length * timeframes.length}...`);
    }
  }
}

// ═══════════════════════════════════════════════════════════════
// SAVE + REPORT
// ═══════════════════════════════════════════════════════════════

// Save scan history
const history = existsSync(SCAN_LOG) ? JSON.parse(readFileSync(SCAN_LOG, 'utf8')) : [];
history.push({
  timestamp: now.toISOString(),
  tickers_scanned: scanned,
  skipped,
  signals_found: signals.length,
  signals: signals.map(s => ({ ticker: s.ticker, tf: s.timeframe, signal: s.signal, price: s.price, plus: s.plusScore })),
});
// Keep last 30 scans
if (history.length > 30) history.splice(0, history.length - 30);
writeFileSync(SCAN_LOG, JSON.stringify(history, null, 2));

// Build Telegram report
let msg = `📊 *Equities Bounce Scan Complete*\n`;
msg += `_${now.toLocaleString('en-GB', { timeZone: 'UTC' })} UTC_\n\n`;
msg += `Scanned: ${TICKERS.length} stocks × ${timeframes.length} TFs = ${scanned} checks\n`;
msg += `Skipped: ${skipped} (low volume or error)\n\n`;

if (signals.length > 0) {
  msg += `🚨 *${signals.length} BOUNCE SIGNAL(S) FOUND:*\n\n`;

  for (const s of signals) {
    const emoji = s.signal === 'LONG' ? '🟢' : '🔴';
    const tfLabel = s.timeframe === 'D' ? 'Daily' : '4H';

    // Calculate SL and TP from the price (approximate — 1.5 ATR not available here)
    const approxSL = s.signal === 'LONG' ? s.price * 0.97 : s.price * 1.03;
    const slDist = Math.abs(s.price - approxSL);
    const approxTP = s.signal === 'LONG' ? s.price + slDist * 3 : s.price - slDist * 3;

    msg += `${emoji} *${s.ticker}* — BOUNCE ${s.signal} (${tfLabel})\n`;
    msg += `   Plus Score: ${s.plusScore}/6\n`;
    msg += `   Stoch: ${s.stochK?.toFixed(0)}/${s.stochD?.toFixed(0)}${s.stochOversold ? ' ✓ OS' : ''}\n`;
    msg += `   Price: $${s.price.toFixed(2)}\n`;
    msg += `   Est SL: $${approxSL.toFixed(2)} | TP: $${approxTP.toFixed(2)} (1:3)\n`;
    msg += `   Enter: \`/entry ${s.ticker} ${s.price.toFixed(2)} 100\`\n\n`;
  }
} else {
  msg += `_No bounce signals today. ${results.filter(r => r.stochOversold).length} stocks approaching oversold — watching._\n\n`;

  // Show watchlist — stocks that are close to triggering
  const approaching = results.filter(r => r.stochK < 40 && r.stochD < 40 && !r.skipped).sort((a, b) => a.stochK - b.stochK).slice(0, 10);
  if (approaching.length > 0) {
    msg += `*👁 Watchlist (approaching oversold):*\n`;
    for (const a of approaching) {
      const tfL = a.timeframe === 'D' ? 'D' : '4H';
      msg += `  ${a.ticker} (${tfL}) — Stoch ${a.stochK?.toFixed(0)}/${a.stochD?.toFixed(0)} | $${a.price?.toFixed(2)}\n`;
    }
  }
}

console.log(`\nDone. ${signals.length} signals found.`);
await sendTG(msg);

// Reload crypto chart as default
try {
  await setSymbol({ symbol: 'BTCUSD' });
  await setTimeframe({ timeframe: 'W' });
} catch {}

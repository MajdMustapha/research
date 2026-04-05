#!/usr/bin/env node
/**
 * Replay Backtester — Steps through historical weekly candles,
 * records VDP + Tone Vase signals, tracks hypothetical trades,
 * and sends a full report to Telegram.
 *
 * Usage:
 *   node scripts/backtest.js BTCUSD 2024-01-01 2025-01-01
 */

import { readFileSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';
import { setSymbol, setTimeframe, getState } from '../src/core/chart.js';
import { getStudyValues } from '../src/core/data.js';
import { start as replayStart, step as replayStep, stop as replayStop, status as replayStatus } from '../src/core/replay.js';
import { evaluate } from '../src/connection.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const envPath = resolve(__dirname, '../.env');

// Load .env
for (const line of readFileSync(envPath, 'utf8').split('\n')) {
  const match = line.match(/^([^#=]+)=(.*)$/);
  if (match) process.env[match[1].trim()] = match[2].trim();
}

const BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const CHAT_ID = process.env.TELEGRAM_CHAT_ID;

async function sendTelegram(text) {
  const maxLen = 4000;
  const chunks = [];
  if (text.length <= maxLen) {
    chunks.push(text);
  } else {
    let current = '';
    for (const line of text.split('\n')) {
      if ((current + '\n' + line).length > maxLen) {
        chunks.push(current);
        current = line;
      } else {
        current += (current ? '\n' : '') + line;
      }
    }
    if (current) chunks.push(current);
  }
  for (const chunk of chunks) {
    await fetch(`https://api.telegram.org/bot${BOT_TOKEN}/sendMessage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: CHAT_ID, text: chunk, parse_mode: 'Markdown', disable_web_page_preview: true }),
    });
  }
}

function parseNumber(val) {
  if (typeof val === 'number') return val;
  if (typeof val === 'string') return parseFloat(val.replace(/,/g, ''));
  return NaN;
}

function getQuote() {
  return evaluate(`
    (function() {
      var chart = window.TradingViewApi._activeChartWidgetWV.value();
      var bars = chart._chartWidget.model().mainSeries().bars();
      var last = bars.lastIndex();
      var v = bars.valueAt(last);
      if (!v) return null;
      return { time: v[0], open: v[1], high: v[2], low: v[3], close: v[4], volume: v[5] };
    })()
  `);
}

async function readIndicators() {
  try {
    const result = await getStudyValues();
    const study = result?.studies?.find(s => s.name === 'VDP + Tone Vase Strategy');
    if (!study) return null;
    return {
      ema21: parseNumber(study.values?.['EMA 21']),
      ema50: parseNumber(study.values?.['EMA 50']),
      ema200: parseNumber(study.values?.['EMA 200']),
      rsi: parseNumber(study.values?.['RSI']),
      macdHist: parseNumber(study.values?.['MACD Hist']),
      volRatio: parseNumber(study.values?.['Vol Ratio']),
    };
  } catch {
    return null;
  }
}

function analyzeBias(price, ind) {
  if (!ind || !price) return { bias: 'UNKNOWN', buyZone: false, confirmed: false };

  const nearEma21 = !isNaN(ind.ema21) && Math.abs(price - ind.ema21) / ind.ema21 < 0.03;
  const nearEma50 = !isNaN(ind.ema50) && Math.abs(price - ind.ema50) / ind.ema50 < 0.03;
  const aboveEma200 = !isNaN(ind.ema200) && price > ind.ema200;
  const belowAll = price < ind.ema21 && price < ind.ema50 && (isNaN(ind.ema200) || price < ind.ema200);

  const buyZoneBase = (nearEma21 || nearEma50) && aboveEma200;
  const volConfirm = !isNaN(ind.volRatio) && ind.volRatio > 0.5;
  const rsiOk = isNaN(ind.rsi) || ind.rsi < 55;
  const macdOk = isNaN(ind.macdHist) || ind.macdHist > -2.0;
  const confirmed = buyZoneBase && volConfirm && rsiOk && macdOk;

  let bias;
  if (confirmed) bias = 'BUY ZONE (CONFIRMED)';
  else if (buyZoneBase) bias = 'BUY ZONE (WEAK)';
  else if (belowAll) bias = 'BEARISH';
  else if (price > ind.ema21 && price > ind.ema50) bias = 'BULLISH';
  else bias = 'NEUTRAL';

  return { bias, buyZone: buyZoneBase, confirmed, volConfirm, rsiOk, macdOk };
}

// ============ MAIN ============

const symbol = process.argv[2] || 'BTCUSD';
const startDate = process.argv[3] || '2024-01-01';
const endDate = process.argv[4] || '2025-06-01';
const endTs = new Date(endDate).getTime();

console.log(`Backtesting ${symbol} Weekly from ${startDate} to ${endDate}`);
await sendTelegram(`⏳ *Backtest Starting*\n\n${symbol} Weekly\nFrom: ${startDate}\nTo: ${endDate}\n\nStepping through candles...`);

// Set chart
await setSymbol({ symbol });
await setTimeframe({ timeframe: 'W' });
await new Promise(r => setTimeout(r, 3000));

// Start replay
console.log('Starting replay...');
const startResult = await replayStart({ date: startDate });
console.log('Replay started:', startResult.current_date);
await new Promise(r => setTimeout(r, 2000));

// Track data
const candles = [];
const signals = [];
const trades = [];
let openTrade = null;
let stepCount = 0;
const maxSteps = 200; // safety limit

while (stepCount < maxSteps) {
  stepCount++;

  // Read current bar
  const quote = await getQuote();
  if (!quote) {
    console.log(`Step ${stepCount}: no quote, waiting...`);
    await new Promise(r => setTimeout(r, 1000));
    continue;
  }

  // Check if we've passed the end date
  const barTime = quote.time * 1000;
  if (barTime > endTs) {
    console.log(`Reached end date at step ${stepCount}`);
    break;
  }

  // Read indicators
  await new Promise(r => setTimeout(r, 500)); // let indicators settle
  const ind = await readIndicators();
  const price = quote.close;
  const dateStr = new Date(barTime).toISOString().split('T')[0];
  const analysis = analyzeBias(price, ind);

  candles.push({
    date: dateStr,
    open: quote.open,
    high: quote.high,
    low: quote.low,
    close: price,
    volume: quote.volume,
    ...analysis,
    rsi: ind?.rsi,
    macdHist: ind?.macdHist,
    volRatio: ind?.volRatio,
  });

  console.log(`[${stepCount}] ${dateStr} | $${price.toLocaleString()} | ${analysis.bias} | RSI: ${ind?.rsi?.toFixed(1) || '?'} | Vol: ${ind?.volRatio?.toFixed(2) || '?'}x`);

  // Signal tracking
  if (analysis.confirmed || analysis.buyZone) {
    signals.push({ date: dateStr, price, bias: analysis.bias, confirmed: analysis.confirmed });
  }

  // Hypothetical trade logic
  if (analysis.confirmed && !openTrade) {
    // Enter on confirmed buy zone
    openTrade = { entry_date: dateStr, entry_price: price, symbol };
    trades.push(openTrade);
    console.log(`  → ENTRY at $${price.toLocaleString()}`);
  } else if (openTrade) {
    // Exit conditions: price rises 15% above entry OR drops 7% below (1:2 R:R roughly)
    const gain = (price - openTrade.entry_price) / openTrade.entry_price;
    if (gain >= 0.15) {
      openTrade.exit_date = dateStr;
      openTrade.exit_price = price;
      openTrade.pnl_pct = (gain * 100).toFixed(2);
      openTrade.result = 'WIN';
      console.log(`  → EXIT (TP) at $${price.toLocaleString()} (+${openTrade.pnl_pct}%)`);
      openTrade = null;
    } else if (gain <= -0.07) {
      openTrade.exit_date = dateStr;
      openTrade.exit_price = price;
      openTrade.pnl_pct = (gain * 100).toFixed(2);
      openTrade.result = 'LOSS';
      console.log(`  → EXIT (SL) at $${price.toLocaleString()} (${openTrade.pnl_pct}%)`);
      openTrade = null;
    }
  }

  // Step forward
  try {
    await replayStep();
    await new Promise(r => setTimeout(r, 800)); // let chart update
  } catch (err) {
    console.log(`Step failed: ${err.message}`);
    break;
  }
}

// Close any open trade at last price
if (openTrade && candles.length > 0) {
  const last = candles[candles.length - 1];
  const gain = (last.close - openTrade.entry_price) / openTrade.entry_price;
  openTrade.exit_date = last.date;
  openTrade.exit_price = last.close;
  openTrade.pnl_pct = (gain * 100).toFixed(2);
  openTrade.result = gain >= 0 ? 'OPEN→WIN' : 'OPEN→LOSS';
}

// Stop replay
await replayStop();
console.log('Replay stopped.');

// ============ GENERATE REPORT ============

const closedTrades = trades.filter(t => t.exit_price);
const wins = closedTrades.filter(t => t.result === 'WIN' || t.result === 'OPEN→WIN');
const losses = closedTrades.filter(t => t.result === 'LOSS' || t.result === 'OPEN→LOSS');
const totalReturn = closedTrades.reduce((sum, t) => sum + parseFloat(t.pnl_pct), 0);
const winRate = closedTrades.length > 0 ? (wins.length / closedTrades.length * 100).toFixed(1) : 'n/a';
const avgWin = wins.length > 0 ? (wins.reduce((s, t) => s + parseFloat(t.pnl_pct), 0) / wins.length).toFixed(2) : '0';
const avgLoss = losses.length > 0 ? (losses.reduce((s, t) => s + parseFloat(t.pnl_pct), 0) / losses.length).toFixed(2) : '0';

const confirmedSignals = signals.filter(s => s.confirmed).length;
const weakSignals = signals.filter(s => !s.confirmed).length;

// Count bias distribution
const biasCounts = {};
for (const c of candles) {
  biasCounts[c.bias] = (biasCounts[c.bias] || 0) + 1;
}

let report = `📊 *BACKTEST REPORT — VDP + Tone Vase*\n\n`;
report += `*${symbol}* Weekly | ${startDate} → ${endDate}\n`;
report += `Candles scanned: ${candles.length}\n\n`;

report += `*📈 Bias Distribution:*\n`;
for (const [bias, count] of Object.entries(biasCounts).sort((a, b) => b[1] - a[1])) {
  const pct = (count / candles.length * 100).toFixed(0);
  report += `  ${bias}: ${count} (${pct}%)\n`;
}

report += `\n*🎯 Signals:*\n`;
report += `  Confirmed buy zones: ${confirmedSignals}\n`;
report += `  Weak (unconfirmed): ${weakSignals}\n`;
report += `  Total signals: ${signals.length}\n\n`;

report += `*💰 Hypothetical Trades:*\n`;
report += `  Entry: confirmed buy zone\n`;
report += `  TP: +15% | SL: -7% (~1:2 R:R)\n\n`;

if (closedTrades.length > 0) {
  report += `  Trades: ${closedTrades.length}\n`;
  report += `  Wins: ${wins.length} | Losses: ${losses.length}\n`;
  report += `  Win Rate: ${winRate}%\n`;
  report += `  Avg Win: +${avgWin}% | Avg Loss: ${avgLoss}%\n`;
  report += `  Total Return: ${totalReturn > 0 ? '+' : ''}${totalReturn.toFixed(2)}%\n\n`;

  report += `*📋 Trade Log:*\n`;
  for (const t of closedTrades) {
    const emoji = t.result.includes('WIN') ? '🟢' : '🔴';
    report += `${emoji} ${t.entry_date} → ${t.exit_date}\n`;
    report += `   $${t.entry_price.toLocaleString()} → $${t.exit_price.toLocaleString()} (${t.pnl_pct}%)\n`;
  }
} else {
  report += `  No trades triggered during this period.\n`;
}

// Enhancements suggestions
report += `\n*🔧 Observations & Suggestions:*\n`;
const suggestions = [];

if (confirmedSignals === 0 && weakSignals > 0) {
  suggestions.push('Volume filter is too strict — blocked all signals. Consider relaxing vol threshold to 0.8x SMA instead of 1.0x.');
}
if (confirmedSignals === 0 && weakSignals === 0) {
  suggestions.push('No buy zones triggered at all. Price may not have pulled back to EMAs during this period. Test a different date range.');
}
if (closedTrades.length > 0 && parseFloat(winRate) < 40) {
  suggestions.push('Win rate below 40%. Consider tightening entry criteria or widening the TP target.');
}
if (closedTrades.length > 0 && parseFloat(avgLoss) < -10) {
  suggestions.push('Average loss exceeds -10%. Consider tighter stop loss (5% instead of 7%).');
}
if (losses.length > wins.length && closedTrades.length >= 3) {
  suggestions.push('More losses than wins. Consider adding trend filter — only enter when EMA 21 > EMA 50 (golden cross on weekly).');
}
if (closedTrades.length > 0 && totalReturn > 0 && parseFloat(winRate) > 50) {
  suggestions.push('Strategy is profitable in this period. Consider adding trailing stop to capture more upside on winning trades.');
}
if (candles.length > 0) {
  const bullishPct = ((biasCounts['BULLISH'] || 0) / candles.length * 100);
  if (bullishPct > 60) {
    suggestions.push(`${bullishPct.toFixed(0)}% of candles were bullish — test in a bearish/sideways period to stress-test the strategy.`);
  }
}

// Always add these
suggestions.push('Consider adding multi-timeframe confirmation (4H entry within weekly buy zone) for higher precision.');
suggestions.push('Consider scaling into positions (1/3 at signal, 1/3 at retest, 1/3 at confirmation) instead of all-in entry.');

for (let i = 0; i < suggestions.length; i++) {
  report += `${i + 1}. ${suggestions[i]}\n`;
}

console.log('\n' + report);
await sendTelegram(report);
console.log('Report sent to Telegram.');

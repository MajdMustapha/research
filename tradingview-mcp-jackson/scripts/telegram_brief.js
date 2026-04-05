#!/usr/bin/env node
/**
 * Telegram Morning Brief & Buy Zone Notifier
 *
 * Runs the morning brief scan, applies Lewis's VDP + Tone Vase rules,
 * and sends results to Telegram. Highlights any active buy zones.
 *
 * Usage:
 *   node scripts/telegram_brief.js           # one-shot brief
 *   node scripts/telegram_brief.js --watch   # continuous monitoring (every 30 min)
 */

import { readFileSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';
import { runBrief } from '../src/core/morning.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const envPath = resolve(__dirname, '../.env');

// Load .env
for (const line of readFileSync(envPath, 'utf8').split('\n')) {
  const match = line.match(/^([^#=]+)=(.*)$/);
  if (match) process.env[match[1].trim()] = match[2].trim();
}

const BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const CHAT_ID = process.env.TELEGRAM_CHAT_ID;

if (!BOT_TOKEN || !CHAT_ID) {
  console.error('Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in .env');
  process.exit(1);
}

async function sendTelegram(text) {
  const url = `https://api.telegram.org/bot${BOT_TOKEN}/sendMessage`;
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      chat_id: CHAT_ID,
      text,
      parse_mode: 'Markdown',
      disable_web_page_preview: true,
    }),
  });
  const data = await res.json();
  if (!data.ok) console.error('Telegram error:', data.description);
  return data;
}

function parseNumber(val) {
  if (typeof val === 'number') return val;
  if (typeof val === 'string') return parseFloat(val.replace(/,/g, ''));
  return NaN;
}

function analyzeBias(symbol) {
  const price = symbol.quote?.close;
  if (!price) return { bias: 'UNKNOWN', details: 'No price data' };

  const study = symbol.indicators?.studies?.find(s => s.name === 'VDP + Tone Vase Strategy');
  if (!study) return { bias: 'UNKNOWN', details: 'Strategy indicator not loaded' };

  const ema21 = parseNumber(study.values?.['EMA 21']);
  const ema50 = parseNumber(study.values?.['EMA 50']);
  const ema200 = parseNumber(study.values?.['EMA 200']);

  // Read plotted values exposed by the Pine script for full-stack detection
  const rsi = parseNumber(study.values?.['RSI']);
  const macdHist = parseNumber(study.values?.['MACD Hist']);
  const volRatio = parseNumber(study.values?.['Vol Ratio']);

  const belowAll = price < ema21 && price < ema50 && (isNaN(ema200) || price < ema200);
  const nearEma21 = !isNaN(ema21) && Math.abs(price - ema21) / ema21 < 0.03;
  const nearEma50 = !isNaN(ema50) && Math.abs(price - ema50) / ema50 < 0.03;
  const aboveEma200 = !isNaN(ema200) && price > ema200;

  // Volume confirmation: current volume above 20-period SMA (ratio > 1.0)
  const volConfirm = !isNaN(volRatio) && volRatio > 0.5;
  // RSI not overbought — room to run on a pullback entry
  const rsiOk = isNaN(rsi) || rsi < 55;
  // MACD histogram not deeply negative — momentum not collapsing
  const macdOk = isNaN(macdHist) || macdHist > -2.0;

  const nearBuyZoneBase = (nearEma21 || nearEma50) && aboveEma200;
  const nearBuyZoneConfirmed = nearBuyZoneBase && volConfirm && rsiOk && macdOk;

  let bias, details;
  if (nearBuyZoneConfirmed) {
    bias = 'BUY ZONE';
    const level = nearEma21 ? `EMA 21 ($${ema21.toLocaleString()})` : `EMA 50 ($${ema50.toLocaleString()})`;
    details = `Near ${level}, above 200 EMA, vol confirmed`;
  } else if (nearBuyZoneBase) {
    bias = 'WEAK BUY ZONE';
    const missing = [];
    if (!volConfirm) missing.push('vol');
    if (!rsiOk) missing.push('RSI>45');
    if (!macdOk) missing.push('MACD weak');
    const level = nearEma21 ? `EMA 21` : `EMA 50`;
    details = `Near ${level} but missing: ${missing.join(', ')}`;
  } else if (belowAll) {
    bias = 'BEARISH';
    details = `Below all EMAs`;
  } else if (!isNaN(ema200) && price > ema200 && price < ema21) {
    bias = 'NEUTRAL';
    details = `Above 200 EMA but below 21/50`;
  } else if (price > ema21 && price > ema50) {
    bias = 'BULLISH';
    details = `Above 21 & 50 EMAs`;
  } else {
    bias = 'NEUTRAL';
    details = `Mixed EMA positioning`;
  }

  return {
    bias,
    details,
    ema21: isNaN(ema21) ? 'n/a' : ema21.toLocaleString(),
    ema50: isNaN(ema50) ? 'n/a' : ema50.toLocaleString(),
    ema200: isNaN(ema200) ? 'n/a' : ema200.toLocaleString(),
    rsi: isNaN(rsi) ? 'n/a' : rsi.toFixed(1),
    macdHist: isNaN(macdHist) ? 'n/a' : macdHist.toFixed(4),
    volRatio: isNaN(volRatio) ? 'n/a' : volRatio.toFixed(2),
    volConfirm,
  };
}

function formatScanResults(symbols, timeframeLabel) {
  const lines = [];
  const buyZones = [];

  for (const sym of symbols) {
    const ticker = sym.symbol;
    const price = sym.quote?.close;
    const analysis = analyzeBias(sym);

    const emoji = analysis.bias === 'BUY ZONE' ? '🟢' :
                  analysis.bias === 'WEAK BUY ZONE' ? '🟡' :
                  analysis.bias === 'BULLISH' ? '🟢' :
                  analysis.bias === 'BEARISH' ? '🔴' : '⚪';

    lines.push(
      `${emoji} *${ticker}* — $${price?.toLocaleString() ?? '?'}`,
      `   ${analysis.bias} | ${analysis.details}`,
      `   EMAs: 21=$${analysis.ema21} | 50=$${analysis.ema50} | 200=$${analysis.ema200}`,
      `   RSI: ${analysis.rsi} | MACD: ${analysis.macdHist} | Vol: ${analysis.volRatio}x ${analysis.volConfirm ? '✓' : '✗'}`,
      ''
    );

    if (analysis.bias === 'BUY ZONE') {
      buyZones.push({ ticker, price, details: analysis.details });
    }
  }

  return { lines, buyZones };
}

async function runAndNotify() {
  console.log(`[${new Date().toISOString()}] Running brief...`);

  // --- Weekly scan ---
  let weeklyBrief;
  try {
    weeklyBrief = await runBrief({ timeframe_override: 'W' });
  } catch (err) {
    const errMsg = `*TradingView MCP Error*\n\n\`${err.message}\``;
    await sendTelegram(errMsg);
    console.error('Weekly brief failed:', err.message);
    return;
  }

  const weekly = formatScanResults(weeklyBrief.symbols_scanned, 'Weekly');

  // --- 4H scan for tighter entries ---
  let fourHBrief;
  try {
    fourHBrief = await runBrief({ timeframe_override: '240' });
  } catch (err) {
    console.error('4H brief failed, sending weekly only:', err.message);
    fourHBrief = null;
  }

  const fourH = fourHBrief ? formatScanResults(fourHBrief.symbols_scanned, '4H') : null;

  // --- Build message ---
  const now = new Date().toLocaleString('en-GB', { timeZone: 'UTC', dateStyle: 'medium', timeStyle: 'short' });
  let message = `*📊 VDP + Tone Vase Brief*\n_${now} UTC_\n\n`;

  // Weekly section
  message += `*--- WEEKLY ---*\n\n`;
  message += weekly.lines.join('\n');

  if (weekly.buyZones.length > 0) {
    message += '\n🚨 *WEEKLY BUY ZONES:*\n';
    for (const bz of weekly.buyZones) {
      message += `   → *${bz.ticker}* at $${bz.price?.toLocaleString()} — ${bz.details}\n`;
    }
  }

  // 4H section
  if (fourH) {
    message += `\n*--- 4H (Entry Timing) ---*\n\n`;
    message += fourH.lines.join('\n');

    if (fourH.buyZones.length > 0) {
      message += '\n🎯 *4H BUY ZONES (tight entries):*\n';
      for (const bz of fourH.buyZones) {
        message += `   → *${bz.ticker}* at $${bz.price?.toLocaleString()} — ${bz.details}\n`;
      }
    }

    // Cross-timeframe confluence
    const weeklyTickers = new Set(weekly.buyZones.map(b => b.ticker));
    const confluence = fourH.buyZones.filter(b => weeklyTickers.has(b.ticker));
    if (confluence.length > 0) {
      message += '\n🔥 *CONFLUENCE (Weekly + 4H):*\n';
      for (const bz of confluence) {
        message += `   → *${bz.ticker}* — Buy zone on BOTH timeframes\n`;
      }
    }
  }

  if (weekly.buyZones.length === 0 && (!fourH || fourH.buyZones.length === 0)) {
    message += '\n_No active buy zones. Wait for pullbacks to EMAs._';
  }

  await sendTelegram(message);
  console.log(`Brief sent. Weekly: ${weekly.buyZones.length}, 4H: ${fourH?.buyZones.length ?? 0} buy zones.`);
}

// Main
const watchMode = process.argv.includes('--watch');
const intervalMin = parseInt(process.argv[process.argv.indexOf('--interval') + 1]) || 30;

await runAndNotify();

if (watchMode) {
  console.log(`Watch mode: re-scanning every ${intervalMin} minutes`);
  setInterval(runAndNotify, intervalMin * 60 * 1000);
}

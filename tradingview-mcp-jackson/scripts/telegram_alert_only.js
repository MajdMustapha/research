#!/usr/bin/env node
/**
 * Silent buy zone monitor — only sends Telegram when a buy zone triggers.
 * Captures chart screenshot and sends it with the alert.
 * Designed to run frequently (hourly) without spamming.
 */

import { readFileSync, writeFileSync, existsSync, createReadStream } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';
import { runBrief } from '../src/core/morning.js';
import { setSymbol, setTimeframe } from '../src/core/chart.js';
import { captureScreenshot } from '../src/core/capture.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const envPath = resolve(__dirname, '../.env');
const statePath = resolve(__dirname, '../.last_alert_state.json');

// Load .env
for (const line of readFileSync(envPath, 'utf8').split('\n')) {
  const match = line.match(/^([^#=]+)=(.*)$/);
  if (match) process.env[match[1].trim()] = match[2].trim();
}

const BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const CHAT_ID = process.env.TELEGRAM_CHAT_ID;

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
  return res.json();
}

async function sendPhoto(photoPath, caption) {
  const form = new FormData();
  form.append('chat_id', CHAT_ID);
  form.append('caption', caption);
  form.append('parse_mode', 'Markdown');

  // Read file as blob
  const fileData = readFileSync(photoPath);
  const blob = new Blob([fileData], { type: 'image/png' });
  form.append('photo', blob, 'chart.png');

  const res = await fetch(`https://api.telegram.org/bot${BOT_TOKEN}/sendPhoto`, {
    method: 'POST',
    body: form,
  });
  return res.json();
}

function parseNumber(val) {
  if (typeof val === 'number') return val;
  if (typeof val === 'string') return parseFloat(val.replace(/,/g, ''));
  return NaN;
}

function detectBuyZone(sym) {
  const price = sym.quote?.close;
  if (!price) return { inZone: false, confirmed: false };

  const study = sym.indicators?.studies?.find(s => s.name === 'VDP + Tone Vase Strategy');
  if (!study) return { inZone: false, confirmed: false };

  const ema21 = parseNumber(study.values?.['EMA 21']);
  const ema50 = parseNumber(study.values?.['EMA 50']);
  const ema200 = parseNumber(study.values?.['EMA 200']);

  // Read plotted values exposed by the Pine script
  const rsi = parseNumber(study.values?.['RSI']);
  const macdHist = parseNumber(study.values?.['MACD Hist']);
  const volRatio = parseNumber(study.values?.['Vol Ratio']);

  const nearEma21 = !isNaN(ema21) && Math.abs(price - ema21) / ema21 < 0.03;
  const nearEma50 = !isNaN(ema50) && Math.abs(price - ema50) / ema50 < 0.03;
  const aboveEma200 = !isNaN(ema200) && price > ema200;

  const baseZone = (nearEma21 || nearEma50) && aboveEma200;

  // Volume confirmation: ratio > 1.0 means above 20-period SMA
  const volConfirm = !isNaN(volRatio) && volRatio > 0.5;
  const rsiOk = isNaN(rsi) || rsi < 55;
  const macdOk = isNaN(macdHist) || macdHist > -2.0;

  const confirmed = baseZone && volConfirm && rsiOk && macdOk;

  return {
    inZone: baseZone,
    confirmed,
    volRatio: isNaN(volRatio) ? 'n/a' : volRatio.toFixed(2),
    rsi: isNaN(rsi) ? 'n/a' : rsi.toFixed(1),
    macdHist: isNaN(macdHist) ? 'n/a' : macdHist.toFixed(4),
    volConfirm,
  };
}

async function captureChart(symbol) {
  try {
    await setSymbol({ symbol });
    await setTimeframe({ timeframe: 'W' });
    await new Promise(r => setTimeout(r, 3000)); // Wait for chart to load
    const result = await captureScreenshot({ region: 'chart', filename: `buyzone_${symbol}` });
    return result;
  } catch (err) {
    console.error(`Screenshot failed for ${symbol}:`, err.message);
    return null;
  }
}

// Load previous state to avoid duplicate alerts
let lastState = {};
if (existsSync(statePath)) {
  try { lastState = JSON.parse(readFileSync(statePath, 'utf8')); } catch {}
}

console.log(`[${new Date().toISOString()}] Silent scan...`);

let brief;
try {
  brief = await runBrief();
} catch (err) {
  console.error('Brief failed:', err.message);
  process.exit(1);
}

const newBuyZones = [];
const currentState = {};

for (const sym of brief.symbols_scanned) {
  const ticker = sym.symbol;
  const detection = detectBuyZone(sym);
  // Only alert on confirmed buy zones (volume + RSI + MACD all pass)
  const inZone = detection.confirmed;
  currentState[ticker] = inZone;

  if (inZone && !lastState[ticker]) {
    const price = sym.quote.close;
    const study = sym.indicators?.studies?.find(s => s.name === 'VDP + Tone Vase Strategy');
    const ema21 = study?.values?.['EMA 21'] || 'n/a';
    const ema50 = study?.values?.['EMA 50'] || 'n/a';
    newBuyZones.push({
      ticker, price, ema21, ema50,
      rsi: detection.rsi,
      macdHist: detection.macdHist,
      volRatio: detection.volRatio,
      volConfirm: detection.volConfirm,
    });
  }
}

// Save state
writeFileSync(statePath, JSON.stringify(currentState, null, 2));

if (newBuyZones.length > 0) {
  let msg = '🚨 *BUY ZONE ALERT — VDP + Tone Vase*\n\n';
  for (const bz of newBuyZones) {
    msg += `🟢 *${bz.ticker}* entered buy zone at $${bz.price.toLocaleString()}\n`;
    msg += `   EMA 21: $${bz.ema21} | EMA 50: $${bz.ema50}\n`;
    msg += `   RSI: ${bz.rsi} | MACD: ${bz.macdHist} | Vol: ${bz.volRatio}x ${bz.volConfirm ? '✓' : '✗'}\n\n`;
  }
  msg += '_Volume confirmed. Price pulling back to EMA support with momentum intact. Review chart and rules before entry._';

  // Send text alert first
  await sendTelegram(msg);

  // Capture and send screenshots for each buy zone asset
  for (const bz of newBuyZones) {
    const screenshot = await captureChart(bz.ticker);
    if (screenshot?.success && screenshot?.file) {
      try {
        await sendPhoto(screenshot.file, `📸 *${bz.ticker}* Weekly — Buy Zone at $${bz.price.toLocaleString()}`);
        console.log(`Screenshot sent for ${bz.ticker}`);
      } catch (err) {
        console.error(`Failed to send photo for ${bz.ticker}:`, err.message);
      }
    }
  }

  console.log(`ALERT SENT: ${newBuyZones.map(b => b.ticker).join(', ')}`);
} else {
  console.log('No new buy zones. Silent.');
}

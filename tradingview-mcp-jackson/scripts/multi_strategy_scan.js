#!/usr/bin/env node
/**
 * Multi-Strategy Scanner — scans watchlist across all strategies
 * and sends Telegram alerts when any strategy triggers.
 *
 * Strategies:
 *   1. VDP + Tone Vase (Weekly swing) — existing
 *   2. RSI + VWAP Mean Reversion (1H counter-trend)
 *   3. CVD Divergence (15M order flow)
 *   4. BB + RSI Mean Reversion (1H/4H ranging)
 *
 * Usage:
 *   node scripts/multi_strategy_scan.js              # scan all
 *   node scripts/multi_strategy_scan.js --strategy 2 # scan specific
 */

import { readFileSync, writeFileSync, existsSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';
import { setSymbol, setTimeframe, getState } from '../src/core/chart.js';
import { getStudyValues } from '../src/core/data.js';
import { evaluate } from '../src/connection.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const envPath = resolve(__dirname, '../.env');
const statePath = resolve(__dirname, '../.multi_strategy_state.json');

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
  if (text.length <= maxLen) chunks.push(text);
  else {
    let current = '';
    for (const line of text.split('\n')) {
      if ((current + '\n' + line).length > maxLen) { chunks.push(current); current = line; }
      else current += (current ? '\n' : '') + line;
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

async function getQuoteData() {
  return evaluate(`
    (function() {
      try {
        var chart = window.TradingViewApi._activeChartWidgetWV.value();
        return {
          symbol: chart.symbol(),
          resolution: chart.resolution(),
          price: chart._chartWidget.model().mainSeries().bars().valueAt(
            chart._chartWidget.model().mainSeries().bars().lastIndex()
          )
        };
      } catch(e) { return { error: e.message }; }
    })()
  `);
}

async function scanSymbol(symbol, timeframe) {
  await setSymbol({ symbol });
  await setTimeframe({ timeframe });
  await new Promise(r => setTimeout(r, 3000)); // wait for data to load

  const quote = await getQuoteData();
  let indicators;
  try {
    indicators = await getStudyValues();
  } catch { indicators = null; }

  return { symbol, timeframe, quote, indicators };
}

// ============ STRATEGY ANALYZERS ============

function analyzeVDP(data) {
  // Strategy 1: VDP + Tone Vase (Weekly)
  const study = data.indicators?.studies?.find(s => s.name === 'VDP + Tone Vase Strategy');
  if (!study) return null;

  const price = data.quote?.price?.[4]; // close
  if (!price) return null;

  const ema21 = parseNumber(study.values?.['EMA 21']);
  const ema50 = parseNumber(study.values?.['EMA 50']);
  const ema200 = parseNumber(study.values?.['EMA 200']);
  const rsi = parseNumber(study.values?.['RSI']);
  const macdHist = parseNumber(study.values?.['MACD Hist']);
  const volRatio = parseNumber(study.values?.['Vol Ratio']);

  const nearEma = (!isNaN(ema21) && Math.abs(price - ema21) / ema21 < 0.03) ||
                  (!isNaN(ema50) && Math.abs(price - ema50) / ema50 < 0.03);
  const aboveEma200 = !isNaN(ema200) && price > ema200;
  const volOk = !isNaN(volRatio) && volRatio > 0.5;
  const rsiOk = isNaN(rsi) || rsi < 55;
  const macdOk = isNaN(macdHist) || macdHist > -2.0;

  const buyZone = nearEma && aboveEma200;
  const confirmed = buyZone && volOk && rsiOk && macdOk;

  if (confirmed) return { signal: 'BUY ZONE (CONFIRMED)', direction: 'long', details: `RSI: ${rsi?.toFixed(1)} | Vol: ${volRatio?.toFixed(2)}x | MACD: ${macdHist?.toFixed(4)}` };
  if (buyZone) return { signal: 'BUY ZONE (WEAK)', direction: 'long', details: `Missing: ${!volOk ? 'vol ' : ''}${!rsiOk ? 'RSI ' : ''}${!macdOk ? 'MACD' : ''}` };
  return null;
}

function analyzeRSIVWAP(data) {
  // Strategy 2: RSI + VWAP Mean Reversion (1H)
  const study = data.indicators?.studies?.find(s => s.name === 'RSI + VWAP Mean Reversion');
  if (!study) return null;

  const rsi = parseNumber(study.values?.['RSI']);
  const vwap = parseNumber(study.values?.['VWAP Price']);
  const price = data.quote?.price?.[4];
  if (!price || isNaN(rsi)) return null;

  if (rsi < 20 && price < vwap) {
    return { signal: 'LONG SETUP', direction: 'long', details: `RSI: ${rsi.toFixed(1)} | Price below VWAP ($${vwap.toFixed(2)})` };
  }
  if (rsi > 80 && price > vwap) {
    return { signal: 'SHORT SETUP', direction: 'short', details: `RSI: ${rsi.toFixed(1)} | Price above VWAP ($${vwap.toFixed(2)})` };
  }
  return null;
}

function analyzeBBRSI(data) {
  // Strategy 3: BB + RSI Mean Reversion (1H/4H)
  const study = data.indicators?.studies?.find(s => s.name === 'BB + RSI Mean Reversion');
  if (!study) return null;

  const rsi = parseNumber(study.values?.['RSI']);
  const adx = parseNumber(study.values?.['ADX']);
  const bbUpper = parseNumber(study.values?.['BB Upper Val']);
  const bbLower = parseNumber(study.values?.['BB Lower Val']);
  const price = data.quote?.price?.[4];
  if (!price || isNaN(rsi)) return null;

  const isRanging = isNaN(adx) || adx < 20;
  if (!isRanging) return null; // regime filter

  if (price <= bbLower && rsi <= 30) {
    return { signal: 'LONG SETUP', direction: 'long', details: `RSI: ${rsi.toFixed(1)} | ADX: ${adx?.toFixed(1) || '?'} | Below BB Lower` };
  }
  if (price >= bbUpper && rsi >= 70) {
    return { signal: 'SHORT SETUP', direction: 'short', details: `RSI: ${rsi.toFixed(1)} | ADX: ${adx?.toFixed(1) || '?'} | Above BB Upper` };
  }
  return null;
}

function analyzeFVG(data) {
  // Strategy 5: FVG Scalper (15M)
  const study = data.indicators?.studies?.find(s => s.name?.includes('FVG') || s.name?.includes('Ramzi'));
  if (!study) return null;

  const fvgSignal = parseNumber(study.values?.['FVG Signal']);
  const htfBias = parseNumber(study.values?.['HTF Bias']);
  const activeFVGs = parseNumber(study.values?.['Active FVGs']);

  if (fvgSignal === 1 && htfBias === 1) {
    return { signal: 'FVG LONG (Bullish Mitigation)', direction: 'long', details: `HTF: Bullish | Active FVGs: ${activeFVGs || '?'}` };
  }
  if (fvgSignal === -1 && htfBias === -1) {
    return { signal: 'FVG SHORT (Bearish Mitigation)', direction: 'short', details: `HTF: Bearish | Active FVGs: ${activeFVGs || '?'}` };
  }
  return null;
}

// ============ MAIN ============

const strategies = [
  { name: 'VDP + Tone Vase', timeframe: 'W', analyzer: analyzeVDP, watchlist: ['BTCUSD', 'ETHUSD', 'SOLUSD', 'XRPUSD', 'LINKUSD', 'PEPEUSDT'] },
  { name: 'BB + RSI Squeeze', timeframe: '60', analyzer: analyzeBBRSI, watchlist: ['BTCUSD', 'ETHUSD'] },
  { name: 'RSI Div + VWAP', timeframe: '60', analyzer: analyzeRSIVWAP, watchlist: ['BTCUSD', 'ETHUSD'] },
  { name: 'FVG Scalper', timeframe: '15', analyzer: analyzeFVG, watchlist: ['BTCUSD', 'EURUSD', 'XAUUSD'] },
];

// Load previous state
let lastState = {};
if (existsSync(statePath)) {
  try { lastState = JSON.parse(readFileSync(statePath, 'utf8')); } catch {}
}

console.log(`[${new Date().toISOString()}] Multi-strategy scan...`);

const alerts = [];
const currentState = {};

for (const strat of strategies) {
  for (const symbol of strat.watchlist) {
    const key = `${strat.name}|${symbol}`;
    try {
      const data = await scanSymbol(symbol, strat.timeframe);
      const result = strat.analyzer(data);
      currentState[key] = result ? result.signal : null;

      if (result && !lastState[key]) {
        alerts.push({
          strategy: strat.name,
          symbol,
          timeframe: strat.timeframe,
          ...result,
        });
      }
    } catch (err) {
      console.error(`Error scanning ${symbol} for ${strat.name}:`, err.message);
    }
  }
}

writeFileSync(statePath, JSON.stringify(currentState, null, 2));

if (alerts.length > 0) {
  let msg = '🚨 *MULTI-STRATEGY ALERT*\n\n';
  for (const a of alerts) {
    const emoji = a.direction === 'long' ? '🟢' : '🔴';
    msg += `${emoji} *${a.symbol}* — ${a.signal}\n`;
    msg += `   Strategy: ${a.strategy} (${a.timeframe})\n`;
    msg += `   ${a.details}\n\n`;
  }
  msg += `_${alerts.length} new signal(s) across ${strategies.length} strategies_`;

  await sendTelegram(msg);
  console.log(`ALERTS SENT: ${alerts.map(a => `${a.symbol}/${a.strategy}`).join(', ')}`);
} else {
  console.log('No new signals. Silent.');
}

#!/usr/bin/env node
/**
 * Equities Bounce Backtest — loads the Bounce Setup strategy on each ticker,
 * reads TradingView's built-in Strategy Tester results, compiles report.
 */

import { readFileSync, writeFileSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';
import { setSymbol, setTimeframe, manageIndicator, getState } from '../src/core/chart.js';
import { ensurePineEditorOpen, setSource, smartCompile } from '../src/core/pine.js';
import { evaluate } from '../src/connection.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const envPath = resolve(__dirname, '../.env');

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

async function clearStudies() {
  const s = await getState();
  for (const study of (s.studies || [])) {
    await manageIndicator({ action: 'remove', entity_id: study.id });
    await new Promise(r => setTimeout(r, 400));
  }
}

async function loadBounce() {
  const pine = readFileSync(resolve(__dirname, '../pine/bounce_setup.pine'), 'utf8');
  await ensurePineEditorOpen();
  await new Promise(r => setTimeout(r, 1500));
  await setSource({ source: pine });
  const result = await smartCompile();
  await new Promise(r => setTimeout(r, 2000));
  await evaluate(`(function(){var b=document.querySelectorAll('button');for(var i=0;i<b.length;i++){var t=b[i].textContent;if(t.indexOf('Add to chart')!==-1||t.indexOf('Update on chart')!==-1){b[i].click();return}}})()` );
  await new Promise(r => setTimeout(r, 3000));
  return result;
}

async function readStrategyTester() {
  await new Promise(r => setTimeout(r, 3000));
  return evaluate(`
    (function() {
      var bottom = document.querySelector('[class*="layout__area--bottom"]');
      if (!bottom) return {};
      var text = bottom.innerText.substring(0, 3000);
      var metrics = {};
      var patterns = {
        'Total Trades': /(?:Total (?:Closed )?Trades)[\\s:]*([\\d,]+)/i,
        'Win Rate': /(?:Percent Profitable|Win Rate)[\\s:]*([\\d,.]+%?)/i,
        'Profit Factor': /Profit Factor[\\s:]*([\\d,.]+)/i,
        'Net Profit': /Net Profit[\\s:]*([\\d,.\\-$%]+)/i,
        'Max Drawdown': /Max(?:imum)? Drawdown[\\s:]*([\\d,.\\-$%]+)/i,
      };
      for (var key in patterns) {
        var m = text.match(patterns[key]);
        if (m) metrics[key] = m[1].trim();
      }
      return metrics;
    })()
  `);
}

// ═══════════════════════════════════════════════════════════════

const TICKERS = [
  'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA',
  'AMD', 'INTC', 'AVGO', 'QCOM', 'MU',
  'JPM', 'BAC', 'GS', 'MS', 'V', 'MA',
  'UNH', 'JNJ', 'PFE', 'ABBV', 'MRK', 'LLY',
  'HD', 'WMT', 'COST', 'PG', 'KO', 'PEP', 'MCD',
  'XOM', 'CVX', 'COP',
  'CAT', 'BA', 'GE', 'UPS', 'HON',
  'DIS', 'NFLX', 'CMCSA',
  'SPY', 'QQQ', 'IWM', 'DIA',
  'CRM', 'ORCL', 'ADBE', 'NOW', 'PYPL',
];

const TIMEFRAMES = [
  { tf: 'D', label: 'Daily' },
  { tf: '240', label: '4H' },
];

console.log(`Equities Bounce Backtest — ${TICKERS.length} stocks × ${TIMEFRAMES.length} TFs`);
await sendTG(`⏳ *Equities Bounce Backtest Starting*\n\n${TICKERS.length} stocks × ${TIMEFRAMES.length} timeframes\nUsing TV Strategy Tester (full history)\nETA: ~20 min`);

// Load Bounce Setup once
await clearStudies();
await new Promise(r => setTimeout(r, 1500));
const loadResult = await loadBounce();
const realErrors = (loadResult.errors || []).filter(e => e.severity >= 8);
if (realErrors.length > 0) {
  const errMsg = realErrors.map(e => `L${e.line}: ${e.message}`).join('; ');
  await sendTG(`❌ Bounce Setup compile error:\n\`${errMsg}\``);
  console.error('Compile error:', errMsg);
  process.exit(1);
}
console.log('Bounce Setup loaded.');

const results = [];

for (const { tf, label } of TIMEFRAMES) {
  console.log(`\n=== ${label} ===`);

  for (const ticker of TICKERS) {
    try {
      await setSymbol({ symbol: ticker });
      await setTimeframe({ timeframe: tf });
      await new Promise(r => setTimeout(r, 5000)); // wait for data + strategy computation

      const metrics = await readStrategyTester();
      const trades = parseInt((metrics['Total Trades'] || '0').replace(/,/g, ''));
      const wr = parseFloat(metrics['Win Rate'] || '0');
      const pf = parseFloat(metrics['Profit Factor'] || '0');

      results.push({ ticker, tf: label, trades, wr, pf, metrics });

      if (trades > 0) {
        const emoji = pf >= 1.5 ? '🟢' : pf >= 1.0 ? '🟡' : '🔴';
        console.log(`  ${emoji} ${ticker} ${label}: ${trades} trades | WR ${wr}% | PF ${pf}`);
      } else {
        console.log(`  ⚪ ${ticker} ${label}: 0 trades`);
      }
    } catch (err) {
      console.error(`  ❌ ${ticker} ${label}: ${err.message}`);
      results.push({ ticker, tf: label, trades: 0, wr: 0, pf: 0, error: err.message });
    }
  }
}

// ═══════════════════════════════════════════════════════════════
// REPORT
// ═══════════════════════════════════════════════════════════════

const withTrades = results.filter(r => r.trades > 0).sort((a, b) => b.pf - a.pf);
const profitable = withTrades.filter(r => r.pf >= 1.0);
const strong = withTrades.filter(r => r.pf >= 1.5);

let report = `📊 *Equities Bounce Backtest Results*\n`;
report += `_${TICKERS.length} stocks × ${TIMEFRAMES.length} TFs — TV Strategy Tester_\n\n`;
report += `Stocks with trades: ${withTrades.length}/${results.length}\n`;
report += `Profitable (PF≥1.0): ${profitable.length}\n`;
report += `Strong (PF≥1.5): ${strong.length}\n\n`;

if (strong.length > 0) {
  report += `*🏆 Strong Performers (PF ≥ 1.5):*\n`;
  for (const r of strong.slice(0, 15)) {
    report += `  🟢 *${r.ticker}* (${r.tf}) — PF ${r.pf} | WR ${r.wr}% | ${r.trades} trades\n`;
  }
  report += '\n';
}

if (profitable.length > strong.length) {
  report += `*🟡 Profitable (PF 1.0–1.5):*\n`;
  for (const r of profitable.filter(r => r.pf < 1.5).slice(0, 10)) {
    report += `  ${r.ticker} (${r.tf}) — PF ${r.pf} | WR ${r.wr}% | ${r.trades} trades\n`;
  }
  report += '\n';
}

// Summary table
report += `*Summary (top 20):*\n\`\`\`\n`;
report += `Ticker  | TF    | WR    | PF   | Trades\n`;
report += `--------|-------|-------|------|-------\n`;
for (const r of withTrades.slice(0, 20)) {
  report += `${r.ticker.padEnd(8)}| ${r.tf.padEnd(6)}| ${(r.wr + '%').padEnd(6)}| ${r.pf.toString().padEnd(5)}| ${r.trades}\n`;
}
report += `\`\`\`\n`;

// Aggregate stats
if (withTrades.length > 0) {
  const totalTrades = withTrades.reduce((s, r) => s + r.trades, 0);
  const avgPF = withTrades.reduce((s, r) => s + r.pf, 0) / withTrades.length;
  const avgWR = withTrades.reduce((s, r) => s + r.wr, 0) / withTrades.length;
  report += `\n*Aggregate:* ${totalTrades} total trades | Avg PF ${avgPF.toFixed(2)} | Avg WR ${avgWR.toFixed(1)}%`;
}

console.log('\n' + report);
await sendTG(report);

// Save results
writeFileSync(resolve(__dirname, '../strategies/bounce_backtest_results.json'), JSON.stringify({
  tested_on: new Date().toISOString(),
  tickers: TICKERS.length,
  timeframes: TIMEFRAMES.map(t => t.label),
  total_results: results.length,
  with_trades: withTrades.length,
  profitable: profitable.length,
  strong: strong.length,
  top_performers: strong.map(r => ({ ticker: r.ticker, tf: r.tf, pf: r.pf, wr: r.wr, trades: r.trades })),
  all_results: results,
}, null, 2));

// Reload crypto
try {
  await clearStudies();
  const vdpPine = readFileSync(resolve(__dirname, '../pine/vdp_tone_vase.pine'), 'utf8');
  await setSource({ source: vdpPine });
  await smartCompile();
  await evaluate(`(function(){var b=document.querySelectorAll('button');for(var i=0;i<b.length;i++){if(b[i].textContent.indexOf('Add to chart')!==-1){b[i].click();return}}})()`);
  await setSymbol({ symbol: 'BTCUSD' });
  await setTimeframe({ timeframe: 'W' });
} catch {}

console.log('Done.');

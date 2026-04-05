#!/usr/bin/env node
/**
 * Pine Strategy Tester — loads each strategy() Pine script,
 * reads TradingView's built-in Strategy Tester results via CDP,
 * updates strategy configs, sends report to Telegram.
 *
 * This uses TV's internal backtester (full historical data) instead of replay mode.
 */

import { readFileSync, writeFileSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';
import { setSymbol, setTimeframe, manageIndicator, getState } from '../src/core/chart.js';
import { ensurePineEditorOpen, setSource, smartCompile } from '../src/core/pine.js';
import { evaluate } from '../src/connection.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const envPath = resolve(__dirname, '../.env');
const PINE_DIR = resolve(__dirname, '../pine');
const STRAT_DIR = resolve(__dirname, '../strategies');

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
    await new Promise(r => setTimeout(r, 500));
  }
}

async function loadPine(pinePath) {
  const source = readFileSync(pinePath, 'utf8');
  await ensurePineEditorOpen();
  await new Promise(r => setTimeout(r, 1500));
  await setSource({ source });
  const result = await smartCompile();
  await new Promise(r => setTimeout(r, 2000));

  // Click Add to chart / Update on chart
  await evaluate(`(function(){var b=document.querySelectorAll('button');for(var i=0;i<b.length;i++){var t=b[i].textContent;if(t.indexOf('Add to chart')!==-1||t.indexOf('Update on chart')!==-1){b[i].click();return 'clicked'}}return 'not found'})()`);
  await new Promise(r => setTimeout(r, 4000));
  return result;
}

async function openStrategyTester() {
  // Click the "Strategy Tester" tab in the bottom panel
  await evaluate(`
    (function() {
      // Try clicking Strategy Tester tab
      var tabs = document.querySelectorAll('[data-name="backtesting"]');
      if (tabs.length > 0) { tabs[0].click(); return 'clicked backtesting'; }

      // Try finding it by text
      var btns = document.querySelectorAll('button');
      for (var i = 0; i < btns.length; i++) {
        if (btns[i].textContent.indexOf('Strategy Tester') !== -1) {
          btns[i].click(); return 'clicked strategy tester';
        }
      }

      // Try the bottom panel tabs
      var allEls = document.querySelectorAll('[class*="tab"]');
      for (var j = 0; j < allEls.length; j++) {
        if (allEls[j].textContent.indexOf('Strategy') !== -1) {
          allEls[j].click(); return 'clicked tab';
        }
      }
      return 'not found';
    })()
  `);
  await new Promise(r => setTimeout(r, 2000));
}

async function readStrategyResults() {
  // Wait for strategy to compute
  await new Promise(r => setTimeout(r, 5000));

  // Read from the Strategy Tester's Overview/Performance tab via DOM
  const results = await evaluate(`
    (function() {
      var r = {};

      // Try reading from the strategy report panel
      // TradingView puts strategy stats in the bottom panel
      var cells = document.querySelectorAll('[class*="report"] td, [class*="strategyReport"] td, [class*="backtesting"] td');
      var pairs = {};
      for (var i = 0; i < cells.length - 1; i += 2) {
        var key = cells[i].textContent.trim();
        var val = cells[i+1] ? cells[i+1].textContent.trim() : '';
        if (key) pairs[key] = val;
      }
      if (Object.keys(pairs).length > 0) { r.tableData = pairs; }

      // Also try reading from any visible report data
      var reportEls = document.querySelectorAll('[data-name="strategy-report"] *, [class*="reportBlock"] *');
      var reportText = [];
      for (var j = 0; j < reportEls.length; j++) {
        var t = reportEls[j].textContent.trim();
        if (t && t.length < 100) reportText.push(t);
      }
      r.reportText = [...new Set(reportText)].slice(0, 50);

      // Try the performance summary specifically
      var perfEls = document.querySelectorAll('[class*="performance"], [class*="summary"]');
      var perfData = [];
      for (var k = 0; k < perfEls.length; k++) {
        var pt = perfEls[k].textContent.trim();
        if (pt && pt.length < 200) perfData.push(pt);
      }
      r.perfData = [...new Set(perfData)].slice(0, 30);

      // Get all bottom panel text as fallback
      var bottom = document.querySelector('[class*="layout__area--bottom"]');
      if (bottom) {
        r.bottomText = bottom.textContent.substring(0, 2000);
      }

      return r;
    })()
  `);

  return results;
}

// ============ TEST PLAN ============

const tests = [
  // Strategies with strategy() mode — TV backtests them internally
  { pine: 'rsi_vwap_meanrev.pine', stratFile: 'rsi_vwap_meanrev.json', name: 'RSI + VWAP MeanRev',
    pairs: [
      { symbol: 'BTCUSD', tf: '60', label: 'BTC 1H' },
      { symbol: 'ETHUSD', tf: '60', label: 'ETH 1H' },
    ]},
  { pine: 'bb_rsi_meanrev.pine', stratFile: 'bb_rsi_meanrev.json', name: 'BB + RSI MeanRev',
    pairs: [
      { symbol: 'BTCUSD', tf: '60', label: 'BTC 1H' },
      { symbol: 'BTCUSD', tf: '240', label: 'BTC 4H' },
    ]},
  { pine: 'fvg_scalper.pine', stratFile: 'fvg_scalper.json', name: 'FVG Scalper',
    pairs: [
      { symbol: 'BTCUSD', tf: '15', label: 'BTC 15M' },
      { symbol: 'EURUSD', tf: '15', label: 'EUR/USD 15M' },
    ]},
];

// ============ MAIN ============

await sendTG('⏳ *Strategy Tester Suite Starting*\n\nLoading each strategy Pine script and reading TV\\'s built-in backtest results.\n3 strategies × 2 pairs each.');

const allResults = [];

for (const test of tests) {
  console.log(`\n>>> ${test.name} (${test.pine})`);
  await sendTG(`🔄 *${test.name}* — loading...`);

  await clearStudies();
  await new Promise(r => setTimeout(r, 1500));

  try {
    await loadPine(resolve(PINE_DIR, test.pine));
  } catch (e) {
    console.error(`  Load failed: ${e.message}`);
    allResults.push({ name: test.name, error: `Pine load failed: ${e.message}` });
    continue;
  }

  const pairResults = [];

  for (const pair of test.pairs) {
    console.log(`  Testing ${pair.label}...`);
    await setSymbol({ symbol: pair.symbol });
    await setTimeframe({ timeframe: pair.tf });
    await new Promise(r => setTimeout(r, 4000)); // Wait for data + strategy computation

    await openStrategyTester();
    const results = await readStrategyResults();

    pairResults.push({ label: pair.label, symbol: pair.symbol, tf: pair.tf, results });
    console.log(`  ${pair.label}: ${JSON.stringify(results.tableData || {}).substring(0, 200)}`);
  }

  allResults.push({ name: test.name, stratFile: test.stratFile, pairs: pairResults });

  // Update strategy config
  if (test.stratFile) {
    try {
      const stratPath = resolve(STRAT_DIR, test.stratFile);
      const strat = JSON.parse(readFileSync(stratPath, 'utf8'));
      strat.strategy_tester_results = {};
      for (const pr of pairResults) {
        strat.strategy_tester_results[pr.label] = {
          tested_on: new Date().toISOString().split('T')[0],
          raw_data: pr.results.tableData || {},
          report_excerpt: (pr.results.reportText || []).slice(0, 10),
        };
      }
      writeFileSync(stratPath, JSON.stringify(strat, null, 2));
      console.log(`  Updated ${test.stratFile}`);
    } catch (e) {
      console.error(`  Config update failed: ${e.message}`);
    }
  }
}

// ============ REPORT ============

let report = '📊 *STRATEGY TESTER RESULTS*\n_Using TradingView\\'s built-in backtester (full history)_\n\n';

for (const r of allResults) {
  if (r.error) { report += `❌ *${r.name}*: ${r.error}\n\n`; continue; }

  report += `*${r.name}*\n`;
  for (const pr of r.pairs) {
    report += `  📈 ${pr.label}:\n`;
    const td = pr.results.tableData;
    if (td && Object.keys(td).length > 0) {
      for (const [k, v] of Object.entries(td).slice(0, 8)) {
        report += `     ${k}: ${v}\n`;
      }
    } else {
      // Use report text
      const text = (pr.results.reportText || []).filter(t => t.length > 3).slice(0, 5);
      if (text.length > 0) {
        report += `     ${text.join(' | ')}\n`;
      } else {
        report += `     (Could not read strategy tester — may need manual check)\n`;
      }
    }
  }
  report += '\n';
}

report += '_Strategy configs updated. Check the Alpha Terminal dashboard._';

console.log('\n' + report);
await sendTG(report);

// Reload VDP
console.log('\nReloading VDP...');
await clearStudies();
try { await loadPine(resolve(PINE_DIR, 'vdp_tone_vase.pine')); } catch {}
console.log('Done.');

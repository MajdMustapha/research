#!/usr/bin/env node
/**
 * Strategy Validator — loads each strategy() Pine script one at a time,
 * reads TradingView's built-in Strategy Tester results via CDP,
 * analyzes performance, suggests tweaks, and sends report to Telegram.
 *
 * Flow per strategy:
 *   1. Clear chart → load Pine → set symbol/TF → wait for backtest
 *   2. Open Strategy Tester tab → scrape performance data
 *   3. Analyze → suggest tweaks → update config
 *   4. Unload → next strategy
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

  // Click Add to chart
  const clicked = await evaluate(`(function(){var b=document.querySelectorAll('button');for(var i=0;i<b.length;i++){var t=b[i].textContent;if(t.indexOf('Add to chart')!==-1||t.indexOf('Update on chart')!==-1){b[i].click();return t.trim()}}return 'not found'})()`);
  await new Promise(r => setTimeout(r, 5000));

  return { compile: result, clicked };
}

async function readStrategyTester() {
  // Wait for strategy to compute
  await new Promise(r => setTimeout(r, 3000));

  // Click Strategy Tester / Overview tab
  await evaluate(`
    (function() {
      // Try to open the Strategy Tester bottom panel
      var btns = document.querySelectorAll('button');
      for (var i = 0; i < btns.length; i++) {
        var t = btns[i].textContent.trim();
        if (t === 'Strategy Tester' || t.indexOf('Strategy Tester') !== -1) {
          btns[i].click();
          return 'clicked';
        }
      }
      // Try data-name
      var el = document.querySelector('[data-name="backtesting"]');
      if (el) { el.click(); return 'data-name'; }
      return 'not found';
    })()
  `);
  await new Promise(r => setTimeout(r, 2000));

  // Try clicking Overview tab within Strategy Tester
  await evaluate(`
    (function() {
      var tabs = document.querySelectorAll('[class*="tab"], [role="tab"]');
      for (var i = 0; i < tabs.length; i++) {
        if (tabs[i].textContent.trim() === 'Overview') {
          tabs[i].click();
          return 'overview clicked';
        }
      }
      return 'no overview tab';
    })()
  `);
  await new Promise(r => setTimeout(r, 2000));

  // Scrape all text from the bottom panel area
  const data = await evaluate(`
    (function() {
      var result = {};

      // Method 1: Find the strategy report area
      var bottom = document.querySelector('[class*="layout__area--bottom"]');
      if (bottom) {
        result.bottomText = bottom.innerText.substring(0, 5000);
      }

      // Method 2: Find specific metric elements
      // TradingView uses data-name attributes for report cells
      var reportCells = document.querySelectorAll('[class*="report"] [class*="cell"], [class*="report"] td, [class*="additional"] [class*="cell"]');
      var cells = [];
      for (var i = 0; i < reportCells.length; i++) {
        cells.push(reportCells[i].textContent.trim());
      }
      result.cells = cells;

      // Method 3: Look for key metric values
      var allText = bottom ? bottom.innerText : document.body.innerText.substring(0, 10000);

      // Parse known patterns
      var patterns = {
        'Net Profit': /Net Profit[\\s:]*([\\d,.\\-$%]+)/i,
        'Gross Profit': /Gross Profit[\\s:]*([\\d,.\\-$%]+)/i,
        'Gross Loss': /Gross Loss[\\s:]*([\\d,.\\-$%]+)/i,
        'Total Trades': /(?:Total (?:Closed )?Trades|Number of Trades)[\\s:]*([\\d,]+)/i,
        'Win Rate': /(?:Percent Profitable|Win Rate)[\\s:]*([\\d,.]+%?)/i,
        'Profit Factor': /Profit Factor[\\s:]*([\\d,.]+)/i,
        'Max Drawdown': /Max(?:imum)? Drawdown[\\s:]*([\\d,.\\-$%]+)/i,
        'Avg Trade': /Avg(?:erage)? Trade[\\s:]*([\\d,.\\-$%]+)/i,
        'Sharpe Ratio': /Sharpe Ratio[\\s:]*([\\d,.\\-]+)/i,
      };

      result.metrics = {};
      for (var key in patterns) {
        var m = allText.match(patterns[key]);
        if (m) result.metrics[key] = m[1].trim();
      }

      return result;
    })()
  `);

  return data;
}

function parseMetrics(data) {
  // Try to extract metrics from the scraped data
  const metrics = data.metrics || {};

  // If metrics are empty, try parsing from bottomText
  if (Object.keys(metrics).length === 0 && data.bottomText) {
    const text = data.bottomText;
    // Look for common patterns in the raw text
    const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
    for (let i = 0; i < lines.length - 1; i++) {
      const key = lines[i];
      const val = lines[i + 1];
      if (key === 'Net Profit' || key === 'Total Closed Trades' || key === 'Percent Profitable' ||
          key === 'Profit Factor' || key === 'Max Drawdown' || key === 'Sharpe Ratio' ||
          key === 'Avg Trade' || key === 'Gross Profit' || key === 'Gross Loss') {
        metrics[key] = val;
      }
    }
  }

  return metrics;
}

function analyzeAndRecommend(name, metrics) {
  const recs = [];
  const wr = parseFloat(metrics['Win Rate'] || metrics['Percent Profitable'] || '0');
  const pf = parseFloat(metrics['Profit Factor'] || '0');
  const trades = parseInt((metrics['Total Trades'] || metrics['Total Closed Trades'] || '0').replace(/,/g, ''));
  const np = metrics['Net Profit'] || 'unknown';

  if (trades < 10) recs.push('Very few trades — try longer timeframe or wider date range for more data');
  if (trades === 0) recs.push('NO TRADES generated — strategy conditions too strict or no data for this TF');
  if (wr > 0 && wr < 40) recs.push('Win rate below 40% — consider tightening entries or widening TP');
  if (wr >= 60) recs.push('Win rate above 60% — solid foundation');
  if (pf > 0 && pf < 1) recs.push('Profit factor below 1 — strategy loses money. Review exit logic');
  if (pf >= 1.5) recs.push('Profit factor above 1.5 — strategy has edge');
  if (pf >= 2) recs.push('Profit factor above 2 — strong strategy');

  return { winRate: wr, profitFactor: pf, trades, netProfit: np, recommendations: recs };
}

// ═══════════════════════════════════════════════════════════════
// TEST PLAN — each strategy on multiple symbol/TF combos
// ═══════════════════════════════════════════════════════════════

const testPlan = [
  {
    name: 'RSI + VWAP Mean Reversion',
    pine: 'rsi_vwap_meanrev.pine',
    stratFile: 'rsi_vwap_meanrev.json',
    tests: [
      { symbol: 'BTCUSD', tf: '60', label: 'BTC 1H' },
      { symbol: 'ETHUSD', tf: '60', label: 'ETH 1H' },
    ],
  },
  {
    name: 'BB + RSI Mean Reversion',
    pine: 'bb_rsi_meanrev.pine',
    stratFile: 'bb_rsi_meanrev.json',
    tests: [
      { symbol: 'BTCUSD', tf: '60', label: 'BTC 1H' },
      { symbol: 'BTCUSD', tf: '240', label: 'BTC 4H' },
    ],
  },
  {
    name: 'FVG Scalper',
    pine: 'fvg_scalper.pine',
    stratFile: 'fvg_scalper.json',
    tests: [
      { symbol: 'BTCUSD', tf: '15', label: 'BTC 15M' },
    ],
  },
];

// ═══════════════════════════════════════════════════════════════
// MAIN
// ═══════════════════════════════════════════════════════════════

await sendTG(`⏳ *Strategy Validation Starting*\n\nLoading each strategy individually into TV's Strategy Tester.\n3 strategies to validate.\n\nThis uses TV's built-in backtester — full historical data, no replay needed.`);

const allResults = [];

for (const plan of testPlan) {
  console.log(`\n═══ ${plan.name} ═══`);
  await sendTG(`🔄 Loading *${plan.name}*...`);

  await clearStudies();
  await new Promise(r => setTimeout(r, 1500));

  let loadResult;
  try {
    loadResult = await loadPine(resolve(PINE_DIR, plan.pine));
    if (loadResult.compile.has_errors) {
      const realErrors = loadResult.compile.errors.filter(e => e.severity >= 8);
      if (realErrors.length > 0) {
        const errMsg = realErrors.map(e => `L${e.line}: ${e.message}`).join('; ');
        console.error(`  Compile errors: ${errMsg}`);
        allResults.push({ name: plan.name, error: `Compile error: ${errMsg}` });
        await sendTG(`❌ *${plan.name}* — compile error:\n\`${errMsg}\``);
        continue;
      }
    }
  } catch (e) {
    console.error(`  Load failed: ${e.message}`);
    allResults.push({ name: plan.name, error: e.message });
    continue;
  }

  console.log(`  Loaded (${loadResult.clicked})`);
  const stratResults = [];

  for (const test of plan.tests) {
    console.log(`  Testing ${test.label}...`);
    await setSymbol({ symbol: test.symbol });
    await setTimeframe({ timeframe: test.tf });
    await new Promise(r => setTimeout(r, 6000)); // Wait for data load + strategy computation

    const raw = await readStrategyTester();
    const metrics = parseMetrics(raw);
    const analysis = analyzeAndRecommend(plan.name, metrics);

    console.log(`  ${test.label}: ${JSON.stringify(metrics)}`);
    console.log(`  Analysis: WR=${analysis.winRate}%, PF=${analysis.profitFactor}, Trades=${analysis.trades}`);

    stratResults.push({ ...test, metrics, analysis, rawExcerpt: (raw.bottomText || '').substring(0, 500) });
  }

  allResults.push({ name: plan.name, stratFile: plan.stratFile, results: stratResults });

  // Update strategy config with results
  if (plan.stratFile) {
    try {
      const stratPath = resolve(STRAT_DIR, plan.stratFile);
      const strat = JSON.parse(readFileSync(stratPath, 'utf8'));

      strat.tv_strategy_tester = {};
      const allRecs = [];

      for (const r of stratResults) {
        strat.tv_strategy_tester[r.label] = {
          tested_on: new Date().toISOString().split('T')[0],
          metrics: r.metrics,
          win_rate: r.analysis.winRate,
          profit_factor: r.analysis.profitFactor,
          total_trades: r.analysis.trades,
          net_profit: r.analysis.netProfit,
        };
        allRecs.push(...r.analysis.recommendations.map(rec => `${r.label}: ${rec}`));
      }

      strat.tv_strategy_tester._recommendations = [...new Set(allRecs)];
      writeFileSync(stratPath, JSON.stringify(strat, null, 2));
      console.log(`  Updated ${plan.stratFile}`);
    } catch (e) {
      console.error(`  Config update failed: ${e.message}`);
    }
  }
}

// ═══════════════════════════════════════════════════════════════
// REPORT
// ═══════════════════════════════════════════════════════════════

let report = '📊 *STRATEGY VALIDATION RESULTS*\n_TradingView Built-in Backtester (full history)_\n\n';

for (const r of allResults) {
  if (r.error) {
    report += `❌ *${r.name}*\n   ${r.error}\n\n`;
    continue;
  }

  report += `*${r.name}*\n`;
  for (const t of r.results) {
    const m = t.metrics;
    const a = t.analysis;
    const emoji = a.profitFactor >= 1.5 ? '🟢' : a.profitFactor >= 1.0 ? '🟡' : a.trades === 0 ? '⚪' : '🔴';

    report += `  ${emoji} *${t.label}*\n`;
    if (Object.keys(m).length > 0) {
      for (const [k, v] of Object.entries(m)) {
        report += `     ${k}: ${v}\n`;
      }
    } else {
      report += `     Raw data: ${t.rawExcerpt.substring(0, 200)}...\n`;
    }

    if (a.recommendations.length > 0) {
      report += `     → ${a.recommendations.join('\n     → ')}\n`;
    }
  }
  report += '\n';
}

// Summary
report += '*Summary:*\n```\n';
report += 'Strategy          | Pair     | WR    | PF   | Trades\n';
report += '------------------|----------|-------|------|-------\n';
for (const r of allResults) {
  if (r.error) { report += `${r.name.substring(0,18).padEnd(18)}| ERROR\n`; continue; }
  for (const t of r.results) {
    report += `${r.name.substring(0,18).padEnd(18)}| ${t.label.padEnd(9)}| ${(t.analysis.winRate + '%').padEnd(6)}| ${t.analysis.profitFactor.toString().padEnd(5)}| ${t.analysis.trades}\n`;
  }
}
report += '```\n';

report += '\n_Next: tweak underperformers, re-run, then combine winners into Meta Strategy._';

console.log('\n' + report);
await sendTG(report);

// Reload VDP as default
console.log('\nReloading VDP...');
await clearStudies();
try { await loadPine(resolve(PINE_DIR, 'vdp_tone_vase.pine')); } catch {}
console.log('Done.');

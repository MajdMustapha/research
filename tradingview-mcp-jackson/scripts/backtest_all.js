#!/usr/bin/env node
/**
 * Comprehensive backtester — tests each strategy in both bull and bear periods.
 * Loads/unloads Pine scripts within free account limit.
 * Updates strategy JSON configs with backtest results and observations.
 * Sends combined report to Telegram.
 */

import { readFileSync, writeFileSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';
import { setSymbol, setTimeframe, manageIndicator, getState } from '../src/core/chart.js';
import { getStudyValues } from '../src/core/data.js';
import { start as replayStart, step as replayStep, stop as replayStop } from '../src/core/replay.js';
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

function pn(val) {
  if (typeof val === 'number') return val;
  if (typeof val === 'string') return parseFloat(val.replace(/,/g, ''));
  return NaN;
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
  await smartCompile();
  await new Promise(r => setTimeout(r, 2000));
  await evaluate(`(function(){var b=document.querySelectorAll('button');for(var i=0;i<b.length;i++)if(b[i].textContent.indexOf('Add to chart')!==-1){b[i].click();return}})()`);
  await new Promise(r => setTimeout(r, 3000));
  return (await getState()).studies || [];
}

async function getBar() {
  return evaluate(`(function(){var c=window.TradingViewApi._activeChartWidgetWV.value();var b=c._chartWidget.model().mainSeries().bars();var l=b.lastIndex();var v=b.valueAt(l);if(!v)return null;return{time:v[0],open:v[1],high:v[2],low:v[3],close:v[4],volume:v[5]}})()`);
}

async function runTest({ symbol, tf, start, end, name, entryFn, tpPct, slPct, maxSteps }) {
  console.log(`  [${name}] ${symbol} ${tf} ${start}→${end}`);
  await setSymbol({ symbol });
  await setTimeframe({ timeframe: tf });
  await new Promise(r => setTimeout(r, 3000));

  try { await replayStart({ date: start }); } catch (e) { return { name, error: `Replay start failed: ${e.message}` }; }
  await new Promise(r => setTimeout(r, 2000));

  const endTs = new Date(end).getTime();
  const candles = [];
  const trades = [];
  let open = null;
  let steps = 0;
  const limit = maxSteps || 200;

  while (steps < limit) {
    steps++;
    const bar = await getBar();
    if (!bar) { await new Promise(r => setTimeout(r, 500)); continue; }
    if (bar.time * 1000 > endTs) break;

    await new Promise(r => setTimeout(r, 300));
    let ind; try { ind = await getStudyValues(); } catch { ind = null; }

    const price = bar.close;
    const date = new Date(bar.time * 1000).toISOString().split('T')[0];
    const sig = entryFn(price, bar, ind);
    candles.push({ date, price, sig: sig?.type || null });

    if (steps % 30 === 0) console.log(`    [${steps}] ${date} $${price.toLocaleString()} ${sig?.type || '-'}`);

    // Trade logic
    if ((sig?.type === 'LONG' || sig?.type === 'SHORT') && !open) {
      open = { entry_date: date, entry_price: price, dir: sig.type === 'LONG' ? 'long' : 'short' };
      trades.push(open);
    } else if (open) {
      const g = open.dir === 'long' ? (price - open.entry_price) / open.entry_price : (open.entry_price - price) / open.entry_price;
      if (g >= (tpPct || 0.12)) {
        open.exit_date = date; open.exit_price = price; open.pnl = (g * 100).toFixed(2); open.result = 'WIN'; open = null;
      } else if (g <= -(slPct || 0.06)) {
        open.exit_date = date; open.exit_price = price; open.pnl = (g * 100).toFixed(2); open.result = 'LOSS'; open = null;
      }
    }

    try { await replayStep(); await new Promise(r => setTimeout(r, 500)); } catch { break; }
  }

  if (open && candles.length > 0) {
    const last = candles[candles.length - 1];
    const g = open.dir === 'long' ? (last.price - open.entry_price) / open.entry_price : (open.entry_price - last.price) / open.entry_price;
    open.exit_date = last.date; open.exit_price = last.price; open.pnl = (g * 100).toFixed(2); open.result = g >= 0 ? 'OPEN+' : 'OPEN-';
  }

  try { await replayStop(); } catch {}

  const closed = trades.filter(t => t.exit_price);
  const wins = closed.filter(t => t.result === 'WIN' || t.result === 'OPEN+');
  const losses = closed.filter(t => t.result === 'LOSS' || t.result === 'OPEN-');
  const totalRet = closed.reduce((s, t) => s + parseFloat(t.pnl || 0), 0);
  const signals = candles.filter(c => c.sig).length;

  return {
    name, symbol, tf, start, end, candles: candles.length, signals, trades: closed.length,
    wins: wins.length, losses: losses.length,
    winRate: closed.length > 0 ? (wins.length / closed.length * 100).toFixed(0) : '-',
    totalReturn: totalRet.toFixed(2),
    tradeLog: closed,
  };
}

// ============ ENTRY FUNCTIONS ============

function vdpEntry(price, bar, ind) {
  const s = ind?.studies?.find(s => s.name === 'VDP + Tone Vase Strategy');
  if (!s) return null;
  const e21 = pn(s.values?.['EMA 21']), e50 = pn(s.values?.['EMA 50']), e200 = pn(s.values?.['EMA 200']);
  const rsi = pn(s.values?.['RSI']), mh = pn(s.values?.['MACD Hist']), vr = pn(s.values?.['Vol Ratio']);
  const near = (!isNaN(e21) && Math.abs(price - e21) / e21 < 0.05) || (!isNaN(e50) && Math.abs(price - e50) / e50 < 0.05);
  const above200 = !isNaN(e200) && price > e200;
  if (near && above200 && (!isNaN(vr) && vr > 0.5) && (isNaN(rsi) || rsi < 55) && (isNaN(mh) || mh > -2.0)) return { type: 'LONG' };
  return null;
}

function rsiVwapEntry(price, bar, ind) {
  const s = ind?.studies?.find(s => s.name?.includes('RSI + VWAP'));
  if (!s) return null;
  const rsi = pn(s.values?.['RSI']), vwap = pn(s.values?.['VWAP Price']);
  if (isNaN(rsi) || isNaN(vwap)) return null;
  if (rsi < 20 && price < vwap) return { type: 'LONG' };
  if (rsi > 80 && price > vwap) return { type: 'SHORT' };
  return null;
}

function bbRsiEntry(price, bar, ind) {
  const s = ind?.studies?.find(s => s.name?.includes('BB + RSI'));
  if (!s) return null;
  const rsi = pn(s.values?.['RSI']), adx = pn(s.values?.['ADX']);
  const bbu = pn(s.values?.['BB Upper Val']), bbl = pn(s.values?.['BB Lower Val']);
  if (isNaN(rsi)) return null;
  if (!isNaN(adx) && adx >= 20) return null;
  if (price <= bbl && rsi <= 30) return { type: 'LONG' };
  if (price >= bbu && rsi >= 70) return { type: 'SHORT' };
  return null;
}

function cvdEntry(price, bar, ind) {
  const s = ind?.studies?.find(s => s.name?.includes('CVD'));
  if (!s) return null;
  const d = pn(s.values?.['Div Signal']);
  if (d === 1) return { type: 'LONG' };
  if (d === -1) return { type: 'SHORT' };
  return null;
}

// ============ TEST PLAN ============
// Each strategy tested in bull AND bear conditions

const testPlan = [
  // VDP + Tone Vase (Weekly swing)
  { pine: 'vdp_tone_vase.pine', stratFile: null, group: 'VDP + Tone Vase', tests: [
    { name: 'VDP Bear 2022', symbol: 'BTCUSD', tf: 'W', start: '2022-01-01', end: '2023-01-01', entryFn: vdpEntry, tpPct: 0.15, slPct: 0.07, maxSteps: 60 },
    { name: 'VDP Bull 2024', symbol: 'BTCUSD', tf: 'W', start: '2024-01-01', end: '2024-12-01', entryFn: vdpEntry, tpPct: 0.15, slPct: 0.07, maxSteps: 60 },
  ]},
  // RSI + VWAP Mean Reversion (1H)
  { pine: 'rsi_vwap_meanrev.pine', stratFile: 'rsi_vwap_meanrev.json', group: 'RSI + VWAP MeanRev', tests: [
    { name: 'RSI+VWAP Bear Q1-25', symbol: 'BTCUSD', tf: '60', start: '2025-02-01', end: '2025-04-01', entryFn: rsiVwapEntry, tpPct: 0.03, slPct: 0.015, maxSteps: 150 },
    { name: 'RSI+VWAP Bull Q4-24', symbol: 'BTCUSD', tf: '60', start: '2024-10-01', end: '2024-12-01', entryFn: rsiVwapEntry, tpPct: 0.03, slPct: 0.015, maxSteps: 150 },
  ]},
  // BB + RSI Mean Reversion (1H)
  { pine: 'bb_rsi_meanrev.pine', stratFile: 'bb_rsi_meanrev.json', group: 'BB + RSI MeanRev', tests: [
    { name: 'BB+RSI Range Q2-24', symbol: 'BTCUSD', tf: '60', start: '2024-04-01', end: '2024-06-01', entryFn: bbRsiEntry, tpPct: 0.03, slPct: 0.015, maxSteps: 150 },
    { name: 'BB+RSI Bear Q1-25', symbol: 'BTCUSD', tf: '60', start: '2025-02-01', end: '2025-04-01', entryFn: bbRsiEntry, tpPct: 0.03, slPct: 0.015, maxSteps: 150 },
  ]},
  // CVD Divergence (15M)
  { pine: 'cvd_divergence.pine', stratFile: 'cvd_divergence.json', group: 'CVD Divergence', tests: [
    { name: 'CVD Volatile Mar-25', symbol: 'BTCUSD', tf: '15', start: '2025-03-01', end: '2025-03-15', entryFn: cvdEntry, tpPct: 0.02, slPct: 0.01, maxSteps: 200 },
    { name: 'CVD Calm Feb-25', symbol: 'BTCUSD', tf: '15', start: '2025-02-01', end: '2025-02-15', entryFn: cvdEntry, tpPct: 0.02, slPct: 0.01, maxSteps: 200 },
  ]},
];

// ============ MAIN ============

await sendTG(`⏳ *Full Backtest Suite Starting*\n\n4 strategies × 2 periods each (bull + bear)\n8 backtests total. ETA: 30-45 min.\n\nPine scripts will be loaded/unloaded one at a time.`);

const allResults = [];

for (const group of testPlan) {
  console.log(`\n>>> LOADING: ${group.group} (${group.pine})`);
  await sendTG(`🔄 *${group.group}* — loading Pine script...`);

  await clearStudies();
  await new Promise(r => setTimeout(r, 1500));

  let loaded;
  try { loaded = await loadPine(resolve(PINE_DIR, group.pine)); }
  catch (e) {
    console.error(`  Load failed: ${e.message}`);
    allResults.push({ group: group.group, tests: [{ name: group.group, error: `Pine load failed: ${e.message}` }] });
    continue;
  }
  console.log(`  Studies: ${loaded.map(s => s.name).join(', ')}`);

  const groupResults = [];
  for (const test of group.tests) {
    const result = await runTest(test);
    groupResults.push(result);

    const emoji = result.error ? '❌' : parseFloat(result.totalReturn) > 0 ? '🟢' : '🔴';
    console.log(`  ${emoji} ${result.name}: ${result.trades} trades, WR ${result.winRate}%, Ret ${result.totalReturn}%`);
  }

  allResults.push({ group: group.group, stratFile: group.stratFile, tests: groupResults });

  // Update strategy JSON with backtest results
  if (group.stratFile) {
    const stratPath = resolve(STRAT_DIR, group.stratFile);
    try {
      const strat = JSON.parse(readFileSync(stratPath, 'utf8'));
      strat.backtest_results = {};
      const observations = [];

      for (const r of groupResults) {
        if (r.error) { strat.backtest_results[r.name] = { error: r.error }; continue; }
        strat.backtest_results[r.name] = {
          period: `${r.start} → ${r.end}`,
          timeframe: r.tf,
          candles: r.candles,
          signals: r.signals,
          trades: r.trades,
          wins: r.wins,
          losses: r.losses,
          win_rate: r.winRate + '%',
          total_return: r.totalReturn + '%',
          tested_on: new Date().toISOString().split('T')[0],
        };

        // Generate observations
        if (r.signals === 0) observations.push(`${r.name}: No signals generated — strategy may not suit this market condition`);
        if (r.trades === 0 && r.signals > 0) observations.push(`${r.name}: ${r.signals} signals but 0 trades — entry thresholds too strict`);
        if (r.trades > 0 && parseFloat(r.winRate) >= 60) observations.push(`${r.name}: Strong ${r.winRate}% win rate — strategy works well in this condition`);
        if (r.trades > 0 && parseFloat(r.winRate) < 40) observations.push(`${r.name}: Weak ${r.winRate}% win rate — consider avoiding this market condition`);
        if (r.trades > 0 && parseFloat(r.totalReturn) > 10) observations.push(`${r.name}: +${r.totalReturn}% return — high conviction period`);
        if (r.trades > 0 && parseFloat(r.totalReturn) < -10) observations.push(`${r.name}: ${r.totalReturn}% return — significant drawdown, add filters`);
      }

      // Compute aggregate
      const allTests = groupResults.filter(r => !r.error && r.trades > 0);
      if (allTests.length > 0) {
        const totalTrades = allTests.reduce((s, r) => s + r.trades, 0);
        const totalWins = allTests.reduce((s, r) => s + r.wins, 0);
        const avgReturn = allTests.reduce((s, r) => s + parseFloat(r.totalReturn), 0) / allTests.length;
        strat.backtest_results._aggregate = {
          total_trades: totalTrades,
          overall_win_rate: (totalWins / totalTrades * 100).toFixed(0) + '%',
          avg_return_per_period: avgReturn.toFixed(2) + '%',
          periods_tested: allTests.length,
        };

        // Auto-generate recommendations
        const recs = [];
        if (totalWins / totalTrades > 0.6) recs.push('Win rate above 60% — consider live paper trading');
        if (totalWins / totalTrades < 0.4) recs.push('Win rate below 40% — tighten entries or widen TP');
        if (avgReturn < 0) recs.push('Negative avg return — review risk management, consider tighter SL');
        if (avgReturn > 5) recs.push('Positive avg return — strategy shows edge, monitor for consistency');

        const bearTest = groupResults.find(r => r.name.toLowerCase().includes('bear'));
        const bullTest = groupResults.find(r => !r.name.toLowerCase().includes('bear') && !r.error);
        if (bearTest && !bearTest.error && bullTest && !bullTest.error) {
          const bearRet = parseFloat(bearTest.totalReturn);
          const bullRet = parseFloat(bullTest.totalReturn);
          if (bearRet < -5 && bullRet > 5) recs.push('Strategy is directional — works in bull, loses in bear. Add trend filter.');
          if (bearRet > 0 && bullRet > 0) recs.push('Profitable in both conditions — robust strategy');
          if (bearRet < 0 && bullRet < 0) recs.push('Loses in both conditions — fundamentally review entry/exit logic');
        }

        strat.backtest_results._recommendations = recs;
      }

      strat.backtest_results._observations = observations;
      writeFileSync(stratPath, JSON.stringify(strat, null, 2));
      console.log(`  Updated ${group.stratFile} with backtest results`);
    } catch (e) {
      console.error(`  Failed to update strategy file: ${e.message}`);
    }
  }
}

// Also update rules.json for VDP
try {
  const rules = JSON.parse(readFileSync(resolve(__dirname, '../rules.json'), 'utf8'));
  const vdpResults = allResults.find(r => r.group === 'VDP + Tone Vase');
  if (vdpResults) {
    rules.backtest_results = {};
    for (const r of vdpResults.tests) {
      if (r.error) continue;
      rules.backtest_results[r.name] = {
        period: `${r.start} → ${r.end}`, candles: r.candles, signals: r.signals,
        trades: r.trades, wins: r.wins, losses: r.losses,
        win_rate: r.winRate + '%', total_return: r.totalReturn + '%',
        tested_on: new Date().toISOString().split('T')[0],
      };
    }
    writeFileSync(resolve(__dirname, '../rules.json'), JSON.stringify(rules, null, 2));
    console.log('  Updated rules.json with VDP backtest results');
  }
} catch (e) { console.error('  Failed to update rules.json:', e.message); }

// ============ COMBINED REPORT ============

let report = '📊 *FULL BACKTEST REPORT*\n_4 strategies × 2 periods (bull + bear)_\n\n';

for (const group of allResults) {
  report += `*${group.group}*\n`;
  for (const r of group.tests) {
    if (r.error) { report += `  ❌ ${r.name}: ${r.error}\n`; continue; }
    const em = parseFloat(r.totalReturn) > 0 ? '🟢' : parseFloat(r.totalReturn) < 0 ? '🔴' : '⚪';
    report += `  ${em} ${r.name}\n`;
    report += `     ${r.candles} candles | ${r.signals} signals | ${r.trades} trades\n`;
    report += `     WR: ${r.winRate}% | Return: ${r.totalReturn}%\n`;
    if (r.tradeLog?.length > 0) {
      for (const t of r.tradeLog.slice(0, 3)) {
        report += `     ${t.result?.includes('WIN') || t.result === 'OPEN+' ? '✓' : '✗'} ${t.entry_date}→${t.exit_date} $${t.entry_price}→$${t.exit_price} (${t.pnl}%)\n`;
      }
      if (r.tradeLog.length > 3) report += `     ...+${r.tradeLog.length - 3} more\n`;
    }
  }
  report += '\n';
}

// Summary table
report += '*Summary:*\n```\n';
report += 'Strategy          | Bull WR | Bear WR | Bull Ret | Bear Ret\n';
report += '------------------|---------|---------|----------|--------\n';
for (const group of allResults) {
  const bull = group.tests.find(t => !t.name?.toLowerCase().includes('bear') && !t.error);
  const bear = group.tests.find(t => t.name?.toLowerCase().includes('bear') && !t.error);
  report += `${group.group.substring(0, 18).padEnd(18)}| ${(bull?.winRate || '-').toString().padEnd(6)}% | ${(bear?.winRate || '-').toString().padEnd(6)}% | ${((bull?.totalReturn || '-') + '%').padEnd(9)}| ${(bear?.totalReturn || '-') + '%'}\n`;
}
report += '```\n';

report += '\n_Strategy configs auto-updated with results. Check /strategies in Telegram or the dashboard._';

console.log('\n' + report);
await sendTG(report);

// Reload VDP as default
console.log('\nReloading VDP...');
await clearStudies();
try { await loadPine(resolve(PINE_DIR, 'vdp_tone_vase.pine')); } catch (e) { console.log('VDP reload failed:', e.message); }
console.log('All done.');

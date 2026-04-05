#!/usr/bin/env node
/**
 * Telegram command bot for TradingView MCP.
 *
 * Commands:
 *   /status  — full system health check
 *   /brief   — run morning brief now
 *   /journal — trade journal stats & open positions
 *   /entry   — log a trade entry: /entry BTCUSD 67000 0.5
 *   /exit    — close a trade: /exit T1234567890 72000
 */

import { readFileSync, existsSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';
import { exec, execSync } from 'child_process';
import { logEntry, logExit, getOpenTrades, formatJournalSummary } from './trade_journal.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const envPath = resolve(__dirname, '../.env');

// Load .env
for (const line of readFileSync(envPath, 'utf8').split('\n')) {
  const match = line.match(/^([^#=]+)=(.*)$/);
  if (match) process.env[match[1].trim()] = match[2].trim();
}

const BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const CHAT_ID = process.env.TELEGRAM_CHAT_ID;
let lastUpdateId = 0;
let briefRunning = false;

async function api(method, body = {}) {
  const res = await fetch(`https://api.telegram.org/bot${BOT_TOKEN}/${method}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return res.json();
}

async function send(text) {
  // Telegram has a 4096 char limit — split if needed
  const maxLen = 4000;
  if (text.length <= maxLen) {
    return api('sendMessage', {
      chat_id: CHAT_ID,
      text,
      parse_mode: 'Markdown',
      disable_web_page_preview: true,
    });
  }
  // Split on newlines
  const chunks = [];
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
  for (const chunk of chunks) {
    await api('sendMessage', {
      chat_id: CHAT_ID,
      text: chunk,
      parse_mode: 'Markdown',
      disable_web_page_preview: true,
    });
  }
}

function runAsync(cmd, timeoutMs = 150000) {
  return new Promise((resolve) => {
    exec(cmd, { timeout: timeoutMs }, (err, stdout, stderr) => {
      if (err) resolve({ ok: false, output: stderr || stdout || err.message });
      else resolve({ ok: true, output: stdout });
    });
  });
}

async function handleCommand(cmd) {
  const text = cmd.message?.text?.trim();
  const chatId = cmd.message?.chat?.id?.toString();
  if (!text || chatId !== CHAT_ID) return;

  console.log(`[${new Date().toISOString()}] Command: ${text}`);

  if (text === '/brief' || text.startsWith('/brief@')) {
    if (briefRunning) {
      await send('⏳ A brief is already running. Please wait.');
      return;
    }
    briefRunning = true;
    await send('📊 Running morning brief — scanning 6 assets on weekly. This takes ~60s...');

    const result = await runAsync('cd /home/ubuntu/tradingview-mcp-jackson && /usr/bin/node scripts/telegram_brief.js 2>&1');
    briefRunning = false;

    // telegram_brief.js sends its own formatted message — just check it worked
    if (result.output.includes('Brief sent')) {
      console.log('Brief sent successfully');
    } else {
      await send('❌ Brief failed:\n`' + result.output.trim().slice(0, 500) + '`');
    }

  } else if (text === '/scan' || text.startsWith('/scan@')) {
    await send('🔍 Running multi-strategy scan (4 strategies)...');
    const result = await runAsync('cd /home/ubuntu/tradingview-mcp-jackson && /usr/bin/node scripts/multi_strategy_scan.js 2>&1');
    if (result.output.includes('ALERTS SENT')) {
      console.log('Multi-scan alerts sent');
    } else if (result.output.includes('No new signals')) {
      await send('No signals across any strategy right now.');
    } else {
      await send('Scan completed.\n`' + result.output.trim().slice(0, 500) + '`');
    }

  } else if (text === '/strategies' || text.startsWith('/strategies@')) {
    await send('*📋 Active Strategies*\n\n' +
      '🏆 *VDP + Tone Vase v3* (Weekly) — PF 1.95 | WR 56%\n   Swing trading, ATR dynamic zones, bear filter\n   Assets: BTC, ETH, SOL, XRP, LINK, PEPE\n\n' +
      '🏆 *BB + RSI Squeeze v2* (1H) — PF 2.16 | WR 63%\n   Keltner squeeze, band reversion, 2-stage exit\n   Assets: BTC, ETH\n\n' +
      '📈 *RSI Div + VWAP* (1H) — PF 1.08 | Promising\n   RSI divergence, vol spike, ATR trail\n   Assets: BTC, ETH\n\n' +
      '📊 *FVG Scalper v2* (15M) — Needs more data\n   Quality-scored FVGs, session filter, nested MTF\n   Assets: BTC, EUR/USD, XAU/USD');

  } else if (text === '/status' || text.startsWith('/status@')) {
    const result = await runAsync('/home/ubuntu/tradingview-mcp-jackson/scripts/tv_health.sh 2>&1', 30000);
    await send('```\n' + (result.output || 'No output').trim() + '\n```');

  } else if (text.startsWith('/journal') || text.startsWith('/journal@')) {
    await send(formatJournalSummary());

  } else if (text.startsWith('/entry ')) {
    const parts = text.split(/\s+/);
    const symbol = parts[1]?.toUpperCase();
    const price = parseFloat(parts[2]);
    const qty = parseFloat(parts[3]) || 0;
    if (!symbol || isNaN(price)) {
      await send('Usage: `/entry BTCUSD 67000 0.5`');
      return;
    }
    try {
      const trade = logEntry({ symbol, price, quantity: qty });
      await send(`✅ *Entry logged*\n\nID: \`${trade.id}\`\n${trade.side.toUpperCase()} ${trade.symbol} @ $${trade.entry_price.toLocaleString()}\nQty: ${trade.quantity}\n\nTo close: \`/exit ${trade.id} <exit_price>\``);
    } catch (err) {
      await send('❌ ' + err.message);
    }

  } else if (text.startsWith('/exit ')) {
    const parts = text.split(/\s+/);
    const id = parts[1];
    const price = parseFloat(parts[2]);
    if (!id || isNaN(price)) {
      await send('Usage: `/exit T1234567890 72000`');
      return;
    }
    try {
      const trade = logExit({ id, price });
      const emoji = trade.pnl > 0 ? '🟢' : '🔴';
      await send(`${emoji} *Trade closed*\n\n${trade.symbol} ${trade.side.toUpperCase()}\nEntry: $${trade.entry_price.toLocaleString()} → Exit: $${trade.exit_price.toLocaleString()}\nP&L: $${trade.pnl.toFixed(2)} (${trade.pnl_pct}%)`);
    } catch (err) {
      await send('❌ ' + err.message);
    }

  } else if (text === '/start' || text.startsWith('/start@')) {
    await send('*TradingView MCP Bot*\n\n/status — Health check\n/brief — Morning brief\n/scan — Multi-strategy scan\n/strategies — List active strategies\n/journal — Trade journal & stats\n/entry — Log trade: `/entry BTCUSD 67000 0.5`\n/exit — Close trade: `/exit T123 72000`');
  }
}

// Polling loop
console.log(`[${new Date().toISOString()}] Telegram bot started. Listening for commands...`);
await send('🤖 *TradingView Bot Online*\n\n/status — Health check\n/brief — Morning brief\n/scan — Multi-strategy scan\n/strategies — List strategies\n/journal — Trade stats\n/entry — Log trade\n/exit — Close trade');

while (true) {
  try {
    const updates = await api('getUpdates', { offset: lastUpdateId + 1, timeout: 30 });
    if (updates.ok && updates.result?.length > 0) {
      for (const update of updates.result) {
        lastUpdateId = update.update_id;
        // Don't await — let commands run without blocking the poll loop
        handleCommand(update).catch(err => console.error('Command error:', err.message));
      }
    }
  } catch (err) {
    console.error('Poll error:', err.message);
    await new Promise(r => setTimeout(r, 5000));
  }
}

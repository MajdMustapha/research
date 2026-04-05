#!/usr/bin/env node
/**
 * Trade Journal — logs entries, exits, and P&L to a local JSON file.
 * Integrates with the trading tools (PR #12) to read positions.
 * Can be triggered manually or via Telegram /journal command.
 *
 * Data stored in ~/.tradingview-mcp/journal/
 */

import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'fs';
import { resolve, dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { homedir } from 'os';

const __dirname = dirname(fileURLToPath(import.meta.url));
const JOURNAL_DIR = join(homedir(), '.tradingview-mcp', 'journal');
const JOURNAL_FILE = join(JOURNAL_DIR, 'trades.json');

mkdirSync(JOURNAL_DIR, { recursive: true });

function loadJournal() {
  if (existsSync(JOURNAL_FILE)) {
    return JSON.parse(readFileSync(JOURNAL_FILE, 'utf8'));
  }
  return { trades: [], stats: {} };
}

function saveJournal(data) {
  writeFileSync(JOURNAL_FILE, JSON.stringify(data, null, 2));
}

export function logEntry({ symbol, side, price, quantity, timeframe, strategy, notes }) {
  const journal = loadJournal();
  const trade = {
    id: `T${Date.now()}`,
    symbol,
    side: side || 'long',
    entry_price: price,
    quantity: quantity || 0,
    timeframe: timeframe || 'W',
    strategy: strategy || 'VDP + Tone Vase',
    entry_date: new Date().toISOString(),
    exit_price: null,
    exit_date: null,
    pnl: null,
    pnl_pct: null,
    status: 'open',
    notes: notes || '',
  };
  journal.trades.push(trade);
  saveJournal(journal);
  return trade;
}

export function logExit({ id, price, notes }) {
  const journal = loadJournal();
  const trade = journal.trades.find(t => t.id === id);
  if (!trade) throw new Error(`Trade ${id} not found`);
  if (trade.status !== 'open') throw new Error(`Trade ${id} is already ${trade.status}`);

  trade.exit_price = price;
  trade.exit_date = new Date().toISOString();
  trade.status = 'closed';
  trade.notes = trade.notes ? `${trade.notes} | Exit: ${notes || ''}` : (notes || '');

  if (trade.side === 'long') {
    trade.pnl = (price - trade.entry_price) * trade.quantity;
    trade.pnl_pct = ((price - trade.entry_price) / trade.entry_price * 100).toFixed(2);
  } else {
    trade.pnl = (trade.entry_price - price) * trade.quantity;
    trade.pnl_pct = ((trade.entry_price - price) / trade.entry_price * 100).toFixed(2);
  }

  saveJournal(journal);
  return trade;
}

export function getOpenTrades() {
  return loadJournal().trades.filter(t => t.status === 'open');
}

export function getStats() {
  const journal = loadJournal();
  const closed = journal.trades.filter(t => t.status === 'closed');
  const open = journal.trades.filter(t => t.status === 'open');

  const wins = closed.filter(t => t.pnl > 0);
  const losses = closed.filter(t => t.pnl <= 0);
  const totalPnl = closed.reduce((sum, t) => sum + (t.pnl || 0), 0);
  const avgWin = wins.length > 0 ? wins.reduce((s, t) => s + t.pnl, 0) / wins.length : 0;
  const avgLoss = losses.length > 0 ? losses.reduce((s, t) => s + t.pnl, 0) / losses.length : 0;

  return {
    total_trades: journal.trades.length,
    open_trades: open.length,
    closed_trades: closed.length,
    wins: wins.length,
    losses: losses.length,
    win_rate: closed.length > 0 ? (wins.length / closed.length * 100).toFixed(1) + '%' : 'n/a',
    total_pnl: totalPnl.toFixed(2),
    avg_win: avgWin.toFixed(2),
    avg_loss: avgLoss.toFixed(2),
    profit_factor: avgLoss !== 0 ? Math.abs(avgWin / avgLoss).toFixed(2) : 'n/a',
  };
}

export function formatJournalSummary() {
  const stats = getStats();
  const open = getOpenTrades();

  let msg = `*📓 Trade Journal*\n\n`;
  msg += `Trades: ${stats.total_trades} (${stats.open_trades} open, ${stats.closed_trades} closed)\n`;
  msg += `Win Rate: ${stats.win_rate}\n`;
  msg += `Total P&L: $${stats.total_pnl}\n`;
  msg += `Avg Win: $${stats.avg_win} | Avg Loss: $${stats.avg_loss}\n`;
  msg += `Profit Factor: ${stats.profit_factor}\n`;

  if (open.length > 0) {
    msg += `\n*Open Positions:*\n`;
    for (const t of open) {
      msg += `  ${t.side.toUpperCase()} ${t.symbol} @ $${t.entry_price} (${t.entry_date.split('T')[0]})\n`;
    }
  }

  return msg;
}

// CLI usage
if (process.argv[1] && process.argv[1].endsWith('trade_journal.js')) {
  const action = process.argv[2];
  if (action === 'stats') {
    console.log(formatJournalSummary());
  } else if (action === 'open') {
    console.log(JSON.stringify(getOpenTrades(), null, 2));
  } else if (action === 'entry') {
    const [, , , symbol, price, qty] = process.argv;
    const trade = logEntry({ symbol, price: parseFloat(price), quantity: parseFloat(qty || 0) });
    console.log('Trade logged:', JSON.stringify(trade, null, 2));
  } else if (action === 'exit') {
    const [, , , id, price] = process.argv;
    const trade = logExit({ id, price: parseFloat(price) });
    console.log('Trade closed:', JSON.stringify(trade, null, 2));
  } else {
    console.log('Usage: node trade_journal.js [stats|open|entry <symbol> <price> [qty]|exit <id> <price>]');
  }
}

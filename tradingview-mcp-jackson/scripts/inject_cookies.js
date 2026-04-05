#!/usr/bin/env node
/**
 * Inject TradingView cookies into headless Chrome via CDP.
 *
 * Usage:
 *   node inject_cookies.js <cookies.json>
 *
 * The cookies.json file should be an array of cookie objects exported from
 * a browser extension like "Cookie-Editor" or "EditThisCookie".
 *
 * Supported formats:
 *   - Cookie-Editor / EditThisCookie (array of {name, value, domain, path, ...})
 *   - Netscape/curl format (one cookie per line, tab-separated)
 *
 * After injecting, the script reloads the TradingView page so the session takes effect.
 */

import { readFileSync } from 'fs';
import CDP from 'chrome-remote-interface';

const CDP_HOST = 'localhost';
const CDP_PORT = process.env.CDP_PORT || 9222;

async function main() {
  const cookieFile = process.argv[2];
  if (!cookieFile) {
    console.error('Usage: node inject_cookies.js <cookies.json>');
    console.error('');
    console.error('Export cookies from your browser using Cookie-Editor or EditThisCookie,');
    console.error('save as JSON, copy to this machine, then run this script.');
    process.exit(1);
  }

  const raw = readFileSync(cookieFile, 'utf-8').trim();
  let cookies;

  // Try JSON first
  try {
    cookies = JSON.parse(raw);
    if (!Array.isArray(cookies)) {
      throw new Error('JSON must be an array of cookie objects');
    }
  } catch {
    console.error('Failed to parse as JSON. Ensure the file is a JSON array of cookie objects.');
    process.exit(1);
  }

  // Filter to TradingView cookies only
  const tvCookies = cookies.filter(c =>
    c.domain && (c.domain.includes('tradingview.com') || c.domain.includes('.tradingview.com'))
  );

  if (tvCookies.length === 0) {
    console.error('No TradingView cookies found in the file.');
    console.error(`Total cookies in file: ${cookies.length}`);
    console.error('Domains found:', [...new Set(cookies.map(c => c.domain).filter(Boolean))].join(', '));
    process.exit(1);
  }

  console.log(`Found ${tvCookies.length} TradingView cookies (out of ${cookies.length} total)`);

  // Find the TradingView tab
  const targets = await CDP.List({ host: CDP_HOST, port: CDP_PORT });
  const tvTarget = targets.find(t => t.type === 'page' && /tradingview\.com/i.test(t.url));
  if (!tvTarget) {
    console.error('No TradingView page found in Chrome. Is headless Chrome running with a TV chart?');
    process.exit(1);
  }

  const client = await CDP({ host: CDP_HOST, port: CDP_PORT, target: tvTarget.id });

  try {
    await client.Network.enable();

    // Clear existing TradingView cookies first
    await client.Network.clearBrowserCookies();
    console.log('Cleared existing cookies');

    // Inject each cookie
    let injected = 0;
    for (const c of tvCookies) {
      const params = {
        name: c.name,
        value: c.value,
        domain: c.domain,
        path: c.path || '/',
        secure: c.secure ?? c.domain?.includes('.tradingview.com'),
        httpOnly: c.httpOnly ?? false,
        sameSite: c.sameSite || 'Lax',
      };

      // Handle expiration — Cookie-Editor uses expirationDate (epoch seconds)
      if (c.expirationDate) {
        params.expires = c.expirationDate;
      } else if (c.expires && typeof c.expires === 'number') {
        params.expires = c.expires;
      }

      try {
        await client.Network.setCookie(params);
        injected++;
      } catch (err) {
        console.warn(`  Warning: failed to set cookie "${c.name}": ${err.message}`);
      }
    }

    console.log(`Injected ${injected}/${tvCookies.length} cookies`);

    // Reload the page to pick up the session
    console.log('Reloading TradingView...');
    await client.Page.enable();
    await client.Page.reload();

    // Wait for page to load
    await new Promise(resolve => setTimeout(resolve, 5000));

    // Check if logged in by looking for username in page
    const result = await client.Runtime.evaluate({
      expression: `
        (function() {
          try {
            var user = window.TradingViewApi?.user?.() || null;
            var username = document.querySelector('[data-name="header-user-menu-button"]')?.textContent?.trim();
            return { user, username: username || 'unknown', loggedIn: !!username };
          } catch(e) { return { error: e.message }; }
        })()
      `,
      returnByValue: true,
    });

    const status = result.result?.value;
    if (status?.loggedIn) {
      console.log(`\nLogged in as: ${status.username}`);
    } else {
      console.log('\nCookies injected and page reloaded.');
      console.log('If not logged in, your cookies may have expired. Re-export fresh ones from your browser.');
    }
  } finally {
    await client.close();
  }
}

main().catch(err => {
  console.error('Error:', err.message);
  process.exit(1);
});

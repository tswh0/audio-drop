const form = document.getElementById('convert');
const format = form.elements.format;
const quality = form.elements.quality;
const urlInput = form.elements.url;
const pasteButton = form.querySelector('.paste');
const qualityField = quality.closest('label');
const button = document.getElementById('convert-button');
const metaForm = document.getElementById('meta');
const metaButton = metaForm.querySelector('button');
const progress = document.querySelector('.progress');
const bar = progress.querySelector('.bar');
const fill = bar.firstElementChild;
const label = progress.querySelector('.phase-label');
const value = progress.querySelector('.phase-value');
const notice = document.querySelector('.notice');

const STRINGS = {
  de: {
    subtitle: 'YouTube-Link einfügen, Format wählen, fertige Audiodatei herunterladen.',
    link: 'YouTube-Link',
    format: 'Format',
    format_original: 'Original (keine Neukodierung)',
    quality: 'Qualität',
    convert: 'Konvertieren',
    paste: 'Einfügen',
    paste_empty: 'Zwischenablage ist leer.',
    paste_failed: 'Zwischenablage nicht verfügbar – bitte manuell einfügen.',
    starting: 'Wird gestartet …',
    ready_download: 'Fertig. Download startet.',
    ready_edit: 'Fertig. Metadaten prüfen und herunterladen.',
    download_started: 'Download startet …',
    meta_title: 'Titel',
    meta_artist: 'Künstler',
    meta_album: 'Album',
    download: 'Herunterladen',
    job_gone: 'Der Auftrag ist nicht mehr vorhanden. Bitte erneut versuchen.',
    unreachable: 'Server nicht erreichbar.',
    request_failed: 'Anfrage fehlgeschlagen.',
  },
  en: {
    subtitle: 'Paste a YouTube link, pick a format, get a tagged audio file.',
    link: 'YouTube link',
    format: 'Format',
    format_original: 'Original (no re-encode)',
    quality: 'Quality',
    convert: 'Convert',
    paste: 'Paste',
    paste_empty: 'Clipboard is empty.',
    paste_failed: 'Clipboard unavailable — paste manually.',
    starting: 'Starting …',
    ready_download: 'Ready. Your download starts now.',
    ready_edit: 'Ready. Check the metadata and download.',
    download_started: 'Download starts …',
    meta_title: 'Title',
    meta_artist: 'Artist',
    meta_album: 'Album',
    download: 'Download',
    job_gone: 'The job is gone. Try again.',
    unreachable: 'Could not reach the server.',
    request_failed: 'Request failed.',
  },
};

const QUALITIES = {
  original: [],
  mp3: [320, 256, 192, 128],
  m4a: [256, 192, 128],
  opus: [192, 160, 128, 96],
};

let language = 'de';
let expert = false;
let currentFilename = 'audio';

function ui(key) {
  return STRINGS[language][key] || STRINGS.de[key] || key;
}

function applyLanguage() {
  document.documentElement.lang = language;
  document.querySelectorAll('[data-i18n]').forEach((element) => {
    const text = STRINGS[language][element.dataset.i18n];
    if (text) element.textContent = text;
  });
}

function syncQuality() {
  const values = QUALITIES[format.value] || [];
  quality.replaceChildren(
    ...(values.length
      ? values.map((points) => new Option(`${points} kbps`, points))
      : [new Option('Source', '')])
  );
  quality.disabled = values.length === 0;
  qualityField.hidden = !expert || values.length === 0;
}

function setProgress(percent, text) {
  const clamped = Math.max(0, Math.min(100, Math.round(percent || 0)));
  fill.style.width = `${clamped}%`;
  bar.setAttribute('aria-valuenow', clamped);
  value.textContent = `${clamped}%`;
  if (text !== undefined) label.textContent = text;
}

function reset() {
  button.disabled = false;
  progress.hidden = true;
  metaForm.hidden = true;
  fill.style.width = '0%';
}

function showNotice(message) {
  notice.textContent = message;
  notice.hidden = false;
}

function fail(message) {
  reset();
  showNotice(message);
}

function saveBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

async function poll(id) {
  let job;
  try {
    const response = await fetch(`/api/job/${id}`, { cache: 'no-store' });
    if (!response.ok) throw new Error(ui('job_gone'));
    job = await response.json();
  } catch (error) {
    return fail(error.message);
  }

  if (job.status === 'error') return fail(job.error || ui('request_failed'));

  setProgress(job.progress, job.phase || ui('starting'));

  if (job.status === 'ready') {
    currentFilename = job.filename || currentFilename;
    if (expert) {
      metaForm.action = `/api/job/${id}/download`;
      metaForm.elements.title.value = job.title || '';
      metaForm.elements.artist.value = job.artist || '';
      metaForm.elements.album.value = job.album || '';
      metaForm.hidden = false;
      setProgress(100, ui('ready_edit'));
      button.disabled = false;
    } else {
      setProgress(100, ui('ready_download'));
      window.location.assign(`/api/job/${id}/file`);
      setTimeout(reset, 2500);
    }
    return;
  }

  setTimeout(() => poll(id), 700);
}

format.addEventListener('change', syncQuality);
syncQuality();

pasteButton.addEventListener('click', async () => {
  if (!navigator.clipboard?.readText) {
    urlInput.focus();
    return showNotice(ui('paste_failed'));
  }

  try {
    const text = (await navigator.clipboard.readText()).trim();
    if (!text) return showNotice(ui('paste_empty'));
    urlInput.value = text;
    notice.hidden = true;
    urlInput.focus();
  } catch {
    urlInput.focus();
    showNotice(ui('paste_failed'));
  }
});

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  notice.hidden = true;
  metaForm.hidden = true;
  progress.hidden = false;
  setProgress(0, ui('starting'));
  button.disabled = true;

  let response;
  let data;
  try {
    response = await fetch('/api/convert', { method: 'POST', body: new FormData(form) });
    data = await response.json();
  } catch {
    return fail(ui('unreachable'));
  }

  if (!response.ok) return fail(data.error || ui('request_failed'));
  poll(data.id);
});

metaForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  metaButton.disabled = true;
  setProgress(100, ui('download_started'));

  let response;
  try {
    response = await fetch(metaForm.action, { method: 'POST', body: new FormData(metaForm) });
  } catch {
    metaButton.disabled = false;
    return fail(ui('unreachable'));
  }

  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    metaButton.disabled = false;
    return fail(data.error || ui('request_failed'));
  }

  const disposition = response.headers.get('Content-Disposition') || '';
  const utf8 = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  const plain = disposition.match(/filename="([^"]+)"/i);
  const name = utf8 ? decodeURIComponent(utf8[1]) : plain ? plain[1] : currentFilename;

  saveBlob(await response.blob(), name);
  metaButton.disabled = false;
});

(async () => {
  try {
    const response = await fetch('/api/config', { cache: 'no-store' });
    if (response.ok) {
      const config = await response.json();
      expert = Boolean(config.expert);
      language = config.language === 'en' ? 'en' : 'de';
    }
  } catch {
    // keep the German defaults
  }
  applyLanguage();
  syncQuality();
})();

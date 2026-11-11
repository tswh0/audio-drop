const form = document.getElementById('convert');
const format = form.elements.format;
const quality = form.elements.quality;
const button = form.querySelector('button');
const progress = document.querySelector('.progress');
const bar = progress.querySelector('.bar');
const fill = bar.firstElementChild;
const label = progress.querySelector('.phase-label');
const value = progress.querySelector('.phase-value');
const notice = document.querySelector('.notice');

const QUALITIES = {
  original: [],
  mp3: [320, 256, 192, 128],
  m4a: [256, 192, 128],
  opus: [192, 160, 128, 96],
};

function syncQuality() {
  const values = QUALITIES[format.value] || [];
  quality.replaceChildren(
    ...(values.length
      ? values.map((points) => new Option(`${points} kbps`, points))
      : [new Option('Source', '')])
  );
  quality.disabled = values.length === 0;
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
  fill.style.width = '0%';
}

function fail(message) {
  reset();
  notice.textContent = message;
  notice.hidden = false;
}

async function poll(id) {
  let job;
  try {
    const response = await fetch(`/api/job/${id}`, { cache: 'no-store' });
    if (!response.ok) throw new Error('The job is gone. Try again.');
    job = await response.json();
  } catch (error) {
    return fail(error.message);
  }

  if (job.status === 'error') return fail(job.error || 'Conversion failed.');

  setProgress(job.progress, job.phase || 'Working...');

  if (job.status === 'ready') {
    setProgress(100, 'Ready. Your download starts now.');
    window.location.assign(`/api/job/${id}/file`);
    setTimeout(reset, 2500);
    return;
  }

  setTimeout(() => poll(id), 700);
}

format.addEventListener('change', syncQuality);
syncQuality();

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  notice.hidden = true;
  progress.hidden = false;
  setProgress(0, 'Starting...');
  button.disabled = true;

  let response;
  let data;
  try {
    response = await fetch('/api/convert', { method: 'POST', body: new FormData(form) });
    data = await response.json();
  } catch {
    return fail('Could not reach the server.');
  }

  if (!response.ok) return fail(data.error || 'Request failed.');
  poll(data.id);
});

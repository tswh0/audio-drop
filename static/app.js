const form = document.getElementById('convert');
const format = form.elements.format;
const quality = form.elements.quality;
const button = form.querySelector('button');
const bar = document.querySelector('.bar');
const fill = bar.firstElementChild;
const phase = document.querySelector('.phase');
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
      ? values.map((value) => new Option(`${value} kbps`, value))
      : [new Option('Source', '')])
  );
  quality.disabled = values.length === 0;
}

function reset() {
  button.disabled = false;
  bar.hidden = true;
  phase.hidden = true;
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

  phase.textContent = job.phase || 'Working...';
  fill.style.width = `${job.progress || 0}%`;

  if (job.status === 'ready') {
    phase.textContent = 'Ready. Your download starts now.';
    fill.style.width = '100%';
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
  bar.hidden = false;
  phase.hidden = false;
  phase.textContent = 'Starting...';
  fill.style.width = '0%';
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

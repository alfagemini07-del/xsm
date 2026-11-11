const clockFormat = new Intl.DateTimeFormat('uz-UZ', {
  timeZone: 'Asia/Tashkent', hour: '2-digit', minute: '2-digit', second: '2-digit'
});
const dateFormat = new Intl.DateTimeFormat('uz-UZ', {
  timeZone: 'Asia/Tashkent', day: '2-digit', month: '2-digit', year: 'numeric',
  hour: '2-digit', minute: '2-digit', second: '2-digit'
});

const clock = document.getElementById('clock');
const connection = document.getElementById('connection');
const updated = document.getElementById('updated');
const panels = new Map([...document.querySelectorAll('[data-purpose]')].map(panel => [panel.dataset.purpose, panel]));
const signatures = new Map();
let timer;
let activeRequest;

function updateClock() {
  clock.textContent = clockFormat.format(new Date());
}
setInterval(updateClock, 1000);
updateClock();

document.getElementById('fullscreen').addEventListener('click', async () => {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await document.documentElement.requestFullscreen();
  } catch {
    connection.className = 'connection error';
    connection.querySelector('span').textContent = 'Brauzerdan to‘liq ekranni tanlang';
  }
});

function queueItem(row) {
  const item = document.createElement('li');
  if (row.position <= 5) item.classList.add('priority');
  const place = document.createElement('span');
  place.className = 'position';
  place.textContent = row.position;
  const details = document.createElement('div');
  details.className = 'vehicle';
  const plate = document.createElement('strong');
  plate.textContent = row.plate;
  const ticket = document.createElement('small');
  ticket.textContent = row.queue_no ? `Navbat: ${row.queue_no}` : 'Navbat raqami kutilmoqda';
  details.append(plate, ticket);
  item.append(place, details);
  return item;
}

function render(data) {
  for (const [purpose, panel] of panels) {
    const rows = data.queues.filter(row => row.purpose === purpose);
    const total = rows.length ? rows[0].total : 0;
    panel.querySelector('.queue-count span').textContent = total;
    panel.classList.toggle('is-empty', rows.length === 0);
    const signature = rows.map(row => `${row.id}:${row.position}:${row.plate}`).join('|');
    if (signatures.get(purpose) !== signature) {
      const list = panel.querySelector('.queue-list');
      list.replaceChildren(...rows.map(queueItem));
      list.classList.remove('shifted');
      void list.offsetWidth;
      list.classList.add('shifted');
      signatures.set(purpose, signature);
    }
  }
  connection.className = 'connection online';
  connection.querySelector('span').textContent = 'Jonli';
  updated.textContent = `Yangilandi: ${dateFormat.format(new Date(data.at))}`;
}

async function refresh() {
  clearTimeout(timer);
  activeRequest?.abort();
  if (document.hidden) return;
  const controller = new AbortController();
  activeRequest = controller;
  const timeout = setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch('/api/tablo', {cache: 'no-store', signal: controller.signal});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    render(await response.json());
  } catch (error) {
    if (controller === activeRequest) {
      connection.className = 'connection error';
      connection.querySelector('span').textContent = 'Aloqa uzildi';
      updated.textContent = 'Oxirgi olingan ro‘yxat ekranda saqlanmoqda';
    }
  } finally {
    clearTimeout(timeout);
    if (controller === activeRequest && !document.hidden) timer = setTimeout(refresh, 3000);
  }
}

document.addEventListener('visibilitychange', refresh);
refresh();

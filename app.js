const moneyValue = n => new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 }).format(n);
const money = n => moneyValue(n) + ' ₸';
const dayInput = document.querySelector('#day');
dayInput.value = '2026-10-01';
let refreshRequest = 0;

const tripTime = value => value.slice(11, 16);
const two = value => String(value).padStart(2, '0');
function readableDate(value) {
  const [year, month, day] = value.split('-').map(Number);
  const label = new Intl.DateTimeFormat('ru-RU', { weekday: 'long', day: 'numeric', month: 'long' })
    .format(new Date(year, month - 1, day));
  return label[0].toLocaleUpperCase('ru-RU') + label.slice(1);
}
const tripWord = n => {
  const lastTwo = n % 100;
  if (lastTwo >= 11 && lastTwo <= 14) return 'поездок';
  if (n % 10 === 1) return 'поездка';
  if (n % 10 >= 2 && n % 10 <= 4) return 'поездки';
  return 'поездок';
};

function setFormDate(day) {
  document.querySelector('#start').value = `${day}T08:00`;
  document.querySelector('#end').value = `${day}T08:30`;
}

function asLocalIso(value) {
  const date = new Date(value);
  const offset = -date.getTimezoneOffset();
  const sign = offset >= 0 ? '+' : '-';
  const hours = two(Math.floor(Math.abs(offset) / 60));
  const minutes = two(Math.abs(offset) % 60);
  return `${date.getFullYear()}-${two(date.getMonth() + 1)}-${two(date.getDate())}T${two(date.getHours())}:${two(date.getMinutes())}:00${sign}${hours}:${minutes}`;
}

setFormDate(dayInput.value);

function shiftDay(offset) {
  const selected = new Date(`${dayInput.value}T12:00:00`);
  selected.setDate(selected.getDate() + offset);
  dayInput.value = `${selected.getFullYear()}-${two(selected.getMonth() + 1)}-${two(selected.getDate())}`;
  refresh();
}

async function refresh() {
  const requestId = ++refreshRequest;
  document.querySelector('#summary').innerHTML = '<div class="empty">Загрузка…</div>';
  try {
    const response = await fetch(`/api/trips?date=${encodeURIComponent(dayInput.value)}`);
    const data = await response.json();
    if (requestId !== refreshRequest) return;
    if (!response.ok) throw new Error(data.error || 'Не удалось загрузить данные');
    const s = data.summary;
    const cashShare = s.revenue ? Math.round(s.cash / s.revenue * 100) : 0;
    const cardShare = s.revenue ? 100 - cashShare : 0;
    document.querySelector('#summary').innerHTML = `
      <div class="net-panel"><div><div class="eyebrow">НА РУКИ</div><div class="net">${moneyValue(s.net)}<small> ₸</small></div><div class="net-caption">${readableDate(dayInput.value)} · после комиссии</div></div><div class="hero-mark" aria-hidden="true">₸</div></div>
      <div class="stats">
        <div class="metric"><div class="eyebrow">Выручка</div><div class="value">${money(s.revenue)}</div></div>
        <div class="metric"><div class="eyebrow">Комиссия</div><div class="value negative">−${money(s.commission)}</div></div>
        <div class="metric"><div class="eyebrow">Поездки</div><div class="value">${s.trip_count}</div></div>
      </div>
      <div class="payment-block">
        <div class="payment-heading"><span>Способы оплаты</span><strong>Наличные и карта</strong></div>
        <div class="payrow">
          <div class="pay-item"><div class="pay-label"><i class="dot cash"></i><span>Наличные</span></div><strong>${money(s.cash)}</strong><small>${cashShare}% выручки</small></div>
          <div class="pay-item"><div class="pay-label"><i class="dot bank-card"></i><span>Карта</span></div><strong>${money(s.card)}</strong><small>${cardShare}% выручки</small></div>
        </div>
        <div class="bar"><span style="width:${cashShare}%"></span><span style="width:${cardShare}%"></span></div>
      </div>`;
    document.querySelector('#count').textContent = `${s.trip_count} ${tripWord(s.trip_count)}`;
    const list = document.querySelector('#trips');
    list.innerHTML = data.trips.length ? data.trips.map(t => `
      <article class="trip"><div class="time-range"><span>${tripTime(t.start)}</span><span class="time-arrow" aria-hidden="true">→</span><span>${tripTime(t.end)}</span></div><div class="details"><div class="payment ${t.payment === 'cash' ? 'cash' : 'bank-card'}"><i class="dot ${t.payment === 'cash' ? 'cash' : 'bank-card'}"></i>${t.payment === 'cash' ? 'Наличные' : 'Карта'}</div><div class="commission">Комиссия ${money(t.commission)}</div></div><div class="amount">${money(t.amount)}</div></article>`).join('') : '<div class="empty">За этот день поездок нет</div>';
  } catch (error) {
    if (requestId !== refreshRequest) return;
    document.querySelector('#summary').innerHTML = `<div class="error">${error.message}</div>`;
    document.querySelector('#trips').innerHTML = '<div class="empty">Не удалось загрузить поездки</div>';
  }
}

dayInput.addEventListener('change', () => { setFormDate(dayInput.value); refresh(); });
document.querySelector('#previous').addEventListener('click', () => shiftDay(-1));
document.querySelector('#next').addEventListener('click', () => shiftDay(1));

let pendingTripId = crypto.randomUUID();

function idForRetry(payload) {
  const fingerprint = JSON.stringify([payload.start, payload.end, payload.amount, payload.payment, payload.commission]);
  try {
    const previous = JSON.parse(sessionStorage.getItem('pending-trip-retry') || 'null');
    if (previous && previous.fingerprint === fingerprint) return previous.id;
    const id = crypto.randomUUID();
    sessionStorage.setItem('pending-trip-retry', JSON.stringify({ id, fingerprint }));
    return id;
  } catch {
    return pendingTripId;
  }
}

document.querySelector('#trip-form').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = {
    start: asLocalIso(form.elements.start.value),
    end: asLocalIso(form.elements.end.value),
    amount: Number(form.elements.amount.value),
    payment: form.elements.payment.value,
    commission: Number(form.elements.commission.value)
  };
  payload.id = idForRetry(payload);
  const button = document.querySelector('#save-trip');
  const message = document.querySelector('#form-message');
  button.disabled = true;
  message.className = 'form-message';
  message.textContent = 'Сохраняем…';
  try {
    const response = await fetch('/api/trips', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Не удалось сохранить поездку');
    sessionStorage.removeItem('pending-trip-retry');
    pendingTripId = crypto.randomUUID();
    message.className = 'form-message success';
    message.textContent = result.created ? 'Поездка добавлена' : 'Эта поездка уже была сохранена';
    form.elements.amount.value = '';
    form.elements.commission.value = '';
    if (dayInput.value !== payload.start.slice(0, 10)) dayInput.value = payload.start.slice(0, 10);
    await refresh();
  } catch (error) {
    message.className = 'form-message error';
    message.textContent = `${error.message}. Повторите отправку, чтобы безопасно проверить результат.`;
  } finally {
    button.disabled = false;
  }
});

refresh();

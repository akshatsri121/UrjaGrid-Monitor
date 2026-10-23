'use strict';
const $ = id => document.getElementById(id);
const number = (value, digits = 2) => value == null ? '—' : Number(value).toFixed(digits);
const date = ts => ts == null ? 'Never' : new Date(ts * 1000).toLocaleString();
const pretty = value => (value || '').replaceAll('_', ' ');
let config, liveData, historyData, busy = false, liveBusy = false, historyRequest = 0;
let activityLimit = 12;
const pendingLoads = new Set();

// Build the additional voltage chart with DOM APIs (no HTML string injection).
const voltagePanel = document.createElement('div');
voltagePanel.className = 'panel chart-panel voltage-panel';
const voltageHeading = document.createElement('h3');
voltageHeading.textContent = 'Battery-feed voltage / V';
const voltageChart = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
voltageChart.id = 'voltageChart';
voltageChart.setAttribute('viewBox', '0 0 1000 260');
voltageChart.setAttribute('role', 'img');
voltageChart.setAttribute('aria-label', 'Battery-feed voltage history');
voltagePanel.append(voltageHeading, voltageChart);
document.querySelector('.history-charts').append(voltagePanel);
const historyStats = document.createElement('p');
historyStats.className = 'fine';
$('historyRange').after(historyStats);

async function api(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12000);
  try {
    const response = await fetch(path, {...options, credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json', ...options.headers}, signal: controller.signal});
    const data = await response.json();
    if (!response.ok) {
      if (response.status === 401) showLogin();
      throw new Error(typeof data.detail === 'string' ? data.detail : 'Request rejected; check the submitted values.');
    }
    return data;
  } finally { clearTimeout(timeout); }
}

function notice(message) {
  $('notice').textContent = message || '';
  $('notice').hidden = !message;
}

function showLogin(message = '') {
  liveData = null;
  $('dashboard').hidden = true;
  $('loginPanel').hidden = false;
  $('logout').hidden = true;
  $('loginMessage').textContent = message;
  $('connection').textContent = 'Sign in required';
}

function showDashboard() {
  $('dashboard').hidden = false;
  $('loginPanel').hidden = true;
  $('logout').hidden = config.auth === 'demo';
  notice('');
}

function onlineNow() {
  return Boolean(liveData?.online && liveData.last_seen != null && Date.now() / 1000 - liveData.last_seen <= 20);
}

function updateControls() {
  const online = onlineNow();
  const modePending = pendingLoads.has('automation') || liveData?.commands?.some(c => c.load === 'automation' && ['queued', 'delivered'].includes(c.status));
  for (const load of ['bulb', 'fan']) {
    const branch = liveData?.latest?.[load];
    const pending = pendingLoads.has(load) || liveData?.commands?.some(c => c.load === load && ['queued', 'delivered'].includes(c.status));
    $(load + 'On').disabled = !online || !branch || Boolean(branch.blocked_reason) || branch.on || pending || modePending;
    // OFF stays available while an ON is pending, allowing it to supersede the request.
    $(load + 'Off').disabled = !online || !branch;
  }
  const automatic = liveData?.latest?.automatic_protection;
  const supported = typeof automatic === 'boolean' && Boolean(liveData?.latest?.mode_revision);
  $('automationSwitch').disabled = !online || !supported || modePending;
  $('automationSwitch').setAttribute('aria-checked', automatic === false ? 'false' : 'true');
  $('automationValue').textContent = !supported ? 'Unavailable' : automatic ? 'ON · Automatic' : 'OFF · Manual';
  $('manualWarning').hidden = automatic !== false;
  $('policyTitle').textContent = automatic === false ? 'Manual battery override active' : 'Battery-first fan protection';
  $('policyDescription').textContent = automatic === false ?
    'Low-voltage and fan SOC rules are bypassed. Sensor, temperature, current and overvoltage checks remain.' :
    'Automatic mode: fan locks below 50% estimated SOC. Recovery at 55%; a fresh ON request is required.';
  $('automationSwitch').closest('.automation-panel').classList.toggle('manual-mode', automatic === false);
  $('automationStatus').textContent = !liveData?.latest ? 'Waiting for the ESP32\'s confirmed mode.' :
    !supported ? 'Update and upload the ESP32 firmware to use this switch.' :
    !online ? 'Device offline. The last confirmed mode is shown; no change can be sent.' :
    modePending ? 'Mode change pending. Waiting for ESP32 confirmation; new ON requests are paused.' :
    automatic ? 'ESP32 confirmed Automatic ON. Battery rules apply locally, even without Wi-Fi.' :
    'ESP32 confirmed Automatic OFF. Battery rules are bypassed; other checks remain active.';
  if (!pendingLoads.has('automation')) {
    const modeRequest = liveData?.commands?.find(c => c.load === 'automation');
    $('automationCommand').textContent = modeRequest ? 'Last mode request: ' + modeRequest.status + (modeRequest.reason ? ' · ' + pretty(modeRequest.reason) : '') : '';
  }
  if (liveData) {
    $('connection').textContent = config.mode === 'demo' ? 'Simulation' : online ? 'Device online' : 'Device offline';
    $('connection').className = 'badge ' + (online ? 'on' : 'offline');
    $('updatedAt').textContent = liveData.last_seen == null ? 'No telemetry yet' : 'Last received ' + date(liveData.last_seen);
  }
}

function renderLive() {
  const s = liveData.latest;
  $('deviceLine').textContent = config.mode === 'demo' ? 'Explore example readings and simulated controls.' : 'Live branch measurements · Local battery-first protection';
  $('deviceName').textContent = liveData.device_id;
  $('socValue').replaceChildren(document.createTextNode(number(s?.soc_estimate, 0)), Object.assign(document.createElement('span'), {textContent: '%'}));
  $('batteryFill').setAttribute('width', s?.soc_estimate == null ? 0 : Math.max(0, Math.min(100, s.soc_estimate)) * 1.09);
  $('batteryVoltage').textContent = number(s?.battery_voltage) + ' V';
  const total = s?.bulb.valid && s?.fan.valid ? s.bulb.power_w + s.fan.power_w : null;
  $('totalPower').textContent = number(total) + ' W';
  $('temperature').textContent = number(s?.temperature_c, 1) + ' °C';
  for (const load of ['bulb', 'fan']) {
    const b = s?.[load];
    $(load + 'Voltage').textContent = number(b?.voltage_v) + ' V';
    $(load + 'Current').textContent = number(b?.current_a, 3) + ' A';
    $(load + 'Power').textContent = number(b?.power_w) + ' W';
    $(load + 'State').textContent = !b ? 'Unknown' : b.on ? 'Reported ON' : b.blocked_reason ? 'Locked OFF' : 'Reported OFF';
    $(load + 'State').className = 'badge ' + (b?.on ? 'on' : b?.blocked_reason ? 'locked' : '');
    $(load + 'Reason').textContent = !b ? 'Waiting for telemetry.' : b.blocked_reason ? pretty(b.blocked_reason) : !b.valid ? 'Sensor unavailable; readings are not assumed zero.' : 'Sensor available · No reported lockout';
    $(load + 'Reason').className = 'reason ' + (b?.blocked_reason || !b?.valid ? 'warning' : '');
    const command = liveData.commands.find(c => c.load === load);
    if (!pendingLoads.has(load)) $(load + 'Command').textContent = command ? 'Last request: ' + command.status : '';
  }
  $('commands').replaceChildren();
  for (const c of liveData.commands) {
    const row = document.createElement('tr');
    for (const value of [new Date(c.created_at * 1000).toLocaleTimeString(), `${c.load === 'automation' ? 'Automatic protection' : c.load} ${c.on ? 'ON' : 'OFF'}`, c.status]) {
      const cell = document.createElement('td'); cell.textContent = value; row.append(cell);
    }
    if (c.reason) { const detail = document.createElement('span'); detail.className = 'command-detail'; detail.textContent = c.reason; row.lastChild.append(detail); }
    $('commands').append(row);
  }
  if (!liveData.commands.length) {
    const row = document.createElement('tr'), cell = document.createElement('td');
    cell.colSpan = 3; cell.textContent = 'No commands yet.'; row.append(cell); $('commands').append(row);
  }
  updateControls();
}

async function refreshLive() {
  if (liveBusy || $('dashboard').hidden) return;
  liveBusy = true;
  try {
    liveData = await api('/api/live');
    renderLive();
  } catch (error) {
    if ($('dashboard').hidden) return;
    if (liveData) liveData.online = false;
    updateControls();
    $('connection').textContent = 'Connection unavailable';
    notice(error.name === 'AbortError' ? 'Server request timed out. Controls disabled until telemetry returns.' : error.message);
  } finally { liveBusy = false; }
}

function drawChart(id, points, keys, colors, start, end, fixedRange = null) {
  const svg = $(id), ns = 'http://www.w3.org/2000/svg';
  svg.replaceChildren();
  const [, , w, h] = svg.getAttribute('viewBox').split(' ').map(Number);
  const add = (tag, attrs, value) => {const n = document.createElementNS(ns, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); if (value != null) n.textContent = value; svg.append(n); return n;};
  const values = points.flatMap(p => keys.map(k => p[k])).filter(v => v != null);
  if (!values.length) { add('text', {x: w / 2, y: h / 2, fill: '#9eafaa', 'font-size': 13, 'text-anchor': 'middle'}, 'No measurements in this period'); return; }
  const low = fixedRange ? fixedRange[0] : Math.min(0, ...values), high = fixedRange ? fixedRange[1] : Math.max(1, ...values) * 1.1;
  const x = t => 46 + (t - start) / (end - start) * (w - 60), y = v => h - 35 - (v - low) / (high - low || 1) * (h - 60);
  for (let i = 0; i <= 4; i++) { const v = low + (high - low) * i / 4; add('line', {x1: 46, x2: w - 14, y1: y(v), y2: y(v), stroke: '#2a3c3c', 'stroke-dasharray': '3 5'}); add('text', {x: 2, y: y(v) + 4, fill: '#9eafaa', 'font-size': 11}, v.toFixed(1)); }
  keys.forEach((key, index) => {
    let path = '', last = null;
    points.forEach(p => {
      if (p[key] == null) { last = null; return; }
      path += (!last || p.ts - last.ts > (end - start) / 720 * 1.6 ? 'M' : 'L') + x(p.ts) + ',' + y(p[key]) + ' ';
      last = p;
    });
    add('path', {d: path, stroke: colors[index], 'stroke-width': 2, fill: 'none', 'stroke-linejoin': 'round'});
    points.forEach(p => {if (p[key] != null) {const dot = add('circle', {cx: x(p.ts), cy: y(p[key]), r: 1.4, fill: colors[index]}); const title = document.createElementNS(ns, 'title'); title.textContent = date(p.ts) + ' · ' + key + ': ' + number(p[key]); dot.append(title);}});
  });
  add('text', {x: 46, y: h - 5, fill: '#9eafaa', 'font-size': 10}, new Date(start * 1000).toLocaleDateString());
  add('text', {x: w - 14, y: h - 5, fill: '#9eafaa', 'font-size': 10, 'text-anchor': 'end'}, new Date(end * 1000).toLocaleString());
}

function renderActivity() {
  $('timeline').replaceChildren();
  for (const event of historyData.activity.slice(0, activityLimit)) {
    const row = document.createElement('li'); row.textContent = event.message;
    const when = document.createElement('time'); when.textContent = date(event.ts) + ' · ' + event.kind; row.append(when); $('timeline').append(row);
  }
  if (!historyData.activity.length) {const li = document.createElement('li'); li.textContent = 'No events or notes in this period.'; $('timeline').append(li);}
  $('moreActivity').hidden = historyData.activity.length <= activityLimit;
}

async function refreshHistory() {
  if ($('dashboard').hidden) return;
  const request = ++historyRequest, period = $('period').value;
  try {
    const result = await api('/api/history?period=' + period);
    if (request !== historyRequest) return;
    historyData = result;
    const d = historyData;
    $('historyRange').textContent = `${date(d.start)} — ${date(d.end)} · ${d.samples.toLocaleString()} archived samples`;
    historyStats.textContent = `Peak sampled bulb: ${number(d.summary.peak_bulb_w)} W · Fan: ${number(d.summary.peak_fan_w)} W · Maximum temperature: ${number(d.summary.maximum_temperature_c, 1)} °C`;
    for (const load of ['bulb', 'fan']) {
      $(load + 'Energy').textContent = number(d.summary[load + '_wh']) + ' Wh';
      $(load + 'Coverage').textContent = number(d.summary[load + '_coverage_percent'], 1) + '% measured-time coverage';
    }
    $('loadEnergy').textContent = number(d.summary.load_wh) + ' Wh';
    drawChart('powerChart', d.points, ['bulb_power_w', 'fan_power_w'], ['#e4c17c', '#83d7ba'], d.start, d.end);
    drawChart('socChart', d.points, ['soc_estimate'], ['#83d7ba'], d.start, d.end, [0, 100]);
    drawChart('tempChart', d.points, ['temperature_c'], ['#a6bded'], d.start, d.end);
    const volts = d.points.map(p => p.battery_voltage).filter(v => v != null);
    const voltageRange = volts.length ? [Math.max(0, Math.min(...volts) - .1), Math.max(...volts) + .1] : null;
    drawChart('voltageChart', d.points, ['battery_voltage'], ['#a6bded'], d.start, d.end, voltageRange);
    renderActivity();
  } catch (error) { if (!$('dashboard').hidden) notice('History: ' + error.message); }
}

async function command(load, on) {
  if (pendingLoads.has(load) || !onlineNow()) return;
  pendingLoads.add(load); updateControls();
  $(load + 'Command').textContent = 'Sending request…';
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
  const hex = [...bytes].map(v => v.toString(16).padStart(2, '0')).join('');
  const requestId = [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join('-');
  try {
    const result = await api('/api/commands', {method: 'POST', body: JSON.stringify({request_id: requestId, load, on})});
    $(load + 'Command').textContent = 'Request ' + result.status;
    notice(config.mode === 'demo' ? 'Simulation request queued. No physical hardware is controlled.' :
      load === 'automation' ? 'Mode change queued. The switch updates only after ESP32 confirmation.' : 'Request queued; waiting for ESP32 acknowledgement.');
  } catch (error) { $(load + 'Command').textContent = 'Request not confirmed'; notice(error.message); }
  finally { pendingLoads.delete(load); await refreshLive(); updateControls(); }
}

$('loginForm').addEventListener('submit', async event => {
  event.preventDefault(); if (busy) return; busy = true;
  $('loginMessage').textContent = 'Signing in…';
  try {await api('/api/login', {method: 'POST', body: JSON.stringify({email: $('email').value, password: $('password').value})}); $('password').value = ''; showDashboard(); await refreshLive(); await refreshHistory();}
  catch (error) {$('loginMessage').textContent = error.message;}
  finally {busy = false;}
});
$('logout').addEventListener('click', async () => {try {await api('/api/logout', {method: 'POST'}); showLogin();} catch (error) {notice(error.message);}});
$('automationSwitch').addEventListener('click', () => {
  const automatic = liveData?.latest?.automatic_protection;
  if (typeof automatic === 'boolean' && !$('automationSwitch').disabled) command('automation', !automatic);
});
for (const load of ['bulb', 'fan']) {$(load + 'On').addEventListener('click', () => command(load, true)); $(load + 'Off').addEventListener('click', () => command(load, false));}
$('period').addEventListener('change', () => {activityLimit = 12; $('export').href = '/api/export.csv?period=' + $('period').value; refreshHistory();});
$('print').addEventListener('click', () => window.print());
$('moreActivity').addEventListener('click', () => {activityLimit = 200; renderActivity();});
$('noteForm').addEventListener('submit', async event => {event.preventDefault(); try {await api('/api/notes', {method: 'POST', body: JSON.stringify({text: $('noteText').value})}); $('noteText').value = ''; $('noteMessage').textContent = 'Saved'; await refreshHistory();} catch (error) {$('noteMessage').textContent = error.message;}});
document.querySelectorAll('[data-demo-soc]').forEach(button => button.addEventListener('click', async () => {try {await api('/api/demo/soc', {method: 'POST', body: JSON.stringify({soc: Number(button.dataset.demoSoc)})}); await refreshLive(); await refreshHistory(); notice('Simulated battery estimate updated.');} catch (error) {notice(error.message);}}));

(async function start() {
  try {
    config = await api('/api/config');
    $('demoBanner').hidden = config.mode !== 'demo'; $('demoControls').hidden = config.mode !== 'demo';
    $('emailLabel').hidden = config.auth !== 'supabase'; $('email').required = config.auth === 'supabase';
    if (config.auth === 'unconfigured') {showLogin('Server setup required. Follow docs/SOFTWARE_SETUP.md to configure authentication.'); $('loginForm').hidden = true; return;}
    showDashboard(); await refreshLive(); await refreshHistory();
  } catch (error) {showLogin('Cannot reach the backend: ' + error.message);}
})();
setInterval(refreshLive, 5000);
setInterval(refreshHistory, 60000);
setInterval(updateControls, 1000);

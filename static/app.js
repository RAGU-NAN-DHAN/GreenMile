/* GreenMile dashboard: Operations map, vehicle detail and strategy comparison.
   Talks to the FastAPI backend (/load, /state, /event). No business logic lives here. */
(() => {
  const $ = id => document.getElementById(id);
  const STRATEGY_NAMES = {replan: 'OR-Tools re-plan', cheapest_insertion: 'Cheapest insertion', do_nothing: 'Do nothing'};
  const STATUS_COLOURS = {pending: '#e8b04a', servicing: '#b7a4ff', served: '#7bb15d', unassigned: '#e5604d'};
  const ACCENT = '#cede62', OLIVE = '#8a852b';

  let data = null;            // latest /state response
  let t = 0;                  // displayed simulation time
  let selected = 0;           // selected vehicle id
  let selectedOrder = null;   // selected order id in the vehicle view
  let filter = 'all';
  let placing = false;
  let timer = null;
  let controller = null;
  let viewRev = 0;            // bump to reset map zoom
  let view = 'ops';
  let mode = 'street';        // 'street' (Leaflet, grid placed on a real map) or 'plain' (Plotly x/y)
  let cardOpen = false;       // the truck card opens over the map only after a truck is clicked
  // Idle trucks wait at the depot (stacked on one spot), so only working trucks are drawn as icons.
  const onMap = v => (v.activity || {}).kind !== 'idle' || filter === 'idle';
  let lmap = null, llayer = null, vlayer = null, fitted = '', trucksSession = '';
  const trucks = new Map();   // truck id -> {marker, heading}; markers persist so they glide while playing
  // The backend places the Solomon grid on the map (data.geo): the point (cx, cy) sits at (lat0, lng0), place_m metres per unit.
  function toLatLng(x, y) {
    const g = data.geo;
    return [g.lat0 + (y - g.cy) * g.place_m / 111320,
            g.lng0 + (x - g.cx) * g.place_m / (111320 * Math.cos(g.lat0 * Math.PI / 180))];
  }
  function fromLatLng(latlng) {
    const g = data.geo;
    return [g.cx + (latlng.lng - g.lng0) * 111320 * Math.cos(g.lat0 * Math.PI / 180) / g.place_m,
            g.cy + (latlng.lat - g.lat0) * 111320 / g.place_m];
  }
  let anchor = null;          // exact depot location chosen on the map: {lat, lng}
  let settingDepot = false;

  // ---------- helpers ----------
  async function api(path, options = {}) {
    const response = await fetch(path, options);
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = body.detail;
      throw new Error(typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map(d => d.msg).join('; ') : 'Request failed');
    }
    return body;
  }
  function say(text, error = false) {
    const el = $('message');
    el.textContent = text;
    el.classList.toggle('error', error);
  }
  function busy(on, label = 'Planning routes…') {
    $('busy').hidden = !on;
    $('busy-label').textContent = label;
  }
  const fmt = (n, d = 1) => Number(n).toFixed(d);
  // Time is in minutes (Solomon time units read as minutes). Distance is km in road mode, Solomon units otherwise.
  const dist = u => data && data.routing === 'road' ? `${(u * data.geo.metres_per_unit / 1000).toFixed(1)} km` : `${fmt(u)} units`;
  const group = status => status === 'broken' ? 'broken' : status === 'idle' ? 'idle' : 'active';
  const current = () => data.strategies[$('strategy').value];
  const vehicleById = id => current().vehicles.find(v => v.id === id);
  const routeLength = (route, key) => route[key].reduce((sum, [a, b]) => sum + Math.hypot(b[0] - a[0], b[1] - a[1]), 0);
  const pill = (text, kind = '') => `<i class="pill ${kind}">${text}</i>`;
  const statusKind = s => ({pending: 'warn', servicing: 'vio', served: 'ok', unassigned: 'bad'}[s] || '');

  // ---------- state ----------
  async function fetchState(time) {
    if (controller) controller.abort();
    controller = new AbortController();
    try {
      accept(await api(`/state?t=${encodeURIComponent(time)}`, {signal: controller.signal}));
    } catch (e) {
      if (e.name !== 'AbortError') say(e.message, true);
    }
  }

  function accept(next) {
    data = next;
    t = data.time;
    const slider = $('time-slider');
    slider.max = data.max_time;
    slider.value = t;
    slider.disabled = false;
    $('play').disabled = false;
    $('map-empty').hidden = true;
    const ids = current().vehicles.map(v => v.id);
    if (!ids.includes(selected)) selected = ids[0] ?? 0;
    render();
  }

  // ---------- rendering ----------
  function render() {
    if (!data) return;
    const s = current(), k = data.kpis[$('strategy').value];
    const live = t >= data.committed_time;

    $('subtitle').textContent = `${data.dataset} · ${k.total_orders} orders · ${fmt(t, 0)} of ${data.max_time} min`;
    $('time-value').textContent = fmt(t, 0);
    $('time-max').textContent = data.max_time;
    $('timeline-status').textContent = live ? 'Viewing the latest state' : `Historical view · latest event at ${fmt(data.committed_time, 0)} min`;
    $('jump-live').hidden = live;

    // KPI chips
    const active = s.vehicles.filter(v => group(v.status) === 'active').length;
    $('c-active').textContent = `${active}/${s.vehicles.length}`;
    $('c-served').textContent = `${k.served_orders}/${k.total_orders}`;
    $('c-late').textContent = k.late_deliveries;
    $('c-drop').textContent = k.dropped_orders;
    $('c-dist').textContent = dist(k.distance);
    $('c-solve').textContent = `${fmt(k.solve_time_sec, 2)}s`;

    // filter counts
    const counts = {all: s.vehicles.length, active, idle: 0, broken: 0};
    s.vehicles.forEach(v => { const g = group(v.status); if (g !== 'active') counts[g]++; });
    for (const key of Object.keys(counts)) $('n-' + key).textContent = counts[key];

    // event controls
    const select = $('vehicle-select');
    const available = s.vehicles.filter(v => v.status !== 'broken');
    const previous = select.value;
    select.innerHTML = available.map(v => `<option value="${v.id}">Vehicle ${v.id} · ${v.status} · ${v.pending_count} pending</option>`).join('');
    if (available.some(v => String(v.id) === previous)) select.value = previous;
    else if (available.some(v => v.id === selected)) select.value = selected;
    select.disabled = !live || !available.length;
    $('break').disabled = select.disabled;
    $('order-fields').disabled = !live;
    $('event-count').textContent = `${data.events.length} event${data.events.length === 1 ? '' : 's'}`;
    renderLog();

    drawMap();
    if (data.routing === 'road') $('c-road').textContent = `${(k.distance * data.geo.metres_per_unit / 1000).toFixed(1)} km`;
    renderCard();
    renderFleet();
    $('clock-t').textContent = fmt(t, 0);
    $('clock-max').textContent = data.max_time;
    if (view === 'vehicle') renderVehicle();
    if (view === 'compare') renderCompare();
    if (view === 'orders') renderOrders();
    if (view === 'analytics') renderAnalytics();
  }

  // ---------- fleet panel ----------
  const left = until => Math.max(0, Math.round(until - t));
  function activityText(v) {
    const a = v.activity || {kind: v.status};
    const order = a.customer_id ? `ORD-${a.customer_id}` : '';
    switch (a.kind) {
      case 'travelling': return a.customer_id === 0 ? `Returning to depot · arrives in ${left(a.until)} min` : `→ ${order} · ETA in ${left(a.until)} min`;
      case 'servicing': return `Servicing ${order} · ${left(a.until)} min left`;
      case 'waiting': return `Waiting at ${order} · starts in ${left(a.until)} min`;
      case 'broken': return 'Offline · orders reassigned';
      default: return v.served_count && !v.pending_count ? 'Finished for the day' : 'Idle at depot';
    }
  }
  // Route time = minutes of driving in total; the day clock can run later because trucks wait for customer time windows.
  const timeText = (v, route) => {
    if (v.status === 'broken') return 'Broke down';
    if (v.finish == null) return 'No route';
    const drive = Math.round(v.distance_travelled + (route ? routeLength(route, 'future') : 0));
    return `${drive} min driving · ${v.finish <= t ? 'back at depot' : 'back at depot at'} ${fmt(v.finish, 0)} min`;
  };

  function renderFleet() {
    const s = current();
    const shown = s.vehicles.filter(v => filter === 'all' || group(v.status) === filter);
    const count = kind => s.vehicles.filter(v => (v.activity || {}).kind === kind).length;
    $('fleet-sub').textContent = `${data.dataset} · ${STRATEGY_NAMES[$('strategy').value]}`;
    $('fleet-count').textContent = `${shown.length} truck${shown.length === 1 ? '' : 's'}`;
    $('fleet-sum').innerHTML = [['travelling', 'Moving'], ['servicing', 'Serving'], ['waiting', 'Waiting'], ['broken', 'Broken']]
      .map(([k, label]) => `<div><b>${count(k)}</b><span>${label}</span></div>`).join('');
    $('fleet-list').innerHTML = shown.map(v => {
      const total = v.served_count + v.pending_count, done = total ? Math.round(v.served_count / total * 100) : 0;
      const loadPct = v.capacity ? Math.round(v.used_capacity / v.capacity * 100) : 0;
      return `<button class="trow ${(v.activity || {}).kind || ''} ${v.id === selected ? 'sel' : ''}" data-id="${v.id}">
        <span class="tbadge">${TRUCK_SVG}<i>${v.id}</i></span>
        <span class="tmain"><b>Truck ${v.id}</b><small>${activityText(v)}</small><small class="tt">${timeText(v, s.routes.find(r => r.vehicle_id === v.id))}</small>
          <span class="bar"><span style="width:${done}%"></span></span></span>
        <span class="tload">${loadPct}%<small>load</small></span></button>`;
    }).join('') || '<p class="muted small pad">No trucks match this filter.</p>';
  }

  function renderLog() {
    const name = $('strategy').value;
    $('event-log').innerHTML = data.events.length ? data.events.map(e => {
      const r = e.strategies[name];
      const title = e.type === 'break_vehicle' ? `Vehicle ${e.payload.vehicle_id} taken offline` : `New order at (${e.payload.x}, ${e.payload.y})`;
      return `<li><span class="ev-t">${fmt(e.time, 0)} min</span>${title}<small>${STRATEGY_NAMES[name]}: unassigned ${r.before.dropped_orders} → ${r.after.dropped_orders} · distance ${r.delta.distance >= 0 ? '+' : ''}${dist(r.delta.distance)} · ${r.status}</small></li>`;
    }).join('') : '<li class="muted">No events yet.</li>';
  }

  function drawMap() {
    $('map').hidden = mode === 'street';
    $('lmap').hidden = mode !== 'street';
    $('map-note').hidden = mode !== 'street';
    $('street').classList.toggle('active', mode === 'street');
    $('map-note').textContent = data.routing === 'road'
      ? `Grid placed on the map · planned on road distances at ${data.geo.speed_kmh || Math.round(data.geo.metres_per_unit * 0.06)} km/h average`
      : 'Illustrative placement of Solomon x/y data on a street map · distances are in Solomon units';
    if (mode === 'street') drawStreet(); else { $('c-road').textContent = '–'; drawPlain(); }
  }

  // ---------- road geometry (street map only, cosmetic) ----------
  const roads = new Map();    // "lat,lng,lat,lng" -> {coords:[[lat,lng]], distance_m} | 'fail'
  const queued = new Set(), waiting = [];
  let inflight = 0, redrawTimer = null;
  const legKey = (p, q) => [...p, ...q].map(v => v.toFixed(5)).join(',');
  function requestRoad(key, p, q) {
    if (roads.has(key) || queued.has(key)) return;
    queued.add(key); waiting.push([key, p, q]); pump();
  }
  function pump() {
    while (inflight < 6 && waiting.length) {
      const [key, p, q] = waiting.shift();
      inflight++;
      fetch(`/road?lat1=${p[0]}&lng1=${p[1]}&lat2=${q[0]}&lng2=${q[1]}`)
        .then(r => r.ok ? r.json() : Promise.reject())
        .then(j => roads.set(key, j)).catch(() => roads.set(key, 'fail'))
        .finally(() => { inflight--; queued.delete(key); clearTimeout(redrawTimer);
          redrawTimer = setTimeout(() => { if (mode === 'street' && data) drawStreet(); }, 250); pump(); });
    }
  }
  const metres = (a, b) => Math.hypot((b[0] - a[0]) * 111320, (b[1] - a[1]) * 111320 * Math.cos(a[0] * Math.PI / 180));
  // Side-view truck (faces right). Flipped horizontally when the truck heads west.
  const TRUCK_SVG = '<svg viewBox="0 0 38 24" aria-hidden="true"><rect x="1" y="2" width="23" height="14" rx="2" class="t-trailer"/><path d="M26 6h6l5 6v6H26z" class="t-cab"/><path d="M28 8h3.4l3 4H28z" class="t-glass"/><circle cx="8" cy="19" r="3.3" class="t-wheel"/><circle cx="18" cy="19" r="3.3" class="t-wheel"/><circle cx="31" cy="19" r="3.3" class="t-wheel"/></svg>';
  function bearing(a, b) {
    const rad = Math.PI / 180, dl = (b[1] - a[1]) * rad;
    const y = Math.sin(dl) * Math.cos(b[0] * rad), x = Math.cos(a[0] * rad) * Math.sin(b[0] * rad) - Math.sin(a[0] * rad) * Math.cos(b[0] * rad) * Math.cos(dl);
    return (Math.atan2(y, x) / rad + 360) % 360;
  }
  // Split a polyline at a fraction of its length: [part before, part after, point at the split].
  function splitAt(coords, fraction) {
    const lengths = coords.slice(1).map((c, i) => metres(coords[i], c));
    const target = lengths.reduce((a, b) => a + b, 0) * fraction;
    let run = 0;
    for (let i = 0; i < lengths.length; i++) {
      if (run + lengths[i] >= target) {
        const f = lengths[i] ? (target - run) / lengths[i] : 0;
        const point = [coords[i][0] + (coords[i + 1][0] - coords[i][0]) * f, coords[i][1] + (coords[i + 1][1] - coords[i][1]) * f];
        return [coords.slice(0, i + 1).concat([point]), [point].concat(coords.slice(i + 1)), point];
      }
      run += lengths[i];
    }
    return [coords, [coords[coords.length - 1]], coords[coords.length - 1]];
  }

  function drawStreet() {
    const s = current();
    if (typeof L === 'undefined') { say('The street map library did not load. Switching back to the plain map.', true); mode = 'plain'; return drawMap(); }
    const ll = (x, y) => toLatLng(x, y);
    // Road mode: the backend sends the nearest on-road point for every node, so nothing is drawn in water.
    const cl = o => o.lat !== undefined ? [o.lat, o.lng] : ll(o.x, o.y);
    if (!lmap) {
      lmap = L.map('lmap', {zoomControl: false, attributionControl: true, preferCanvas: true});
      L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
        {maxZoom: 19, attribution: '© OpenStreetMap contributors'}).addTo(lmap);
      llayer = L.layerGroup().addTo(lmap);
      vlayer = L.layerGroup().addTo(lmap);
      // Truck badges shrink as the map zooms out so they do not pile up: full badge, smaller badge, then a numbered dot.
      const sizeClass = () => { const z = lmap.getZoom(); $('lmap').classList.remove('z-low', 'z-mid'); if (z <= 13) $('lmap').classList.add('z-low'); else if (z <= 15) $('lmap').classList.add('z-mid'); };
      lmap.on('zoomend', sizeClass); sizeClass();
      lmap.on('click', ev => {
        if (settingDepot) { settingDepot = false; $('depot-pin').classList.remove('active'); anchor = {lat: ev.latlng.lat, lng: ev.latlng.lng}; loadDataset(); return; }
        if (!placing) return;
        const [x, y] = fromLatLng(ev.latlng);
        $('order-x').value = Math.round(x); $('order-y').value = Math.round(y);
        $('order-ready').value = Math.round(t); $('order-due').value = data.max_time;
        setPlacing(false);
        say(`Location set to (${Math.round(x)}, ${Math.round(y)}). Review the form below the map and press Add order.`);
      });
    }
    llayer.clearLayers();
    const showRoutes = $('t-routes').checked, showAlerts = $('t-alerts').checked;

    let roadMetres = 0, legsTotal = 0, legsLoaded = 0;
    const vehiclePoint = {};
    s.routes.forEach(route => {
      const isSel = route.vehicle_id === selected;
      route.legs.forEach(leg => {
        const p = leg.al || ll(...leg.a), q = leg.bl || ll(...leg.b);
        if (p[0] === q[0] && p[1] === q[1]) return;
        const key = legKey(p, q);
        let road = roads.get(key);
        if (!road) requestRoad(key, p, q);
        legsTotal++;
        if (road && road !== 'fail') { legsLoaded++; roadMetres += road.distance_m; }
        const coords = road && road !== 'fail' ? road.coords : [p, q];
        const [done, rest, here] = leg.p >= 1 ? [coords, [], null] : leg.p <= 0 ? [[], coords, null] : splitAt(coords, leg.p);
        if (leg.p > 0 && leg.p < 1) vehiclePoint[route.vehicle_id] = here;
        if (!showRoutes) return;
        if (done.length > 1) L.polyline(done, {color: '#6a6a68', weight: 2, dashArray: '2 6', interactive: false}).addTo(llayer);
        if (rest.length > 1) L.polyline(rest, {color: isSel ? ACCENT : OLIVE, weight: isSel ? 4 : 2.5, opacity: isSel ? 1 : .85, interactive: false}).addTo(llayer);
      });
    });
    if (data.routing !== 'road') $('c-road').textContent = legsTotal ? `${(roadMetres / 1000).toFixed(1)} km${legsLoaded < legsTotal ? '…' : ''}` : '–';
    s.customers.forEach(c => {
      L.circleMarker(cl(c), {radius: c.status === 'servicing' ? 8 : 6, color: '#040404', weight: 1, fillColor: STATUS_COLOURS[c.status], fillOpacity: 1})
        .bindTooltip(`Order ${c.id} · ${c.status}<br>Demand ${c.demand} · window ${c.ready}–${c.due} min<br>Vehicle ${c.vehicle_id ?? 'unassigned'}`).addTo(llayer);
      if (showAlerts && c.status === 'unassigned') L.circleMarker(cl(c), {radius: 13, color: '#e5604d', weight: 2, fill: false, interactive: false}).addTo(llayer);
    });
    if (trucksSession !== data.session_id) { vlayer.clearLayers(); trucks.clear(); trucksSession = data.session_id; }
    const shownIds = new Set();
    s.vehicles.filter(v => (filter === 'all' || group(v.status) === filter) && onMap(v)).forEach(v => {
      shownIds.add(v.id);
      const pos = vehiclePoint[v.id] || cl(v);
      let entry = trucks.get(v.id);
      if (!entry) {
        const marker = L.marker(pos, {icon: L.divIcon({className: 'vtruck', iconSize: [48, 46], iconAnchor: [24, 23],
          html: `<div class="tkb">${TRUCK_SVG}<i>${v.id}</i></div>`})}).addTo(vlayer);
        const id = v.id;
        marker.on('click', () => { selected = id; cardOpen = true; render(); });
        entry = {marker, heading: 0, flip: false};
        trucks.set(v.id, entry);
      } else {
        const prev = entry.marker.getLatLng();
        if (metres([prev.lat, prev.lng], pos) > 2) { entry.heading = bearing([prev.lat, prev.lng], pos); entry.flip = Math.sin(entry.heading * Math.PI / 180) < -0.15; }
        entry.marker.setLatLng(pos);
      }
      const el = entry.marker.getElement();
      if (el) {
        el.querySelector('.tkb').className = `tkb ${(v.activity || {}).kind || ''} ${v.id === selected ? 'sel' : ''}`;
      }
      entry.marker.setZIndexOffset(v.id === selected ? 1000 : 0);
      entry.marker.bindTooltip(`Truck ${v.id} · ${activityText(v)}<br>Load ${v.used_capacity}/${v.capacity} · ${timeText(v, s.routes.find(r => r.vehicle_id === v.id))}`);
    });
    for (const [id, entry] of trucks) if (!shownIds.has(id)) { vlayer.removeLayer(entry.marker); trucks.delete(id); }
    const idleCount = s.vehicles.filter(v => (v.activity || {}).kind === 'idle').length;
    if (idleCount && filter !== 'idle') L.marker(cl(s.depot), {interactive: false, icon: L.divIcon({className: '', iconSize: [64, 20], iconAnchor: [-12, 22],
      html: `<div class="idle-badge">${idleCount} idle</div>`})}).addTo(llayer);
    L.marker(cl(s.depot), {icon: L.divIcon({className: '', html: '<div class="vmark" style="transform:rotate(45deg);background:#fff"></div>', iconSize: [16, 16], iconAnchor: [8, 8]})})
      .bindTooltip(`Depot · closes at ${data.max_time} min`).addTo(llayer);

    const key = `${data.session_id}-${viewRev}`;
    lmap.invalidateSize();
    if (fitted !== key) {
      fitted = key;
      lmap.fitBounds(L.latLngBounds(s.customers.map(c => cl(c)).concat([cl(s.depot)])), {padding: [40, 40]});
    }
    $('lmap').style.cursor = placing ? 'crosshair' : '';
  }

  function drawPlain() {
    const s = current();
    const showRoutes = $('t-routes').checked, showAlerts = $('t-alerts').checked;
    const traces = [];

    if (showRoutes) {
      s.routes.forEach(route => {
        const isSel = route.vehicle_id === selected;
        for (const [key, dash, width, colour] of [['past', 'dot', 1, '#4a4a48'], ['future', 'solid', isSel ? 3 : 1.5, isSel ? ACCENT : OLIVE]]) {
          const x = [], y = [];
          route[key].forEach(([a, b]) => { x.push(a[0], b[0], null); y.push(a[1], b[1], null); });
          if (x.length) traces.push({x, y, mode: 'lines', type: 'scatter', line: {color: colour, width, dash}, hoverinfo: 'skip', showlegend: false});
        }
      });
    }

    for (const status of ['pending', 'servicing', 'served', 'unassigned']) {
      const list = s.customers.filter(c => c.status === status);
      if (!list.length) continue;
      traces.push({x: list.map(c => c.x), y: list.map(c => c.y), type: 'scatter', mode: 'markers+text',
        text: list.map(c => String(c.id)), textposition: 'top center', textfont: {size: 9, color: '#9e9f9f'},
        marker: {size: status === 'servicing' ? 12 : 9, color: STATUS_COLOURS[status], symbol: status === 'unassigned' ? 'x' : 'circle', line: {width: 1, color: '#040404'}},
        hovertemplate: list.map(c => `Order ${c.id} · ${status}<br>Demand ${c.demand} · window ${c.ready}–${c.due} min<br>Vehicle ${c.vehicle_id ?? 'unassigned'}<extra></extra>`),
        showlegend: false});
    }

    if (showAlerts) {
      const late = s.customers.filter(c => c.status === 'unassigned');
      if (late.length) traces.push({x: late.map(c => c.x), y: late.map(c => c.y), type: 'scatter', mode: 'markers', hoverinfo: 'skip',
        marker: {size: 24, color: 'rgba(0,0,0,0)', line: {width: 1.5, color: '#e5604d'}}, showlegend: false});
    }

    const shown = s.vehicles.filter(v => (filter === 'all' || group(v.status) === filter) && onMap(v));
    const sel = shown.find(v => v.id === selected);
    if (sel) traces.push({x: [sel.x], y: [sel.y], type: 'scatter', mode: 'markers', hoverinfo: 'skip', showlegend: false,
      marker: {size: 40, color: 'rgba(206,222,98,.22)'}});
    traces.push({x: shown.map(v => v.x), y: shown.map(v => v.y), type: 'scatter', mode: 'markers+text', name: 'vehicles', text: shown.map(v => String(v.id)), textposition: 'middle center', textfont: {size: 10, color: '#040404'},
      customdata: shown.map(v => [v.id, v.status, v.used_capacity, v.capacity]),
      marker: {size: 22, symbol: shown.map(v => v.status === 'broken' ? 'x' : 'square'),
        color: shown.map(v => v.status === 'broken' ? '#e5604d' : v.id === selected ? ACCENT : '#f4f4f4'), line: {width: 1.5, color: '#040404'}},
      hovertemplate: 'Vehicle %{customdata[0]} · %{customdata[1]}<br>Load %{customdata[2]}/%{customdata[3]}<extra></extra>', showlegend: false});
    traces.push({x: [s.depot.x], y: [s.depot.y], type: 'scatter', mode: 'markers+text', text: ['DEPOT'], textposition: 'bottom center',
      textfont: {size: 9, color: '#f4f4f4'}, marker: {size: 15, symbol: 'diamond', color: '#f4f4f4', line: {width: 2, color: '#040404'}},
      hovertemplate: `Depot · closes at ${data.max_time} min<extra></extra>`, showlegend: false});

    const xs = s.customers.map(c => c.x).concat(s.depot.x), ys = s.customers.map(c => c.y).concat(s.depot.y);
    const axis = (title, lo, hi, extra = {}) => ({title: {text: title, font: {size: 9, color: '#5c5c5b'}}, range: [lo - 6, hi + 6], showgrid: false, zeroline: false, tickfont: {size: 9, color: '#5c5c5b'}, linecolor: '#2a2a29', ...extra});
    const layout = {paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', font: {color: '#9e9f9f', family: 'Segoe UI, Arial'},
      margin: {l: 40, r: 20, t: 14, b: 34}, hovermode: 'closest', dragmode: placing ? false : 'pan', showlegend: false,
      uirevision: `${data.session_id}-${viewRev}`,
      xaxis: axis('X', Math.min(...xs), Math.max(...xs)), yaxis: axis('Y', Math.min(...ys), Math.max(...ys), {scaleanchor: 'x', scaleratio: 1})};
    const gd = $('map');
    Plotly.react(gd, traces, layout, {displayModeBar: false, scrollZoom: true, responsive: true});
    if (!gd.dataset.bound) {
      gd.dataset.bound = '1';
      gd.on('plotly_click', ev => {
        const p = ev.points[0];
        if (p && p.data.name === 'vehicles') { selected = p.customdata[0]; render(); }
      });
      gd.addEventListener('click', ev => {
        if (!placing) return;
        const box = gd.getBoundingClientRect(), size = gd._fullLayout._size;
        const x = gd._fullLayout.xaxis.p2d(ev.clientX - box.left - size.l), y = gd._fullLayout.yaxis.p2d(ev.clientY - box.top - size.t);
        $('order-x').value = Math.round(x); $('order-y').value = Math.round(y);
        $('order-ready').value = Math.round(t); $('order-due').value = data.max_time;
        setPlacing(false);
        say(`Location set to (${Math.round(x)}, ${Math.round(y)}). Review the form below the map and press Add order.`);
      });
    }
  }

  function setPlacing(on) {
    placing = on;
    $('place').classList.toggle('active', on);
    if (data) drawMap();
  }

  function renderCard() {
    const card = $('vcard'), v = data && vehicleById(selected);
    if (!v || !cardOpen) { card.hidden = true; return; }
    const route = current().routes.find(r => r.vehicle_id === selected);
    const remaining = route ? routeLength(route, 'future') : 0;
    const total = v.served_count + v.pending_count;
    const done = total ? Math.round(v.served_count / total * 100) : 0;
    const loadPct = v.capacity ? Math.round(v.used_capacity / v.capacity * 100) : 0;
    const timePct = Math.min(100, Math.round(t / data.max_time * 100));
    const orders = current().customers.filter(c => c.vehicle_id === selected && c.status !== 'served');
    const urgent = orders.filter(c => c.due - t < 20 && c.due >= t).sort((a, b) => a.due - b.due)[0];
    const alert = v.status === 'broken' ? ['bad', `Offline — remaining orders were reassigned to other vehicles.`]
      : urgent ? ['', `<b>Window closing:</b> order ${urgent.id} is due at ${urgent.due} min.`]
      : ['good', 'On schedule — no time windows at risk.'];
    card.hidden = false;
    card.innerHTML = `
      <div class="vc-head"><div><h3>Vehicle ${v.id} ${pill(v.status, v.status === 'broken' ? 'bad' : 'ok')}</h3><span class="muted small">Capacity ${v.capacity} · ${v.served_count} served</span></div>
        <button class="vc-x" id="vc-close" aria-label="Close">×</button></div>
      <div class="vc-route"><div class="vc-row"><b>Depot → ${total} stop${total === 1 ? '' : 's'}</b><span>${done}% done</span></div>
        <div class="bar"><span style="width:${done}%"></span></div>
        <div class="vc-row"><span class="muted">Now</span><span>${activityText(v)}</span></div>
        <div class="vc-row"><span class="muted">${v.pending_count} pending</span><span>${dist(remaining)} <span class="muted">remaining</span></span></div></div>
      <div class="vc-gauges">
        <div class="gauge"><span class="muted small">Load</span><b>${loadPct}%</b><div class="bar ${loadPct > 90 ? 'red' : ''}"><span style="width:${loadPct}%"></span></div><span class="small muted">${v.used_capacity}/${v.capacity}</span></div>
        <div class="gauge"><span class="muted small">Day elapsed</span><b>${timePct}%</b><div class="bar"><span style="width:${timePct}%"></span></div><span class="small muted">${fmt(t, 0)} / ${data.max_time} min</span></div></div>
      <div class="alert ${alert[0]}">${alert[1]}</div>
      <div class="row-gap" style="margin-top:10px"><button class="btn ghost" id="vc-detail">Details</button>
        <button class="btn danger" id="vc-break" ${v.status === 'broken' || t < data.committed_time ? 'disabled' : ''}>Take offline</button></div>`;
    $('vc-close').onclick = () => { cardOpen = false; card.hidden = true; };
    $('vc-detail').onclick = () => showView('vehicle');
    $('vc-break').onclick = () => breakVehicle(selected);
  }

  function renderVehicle() {
    const v = vehicleById(selected);
    if (!v) return;
    const s = current();
    const orders = s.customers.filter(c => c.vehicle_id === selected).sort((a, b) => a.due - b.due);
    const total = v.served_count + v.pending_count;
    const done = total ? Math.round(v.served_count / total * 100) : 0;
    const loadPct = v.capacity ? Math.round(v.used_capacity / v.capacity * 100) : 0;
    const timePct = Math.min(100, Math.round(t / data.max_time * 100));
    const route = s.routes.find(r => r.vehicle_id === selected);

    $('crumb-v').textContent = `Vehicle ${v.id}`;
    $('vd-name').textContent = `Vehicle ${v.id}`;
    $('vd-status').textContent = v.status;
    $('vd-status').className = `pill ${v.status === 'broken' ? 'bad' : 'ok'}`;
    $('vd-sub').textContent = `${STRATEGY_NAMES[$('strategy').value]} · capacity ${v.capacity} · at (${fmt(v.x, 0)}, ${fmt(v.y, 0)})`;
    $('vd-loaded').textContent = loadPct >= 100 ? 'Fully loaded' : `${loadPct}% loaded`;
    $('vd-loaded').className = `pill ${loadPct >= 90 ? 'bad' : 'warn'}`;
    $('vd-cap-text').textContent = `${v.used_capacity} / ${v.capacity}`;
    $('vd-cap-pct').textContent = `${loadPct}%`;
    $('vd-cap-bar').style.width = `${Math.min(100, loadPct)}%`;
    $('vd-prog-text').textContent = `${v.served_count} / ${total} stops`;
    $('vd-prog-pct').textContent = `${done}%`;
    $('vd-prog-bar').style.width = `${done}%`;

    $('tiles').innerHTML = orders.length ? orders.map(c => `<div class="tile ${c.status} ${c.id === selectedOrder ? 'sel' : ''}" data-id="${c.id}">
        <span class="id">ORD-${c.id}</span><span>${pill(c.status, statusKind(c.status))} <span class="muted">${c.demand}</span></span></div>`).join('')
      : '<div class="muted small" style="grid-column:1/-1;padding:14px">No orders assigned to this vehicle.</div>';
    $('pk-sub').textContent = `${orders.length} orders · total demand ${orders.reduce((n, c) => n + c.demand, 0)}`;
    $('pk-list').innerHTML = orders.map(c => `<div class="pk ${c.id === selectedOrder ? 'sel' : ''}" data-id="${c.id}">
        <div class="pk-top"><b>ORD-${c.id}</b>${pill(c.status, statusKind(c.status))}</div>
        <div class="pk-tags">${pill('demand ' + c.demand)}${pill('ready ' + c.ready + ' min')}${pill('due ' + c.due + ' min')}</div>
        ${c.id === selectedOrder ? `<div class="pk-more"><span>Location<b>(${c.x}, ${c.y})</b></span><span>Window<b>${c.ready}–${c.due} min</b></span><span>Assigned to<b>Vehicle ${c.vehicle_id}</b></span><span>Status<b>${c.status}</b></span></div>` : ''}</div>`).join('');
    document.querySelectorAll('#tiles .tile, #pk-list .pk').forEach(el => el.onclick = () => { selectedOrder = Number(el.dataset.id); renderVehicle(); });

    const setGauge = (id, pct, label) => { $(id).setAttribute('stroke-dasharray', `${Math.min(100, pct)} 100`); $(id + '-t').textContent = label; };
    setGauge('g-load', loadPct, `${loadPct}%`);
    setGauge('g-time', timePct, `${fmt(t, 0)} / ${data.max_time} min`);
    const urgent = orders.filter(c => c.status !== 'served' && c.due >= t && c.due - t < 20).sort((a, b) => a.due - b.due)[0];
    const alert = $('vd-alert');
    alert.className = 'alert' + (v.status === 'broken' ? ' bad' : urgent ? '' : ' good');
    alert.innerHTML = v.status === 'broken' ? 'Offline: remaining orders were reassigned.'
      : urgent ? `<b>Window closing:</b> ORD-${urgent.id} is due at ${urgent.due} min.` : 'On schedule: no time windows at risk.';
    $('vd-stops').textContent = v.served_count;
    $('vd-pending').textContent = v.pending_count;
    $('vd-dist').textContent = dist(v.distance_travelled);
    $('vd-remain').textContent = dist(route ? routeLength(route, 'future') : 0);
    $('vd-break').disabled = v.status === 'broken' || t < data.committed_time;
  }

  function renderCompare() {
    const names = Object.keys(data.kpis);
    const rank = n => { const k = data.kpis[n]; return [k.dropped_orders, k.late_deliveries, k.distance]; };
    const best = [...names].sort((a, b) => { const x = rank(a), y = rank(b); return x[0] - y[0] || x[1] - y[1] || x[2] - y[2]; })[0];
    $('cards').innerHTML = names.map(n => { const k = data.kpis[n];
      return `<div class="kpi-card ${n === best ? 'best' : ''}"><h3>${STRATEGY_NAMES[n]} ${n === best ? pill('best', 'ok') : ''}</h3><dl>
        <dt>Delivered</dt><dd>${k.served_orders}/${k.total_orders}</dd><dt>Unassigned</dt><dd>${k.dropped_orders}</dd>
        <dt>Late</dt><dd>${k.late_deliveries}</dd><dt>Distance</dt><dd>${dist(k.distance)}</dd><dt>Recovery time</dt><dd>${fmt(k.solve_time_sec, 3)}s</dd></dl></div>`; }).join('');
    $('comparison-body').innerHTML = names.map(n => { const k = data.kpis[n];
      return `<tr class="${n === $('strategy').value ? 'selected' : ''}"><td>${STRATEGY_NAMES[n]}</td><td>${k.served_orders}/${k.total_orders}</td><td>${dist(k.distance)}</td><td>${k.dropped_orders}</td><td>${k.late_deliveries}</td><td>${fmt(k.solve_time_sec, 3)}s</td></tr>`; }).join('');
  }

  // ---------- orders page ----------
  let ordSort = {k: 'id', dir: 1}, ordFilter = 'all', ordQuery = '';
  const slackOf = c => c.arrival == null ? null : c.due - c.arrival;
  function orderFlag(c) {
    const slack = slackOf(c);
    if (c.status === 'unassigned') return 'unassigned';
    if (slack !== null && slack < 0) return 'late';
    if (c.status !== 'served' && slack !== null && slack < 10) return 'risk';
    return c.status;
  }

  function renderOrders() {
    const s = current();
    const rows = s.customers.map(c => ({c, slack: slackOf(c), flag: orderFlag(c)}));
    const count = f => rows.filter(r => f === 'all' ? true : f === 'risk' ? (r.flag === 'risk' || r.flag === 'late') : r.c.status === f).length;
    ['all', 'pending', 'servicing', 'served', 'unassigned', 'risk'].forEach(f => { $('ord-n-' + f).textContent = count(f); });
    $('ord-sub').textContent = `${rows.length} orders · ${count('served')} delivered · ${count('unassigned')} unassigned · ${STRATEGY_NAMES[$('strategy').value]}`;
    $('book-when').textContent = t >= data.committed_time ? `Booking at ${fmt(t, 0)} min` : 'View is in the past';
    $('book-go').disabled = !bookPoint || t < data.committed_time;
    const q = ordQuery.trim().toLowerCase();
    let list = rows.filter(r => (ordFilter === 'all' || (ordFilter === 'risk' ? (r.flag === 'risk' || r.flag === 'late') : r.c.status === ordFilter))
      && (!q || `ord-${r.c.id} truck ${r.c.vehicle_id ?? ''} ${r.c.status} ${r.flag}`.includes(q)));
    const key = {id: r => r.c.id, status: r => r.c.status, truck: r => r.c.vehicle_id ?? 999, ready: r => r.c.ready, arrival: r => r.c.arrival ?? 1e9, slack: r => r.slack ?? 1e9, demand: r => r.c.demand}[ordSort.k];
    list.sort((a, b) => (key(a) > key(b) ? 1 : key(a) < key(b) ? -1 : 0) * ordSort.dir);
    $('ord-body').innerHTML = list.map(r => {
      const c = r.c, kind = r.flag === 'late' ? 'bad' : r.flag === 'risk' ? 'warn' : statusKind(c.status);
      const label = r.flag === 'late' ? 'late' : r.flag === 'risk' ? 'at risk' : c.status;
      const slack = r.slack === null ? '–' : r.slack < 0 ? `${fmt(-r.slack, 0)} min late` : `${fmt(r.slack, 0)} min`;
      return `<tr data-truck="${c.vehicle_id ?? ''}"><td><b>ORD-${c.id}</b></td><td>${pill(label, kind)}</td><td>${c.vehicle_id == null ? '–' : 'Truck ' + c.vehicle_id}</td>
        <td>${c.ready}–${c.due} min</td><td>${c.arrival == null ? '–' : fmt(c.arrival, 0) + ' min'}</td>
        <td class="${r.slack !== null && r.slack < 0 ? 'slack-bad' : r.slack !== null && r.slack < 10 && c.status !== 'served' ? 'slack-warn' : ''}">${slack}</td><td>${c.demand}</td></tr>`;
    }).join('') || '<tr><td colspan="7" class="muted">No orders match.</td></tr>';
  }

  // ---------- order booking ----------
  let bookMap = null, bookMarker = null, bookPoint = null, bookSize = 10, bookWindow = 60;
  function initBookMap() {
    if (!data || typeof L === 'undefined') return;
    if (!bookMap) {
      bookMap = L.map('book-map', {attributionControl: false}).setView([data.geo.lat0, data.geo.lng0], 14);
      L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 19}).addTo(bookMap);
      bookMap.on('click', ev => setBookPoint(ev.latlng.lat, ev.latlng.lng, 'Pinned location'));
    }
    setTimeout(() => bookMap.invalidateSize(), 60);
  }
  function setBookPoint(lat, lng, label) {
    bookPoint = {lat, lng};
    if (bookMarker) bookMarker.setLatLng([lat, lng]);
    else bookMarker = L.circleMarker([lat, lng], {radius: 9, color: '#040404', weight: 2, fillColor: '#cede62', fillOpacity: 1}).addTo(bookMap);
    $('book-loc').textContent = `${label} · ${lat.toFixed(5)}, ${lng.toFixed(5)}`;
    $('book-go').disabled = !(data && t >= data.committed_time);
  }
  async function searchPlaces(q) {
    const g = data.geo, d = 0.08;
    const url = `https://nominatim.openstreetmap.org/search?format=json&limit=5&q=${encodeURIComponent(q)}&viewbox=${g.lng0 - d},${g.lat0 + d},${g.lng0 + d},${g.lat0 - d}`;
    const res = await fetch(url, {headers: {Accept: 'application/json'}});
    return res.ok ? res.json() : [];
  }
  function showBookResult() {
    const id = Math.max(...current().customers.map(c => c.id));
    const rows = Object.keys(data.strategies).map(n => {
      const c = data.strategies[n].customers.find(x => x.id === id);
      const ok = c && c.vehicle_id != null;
      return `<li class="${ok ? 'good' : 'bad'}"><b>${STRATEGY_NAMES[n]}</b><span>${ok ? `Truck ${c.vehicle_id} · arrives ${fmt(c.arrival, 0)} min (window ${c.ready}–${c.due})` : 'Could not be scheduled'}</span></li>`;
    }).join('');
    $('book-result').hidden = false;
    $('book-result').innerHTML = `<h3>ORD-${id} booked</h3><ul>${rows}</ul>`;
  }

  // ---------- analytics page ----------
  const STRATEGY_COLOURS = {do_nothing: '#e5604d', cheapest_insertion: '#e8b04a', replan: '#cede62'};
  let seriesCache = {key: '', data: null};
  const plotLayout = (extra = {}) => ({paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', font: {color: '#9e9f9f', family: 'Segoe UI, Arial', size: 12},
    margin: {l: 44, r: 16, t: 14, b: 56}, xaxis: {title: {text: 'Minutes into the day'}, gridcolor: '#232326', zeroline: false},
    yaxis: {gridcolor: '#232326', zeroline: false}, legend: {orientation: 'h', y: 1.14}, hovermode: 'x unified', ...extra});
  const plotConfig = {displayModeBar: false, responsive: true};

  async function renderAnalytics() {
    const key = `${data.session_id}:${data.revision}`;
    if (seriesCache.key !== key) {
      $('an-sub').textContent = 'Building charts…';
      try { seriesCache = {key, data: await api('/timeseries?step=5')}; } catch (e) { $('an-sub').textContent = e.message; return; }
    }
    if (view !== 'analytics') return;
    const ts = seriesCache.data, names = Object.keys(ts.series), current_name = $('strategy').value;
    $('an-sub').textContent = `${data.dataset} · ${ts.events.length} event${ts.events.length === 1 ? '' : 's'} · sampled every 5 minutes`;
    $('an-strategy').textContent = STRATEGY_NAMES[current_name];

    const s = current(), k = data.kpis[current_name];
    const working = s.vehicles.filter(v => v.served_count + v.pending_count > 0).length;
    const driving = s.vehicles.map(v => v.distance_travelled + (s.routes.find(r => r.vehicle_id === v.id) ? routeLength(s.routes.find(r => r.vehicle_id === v.id), 'future') : 0)).filter(d => d > 0);
    const avgDrive = driving.length ? driving.reduce((a, b) => a + b, 0) / driving.length : 0;
    const util = working ? Math.round(avgDrive / data.max_time * 100) : 0;
    $('an-cards').innerHTML = [[`${k.served_orders}/${k.total_orders}`, 'Orders delivered'], [`${working}/${s.vehicles.length}`, 'Trucks working'],
      [`${fmt(avgDrive, 0)} min`, 'Average driving per truck'], [`${util}%`, 'Fleet utilisation (driving ÷ day)']]
      .map(([v, l]) => `<div class="an-card"><b>${v}</b><span>${l}</span></div>`).join('');

    const marks = ts.events.map(e => ({type: 'line', x0: e.time, x1: e.time, y0: 0, y1: 1, yref: 'paper', line: {color: '#e5604d', width: 2, dash: 'dot'}}));
    const notes = ts.events.map(e => ({x: e.time, y: 0.97, yref: 'paper', text: e.type === 'break_vehicle' ? `Truck ${e.payload.vehicle_id} offline` : 'New order', showarrow: false, xanchor: 'left', xshift: 4, yanchor: 'top', font: {color: '#e5604d', size: 11}}));
    const lines = field => names.map(n => ({x: ts.times, y: ts.series[n][field], name: STRATEGY_NAMES[n], mode: 'lines', line: {color: STRATEGY_COLOURS[n], width: 3, shape: 'hv'}}));
    Plotly.react('ch-served', lines('served'), plotLayout({shapes: marks, annotations: notes, yaxis: {title: {text: 'Orders'}, gridcolor: '#232326', zeroline: false}}), plotConfig);
    Plotly.react('ch-dropped', lines('dropped'), plotLayout({shapes: marks, annotations: notes, yaxis: {title: {text: 'Orders'}, gridcolor: '#232326', zeroline: false, dtick: 1, rangemode: 'tozero'}}), plotConfig);

    const fs = ts.series[current_name];
    const area = (f, name, colour) => ({x: ts.times, y: fs[f], name, stackgroup: 'one', mode: 'lines', line: {width: 0}, fillcolor: colour});
    Plotly.react('ch-fleet', [area('moving', 'Moving', 'rgba(206,222,98,.85)'), area('serving', 'Servicing', 'rgba(183,164,255,.85)'), area('waiting', 'Waiting', 'rgba(232,176,74,.85)'),
      area('broken', 'Broken', 'rgba(229,96,77,.9)'), area('idle', 'Idle', 'rgba(90,90,88,.55)')],
      plotLayout({shapes: marks, annotations: notes, yaxis: {title: {text: 'Trucks'}, gridcolor: '#232326', zeroline: false}}), plotConfig);

    // Easy to read: for each strategy, how many of the orders ended up delivered and how many did not.
    const finalOf = (n, f) => ts.series[n][f][ts.series[n][f].length - 1];
    const total = data.kpis[current_name].total_orders;
    const labels = names.map(n => `${STRATEGY_NAMES[n]}<br>${dist(data.kpis[n].distance)} driven · ${data.kpis[n].late_deliveries} late`);
    const bar = (name, vals, colour, textColour) => ({type: 'bar', orientation: 'h', y: labels, x: vals, name, marker: {color: colour},
      text: vals.map(v => v ? String(v) : ''), textposition: 'inside', insidetextanchor: 'middle', textangle: 0, textfont: {color: textColour, size: 14}, hoverinfo: 'skip'});
    Plotly.react('ch-trade', [bar('Delivered', names.map(n => finalOf(n, 'served')), '#cede62', '#1a1d00'), bar('Not delivered', names.map(n => finalOf(n, 'dropped')), '#e5604d', '#ffffff')],
      plotLayout({barmode: 'stack', bargap: 0.5, hovermode: false, margin: {l: 190, r: 16, t: 8, b: 56},
        xaxis: {title: {text: `Orders (out of ${total})`}, range: [0, total], gridcolor: '#232326', zeroline: false},
        yaxis: {autorange: 'reversed', automargin: true}, legend: {orientation: 'h', y: 1.2}}), plotConfig);

    $('an-events').innerHTML = ts.events.length ? ts.events.map(e => {
      const title = e.type === 'break_vehicle' ? `Truck ${e.payload.vehicle_id} taken offline` : `New order at (${fmt(e.payload.x, 0)}, ${fmt(e.payload.y, 0)})`;
      const cell = n => { const r = e.strategies[n]; return `${r.before.dropped_orders} → ${r.after.dropped_orders} unassigned · ${r.delta.distance >= 0 ? '+' : ''}${dist(r.delta.distance)}`; };
      return `<tr><td>${fmt(e.time, 0)} min</td><td>${title}</td><td>${cell('do_nothing')}</td><td>${cell('cheapest_insertion')}</td><td>${cell('replan')}</td></tr>`;
    }).join('') : '<tr><td colspan="5" class="muted">No events yet. Take a truck offline or book an order.</td></tr>';
  }

  // ---------- actions ----------
  function showView(name) {
    view = name;
    ['ops', 'orders', 'vehicle', 'analytics', 'compare'].forEach(v => { $('view-' + v).hidden = v !== name; });
    if (name === 'orders') setTimeout(initBookMap, 0);
    document.querySelectorAll('.nav').forEach(b => b.classList.toggle('active', b.dataset.view === name));
    if (data) { render(); if (name === 'ops') setTimeout(() => { if (mode === 'street' && lmap) lmap.invalidateSize(); else if (!$('map').hidden) Plotly.Plots.resize($('map')); }, 0); }
  }

  async function post(path, body, label) {
    busy(true, label);
    try { return await api(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}); }
    finally { busy(false); }
  }

  async function loadDataset() {
    stop();
    try {
      const body = {dataset: $('dataset').value, reset: true, routing: $('routing').value, city: $('city').value, speed_kmh: Number($('speed-kmh').value)};
      if (anchor) { body.depot_lat = anchor.lat; body.depot_lng = anchor.lng; }
      const next = await post('/load', body, $('routing').value === 'road' ? 'Fetching road distances and planning…' : 'Planning routes…');
      selectedOrder = null; viewRev++;
      say(`${next.dataset} loaded. Press Play, or move the slider to 90 min and take a vehicle offline.`);
      await fetchState(0);
    } catch (e) { say(e.message, true); }
  }

  async function sendEvent(payload, okMessage) {
    stop();
    try {
      const next = await post('/event', {...payload, time: t, session_id: data.session_id, revision: data.revision}, 'Re-planning all strategies…');
      accept(next);
      say(okMessage);
      return true;
    } catch (e) { say(e.message, true); return false; }
  }
  const breakVehicle = id => sendEvent({event_type: 'breakdown', vehicle_id: Number(id)}, `Vehicle ${id} taken offline at ${fmt(t, 0)} min. All three strategies re-planned.`);

  function stop() { clearInterval(timer); timer = null; $('play').textContent = '▶ Play'; $('lmap').classList.remove('playing'); }
  // Speed is simulated minutes per real second: the default 1 min/s plays a 230-minute day in about 4 minutes.
  // Time follows the wall clock (not the tick count), and a tick is skipped while the previous state request is still in flight.
  function togglePlay() {
    if (timer) return stop();
    if (t >= data.max_time) t = 0;
    $('play').textContent = '⏸ Pause'; $('lmap').classList.add('playing');
    let last = performance.now(), fetching = false;
    timer = setInterval(() => {
      const now = performance.now();
      t = Math.min(data.max_time, t + Number($('speed').value) * (now - last) / 1000);
      last = now;
      $('time-slider').value = t;
      $('clock-t').textContent = fmt(t, 0);
      if (!fetching) { fetching = true; fetchState(t).finally(() => { fetching = false; }); }
      if (t >= data.max_time) stop();
    }, 250);
  }

  function zoom(factor) {
    if (mode === 'street') { if (lmap) lmap.setZoom(lmap.getZoom() + (factor < 1 ? 1 : -1)); return; }
    const gd = $('map'); if (!gd.layout) return;
    const [x0, x1] = gd.layout.xaxis.range, [y0, y1] = gd.layout.yaxis.range;
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2, hx = (x1 - x0) / 2 * factor, hy = (y1 - y0) / 2 * factor;
    Plotly.relayout(gd, {'xaxis.range': [cx - hx, cx + hx], 'yaxis.range': [cy - hy, cy + hy]});
  }

  // ---------- wiring ----------
  $('load').onclick = loadDataset;
  $('play').onclick = togglePlay;
  $('time-slider').oninput = e => { stop(); t = Number(e.target.value); $('time-value').textContent = t; fetchState(t); };
  $('jump-live').onclick = () => fetchState(data.committed_time);
  $('strategy').onchange = () => data && render();
  $('t-routes').onchange = $('t-alerts').onchange = () => data && drawMap();
  $('fleet-list').onclick = e => {
    const row = e.target.closest('.trow'); if (!row) return;
    selected = Number(row.dataset.id); cardOpen = true; render();
    const entry = trucks.get(selected);
    if (mode === 'street' && lmap && entry) lmap.flyTo(entry.marker.getLatLng(), Math.max(lmap.getZoom(), 15), {duration: .6});
  };
  $('filters').onclick = e => {
    const tab = e.target.closest('.tab'); if (!tab) return;
    filter = tab.dataset.f;
    document.querySelectorAll('.tab').forEach(b => b.classList.toggle('active', b === tab));
    if (data) drawMap();
  };
  document.querySelectorAll('.nav').forEach(b => b.onclick = () => showView(b.dataset.view));
  $('z-in').onclick = () => zoom(0.8);
  $('z-out').onclick = () => zoom(1.25);
  $('z-reset').onclick = () => { viewRev++; if (data) drawMap(); };
  $('street').onclick = () => { mode = mode === 'street' ? 'plain' : 'street'; if (data) { drawMap(); if (mode === 'plain') Plotly.Plots.resize($('map')); } };
  $('city').onchange = () => { anchor = null; if (data) loadDataset(); };
  $('speed-kmh').onchange = () => { if (data) loadDataset(); };
  $('depot-pin').onclick = () => {
    if (!data) return;
    if (mode !== 'street') { mode = 'street'; drawMap(); }
    settingDepot = !settingDepot; $('depot-pin').classList.toggle('active', settingDepot);
    if (settingDepot) say('Zoom to the exact spot and click the map to place the depot there. Routes are re-planned around it.');
  };
  $('place').onclick = () => { if (data && t >= data.committed_time) { setPlacing(!placing); if (placing) say('Click the map to choose the new order location.'); } };
  $('break').onclick = () => breakVehicle($('vehicle-select').value);
  $('vd-break').onclick = () => breakVehicle(selected);
  $('vd-map').onclick = () => showView('ops');
  const step = d => { if (!data) return; const ids = current().vehicles.map(v => v.id); selected = ids[(ids.indexOf(selected) + d + ids.length) % ids.length]; renderVehicle(); };
  $('vd-prev').onclick = () => step(-1);
  $('vd-next').onclick = () => step(1);
  // orders + booking wiring
  $('ord-search').oninput = e => { ordQuery = e.target.value; if (data) renderOrders(); };
  $('ord-filters').onclick = e => {
    const tab = e.target.closest('.tab'); if (!tab) return;
    ordFilter = tab.dataset.s;
    document.querySelectorAll('#ord-filters .tab').forEach(b => b.classList.toggle('active', b === tab));
    if (data) renderOrders();
  };
  $('ord-head').onclick = e => {
    const th = e.target.closest('th'); if (!th) return;
    ordSort = {k: th.dataset.k, dir: ordSort.k === th.dataset.k ? -ordSort.dir : 1};
    if (data) renderOrders();
  };
  $('ord-body').onclick = e => {
    const row = e.target.closest('tr'); if (!row || !row.dataset.truck) return;
    selected = Number(row.dataset.truck); cardOpen = true; showView('ops');
  };
  $('ord-book-open').onclick = () => $('book-card').scrollIntoView({behavior: 'smooth', block: 'start'});
  $('book-size').onclick = e => { const b = e.target.closest('.opt'); if (!b) return; bookSize = Number(b.dataset.d); document.querySelectorAll('#book-size .opt').forEach(x => x.classList.toggle('on', x === b)); };
  $('book-slot').onclick = e => { const b = e.target.closest('.opt'); if (!b) return; bookWindow = Number(b.dataset.w); document.querySelectorAll('#book-slot .opt').forEach(x => x.classList.toggle('on', x === b)); };
  const runSearch = async () => {
    const q = $('book-q').value.trim(); if (!q || !data) return;
    $('book-results').innerHTML = '<li class="muted small">Searching…</li>';
    try {
      const found = await searchPlaces(q);
      $('book-results').innerHTML = found.length ? found.map((p, i) => `<li><button data-i="${i}">${p.display_name}</button></li>`).join('') : '<li class="muted small">No results near the depot. Click the map instead.</li>';
      $('book-results').onclick = ev => {
        const b = ev.target.closest('button'); if (!b) return;
        const p = found[Number(b.dataset.i)];
        setBookPoint(Number(p.lat), Number(p.lon), p.display_name.split(',')[0]);
        bookMap.setView([Number(p.lat), Number(p.lon)], 16);
        $('book-results').innerHTML = '';
      };
    } catch (err) { $('book-results').innerHTML = '<li class="muted small">Search is unavailable. Click the map instead.</li>'; }
  };
  $('book-find').onclick = runSearch;
  $('book-q').onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); runSearch(); } };
  $('book-go').onclick = async () => {
    if (!bookPoint || !data) return;
    const [x, y] = fromLatLng({lat: bookPoint.lat, lng: bookPoint.lng});
    const ready = Math.ceil(t), due = bookWindow ? Math.min(data.max_time, ready + bookWindow) : data.max_time;
    $('book-result').hidden = true;
    const ok = await sendEvent({event_type: 'new_order', x, y, demand: bookSize, ready, due, service: 10}, `Order booked at ${fmt(t, 0)} min. All three strategies re-planned.`);
    if (ok) { showBookResult(); renderOrders(); }
  };
  $('order-form').onsubmit = e => {
    e.preventDefault();
    sendEvent({event_type: 'new_order', x: Number($('order-x').value), y: Number($('order-y').value), demand: Number($('order-demand').value),
      ready: Number($('order-ready').value), due: Number($('order-due').value), service: Number($('order-service').value)},
      `New order added at ${fmt(t, 0)} min. All three strategies re-planned.`);
  };

  // Optional deep link, handy for screenshots and demos: ?t=60&truck=4&view=compare&map=plain&clean=1
  const params = new URLSearchParams(location.search);
  if (params.get('clean')) document.body.classList.add('clean');
  if (params.get('map') === 'plain') mode = 'plain';
  api('/state?t=0').then(async first => {
    const wanted = params.has('t') ? Number(params.get('t')) : first.committed_time;
    if (wanted > 0) first = await api(`/state?t=${wanted}`);
    if (params.has('truck')) { selected = Number(params.get('truck')); cardOpen = true; }
    accept(first);
    if (['vehicle', 'compare', 'orders', 'analytics'].includes(params.get('view'))) showView(params.get('view'));
    $('order-ready').value = Math.round(first.time); $('order-due').value = first.max_time;
    say(`${first.dataset} is loaded. Move the slider or press Play.`);
  }).catch(() => {});
})();

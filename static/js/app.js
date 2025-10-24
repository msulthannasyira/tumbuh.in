/* global L, axios */

const state = {
  map: null,
  drawnLayer: null,
  tileLayerGroup: null,
  legendItems: new Map(),
};

const Colors = {
  palette: [
    '#88c34e',
    '#73cf7b',
    '#96d990',
    '#5b7b45',
    '#bedf9d',
    '#d7f1c0',
    '#4b6d33',
  ],
  getColorForKey(key) {
    if (!this.cache) this.cache = new Map();
    if (!this.cache.has(key)) {
      const index = this.cache.size % this.palette.length;
      this.cache.set(key, this.palette[index]);
    }
    return this.cache.get(key);
  },
};

function initMap() {
  const mapContainer = document.getElementById('map');
  if (!mapContainer) return;
  state.map = L.map('map', {
    zoomControl: false,
    minZoom: 4,
  }).setView([-2.5, 117.5], 5);

  L.control.zoom({ position: 'topright' }).addTo(state.map);

  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19,
  }).addTo(state.map);

  state.tileLayerGroup = L.layerGroup().addTo(state.map);

  const drawControl = new L.Control.Draw({
    position: 'topright',
    draw: {
      marker: false,
      circle: false,
      circlemarker: false,
      rectangle: false,
      polyline: false,
      polygon: {
        shapeOptions: {
          color: Colors.getColorForKey('polygon'),
          weight: 2,
          fillOpacity: 0.25,
        },
        allowIntersection: false,
        showArea: true,
      },
    },
    edit: {
      featureGroup: new L.FeatureGroup(),
    },
  });

  state.map.addControl(drawControl);

  state.map.on(L.Draw.Event.CREATED, (event) => {
    if (state.drawnLayer) {
      state.map.removeLayer(state.drawnLayer);
    }
    state.drawnLayer = event.layer;
    state.drawnLayer.addTo(state.map);
    document.querySelector('#area-form button').disabled = false;
    updateStatus('Area siap diproses', 'ready');
  });
}

function updateStatus(message, tone = 'info') {
  const pill = document.querySelector('[data-status-label]');
  if (!pill) return;
  pill.textContent = message;
  pill.dataset.tone = tone;
}

function serializePolygon() {
  if (!state.drawnLayer) return null;
  return state.drawnLayer.toGeoJSON();
}

async function submitArea(event) {
  event.preventDefault();
  const polygon = serializePolygon();
  if (!polygon) {
    updateStatus('Gambar polygon terlebih dahulu', 'warning');
    return;
  }

  updateStatus('Memproses area, mohon tunggu...', 'loading');
  document.querySelector('#area-form button').disabled = true;

  try {
    const tileSizeRaw = event.target.tile_size?.value ?? '15';
    const tileSize = Number(tileSizeRaw);
    if (!Number.isFinite(tileSize) || tileSize < 5 || tileSize > 100) {
      updateStatus('Ukuran tile harus antara 5 dan 100 meter.', 'warning');
      document.querySelector('#area-form button').disabled = false;
      return;
    }

    const payload = {
      name: event.target.name.value || null,
      geometry: polygon,
      tile_size: Math.round(tileSize),
    };

    const response = await axios.post('/areas/process/', payload, {
      headers: { 'Content-Type': 'application/json' },
    });

    if (response.data.status !== 'success') {
      throw new Error(response.data.message || 'Terjadi kesalahan.');
    }

    renderResults(response.data);
    updateStatus('Area berhasil dianalisis', 'success');
    document.querySelector('#area-form button').disabled = false;
  } catch (error) {
    console.error(error);
    updateStatus('Gagal memproses area, coba ulangi.', 'error');
    document.querySelector('#area-form button').disabled = false;
  }
}

function renderResults(data) {
  const { area, tiles, aggregates } = data;
  renderMapTiles(tiles, area.tile_size_m);
  populateLegend(tiles);
  populateSummary(area, aggregates);
  populateTable(tiles);
  renderDominantList(aggregates?.dominant_crops || []);
  renderStatGrid(aggregates?.env_summary || {});
  document.getElementById('results').hidden = false;
}

function renderMapTiles(tiles, tileSize) {
  state.tileLayerGroup.clearLayers();
  tiles.forEach((tile) => {
    if (!tile.geometry) return;
    const color = Colors.getColorForKey(tile.recommendations?.[0]?.plant || 'unknown');
    const polygon = L.polygon(tile.geometry.coordinates[0].map((coord) => [coord[1], coord[0]]), {
      color,
      weight: 1,
      fillOpacity: 0.45,
      fillColor: color,
    });

    polygon.on('click', () => openTileModal(tile));
    polygon.bindTooltip(buildTileTooltip(tile), { sticky: true });
    state.tileLayerGroup.addLayer(polygon);
  });
}

function buildTileTooltip(tile) {
  const top = tile.recommendations?.[0];
  return `Tile (${tile.row_index}, ${tile.col_index})<br>` +
    `Tanaman unggulan: <strong>${top ? top.plant : '—'}</strong><br>` +
    `Confidence: ${(top?.confidence ?? 0) * 100} %`;
}

function openTileModal(tile) {
  const modal = document.getElementById('tile-modal');
  const body = document.getElementById('modal-body');
  const vars = tile.variables || {};
  const recs = tile.recommendations || [];
  const formatCoord = (value) => (typeof value === 'number' ? value.toFixed(6) : '—');
  const formatPercent = (value) => (typeof value === 'number' ? `${(value * 100).toFixed(1)}%` : '—');
  const formatNumber = (value, digits = 1, suffix = '') => (
    typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(digits)}${suffix}` : '—'
  );

  const precipitation = vars?.seasonal?.data?.long_term_avg_precip_mm
    ?? vars?.seasonal?.long_term_avg_precip_mm;
  const temperature = vars?.climate?.data?.[0]?.temp_mean_c ?? vars?.climate?.temp_mean_c;
  const ph = vars?.soil?.properties_at_0_5cm?.ph ?? vars?.soil?.ph;
  const ndvi = vars?.landcover?.vegetation_indices?.ndvi;
  const ndwi = vars?.landcover?.vegetation_indices?.ndwi;
  const elevation = vars?.topography?.data?.elevation_meters ?? vars?.topography?.elevation_meters;

  const stats = [
    { label: 'Curah Hujan', value: formatNumber(precipitation, 1, ' mm') },
    { label: 'Suhu Rata-rata', value: formatNumber(temperature, 1, ' °C') },
    { label: 'pH Tanah', value: formatNumber(ph, 2) },
    { label: 'NDVI', value: formatNumber(ndvi, 3) },
    { label: 'NDWI', value: formatNumber(ndwi, 3) },
    { label: 'Elevasi', value: formatNumber(elevation, 0, ' m') },
  ].filter((item) => item.value !== '—');

  const statsHtml = stats.length
    ? stats.map((stat) => `
        <div class="modal__stat-card">
          <span>${stat.label}</span>
          <strong>${stat.value}</strong>
        </div>
      `).join('')
    : '<p class="modal__empty">Tidak ada data parameter kunci.</p>';

  const recItems = recs.length
    ? recs.map((r, idx) => `
        <li>
          <div>
            <span class="modal__plant">${idx + 1}. ${r.plant || 'Tidak diketahui'}</span>
            ${r.rationale ? `<p class="modal__note">${r.rationale}</p>` : ''}
          </div>
          <strong>${formatPercent(r.confidence)}</strong>
        </li>
      `).join('')
    : '<li class="modal__empty">Belum ada rekomendasi</li>';

  body.innerHTML = `
    <header class="modal__header">
      <h3>Tile (${tile.row_index}, ${tile.col_index})</h3>
      <p class="modal__subtitle">Koordinat pusat tile</p>
      <div class="modal__meta">
        <div class="modal__chip">
          <span>Latitude</span>
          <strong>${formatCoord(tile.centroid.lat)}</strong>
        </div>
        <div class="modal__chip">
          <span>Longitude</span>
          <strong>${formatCoord(tile.centroid.lon)}</strong>
        </div>
      </div>
    </header>
    <section class="modal__section">
      <h4>Rekomendasi Tanaman</h4>
      <ol class="modal__list">${recItems}</ol>
    </section>
    <section class="modal__section">
      <h4>Parameter Kunci</h4>
      <div class="modal__stats">${statsHtml}</div>
    </section>
    <section class="modal__section">
      <details class="modal__details" open>
        <summary>JSON Variabel Lengkap</summary>
        <pre>${JSON.stringify(vars, null, 2)}</pre>
      </details>
    </section>
  `;
  modal.hidden = false;
}

function populateLegend(tiles) {
  const list = document.querySelector('[data-legend-list]');
  list.innerHTML = '';
  const seen = new Map();
  tiles.forEach((tile) => {
    const key = tile.recommendations?.[0]?.plant;
    if (!key || seen.has(key)) return;
    const color = Colors.getColorForKey(key);
    seen.set(key, color);
    const li = document.createElement('li');
    const swatch = document.createElement('span');
    swatch.style.background = color;
    li.appendChild(swatch);
    li.appendChild(document.createTextNode(key));
    list.appendChild(li);
  });
  if (!list.children.length) {
    const li = document.createElement('li');
    li.textContent = 'Belum ada rekomendasi';
    list.appendChild(li);
  }
}

function populateSummary(area, aggregates) {
  const summary = document.getElementById('area-summary');
  const tileCount = document.getElementById('tile-count');
  const processingTime = document.getElementById('processing-time');
  summary.textContent = `${area.name || 'Tanpa Nama'} • UID ${area.id} • Tile ${area.tile_size_m} m`;
  tileCount.textContent = `${aggregates?.tile_count || 0} Tiles`;
  processingTime.textContent = `Waktu proses: ${(aggregates?.processing_seconds || 0).toFixed(1)} detik`;
}

function renderDominantList(dominant) {
  const list = document.querySelector('[data-dominant-list]');
  list.innerHTML = '';
  if (!dominant.length) {
    list.innerHTML = '<li>Tidak ada data</li>';
    return;
  }
  dominant.forEach((item) => {
    const li = document.createElement('li');
    li.innerHTML = `<strong>${item.plant}</strong><span>Jumlah tiles: ${item.tiles} • Rata-rata confidence ${(item.avg_confidence * 100).toFixed(1)}%</span>`;
    list.appendChild(li);
  });
}

function renderStatGrid(stats) {
  const grid = document.querySelector('[data-stat-grid]');
  grid.innerHTML = '';
  const entries = Object.entries(stats);
  if (!entries.length) {
    grid.innerHTML = '<p>Tidak ada statistik tersedia.</p>';
    return;
  }
  entries.forEach(([key, value]) => {
    const div = document.createElement('div');
    div.className = 'stat';
    div.innerHTML = `<h4>${key}</h4><p>${value}</p>`;
    grid.appendChild(div);
  });
}

function populateTable(tiles) {
  const tbody = document.querySelector('#tiles-table tbody');
  tbody.innerHTML = '';
  tiles.forEach((tile) => {
    const tr = document.createElement('tr');
    const top5 = (tile.recommendations || [])
      .map((r, idx) => `${idx + 1}. ${r.plant} (${(r.confidence * 100).toFixed(1)}%)`).join('<br>');
    const climateMean = tile.variables?.climate?.data?.[0]?.temp_mean_c ?? tile.variables?.climate?.temp_mean_c;
    const precip = tile.variables?.seasonal?.data?.long_term_avg_precip_mm ?? tile.variables?.seasonal?.long_term_avg_precip_mm;
    const soil = tile.variables?.soil?.properties_at_0_5cm || {};
    const row = [
      `${tile.row_index},${tile.col_index}`,
      tile.centroid.lat.toFixed(5),
      tile.centroid.lon.toFixed(5),
      precip ? Number(precip).toFixed(1) : '—',
      climateMean ? Number(climateMean).toFixed(1) : '—',
      soil.ph ? Number(soil.ph).toFixed(2) : '—',
      soilTexture(soil),
      tile.variables?.topography?.data?.elevation_meters ?? '—',
      tile.variables?.landcover?.vegetation_indices?.ndvi?.toFixed?.(3) ?? '—',
      tile.variables?.landcover?.vegetation_indices?.ndwi?.toFixed?.(3) ?? '—',
      top5 || '—',
    ];
    row.forEach((value) => {
      const td = document.createElement('td');
      td.innerHTML = String(value);
      tr.appendChild(td);
    });
    tr.addEventListener('click', () => openTileModal(tile));
    tbody.appendChild(tr);
  });
}

function soilTexture(soil) {
  if (!soil.sand_g_kg || !soil.clay_g_kg) return '—';
  return `Sand ${soil.sand_g_kg} g/kg · Clay ${soil.clay_g_kg} g/kg`;
}

function bindModal() {
  const modal = document.getElementById('tile-modal');
  if (!modal) return;
  document.querySelector('[data-close-modal]')?.addEventListener('click', () => {
    modal.hidden = true;
  });
  modal.addEventListener('click', (event) => {
    if (event.target.id === 'tile-modal') {
      modal.hidden = true;
    }
  });
}

function bindForm() {
  const form = document.getElementById('area-form');
  if (!form) return;
  form.addEventListener('submit', submitArea);
}

function initEvents() {
  const mapElement = document.getElementById('map');
  const form = document.getElementById('area-form');
  if (!mapElement || !form) return;

  initMap();
  bindForm();
  bindModal();
}

window.addEventListener('DOMContentLoaded', initEvents);

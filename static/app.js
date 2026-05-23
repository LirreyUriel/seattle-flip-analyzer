/* ============================================================
   Seattle Flip Analyzer — Frontend JS
   ============================================================ */

'use strict';

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let allProperties = [];
let activeFilters = {
  min_score: 0, max_score: 100,
  neighborhood: '',
  distress_type: '',
  min_price: 0, max_price: 500000,
  property_type: '',
  status_filter: '',
  sort_by: 'score',
  sort_dir: 'desc',
  favorites_only: false,
};
let currentPropertyId = null;
let priceChart = null;
let map = null;
let mapMarkers = [];

// Dashboard state
let scatterChart = null;
let dashboardProps = [];       // last-rendered property list
let dashView = 'grid';         // 'grid' | 'dashboard'
let dashSortCol = 'flip_score';
let dashSortDir = 'desc';
let dashSearch = '';
let quickViewActiveId = null;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const fmt$ = n => '$' + Number(n).toLocaleString();
const fmtPct = n => (n > 0 ? '+' : '') + n.toFixed(1) + '%';

function distressClass(type) {
  const t = (type || '').toLowerCase();
  if (t.includes('reo'))           return 'distress-reo';
  if (t.includes('short'))         return 'distress-short-sale';
  if (t.includes('pre'))           return 'distress-pre-foreclos';
  if (t.includes('estate'))        return 'distress-estate-sale';
  if (t.includes('back'))          return 'distress-back-on-mark';
  return 'distress-standard';
}

function scoreClass(score) {
  if (score >= 70) return 'score-green';
  if (score >= 40) return 'score-yellow';
  return 'score-red';
}

function showToast(msg, duration = 2500) {
  const toast = document.getElementById('toast');
  toast.textContent = msg;
  toast.classList.add('show');
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => toast.classList.remove('show'), duration);
}

function esc(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ---------------------------------------------------------------------------
// Map
// ---------------------------------------------------------------------------
function initMap() {
  if (map) return;
  map = L.map('property-map', {
    center: [47.615, -122.320],
    zoom: 12,
    zoomControl: true,
  });
  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
    attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> © <a href="https://carto.com/">CARTO</a>',
    maxZoom: 19,
  }).addTo(map);
}

function updateMapMarkers(props) {
  if (!map) return;

  // Remove old markers
  mapMarkers.forEach(m => m.remove());
  mapMarkers = [];

  props.forEach(p => {
    if (!p.lat || !p.lng) return;
    const cls = scoreClass(p.flip_score);
    const icon = L.divIcon({
      className: '',
      html: `<div class="map-pin ${cls}">${p.flip_score}</div>`,
      iconSize: [38, 38],
      iconAnchor: [19, 19],
      popupAnchor: [0, -22],
    });

    const marker = L.marker([p.lat, p.lng], { icon }).addTo(map);
    const popupHTML = `
<div class="map-popup">
  <div class="map-popup-address">${esc(p.address)}</div>
  <div class="map-popup-sub">${esc(p.neighborhood)} · ${esc(p.distress_type)}</div>
  <div class="map-popup-price">${fmt$(p.price)}</div>
  <button class="map-popup-btn" onclick="openDetail('${esc(p.id)}');this.closest('.leaflet-popup').querySelector('.leaflet-popup-close-button').click()">View Details</button>
</div>`;
    marker.bindPopup(popupHTML, { maxWidth: 260, closeButton: true });
    mapMarkers.push(marker);
  });
}

function toggleMap() {
  const panel = document.getElementById('map-panel');
  const collapsed = panel.classList.toggle('collapsed');
  if (!collapsed) {
    // Panel just expanded — invalidate Leaflet size so tiles render correctly
    setTimeout(() => map && map.invalidateSize(), 310);
  }
  document.getElementById('map-toggle-btn').textContent = collapsed ? '🗺 Map' : '🗺 Hide';
}

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------
async function apiFetch(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function loadStatus() {
  try {
    const s = await apiFetch('/api/status');
    const ts = s.last_updated
      ? new Date(s.last_updated).toLocaleString()
      : 'Never';
    document.getElementById('meta-text').textContent =
      `${s.property_count} properties · Updated ${ts} · ${s.demo_mode ? '⚡ Demo Mode' : '🔴 Live'}`;

    // Show/hide demo banner
    const banner = document.getElementById('demo-banner');
    const dismissed = sessionStorage.getItem('demo-banner-dismissed');
    if (s.demo_mode && !dismissed) {
      banner.innerHTML = `
<div class="demo-banner">
  <span>⚡ <strong>Demo Mode</strong> — Sample properties with generated data.
  External links search by neighborhood, not a specific property.
  To use real listings, add a <code>RAPIDAPI_KEY</code> to your <code>.env</code> file.</span>
  <button class="demo-banner-close" onclick="this.closest('.demo-banner').parentElement.innerHTML='';sessionStorage.setItem('demo-banner-dismissed','1')">✕</button>
</div>`;
    } else {
      banner.innerHTML = '';
    }
  } catch {}
}

async function loadProperties() {
  const grid = document.getElementById('property-grid');
  grid.innerHTML = `<div class="loading-overlay"><div class="spinner"></div><p>Loading properties…</p></div>`;

  const params = new URLSearchParams({
    min_score: activeFilters.min_score,
    max_score: activeFilters.max_score,
    neighborhood: activeFilters.neighborhood,
    distress_type: activeFilters.distress_type,
    min_price: activeFilters.min_price,
    max_price: activeFilters.max_price,
    property_type: activeFilters.property_type,
    status_filter: activeFilters.status_filter,
    sort_by: activeFilters.sort_by,
    sort_dir: activeFilters.sort_dir,
    favorites_only: activeFilters.favorites_only,
  });

  try {
    const data = await apiFetch('/api/properties?' + params);
    allProperties = data.properties;
    renderGrid(allProperties);
    renderDashboard(allProperties);
    updateStatsBar(data);
  } catch (err) {
    grid.innerHTML = `<div class="no-results"><h3>Failed to load properties</h3><p>${err.message}</p></div>`;
  }
}

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------
function renderGrid(props) {
  const grid = document.getElementById('property-grid');
  if (!props.length) {
    grid.innerHTML = `<div class="no-results"><h3>No properties match your filters</h3><p>Try adjusting the score range, price, or neighborhood.</p></div>`;
    updateMapMarkers([]);
    return;
  }

  grid.innerHTML = props.map(p => cardHTML(p)).join('');
  updateMapMarkers(props);

  // Attach card + favorite listeners
  grid.querySelectorAll('.property-card').forEach(card => {
    card.addEventListener('click', e => {
      if (e.target.closest('.fav-btn')) return;
      if (e.target.closest('.status-badge')) return;
      openDetail(card.dataset.id);
    });
  });

  grid.querySelectorAll('.fav-btn').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      toggleFavorite(btn.dataset.id, btn);
    });
  });
}

function cardHTML(p) {
  const roiColor = p.roi_pct >= 15 ? 'positive' : p.roi_pct >= 0 ? '' : 'negative';
  const noteHint = p.note ? `<div class="note-indicator">📝 Note</div>` : '';
  const bom = p.back_on_market
    ? `<span style="color:var(--accent);font-size:.72rem;font-weight:700">BOM</span>` : '';
  const status = p.status || 'new';

  return `
<div class="property-card" data-id="${esc(p.id)}">
  <div class="card-top">
    <div class="flip-score-badge ${scoreClass(p.flip_score)}" title="Flip Score">${p.flip_score}</div>
    <div class="card-top-right">
      <button class="fav-btn ${p.is_favorite ? 'active' : ''}" data-id="${esc(p.id)}" title="Star property">
        ${p.is_favorite ? '★' : '☆'}
      </button>
      <span class="distress-badge ${distressClass(p.distress_type)}">${esc(p.distress_type)}</span>
      ${bom}
    </div>
  </div>

  <div>
    <div class="card-address">${esc(p.address)}</div>
    <div class="card-neighborhood">${esc(p.neighborhood)} · ${esc(p.property_type)}</div>
  </div>

  <div class="card-price">${fmt$(p.price)}</div>

  <div class="card-stats">
    <span>🛏 ${p.beds}</span>
    <span>🛁 ${p.baths}</span>
    <span>📐 ${Number(p.sqft).toLocaleString()} sqft</span>
    <span>📅 ${p.dom}d on mkt</span>
    <span title="Built">🏗 ${p.year_built}</span>
  </div>

  <div class="card-financials">
    <div class="fin-label">ARV</div>
    <div class="fin-label">Reno</div>
    <div class="fin-label">ROI</div>
    <div class="fin-value neutral">${fmt$(p.arv)}</div>
    <div class="fin-value">${fmt$(p.renovation_cost)}</div>
    <div class="fin-value ${roiColor}">${fmtPct(p.roi_pct)}</div>
  </div>

  <div class="card-footer-row">
    <span class="status-badge status-${status}" data-status="${status}" data-id="${esc(p.id)}"
          onclick="event.stopPropagation();cycleStatus('${esc(p.id)}',this)"
          title="Click to change status">${formatStatus(status)}</span>
    ${noteHint}
  </div>
</div>`;
}

function updateStatsBar(data) {
  const props = data.properties;
  if (!props.length) {
    document.getElementById('stats-bar').textContent = 'No results';
    return;
  }
  const avgScore = Math.round(props.reduce((s, p) => s + p.flip_score, 0) / props.length);
  const highScore = props.reduce((best, p) => p.flip_score > best.flip_score ? p : best, props[0]);
  const avgROI = (props.reduce((s, p) => s + p.roi_pct, 0) / props.length).toFixed(1);
  document.getElementById('stats-bar').innerHTML =
    `<span>Showing <strong>${props.length}</strong> properties</span>` +
    `<span>Avg score: <strong>${avgScore}</strong></span>` +
    `<span>Avg ROI: <strong>${avgROI}%</strong></span>` +
    `<span>Top pick: <strong>${highScore.address}</strong> (${highScore.flip_score})</span>`;
}

// ---------------------------------------------------------------------------
// Detail modal
// ---------------------------------------------------------------------------
async function openDetail(id) {
  currentPropertyId = id;
  const backdrop = document.getElementById('modal-backdrop');
  backdrop.classList.remove('hidden');
  document.body.style.overflow = 'hidden';
  document.getElementById('modal-body').innerHTML =
    `<div class="loading-overlay"><div class="spinner"></div><p>Loading details…</p></div>`;

  try {
    const p = await apiFetch(`/api/properties/${id}`);
    renderModal(p);
  } catch (err) {
    document.getElementById('modal-body').innerHTML =
      `<p style="color:var(--red)">Failed to load: ${err.message}</p>`;
  }
}

function renderModal(p) {
  const roiColor = p.roi_pct >= 15 ? 'positive' : p.roi_pct >= 0 ? '' : 'negative';
  const profit = p.arv - p.price - p.renovation_cost;

  // Header
  document.getElementById('modal-header').innerHTML = `
<div class="modal-top-row">
  <div class="modal-title-block">
    <div style="display:flex;gap:.6rem;flex-wrap:wrap;margin-bottom:.4rem">
      <span class="distress-badge ${distressClass(p.distress_type)}">${esc(p.distress_type)}</span>
      ${p.back_on_market ? '<span class="distress-badge distress-back-on-mark">Back on Market</span>' : ''}
      <span class="source-badge">${esc(p.source)}</span>
    </div>
    <div class="modal-title">${esc(p.address)}</div>
    <div class="modal-subtitle">${esc(p.neighborhood)} · ${esc(p.property_type)} · Built ${p.year_built}</div>
    <div class="modal-price">${fmt$(p.price)}</div>
    <div class="modal-ext-links">
      <a href="${esc(p.zillow_url)}" target="_blank" rel="noopener" class="ext-link-pill">
        <svg viewBox="0 0 24 24" width="13" height="13"><path d="M12 2L2 9h3v13h14V9h3L12 2z"/></svg>
        Zillow
      </a>
      <a href="${esc(p.redfin_url)}" target="_blank" rel="noopener" class="ext-link-pill">
        <svg viewBox="0 0 24 24" width="13" height="13"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 15v-4H7l5-8v4h4l-5 8z"/></svg>
        Redfin
      </a>
      <a href="${esc(p.propwire_url || '#')}" target="_blank" rel="noopener" class="ext-link-pill ext-link-propwire">
        <svg viewBox="0 0 24 24" width="13" height="13"><path d="M12 3C7 3 3 7 3 12s4 9 9 9 9-4 9-9-4-9-9-9zm1 14h-2v-6h2v6zm0-8h-2V7h2v2z"/></svg>
        Propwire
      </a>
    </div>
  </div>
  <div class="modal-score-block">
    <div class="flip-score-badge ${scoreClass(p.flip_score)}">${p.flip_score}</div>
    <div class="modal-score-label">Flip Score</div>
    <button class="fav-btn ${p.is_favorite ? 'active' : ''}" id="modal-fav-btn" data-id="${esc(p.id)}" style="font-size:1.6rem">
      ${p.is_favorite ? '★' : '☆'}
    </button>
  </div>
</div>
<button class="modal-close" id="modal-close-btn" aria-label="Close">✕</button>`;

  document.getElementById('modal-close-btn').addEventListener('click', closeModal);
  document.getElementById('modal-fav-btn').addEventListener('click', e => {
    toggleFavorite(p.id, e.currentTarget);
  });

  // Body
  const arv_bd = p.arv_breakdown || {};

  const renoRows = Object.entries(p.renovation_breakdown || {}).map(([k, v]) =>
    `<tr><td>${esc(k)}</td><td class="td-right">${fmt$(v)}</td></tr>`
  ).join('');

  const kwHTML = p.distress_keywords?.length
    ? p.distress_keywords.map(kw => `<span class="keyword-chip">${esc(kw)}</span>`).join('')
    : '<span style="color:var(--text-muted);font-size:.82rem">None detected</span>';

  // ── Score factor computations (mirrors flip_scorer.py logic) ──────────────
  const _avg_dom   = p.neighborhood_avg_dom || 22;
  const _profit    = p.arv - p.price - p.renovation_cost;
  const _eq_pct    = p.arv > p.price ? (p.arv - p.price) / p.arv * 100 : 0;

  const f_arv      = Math.min(100, _eq_pct / 30 * 100);
  const f_roi      = p.roi_pct > 0 ? Math.min(100, p.roi_pct / 25 * 100) : 0;

  const _pr_ratio  = p.renovation_cost > 0 ? _profit / p.renovation_cost : 0;
  const f_pr       = _pr_ratio >= 2 ? 100 : _pr_ratio >= 1 ? 75 : _pr_ratio >= 0.5 ? 50 : _pr_ratio >= 0 ? 25 : 0;
  const f_reno_lvl = { light: 100, medium: 50, heavy: 10 }[p.renovation_level?.toLowerCase()] ?? 50;

  const _sqft      = p.sqft || 0;
  const f_size     = _sqft >= 800 && _sqft <= 2500 ? 100
                   : (_sqft >= 600 && _sqft < 800) || (_sqft > 2500 && _sqft <= 3500) ? 75
                   : (_sqft >= 500 && _sqft < 600) || (_sqft > 3500 && _sqft <= 4500) ? 45
                   : _sqft > 0 ? 10 : 50;
  const _yr        = p.year_built || 1970;
  const f_struct   = _yr >= 2000 ? 100 : _yr >= 1990 ? 82 : _yr >= 1980 ? 60 : _yr >= 1970 ? 38 : _yr >= 1960 ? 20 : 8;
  const _dom_ratio = p.dom / _avg_dom;
  const f_vel      = _dom_ratio <= 0.50 ? 100 : _dom_ratio <= 0.75 ? 85 : _dom_ratio <= 1.00 ? 70
                   : _dom_ratio <= 1.50 ? 50 : _dom_ratio <= 2.00 ? 30 : _dom_ratio <= 3.00 ? 15 : 5;

  const _kw_sub    = Math.min(50, (p.distress_keywords?.length || 0) * 15);
  const _red_sub   = Math.min(30, p.price_reductions * 8 + p.price_reduction_pct * 1.2);
  const _bom_sub   = p.back_on_market ? 20 : 0;
  const f_distress = Math.min(100, _kw_sub + _red_sub + _bom_sub);
  const _nbhd      = p.neighborhood.toLowerCase();
  const f_nbhd     = ['rainier valley','beacon hill','white center','delridge','georgetown'].some(n => _nbhd.includes(n)) ? 100
                   : ['columbia city','northgate','west seattle'].some(n => _nbhd.includes(n)) ? 65 : 35;

  // ── Score breakdown HTML (4 categories) ───────────────────────────────────
  const scoreCategories = [
    {
      name: 'Profitability', totalWeight: 40,
      factors: [
        { label: 'Price vs ARV',   desc: `${_eq_pct.toFixed(1)}% equity spread (target >30%)`,                    score: f_arv,      w: 0.30 },
        { label: 'Estimated ROI',  desc: `${fmtPct(p.roi_pct)} ROI (target >25%)`,                                score: f_roi,      w: 0.10 },
      ],
    },
    {
      name: 'Execution Efficiency', totalWeight: 20,
      factors: [
        { label: 'Profit-to-Reno', desc: `${_pr_ratio.toFixed(2)}× (profit ÷ reno cost)`,                         score: f_pr,       w: 0.10 },
        { label: 'Reno Level',     desc: `${esc(p.renovation_level || '—')} — light=100, medium=50, heavy=10`,     score: f_reno_lvl, w: 0.10 },
      ],
    },
    {
      name: 'Liquidity & Asset Risk', totalWeight: 25,
      factors: [
        { label: 'Property Size',   desc: `${Number(_sqft).toLocaleString()} sqft — sweet spot 800–2,500`,         score: f_size,     w: 0.10 },
        { label: 'Structural Risk', desc: `Built ${_yr} — post-2000=100, pre-1960=8 (infra risk)`,                 score: f_struct,   w: 0.10 },
        { label: 'Market Velocity', desc: `${p.dom}d vs ${_avg_dom}d avg (${_dom_ratio.toFixed(1)}×) — high DOM = liquidity risk`, score: f_vel, w: 0.05 },
      ],
    },
    {
      name: 'Market Momentum', totalWeight: 15,
      factors: [
        { label: 'Distress & Cuts', desc: `${p.distress_keywords?.length || 0} keywords · ${p.price_reductions} price cuts · ${p.back_on_market ? 'BOM' : 'no BOM'}`, score: f_distress, w: 0.10 },
        { label: 'Nbhd Upside',    desc: `${esc(p.neighborhood)} — top=100, moderate=65, other=35`,                score: f_nbhd,     w: 0.05 },
      ],
    },
  ];

  const scoreCatHTML = scoreCategories.map(cat => `
<div class="score-category">
  <div class="score-cat-header">
    <span class="score-cat-name">${esc(cat.name)}</span>
    <span class="score-cat-weight">${cat.totalWeight}% weight · +${cat.factors.reduce((s,f) => s + Math.round(f.score * f.w), 0)} pts</span>
  </div>
  ${cat.factors.map(f => `
  <div class="score-row">
    <div class="score-row-info">
      <span class="score-row-label">${esc(f.label)}</span>
      <span class="score-row-desc">${f.desc}</span>
    </div>
    <div class="score-bar-wrap"><div class="score-bar-fill" style="width:${Math.round(f.score)}%"></div></div>
    <span class="score-row-val">+${Math.round(f.score * f.w)}</span>
  </div>`).join('')}
</div>`).join('');

  document.getElementById('modal-body').innerHTML = `

<!-- Key metrics -->
<div class="modal-section">
  <h3>Key Metrics</h3>
  <div class="metrics-grid">
    <div class="metric-card">
      <div class="metric-label">ARV <span style="font-size:.65rem;color:var(--yellow)">(estimated)</span></div>
      <div class="metric-value neutral">${fmt$(p.arv)}</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">Reno Cost <span style="font-size:.65rem;color:var(--yellow)">(estimated)</span></div>
      <div class="metric-value">${fmt$(p.renovation_cost)}</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">Est. Profit <span style="font-size:.65rem;color:var(--yellow)">(estimated)</span></div>
      <div class="metric-value ${profit >= 0 ? 'positive' : 'negative'}">${fmt$(profit)}</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">ROI <span style="font-size:.65rem;color:var(--yellow)">(estimated)</span></div>
      <div class="metric-value ${roiColor}">${fmtPct(p.roi_pct)}</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">DOM <span style="font-size:.65rem;color:var(--green)">(listing)</span></div>
      <div class="metric-value">${p.dom}d <span style="font-size:.72rem;color:var(--text-muted)">(avg ${_avg_dom}d)</span></div>
    </div>
    <div class="metric-card">
      <div class="metric-label">Price Reductions <span style="font-size:.65rem;color:var(--green)">(listing)</span></div>
      <div class="metric-value">${p.price_reductions} <span style="font-size:.72rem;color:var(--text-muted)">(${p.price_reduction_pct}%)</span></div>
    </div>
    <div class="metric-card">
      <div class="metric-label">Reno Level <span style="font-size:.65rem;color:var(--yellow)">(estimated)</span></div>
      <div class="metric-value">${esc(p.renovation_level || '—')}</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">ARV $/sqft <span style="font-size:.65rem;color:var(--yellow)">(estimated)</span></div>
      <div class="metric-value">${fmt$(p.arv_psf)}</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">Sq Ft <span style="font-size:.65rem;color:var(--green)">(listing)</span></div>
      <div class="metric-value">${Number(p.sqft).toLocaleString()}</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">Year Built <span style="font-size:.65rem;color:var(--green)">(listing)</span></div>
      <div class="metric-value">${p.year_built}</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">Price/sqft <span style="font-size:.65rem;color:var(--green)">(listing)</span></div>
      <div class="metric-value">${fmt$(Math.round(p.price / p.sqft))}</div>
    </div>
  </div>
</div>

<!-- Additional details -->
<div class="modal-section">
  <h3>Additional Details</h3>
  <p class="source-note"><strong>HOA, tax &amp; buyer's agent:</strong> Estimated values — HOA based on property type &amp; size (King County averages); tax based on ~1.1% of list price (King County avg effective rate); buyer's agent is market standard 2.5%. Verify with listing agent and county records.</p>
  <div class="metrics-grid">
    <div class="metric-card">
      <div class="metric-label">HOA Dues <span style="font-size:.65rem;color:var(--yellow)">(estimated)</span></div>
      <div class="metric-value">${p.hoa_monthly > 0 ? fmt$(p.hoa_monthly) + '<span style="font-size:.72rem;color:var(--text-muted)">/mo</span>' : '<span style="color:var(--text-muted)">None / N/A</span>'}</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">Annual HOA <span style="font-size:.65rem;color:var(--yellow)">(estimated)</span></div>
      <div class="metric-value">${p.hoa_monthly > 0 ? fmt$(p.hoa_monthly * 12) : '<span style="color:var(--text-muted)">—</span>'}</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">Tax (Annual) <span style="font-size:.65rem;color:var(--yellow)">(estimated)</span></div>
      <div class="metric-value">${p.tax_annual != null ? fmt$(p.tax_annual) : '—'}</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">Tax (Monthly) <span style="font-size:.65rem;color:var(--yellow)">(estimated)</span></div>
      <div class="metric-value">${p.tax_annual != null ? fmt$(Math.round(p.tax_annual / 12)) : '—'}</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">Buyer's Agent <span style="font-size:.65rem;color:var(--yellow)">(estimated)</span></div>
      <div class="metric-value">${p.buyers_agent_pct != null ? p.buyers_agent_pct + '%' : '—'} <span style="font-size:.72rem;color:var(--text-muted)">(${p.buyers_agent_pct != null ? fmt$(Math.round(p.price * p.buyers_agent_pct / 100)) : '—'})</span></div>
    </div>
    <div class="metric-card">
      <div class="metric-label">Lot Size <span style="font-size:.65rem;color:var(--green)">(listing)</span></div>
      <div class="metric-value">${p.lot_sqft > 0 ? Number(p.lot_sqft).toLocaleString() + ' sqft' : '<span style="color:var(--text-muted)">—</span>'}</div>
    </div>
  </div>
</div>

<!-- Description -->
<div class="modal-section">
  <h3>Listing Description</h3>
  <p style="font-size:.85rem;line-height:1.7;color:var(--text-muted)">${esc(p.description)}</p>
</div>

<!-- Distress keywords -->
<div class="modal-section">
  <h3>Distress Signals</h3>
  <div class="keyword-chips">${kwHTML}</div>
</div>

<!-- ARV Estimate -->
<div class="modal-section">
  <h3>ARV Estimate
    <span class="arv-confidence arv-conf-${esc(arv_bd.arv_confidence || 'static')}">
      ${esc(arv_bd.arv_confidence || 'static')} · ${arv_bd.n_comps || 0} comps
    </span>
    <span class="info-wrap">
      <span class="info-icon">i</span>
      <div class="info-tooltip">
        <span class="tip-head">How ARV is calculated</span>
        ARV = Neighborhood median $/sqft × property sqft, with a property-type adjustment.<br><br>
        <span class="tip-head">Type adjustments</span>
        <div class="tip-row"><span>SFH</span><span>No adjustment</span></div>
        <div class="tip-row"><span>Townhouse</span><span>−5%</span></div>
        <div class="tip-row"><span>Condo</span><span>−15%</span></div>
        <span class="tip-head">$/sqft used for ${esc(p.neighborhood)}</span>
        <div class="tip-row"><span>Base $/sqft</span><span>${fmt$(arv_bd.price_per_sqft || 0)}</span></div>
        <div class="tip-source">Method: ${esc(arv_bd.arv_method || 'static table')}.</div>
      </div>
    </span>
  </h3>
  <p class="source-note"><strong>Method:</strong> ${esc(arv_bd.arv_method || 'static table')}. ${arv_bd.n_comps > 0 ? `Based on ${arv_bd.n_comps} recent sold comps filtered for similar SFH within ±20% sqft.` : 'No recent comps available — using static neighborhood table.'}</p>
  <table class="data-table">
    <thead><tr><th>ARV Factor</th><th class="td-right">Value</th></tr></thead>
    <tbody>
      <tr><td>$/sqft used (${esc(p.neighborhood)})</td><td class="td-right">${fmt$(arv_bd.price_per_sqft || 0)}/sqft</td></tr>
      <tr><td>Static table $/sqft</td><td class="td-right" style="color:var(--text-muted)">${fmt$(arv_bd.static_psf || arv_bd.price_per_sqft || 0)}/sqft</td></tr>
      <tr><td>Property sqft</td><td class="td-right">${Number(arv_bd.sqft || p.sqft).toLocaleString()} sqft</td></tr>
      <tr><td>Property type adjustment</td><td class="td-right" style="font-size:.78rem">${esc(arv_bd.property_type_adjustment || 'No adjustment')}</td></tr>
      <tr><td class="td-total">Estimated ARV</td><td class="td-right td-total">${fmt$(p.arv)}</td></tr>
    </tbody>
  </table>
</div>

<!-- Renovation Estimate -->
<div class="modal-section">
  <h3>Renovation Estimate
    <span class="info-wrap">
      <span class="info-icon">i</span>
      <div class="info-tooltip">
        <span class="tip-head">Level (detected from listing keywords)</span>
        <div class="tip-row"><span>Light — $28/sqft</span><span>no distress keywords</span></div>
        <div class="tip-row"><span>Medium — $52/sqft</span><span>fixer, TLC, as-is, needs work…</span></div>
        <div class="tip-row"><span>Heavy — $85/sqft</span><span>gut, foundation, pre-1945</span></div>
        <span class="tip-head">Age multiplier (applied to base cost)</span>
        <div class="tip-row"><span>Built before 1960</span><span>×1.18</span></div>
        <div class="tip-row"><span>Built before 1980</span><span>×1.08</span></div>
        <div class="tip-row"><span>1980 or newer</span><span>×1.00</span></div>
        <span class="tip-head">Cost allocation (% of total)</span>
        <div class="tip-row"><span>Kitchen</span><span>20%</span></div>
        <div class="tip-row"><span>Bathrooms</span><span>15%</span></div>
        <div class="tip-row"><span>Roof &amp; Exterior</span><span>15%</span></div>
        <div class="tip-row"><span>HVAC / Plumbing / Electric</span><span>18%</span></div>
        <div class="tip-row"><span>Flooring</span><span>12%</span></div>
        <div class="tip-row"><span>Windows &amp; Doors</span><span>8%</span></div>
        <div class="tip-row"><span>Permits &amp; Overhead</span><span>7%</span></div>
        <div class="tip-row"><span>Landscaping</span><span>5%</span></div>
        <div class="tip-source">Source: Internal keyword-based model. Not contractor quotes or permit data. Get a professional estimate before committing.</div>
      </div>
    </span>
  </h3>
  <p class="source-note"><strong>Source:</strong> Internal estimation model — level determined by scanning the listing description for distress keywords; costs derived from Seattle contractor rate benchmarks. Not a contractor quote or inspection report. <strong>Level detected: ${esc(p.renovation_level || '—')}</strong> (${p.year_built < 1945 ? 'pre-1945 → heavy forced' : p.year_built < 1960 ? '×1.18 age multiplier applied' : p.year_built < 1980 ? '×1.08 age multiplier applied' : 'no age multiplier'}).</p>
  <table class="data-table">
    <thead><tr><th>Renovation Item</th><th class="td-right">% Allocation</th><th class="td-right">Est. Cost</th></tr></thead>
    <tbody>
      <tr><td>Kitchen</td><td class="td-right">20%</td><td class="td-right">${fmt$(Math.round(p.renovation_cost * 0.20))}</td></tr>
      <tr><td>Bathrooms</td><td class="td-right">15%</td><td class="td-right">${fmt$(Math.round(p.renovation_cost * 0.15))}</td></tr>
      <tr><td>Roof &amp; Exterior</td><td class="td-right">15%</td><td class="td-right">${fmt$(Math.round(p.renovation_cost * 0.15))}</td></tr>
      <tr><td>HVAC / Plumbing / Electric</td><td class="td-right">18%</td><td class="td-right">${fmt$(Math.round(p.renovation_cost * 0.18))}</td></tr>
      <tr><td>Flooring</td><td class="td-right">12%</td><td class="td-right">${fmt$(Math.round(p.renovation_cost * 0.12))}</td></tr>
      <tr><td>Windows &amp; Doors</td><td class="td-right">8%</td><td class="td-right">${fmt$(Math.round(p.renovation_cost * 0.08))}</td></tr>
      <tr><td>Permits &amp; Overhead</td><td class="td-right">7%</td><td class="td-right">${fmt$(Math.round(p.renovation_cost * 0.07))}</td></tr>
      <tr><td>Landscaping</td><td class="td-right">5%</td><td class="td-right">${fmt$(Math.round(p.renovation_cost * 0.05))}</td></tr>
      <tr><td class="td-total">Total (${esc(p.renovation_level || '')} — $${p.renovation_level === 'heavy' ? '85' : p.renovation_level === 'medium' ? '52' : '28'}/sqft base)</td><td class="td-right td-total">100%</td><td class="td-right td-total">${fmt$(p.renovation_cost)}</td></tr>
    </tbody>
  </table>
</div>

<!-- Score breakdown -->
<div class="modal-section">
  <h3>Score Breakdown</h3>
  <div class="score-categories">${scoreCatHTML}</div>
</div>

<!-- Price history chart -->
<div class="modal-section">
  <h3>Price History</h3>
  <div class="chart-container">
    <canvas id="price-chart"></canvas>
  </div>
</div>

<!-- Notes -->
<div class="modal-section">
  <h3>Personal Notes</h3>
  <textarea class="notes-textarea" id="notes-input" placeholder="Add your notes about this property…">${esc(p.note || '')}</textarea>
  <div class="notes-row">
    <span class="note-saved" id="note-saved">Saved ✓</span>
    <button class="btn btn-primary" id="save-note-btn">Save Note</button>
  </div>
</div>

<!-- External links -->
<div class="modal-section">
  <h3>External Links</h3>
  <div class="links-row">
    <a href="${esc(p.zillow_url)}" target="_blank" rel="noopener" class="ext-link">
      <svg viewBox="0 0 24 24"><path d="M12 2L2 9h3v13h14V9h3L12 2z"/></svg>
        ${p.source === 'demo' ? 'Search Zillow (neighborhood)' : 'Search on Zillow'}
    </a>
    <a href="${esc(p.redfin_url)}" target="_blank" rel="noopener" class="ext-link">
      <svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 15v-4H7l5-8v4h4l-5 8z"/></svg>
      ${p.source === 'redfin' ? 'View on Redfin ↗' : p.source === 'demo' ? 'Search Redfin (neighborhood)' : 'View on Redfin'}
    </a>
  </div>
  ${p.source === 'demo' ? `<p class="demo-link-note">Sample data — links search <strong>${esc(p.neighborhood)}</strong> on the real site, not this specific property.</p>` : ''}
  ${p.source === 'redfin' ? `<p class="demo-link-note" style="font-style:normal;color:var(--green)">✓ Real listing — Redfin link goes directly to this property.</p>` : ''}
</div>

${p.source === 'demo' ? `
<div class="modal-section">
  <details class="setup-guide">
    <summary>How to connect real Zillow listings</summary>
    <ol>
      <li>Sign up at <strong>rapidapi.com</strong> and search for <em>"Zillow"</em></li>
      <li>Subscribe to the <strong>zillow-com1</strong> API (free tier available)</li>
      <li>Copy your API key from the dashboard</li>
      <li>In <code>C:\\Users\\lirre\\seattle-flip-analyzer\\</code>, create a file named <code>.env</code>:<br>
        <code>RAPIDAPI_KEY=your_key_here</code></li>
      <li>Restart the server — real Seattle listings load automatically</li>
    </ol>
  </details>
</div>` : ''}`;

  // Render price history chart
  renderPriceChart(p.price_history || []);

  // Save note handler
  document.getElementById('save-note-btn').addEventListener('click', () => {
    saveNote(p.id);
  });
}

function renderPriceChart(history) {
  const canvas = document.getElementById('price-chart');
  if (!canvas || !history.length) return;

  if (priceChart) { priceChart.destroy(); priceChart = null; }

  const labels = history.map(h => h.date);
  const data   = history.map(h => h.price);

  priceChart = new Chart(canvas, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'List Price',
        data,
        borderColor: '#4f8ef7',
        backgroundColor: 'rgba(79,142,247,.08)',
        borderWidth: 2,
        pointRadius: 3,
        pointHoverRadius: 5,
        fill: true,
        tension: 0.3,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => '$' + Number(ctx.parsed.y).toLocaleString(),
          },
        },
      },
      scales: {
        x: {
          grid: { color: 'rgba(46,50,80,.4)' },
          ticks: { color: '#8892a4', maxTicksLimit: 6, font: { size: 11 } },
        },
        y: {
          grid: { color: 'rgba(46,50,80,.4)' },
          ticks: {
            color: '#8892a4',
            font: { size: 11 },
            callback: v => '$' + (v / 1000).toFixed(0) + 'k',
          },
        },
      },
    },
  });
}

function closeModal() {
  document.getElementById('modal-backdrop').classList.add('hidden');
  document.body.style.overflow = '';
  currentPropertyId = null;
  if (priceChart) { priceChart.destroy(); priceChart = null; }
}

// ---------------------------------------------------------------------------
// Favorites + Notes
// ---------------------------------------------------------------------------
async function toggleFavorite(id, btn) {
  try {
    const res = await apiFetch(`/api/favorites/${id}`, { method: 'POST' });
    const isFav = res.is_favorite;
    btn.textContent = isFav ? '★' : '☆';
    btn.classList.toggle('active', isFav);
    showToast(isFav ? '★ Added to favorites' : '☆ Removed from favorites');
    // Update card in grid too
    const gridBtn = document.querySelector(`.property-grid .fav-btn[data-id="${id}"]`);
    if (gridBtn) {
      gridBtn.textContent = isFav ? '★' : '☆';
      gridBtn.classList.toggle('active', isFav);
    }
    // If favorites_only filter is on, reload
    if (activeFilters.favorites_only) loadProperties();
  } catch (err) {
    showToast('Error updating favorite');
  }
}

async function saveNote(id) {
  const textarea = document.getElementById('notes-input');
  const note = textarea.value;
  try {
    await apiFetch(`/api/notes/${id}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note }),
    });
    const saved = document.getElementById('note-saved');
    saved.classList.add('visible');
    setTimeout(() => saved.classList.remove('visible'), 2000);
    // Update note indicator on card
    const card = document.querySelector(`.property-card[data-id="${id}"]`);
    if (card) {
      let ni = card.querySelector('.note-indicator');
      if (note.trim()) {
        if (!ni) {
          ni = document.createElement('div');
          ni.className = 'note-indicator';
          card.appendChild(ni);
        }
        ni.textContent = '📝 Note';
      } else if (ni) {
        ni.remove();
      }
    }
  } catch {
    showToast('Failed to save note');
  }
}

// ---------------------------------------------------------------------------
// Filter bar
// ---------------------------------------------------------------------------
function bindFilters() {
  const get = id => document.getElementById(id);

  // Score slider
  const scoreSlider = get('filter-score');
  const scoreVal    = get('score-val');
  scoreSlider.addEventListener('input', () => {
    scoreVal.textContent = scoreSlider.value;
    activeFilters.min_score = parseInt(scoreSlider.value);
  });
  scoreSlider.addEventListener('change', loadProperties);

  // Neighborhood
  get('filter-nbhd').addEventListener('change', e => {
    activeFilters.neighborhood = e.target.value;
    loadProperties();
  });

  // Distress type
  get('filter-distress').addEventListener('change', e => {
    activeFilters.distress_type = e.target.value;
    loadProperties();
  });

  // Property type
  get('filter-type').addEventListener('change', e => {
    activeFilters.property_type = e.target.value;
    loadProperties();
  });

  // Price range
  const priceMin = get('price-min');
  const priceMax = get('price-max');
  const applyPrice = () => {
    activeFilters.min_price = parseInt(priceMin.value) || 0;
    activeFilters.max_price = parseInt(priceMax.value) || 500000;
    loadProperties();
  };
  priceMin.addEventListener('change', applyPrice);
  priceMax.addEventListener('change', applyPrice);

  // Sort
  get('sort-by').addEventListener('change', e => {
    activeFilters.sort_by = e.target.value;
    loadProperties();
  });
  get('sort-dir').addEventListener('change', e => {
    activeFilters.sort_dir = e.target.value;
    loadProperties();
  });

  // Favorites toggle
  get('filter-favs').addEventListener('change', e => {
    activeFilters.favorites_only = e.target.checked;
    loadProperties();
  });

  // Reset
  get('reset-filters').addEventListener('click', () => {
    activeFilters = {
      min_score: 0, max_score: 100,
      neighborhood: '', distress_type: '', property_type: '',
      min_price: 0, max_price: 500000,
      sort_by: 'score', sort_dir: 'desc', favorites_only: false,
    };
    get('filter-score').value = 0;
    get('score-val').textContent = 0;
    get('filter-nbhd').value = '';
    get('filter-distress').value = '';
    get('filter-type').value = '';
    get('price-min').value = 0;
    get('price-max').value = 500000;
    get('sort-by').value = 'score';
    get('sort-dir').value = 'desc';
    get('filter-favs').checked = false;
    loadProperties();
  });
}

// ---------------------------------------------------------------------------
// Refresh button
// ---------------------------------------------------------------------------
function bindRefresh() {
  document.getElementById('refresh-btn').addEventListener('click', async () => {
    const btn = document.getElementById('refresh-btn');
    btn.disabled = true;
    btn.textContent = '⏳ Refreshing…';
    try {
      await apiFetch('/api/refresh', { method: 'POST' });
      await new Promise(r => setTimeout(r, 2000));
      await loadStatus();
      await loadProperties();
      showToast('Properties refreshed!');
    } catch {
      showToast('Refresh failed');
    } finally {
      btn.disabled = false;
      btn.textContent = '↻ Refresh';
    }
  });
}

// ---------------------------------------------------------------------------
// Modal backdrop close
// ---------------------------------------------------------------------------
function bindModal() {
  document.getElementById('modal-backdrop').addEventListener('click', e => {
    if (e.target === e.currentTarget) closeModal();
  });

  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      closeModal();
      closeSettings();
      closeConfig();
    }
  });
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------
// Grouped for display in the settings modal
const WEIGHT_META = [
  // Profitability
  { key: 'w_arv',             label: 'Price vs ARV',          group: 'Profitability (40%)',          desc: 'Equity spread: purchase price vs. ARV. >30% spread = 100 pts.' },
  { key: 'w_roi',             label: 'Estimated ROI',         group: 'Profitability (40%)',          desc: 'Total ROI (price + reno). Target >25% = 100 pts.' },
  // Execution Efficiency
  { key: 'w_profit_reno',     label: 'Profit-to-Reno Ratio',  group: 'Execution Efficiency (20%)',   desc: 'Efficiency: Est. Profit ÷ Reno Cost. ≥2× = 100 pts.' },
  { key: 'w_reno_level',      label: 'Renovation Level',      group: 'Execution Efficiency (20%)',   desc: 'Light reno = 100 pts · Medium = 50 · Heavy/structural = 10.' },
  // Liquidity & Asset Risk
  { key: 'w_size',            label: 'Property Size',         group: 'Liquidity & Asset Risk (25%)', desc: 'Liquidity: sweet spot 800–2,500 sqft. <500 or >4,000 = penalty.' },
  { key: 'w_structural',      label: 'Structural Risk',       group: 'Liquidity & Asset Risk (25%)', desc: 'Year built: post-2000 = 100 pts. Pre-1960 = 8 pts (infrastructure risk).' },
  { key: 'w_market_velocity', label: 'Market Velocity',       group: 'Liquidity & Asset Risk (25%)', desc: 'DOM vs. neighborhood avg. High DOM = liquidity risk = lower score.' },
  // Market Momentum
  { key: 'w_distress',        label: 'Distress & Reductions', group: 'Market Momentum (15%)',        desc: 'Distress keywords (max 50 pts) + price cuts (max 30 pts) + BOM (20 pts).' },
  { key: 'w_neighborhood',    label: 'Neighborhood Upside',   group: 'Market Momentum (15%)',        desc: 'Top-tier appreciation areas = 100 pts. Moderate = 65. Others = 35.' },
];

let currentSettings = {};

function renderSettingsRows(settings) {
  const container = document.getElementById('settings-rows');
  let html = '';
  let lastGroup = null;
  WEIGHT_META.forEach(m => {
    if (m.group !== lastGroup) {
      lastGroup = m.group;
      html += `<div class="settings-group-header">${esc(m.group)}</div>`;
    }
    const val = Math.round(settings[m.key] ?? 0);
    html += `
<div class="weight-row">
  <div class="weight-label">
    <strong>${esc(m.label)}</strong>
    <span>${esc(m.desc)}</span>
  </div>
  <div class="weight-slider-wrap">
    <input type="range" min="0" max="100" value="${val}"
      data-key="${esc(m.key)}" class="weight-slider" />
  </div>
  <input type="number" min="0" max="100" value="${val}"
    data-key="${esc(m.key)}" class="weight-number" />
</div>`;
  });
  container.innerHTML = html;

  updateSettingsTotal();

  // Sync slider ↔ number input
  container.querySelectorAll('.weight-slider').forEach(slider => {
    slider.addEventListener('input', () => syncWeight(slider.dataset.key, slider.value, 'slider'));
  });
  container.querySelectorAll('.weight-number').forEach(input => {
    input.addEventListener('input', () => syncWeight(input.dataset.key, input.value, 'number'));
  });
}

function syncWeight(key, val, source) {
  const v = Math.max(0, Math.min(100, parseInt(val) || 0));
  currentSettings[key] = v;
  document.querySelectorAll(`[data-key="${key}"]`).forEach(el => { el.value = v; });
  updateSettingsTotal();
}

function updateSettingsTotal() {
  const total = WEIGHT_META.reduce((s, m) => s + (parseInt(currentSettings[m.key]) || 0), 0);
  const el = document.getElementById('settings-total');
  el.textContent = total;
  el.className = total === 100 ? '' : total > 100 ? 'over' : 'under';
  el.textContent = total === 100 ? `${total} ✓` : `${total} ${total > 100 ? '↑ over' : '↓ under'}`;
}

async function openSettings() {
  const backdrop = document.getElementById('settings-backdrop');
  backdrop.classList.remove('hidden');
  document.body.style.overflow = 'hidden';
  try {
    const s = await apiFetch('/api/settings');
    currentSettings = { ...s };
    document.getElementById('settings-max-price').value = s.max_price ?? 500000;
    renderSettingsRows(currentSettings);
  } catch {
    showToast('Failed to load settings');
  }
}

function closeSettings() {
  document.getElementById('settings-backdrop').classList.add('hidden');
  document.body.style.overflow = '';
}

async function saveSettings() {
  const btn = document.getElementById('settings-save-btn');
  btn.disabled = true;
  btn.textContent = '⏳ Saving…';
  try {
    const maxPrice = parseInt(document.getElementById('settings-max-price').value) || 500000;
    const payload = { ...currentSettings, max_price: maxPrice };
    const res = await apiFetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    currentSettings = res.settings;
    document.getElementById('settings-max-price').value = res.settings.max_price;
    renderSettingsRows(currentSettings);
    closeSettings();

    if (res.status === 'refetching') {
      showToast(`⏳ Max price changed to $${Number(maxPrice).toLocaleString()} — fetching new listings…`, 4000);
      // Poll until refresh is done (status last_updated changes) then reload
      const before = (await apiFetch('/api/status')).last_updated;
      const poll = setInterval(async () => {
        try {
          const s = await apiFetch('/api/status');
          if (s.last_updated !== before) {
            clearInterval(poll);
            await loadProperties();
            showToast(`✓ Loaded listings under $${Number(maxPrice).toLocaleString()}`);
          }
        } catch { clearInterval(poll); }
      }, 2000);
    } else {
      showToast(`✓ Weights saved — ${res.rescored} properties re-scored`);
      await loadProperties();
    }
  } catch (err) {
    showToast(`Save failed: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Save & Re-score';
  }
}

async function resetSettings() {
  const btn = document.getElementById('settings-reset-btn');
  btn.disabled = true;
  try {
    const res = await apiFetch('/api/settings/reset', { method: 'POST' });
    currentSettings = res.settings;
    document.getElementById('settings-max-price').value = res.settings.max_price ?? 500000;
    renderSettingsRows(currentSettings);
    showToast('Settings reset to defaults');
    await loadProperties();
  } catch {
    showToast('Reset failed');
  } finally {
    btn.disabled = false;
  }
}

function bindSettings() {
  document.getElementById('settings-btn').addEventListener('click', openSettings);
  document.getElementById('settings-close-btn').addEventListener('click', closeSettings);
  document.getElementById('settings-save-btn').addEventListener('click', saveSettings);
  document.getElementById('settings-reset-btn').addEventListener('click', resetSettings);
  document.getElementById('settings-backdrop').addEventListener('click', e => {
    if (e.target === e.currentTarget) closeSettings();
  });
}

// ---------------------------------------------------------------------------
// Status Badge
// ---------------------------------------------------------------------------
const STATUS_CYCLE = ['new', 'waiting', 'ongoing', 'irrelevant'];

function formatStatus(s) {
  return { new: 'New', waiting: 'Waiting', ongoing: 'Ongoing', irrelevant: 'Irrelevant' }[s] || 'New';
}

async function cycleStatus(propertyId, el) {
  const current = el.dataset.status || 'new';
  const next = STATUS_CYCLE[(STATUS_CYCLE.indexOf(current) + 1) % STATUS_CYCLE.length];
  try {
    const res = await apiFetch(`/api/status/${propertyId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: next }),
    });
    if (res.status) {
      // Update all badges for this property (grid + dashboard may both show it)
      document.querySelectorAll(`.status-badge[data-id="${propertyId}"]`).forEach(badge => {
        badge.dataset.status = next;
        badge.className = `status-badge status-${next}`;
        badge.textContent = formatStatus(next);
      });
    }
  } catch {
    showToast('Failed to update status');
  }
}

// ---------------------------------------------------------------------------
// Model Config
// ---------------------------------------------------------------------------
let _configData = null;

async function openConfig() {
  document.getElementById('config-backdrop').classList.remove('hidden');
  document.body.style.overflow = 'hidden';
  if (!_configData) {
    try {
      _configData = await apiFetch('/api/config');
    } catch {
      showToast('Failed to load config');
      closeConfig();
      return;
    }
  }
  _renderConfigTab('neighborhoods');
}

function closeConfig() {
  document.getElementById('config-backdrop').classList.add('hidden');
  document.body.style.overflow = '';
}

function _renderConfigTab(name) {
  document.querySelectorAll('.ctab-panel').forEach(p => p.style.display = 'none');
  document.querySelectorAll('.config-sub-tab').forEach(b => b.classList.remove('active'));
  const panel = document.getElementById('ctab-' + name);
  if (panel) panel.style.display = '';
  const btn = document.querySelector(`.config-sub-tab[data-ctab="${name}"]`);
  if (btn) btn.classList.add('active');

  if (name === 'neighborhoods')  renderNeighborhoodTable();
  if (name === 'renovation')     { renderRenoLevels(); renderAgeMultipliers(); renderPropertyTypeDiscounts(); }
  if (name === 'thresholds')     renderThresholds();
  if (name === 'keywords')       renderKeywords();
}

// ── Neighborhoods ──────────────────────────────────────────────────────────
function renderNeighborhoodTable() {
  const tbody = document.getElementById('nbhd-tbody');
  if (!tbody) return;
  tbody.innerHTML = (_configData.neighborhoods || []).map((n, i) => `
    <tr>
      <td><input type="text" value="${esc(n.name)}" onchange="_configData.neighborhoods[${i}].name=this.value"></td>
      <td><input type="number" value="${n.arv_psf}" onchange="_configData.neighborhoods[${i}].arv_psf=+this.value"></td>
      <td><input type="number" value="${n.avg_dom}" onchange="_configData.neighborhoods[${i}].avg_dom=+this.value"></td>
      <td>
        <select onchange="_configData.neighborhoods[${i}].tier=this.value">
          <option value="top"   ${n.tier==='top'   ?'selected':''}>Top (100)</option>
          <option value="mid"   ${n.tier==='mid'   ?'selected':''}>Mid (65)</option>
          <option value="other" ${n.tier==='other' ?'selected':''}>Other (35)</option>
        </select>
      </td>
      <td><button class="btn-remove" onclick="removeNbhd(${i})">×</button></td>
    </tr>`).join('');
}

function removeNbhd(i) { _configData.neighborhoods.splice(i, 1); renderNeighborhoodTable(); }
function addNeighborhoodRow() {
  _configData.neighborhoods.push({ name: '', arv_psf: 510, avg_dom: 22, tier: 'other' });
  renderNeighborhoodTable();
  const rows = document.querySelectorAll('#nbhd-tbody tr');
  rows[rows.length - 1].querySelector('input').focus();
}

// ── Renovation Levels ──────────────────────────────────────────────────────
function renderRenoLevels() {
  const c = document.getElementById('reno-levels-container');
  if (!c) return;
  const levels = _configData.reno_config.levels;
  c.innerHTML = Object.entries(levels).map(([name, v]) => `
    <div class="reno-block">
      <h5>${name.toUpperCase()}</h5>
      <div class="reno-grid">
        <div class="reno-field">
          <label>Cost $/sqft</label>
          <input type="number" value="${v.cost_psf}"
            onchange="_configData.reno_config.levels['${name}'].cost_psf=+this.value">
        </div>
        <div class="reno-field">
          <label>Score (0–100)</label>
          <input type="number" min="0" max="100" value="${v.score}"
            onchange="_configData.reno_config.levels['${name}'].score=+this.value">
        </div>
      </div>
    </div>`).join('');
}

// ── Age Multipliers ────────────────────────────────────────────────────────
function renderAgeMultipliers() {
  const tbody = document.getElementById('age-mult-tbody');
  if (!tbody) return;
  tbody.innerHTML = (_configData.reno_config.age_multipliers || []).map((m, i) => `
    <tr>
      <td><input type="number" value="${m.min_age}"
            onchange="_configData.reno_config.age_multipliers[${i}].min_age=+this.value"></td>
      <td><input type="number" step="0.01" value="${m.multiplier}"
            onchange="_configData.reno_config.age_multipliers[${i}].multiplier=+this.value"></td>
      <td><button class="btn-remove" onclick="removeAgeMult(${i})">×</button></td>
    </tr>`).join('');
}
function removeAgeMult(i) { _configData.reno_config.age_multipliers.splice(i, 1); renderAgeMultipliers(); }
function addAgeMult() {
  _configData.reno_config.age_multipliers.push({ min_age: 0, multiplier: 1.0 });
  renderAgeMultipliers();
}

// ── Property Type Discounts ────────────────────────────────────────────────
function renderPropertyTypeDiscounts() {
  const tbody = document.getElementById('pt-discounts-tbody');
  if (!tbody) return;
  const discounts = _configData.reno_config.property_type_discounts || {};
  tbody.innerHTML = Object.entries(discounts).map(([type, mult]) => `
    <tr>
      <td><input type="text" value="${esc(type)}"
            data-orig="${esc(type)}"
            onchange="renameDiscount(this.dataset.orig,this.value);this.dataset.orig=this.value"></td>
      <td><input type="number" step="0.01" min="0.1" max="1" value="${mult}"
            onchange="_configData.reno_config.property_type_discounts['${type}']=+this.value"></td>
      <td><button class="btn-remove" onclick="removeDiscount('${type}')">×</button></td>
    </tr>`).join('');
}
function removeDiscount(type) {
  delete _configData.reno_config.property_type_discounts[type];
  renderPropertyTypeDiscounts();
}
function renameDiscount(oldType, newType) {
  if (oldType === newType) return;
  const val = _configData.reno_config.property_type_discounts[oldType];
  delete _configData.reno_config.property_type_discounts[oldType];
  _configData.reno_config.property_type_discounts[newType] = val;
}
function addPropertyTypeDiscount() {
  _configData.reno_config.property_type_discounts['new_type'] = 1.0;
  renderPropertyTypeDiscounts();
}

// ── Score Thresholds ───────────────────────────────────────────────────────
const THRESHOLD_LABELS = {
  arv_target_equity_pct:        'ARV target equity % (equity ≥ this % → 100 pts)',
  roi_target_pct:               'ROI target % (ROI ≥ this % → 100 pts)',
  distress_kw_points:           'Points per distress keyword',
  distress_kw_max:              'Max points from keywords',
  distress_reduction_pts:       'Points per price reduction count',
  distress_reduction_pct_pts:   'Points per reduction %',
  distress_reduction_max:       'Max points from reductions',
  distress_bom_bonus:           'Back-on-market bonus points',
};

function renderThresholds() {
  const tbody = document.getElementById('thresholds-tbody');
  if (!tbody) return;
  const t = _configData.reno_config.score_thresholds;
  tbody.innerHTML = Object.entries(THRESHOLD_LABELS).map(([k, label]) => `
    <tr>
      <td style="color:var(--text-muted);font-size:.82rem">${label}</td>
      <td><input type="number" step="0.1" value="${t[k]}"
            onchange="_configData.reno_config.score_thresholds['${k}']=+this.value"></td>
    </tr>`).join('');
}

// ── Keywords ───────────────────────────────────────────────────────────────
function renderKeywords() {
  const c = document.getElementById('keywords-container');
  if (!c) return;
  c.innerHTML = (_configData.distress_keywords || []).map((kw, i) => `
    <div class="keyword-chip">
      ${esc(kw)}
      <button onclick="removeKeyword(${i})" title="Remove">×</button>
    </div>`).join('');
}
function removeKeyword(i) { _configData.distress_keywords.splice(i, 1); renderKeywords(); }
function addKeyword() {
  const inp = document.getElementById('new-keyword-input');
  const val = inp.value.trim().toLowerCase();
  if (!val) return;
  if (!_configData.distress_keywords.includes(val)) {
    _configData.distress_keywords.push(val);
    renderKeywords();
  }
  inp.value = '';
}

// ── Save / Reset ───────────────────────────────────────────────────────────
async function saveConfig() {
  const btn = document.getElementById('config-save-btn');
  btn.disabled = true; btn.textContent = '⏳ Saving…';
  try {
    const res = await apiFetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(_configData),
    });
    if (res.status === 'ok') {
      showToast(`✓ Config saved — ${res.rescored} properties re-scored`);
      closeConfig();
      await loadProperties();
    } else {
      showToast('Error saving config', 3000);
    }
  } catch (err) {
    showToast(`Save failed: ${err.message}`, 3000);
  } finally {
    btn.disabled = false; btn.textContent = 'Save & Re-score';
  }
}

async function resetConfig() {
  if (!confirm('Reset all model config to defaults?')) return;
  const btn = document.getElementById('config-reset-btn');
  btn.disabled = true;
  try {
    const res = await apiFetch('/api/config/reset', { method: 'POST' });
    _configData = res.config;
    _renderConfigTab(document.querySelector('.config-sub-tab.active')?.dataset.ctab || 'neighborhoods');
    showToast('Config reset to defaults');
    await loadProperties();
  } catch {
    showToast('Reset failed');
  } finally {
    btn.disabled = false;
  }
}

function bindConfig() {
  document.getElementById('config-btn').addEventListener('click', openConfig);
  document.getElementById('config-close-btn').addEventListener('click', closeConfig);
  document.getElementById('config-save-btn').addEventListener('click', saveConfig);
  document.getElementById('config-reset-btn').addEventListener('click', resetConfig);
  document.getElementById('config-backdrop').addEventListener('click', e => {
    if (e.target === e.currentTarget) closeConfig();
  });
  document.querySelectorAll('.config-sub-tab').forEach(btn => {
    btn.addEventListener('click', () => _renderConfigTab(btn.dataset.ctab));
  });
}

// ---------------------------------------------------------------------------
// Light / Dark mode
// ---------------------------------------------------------------------------
function initTheme() {
  const saved = localStorage.getItem('theme') || 'dark';
  document.documentElement.setAttribute('data-theme', saved);
  const btn = document.getElementById('theme-toggle-btn');
  if (btn) btn.textContent = saved === 'dark' ? '☀ Light' : '🌙 Dark';
}

function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme') || 'dark';
  const next = current === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('theme', next);
  const btn = document.getElementById('theme-toggle-btn');
  if (btn) btn.textContent = next === 'dark' ? '☀ Light' : '🌙 Dark';
}

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------

function switchView(view) {
  dashView = view;
  const gridEl  = document.getElementById('grid-view');
  const dashEl  = document.getElementById('dashboard-view');
  const gridBtn = document.getElementById('view-grid-btn');
  const dashBtn = document.getElementById('view-dashboard-btn');

  if (view === 'dashboard') {
    gridEl.classList.add('hidden');
    dashEl.classList.remove('hidden');
    // Hide map toggle when in dashboard (map panel is inside grid-view)
    document.getElementById('map-toggle-btn').style.display = 'none';
    gridBtn.classList.remove('active');
    dashBtn.classList.add('active');
    // Render with current data — brief delay lets the container paint first
    setTimeout(() => {
      renderScatterChart(dashboardProps);
      renderCompTable(dashboardProps);
    }, 0);
  } else {
    dashEl.classList.add('hidden');
    gridEl.classList.remove('hidden');
    document.getElementById('map-toggle-btn').style.display = '';
    dashBtn.classList.remove('active');
    gridBtn.classList.add('active');
  }
}

// Called from loadProperties — always keep dashboardProps fresh
function renderDashboard(props) {
  dashboardProps = props;
  if (dashView === 'dashboard') {
    renderScatterChart(props);
    renderCompTable(props);
  }
}

function renderScatterChart(props) {
  const canvas = document.getElementById('scatter-chart');
  if (!canvas) return;
  if (scatterChart) { scatterChart.destroy(); scatterChart = null; }

  const pts = props.map(p => ({
    x: p.renovation_cost,
    y: p.arv - p.price - p.renovation_cost,
    r: Math.max(6, Math.min(24, 5 + Math.max(0, p.roi_pct) * 0.62)),
    prop: p,
  }));

  scatterChart = new Chart(canvas, {
    type: 'bubble',
    data: {
      datasets: [{
        label: 'Properties',
        data: pts,
        backgroundColor: pts.map(d =>
          d.prop.flip_score >= 70 ? 'rgba(34,197,94,.68)'  :
          d.prop.flip_score >= 40 ? 'rgba(245,158,11,.68)' :
                                    'rgba(239,68,68,.68)'
        ),
        borderColor: pts.map(d =>
          d.prop.flip_score >= 70 ? '#22c55e' :
          d.prop.flip_score >= 40 ? '#f59e0b' :
                                    '#ef4444'
        ),
        borderWidth: 1.5,
        hoverBorderWidth: 2.5,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      onClick: (_evt, elements) => {
        if (elements.length > 0) openQuickView(pts[elements[0].index].prop);
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => {
              const d = ctx.raw;
              return [
                d.prop.address,
                `Score: ${d.prop.flip_score}   ROI: ${fmtPct(d.prop.roi_pct)}`,
                `Reno: ${fmt$(d.prop.renovation_cost)}   Profit: ${fmt$(d.y)}`,
              ];
            },
          },
          backgroundColor: 'rgba(26,29,39,.97)',
          borderColor: '#2e3250',
          borderWidth: 1,
          titleColor: '#e2e8f0',
          bodyColor: '#8892a4',
          padding: 10,
          displayColors: false,
        },
      },
      scales: {
        x: {
          title: { display: true, text: 'Renovation Cost (estimated)', color: '#8892a4', font: { size: 11 } },
          grid: { color: 'rgba(46,50,80,.35)' },
          ticks: { color: '#8892a4', font: { size: 11 }, callback: v => '$' + (v / 1000).toFixed(0) + 'k' },
        },
        y: {
          title: { display: true, text: 'Estimated Profit', color: '#8892a4', font: { size: 11 } },
          grid: { color: 'rgba(46,50,80,.35)' },
          ticks: { color: '#8892a4', font: { size: 11 }, callback: v => '$' + (v / 1000).toFixed(0) + 'k' },
        },
      },
    },
  });
}

// Enrich props with computed fields needed by the table
function _enrich(props) {
  return props.map(p => ({
    ...p,
    profit:    p.arv - p.price - p.renovation_cost,
    price_psf: p.sqft > 0 ? Math.round(p.price / p.sqft) : 0,
  }));
}

function renderCompTable(props) {
  const tbody = document.getElementById('comp-tbody');
  if (!tbody) return;

  let rows = _enrich(props);

  // Search filter
  if (dashSearch) {
    rows = rows.filter(p =>
      p.address.toLowerCase().includes(dashSearch) ||
      p.neighborhood.toLowerCase().includes(dashSearch) ||
      p.distress_type.toLowerCase().includes(dashSearch)
    );
  }

  // Sort
  rows.sort((a, b) => {
    const av = a[dashSortCol] ?? 0;
    const bv = b[dashSortCol] ?? 0;
    if (typeof av === 'string')
      return dashSortDir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
    return dashSortDir === 'asc' ? av - bv : bv - av;
  });

  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="10" style="text-align:center;padding:2rem;color:var(--text-muted)">No properties match your filters</td></tr>`;
    return;
  }

  tbody.innerHTML = rows.map(p => {
    // ROI conditional formatting
    const roiColor = p.roi_pct >= 15 ? 'var(--green)' : p.roi_pct >= 0 ? 'var(--yellow)' : 'var(--red)';
    const roiBg    = p.roi_pct >= 20 ? 'rgba(34,197,94,.15)' :
                     p.roi_pct >= 10 ? 'rgba(245,158,11,.1)'  :
                     p.roi_pct < 0   ? 'rgba(239,68,68,.15)'  : 'transparent';
    const profColor = p.profit >= 0 ? 'var(--green)' : 'var(--red)';
    const isActive  = p.id === quickViewActiveId;
    const status    = p.status || 'new';

    return `<tr class="comp-row${isActive ? ' qv-active' : ''}" data-id="${esc(p.id)}">
  <td class="tc">
    <div class="flip-score-badge ${scoreClass(p.flip_score)}" style="width:34px;height:34px;font-size:.78rem;margin:auto">${p.flip_score}</div>
  </td>
  <td style="font-weight:600;font-size:.82rem;max-width:200px;overflow:hidden;text-overflow:ellipsis">${esc(p.address)}</td>
  <td style="color:var(--text-muted);font-size:.8rem">${esc(p.neighborhood)}</td>
  <td class="tr">${fmt$(p.price)}</td>
  <td class="tr" style="color:var(--accent)">${fmt$(p.arv)}</td>
  <td class="tr">${fmt$(p.renovation_cost)}</td>
  <td class="tr" style="color:${profColor};font-weight:700">${fmt$(p.profit)}</td>
  <td class="tr"><span class="roi-pill" style="background:${roiBg};color:${roiColor}">${fmtPct(p.roi_pct)}</span></td>
  <td class="tr">${p.dom}d</td>
  <td class="tr">${fmt$(p.price_psf)}</td>
  <td class="tc"><span class="status-badge status-${status}" data-status="${status}" data-id="${esc(p.id)}"
        onclick="event.stopPropagation();cycleStatus('${esc(p.id)}',this)"
        title="Click to change status">${formatStatus(status)}</span></td>
</tr>`;
  }).join('');

  // Click → quick view
  tbody.querySelectorAll('.comp-row').forEach(row => {
    row.addEventListener('click', () => {
      const p = _enrich(props).find(x => x.id === row.dataset.id);
      if (p) openQuickView(p);
    });
  });
}

function bindCompTableSort() {
  document.querySelectorAll('#comp-table th[data-col]').forEach(th => {
    th.addEventListener('click', () => {
      const col = th.dataset.col;
      if (dashSortCol === col) {
        dashSortDir = dashSortDir === 'asc' ? 'desc' : 'asc';
      } else {
        dashSortCol = col;
        dashSortDir = 'desc';
      }
      // Update header indicators
      document.querySelectorAll('#comp-table th[data-col]').forEach(h => {
        h.classList.remove('sort-asc', 'sort-desc');
      });
      th.classList.add(dashSortDir === 'asc' ? 'sort-asc' : 'sort-desc');
      renderCompTable(dashboardProps);
    });
  });
}

// ---------------------------------------------------------------------------
// Quick View Sidebar
// ---------------------------------------------------------------------------

function openQuickView(prop) {
  quickViewActiveId = prop.id;
  const profit    = prop.profit ?? (prop.arv - prop.price - prop.renovation_cost);
  const profColor = profit >= 0 ? 'var(--green)' : 'var(--red)';
  const roiColor  = prop.roi_pct >= 15 ? 'var(--green)' : prop.roi_pct >= 0 ? 'var(--yellow)' : 'var(--red)';

  const renoRows = Object.entries(prop.renovation_breakdown || {}).map(([k, v]) =>
    `<tr>
      <td style="font-size:.79rem;color:var(--text-muted)">${esc(k)}</td>
      <td class="td-right" style="font-size:.79rem;font-weight:600">${fmt$(v)}</td>
    </tr>`
  ).join('');

  // Score factor computations (mirrors renderModal logic)
  const avg_dom    = prop.neighborhood_avg_dom || 22;
  const qv_profit  = prop.arv - prop.price - prop.renovation_cost;
  const qv_eq_pct  = prop.arv > prop.price ? (prop.arv - prop.price) / prop.arv * 100 : 0;
  const qv_f_arv   = Math.min(100, qv_eq_pct / 30 * 100);
  const qv_f_roi   = prop.roi_pct > 0 ? Math.min(100, prop.roi_pct / 25 * 100) : 0;
  const qv_pr_rat  = prop.renovation_cost > 0 ? qv_profit / prop.renovation_cost : 0;
  const qv_f_pr    = qv_pr_rat >= 2 ? 100 : qv_pr_rat >= 1 ? 75 : qv_pr_rat >= 0.5 ? 50 : qv_pr_rat >= 0 ? 25 : 0;
  const qv_f_rlvl  = { light: 100, medium: 50, heavy: 10 }[prop.renovation_level?.toLowerCase()] ?? 50;
  const qv_sq      = prop.sqft || 0;
  const qv_f_size  = qv_sq >= 800 && qv_sq <= 2500 ? 100 : (qv_sq >= 600 && qv_sq < 800) || (qv_sq > 2500 && qv_sq <= 3500) ? 75 : (qv_sq >= 500 && qv_sq < 600) || (qv_sq > 3500 && qv_sq <= 4500) ? 45 : qv_sq > 0 ? 10 : 50;
  const qv_yr      = prop.year_built || 1970;
  const qv_f_str   = qv_yr >= 2000 ? 100 : qv_yr >= 1990 ? 82 : qv_yr >= 1980 ? 60 : qv_yr >= 1970 ? 38 : qv_yr >= 1960 ? 20 : 8;
  const qv_dr      = prop.dom / avg_dom;
  const qv_f_vel   = qv_dr <= 0.5 ? 100 : qv_dr <= 0.75 ? 85 : qv_dr <= 1 ? 70 : qv_dr <= 1.5 ? 50 : qv_dr <= 2 ? 30 : qv_dr <= 3 ? 15 : 5;
  const qv_kw      = Math.min(50, (prop.distress_keywords?.length || 0) * 15);
  const qv_rd      = Math.min(30, prop.price_reductions * 8 + prop.price_reduction_pct * 1.2);
  const qv_f_dist  = Math.min(100, qv_kw + qv_rd + (prop.back_on_market ? 20 : 0));
  const qv_nb      = prop.neighborhood.toLowerCase();
  const qv_f_nbhd  = ['rainier valley','beacon hill','white center','delridge','georgetown'].some(n => qv_nb.includes(n)) ? 100
                   : ['columbia city','northgate','west seattle'].some(n => qv_nb.includes(n)) ? 65 : 35;

  const scoreBarHTML = [
    { label: 'Price vs ARV',   score: qv_f_arv,  w: 0.30 },
    { label: 'ROI',            score: qv_f_roi,  w: 0.10 },
    { label: 'Profit/Reno',    score: qv_f_pr,   w: 0.10 },
    { label: 'Reno Level',     score: qv_f_rlvl, w: 0.10 },
    { label: 'Size',           score: qv_f_size, w: 0.10 },
    { label: 'Structural',     score: qv_f_str,  w: 0.10 },
    { label: 'Market Velocity',score: qv_f_vel,  w: 0.05 },
    { label: 'Distress',       score: qv_f_dist, w: 0.10 },
    { label: 'Nbhd Upside',    score: qv_f_nbhd, w: 0.05 },
  ].map(r => `
<div class="score-row" style="margin-bottom:.3rem">
  <span class="score-row-label" style="width:110px;font-size:.74rem">${esc(r.label)}</span>
  <div class="score-bar-wrap"><div class="score-bar-fill" style="width:${Math.round(r.score)}%"></div></div>
  <span class="score-row-val" style="font-size:.72rem">+${Math.round(r.score * r.w)}</span>
</div>`).join('');

  document.getElementById('qv-body').innerHTML = `

<!-- Header -->
<div class="qv-section">
  <div style="display:flex;gap:.4rem;flex-wrap:wrap;margin-bottom:.45rem">
    <span class="distress-badge ${distressClass(prop.distress_type)}">${esc(prop.distress_type)}</span>
    <span class="source-badge">${esc(prop.source)}</span>
    ${prop.back_on_market ? '<span class="distress-badge distress-back-on-mark">BOM</span>' : ''}
  </div>
  <div style="font-size:1rem;font-weight:700;line-height:1.3">${esc(prop.address)}</div>
  <div style="font-size:.78rem;color:var(--text-muted);margin:.15rem 0 .4rem">${esc(prop.neighborhood)} · ${esc(prop.property_type)} · Built ${prop.year_built}</div>
  <div style="display:flex;align-items:center;justify-content:space-between">
    <span style="font-size:1.4rem;font-weight:800">${fmt$(prop.price)}</span>
    <div class="flip-score-badge ${scoreClass(prop.flip_score)}" style="width:42px;height:42px;font-size:.95rem">${prop.flip_score}</div>
  </div>
</div>

<!-- Key metrics -->
<div class="qv-section">
  <div class="qv-section-title">Key Metrics</div>
  <div class="qv-metrics">
    <div class="qv-metric"><div class="qv-metric-label">Est. Profit</div><div class="qv-metric-value" style="color:${profColor}">${fmt$(profit)}</div></div>
    <div class="qv-metric"><div class="qv-metric-label">ROI (est.)</div><div class="qv-metric-value" style="color:${roiColor}">${fmtPct(prop.roi_pct)}</div></div>
    <div class="qv-metric"><div class="qv-metric-label">ARV (est.)</div><div class="qv-metric-value" style="color:var(--accent)">${fmt$(prop.arv)}</div></div>
    <div class="qv-metric"><div class="qv-metric-label">Reno Cost (est.)</div><div class="qv-metric-value">${fmt$(prop.renovation_cost)}</div></div>
    <div class="qv-metric"><div class="qv-metric-label">DOM</div><div class="qv-metric-value">${prop.dom}d <span style="font-size:.68rem;color:var(--text-muted)">(avg ${avg_dom}d)</span></div></div>
    <div class="qv-metric"><div class="qv-metric-label">Price/sqft</div><div class="qv-metric-value">${fmt$(Math.round(prop.price / prop.sqft))}</div></div>
    <div class="qv-metric"><div class="qv-metric-label">Sq Ft</div><div class="qv-metric-value">${Number(prop.sqft).toLocaleString()}</div></div>
    <div class="qv-metric"><div class="qv-metric-label">Reno Level (est.)</div><div class="qv-metric-value">${esc(prop.renovation_level || '—')}</div></div>
  </div>
</div>

<!-- Renovation breakdown -->
<div class="qv-section">
  <div class="qv-section-title">Renovation Breakdown <span style="font-weight:400;font-size:.62rem;color:var(--yellow)">(estimated)</span></div>
  <table class="data-table">
    <tbody>${renoRows}</tbody>
    <tfoot>
      <tr><td class="td-total">Total (${esc(prop.renovation_level || '')})</td><td class="td-right td-total">${fmt$(prop.renovation_cost)}</td></tr>
    </tfoot>
  </table>
</div>

<!-- Score breakdown -->
<div class="qv-section">
  <div class="qv-section-title">Score Breakdown</div>
  <div class="score-breakdown">${scoreBarHTML}</div>
</div>

<!-- Actions -->
<div class="qv-section" style="border-bottom:none">
  <button class="btn btn-primary" style="width:100%;justify-content:center;padding:.6rem"
    onclick="openDetail('${esc(prop.id)}')">Open Full Details</button>
</div>`;

  document.getElementById('quick-view').classList.add('open');

  // Highlight active row in table
  document.querySelectorAll('#comp-tbody .comp-row').forEach(r => {
    r.classList.toggle('qv-active', r.dataset.id === prop.id);
  });
}

function closeQuickView() {
  quickViewActiveId = null;
  document.getElementById('quick-view').classList.remove('open');
  document.querySelectorAll('#comp-tbody .comp-row').forEach(r => r.classList.remove('qv-active'));
}

function bindDashboard() {
  document.getElementById('view-grid-btn').addEventListener('click',      () => switchView('grid'));
  document.getElementById('view-dashboard-btn').addEventListener('click', () => switchView('dashboard'));
  document.getElementById('qv-close').addEventListener('click', closeQuickView);

  document.getElementById('comp-search').addEventListener('input', e => {
    dashSearch = e.target.value.toLowerCase().trim();
    renderCompTable(dashboardProps);
  });

  bindCompTableSort();
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', async () => {
  initTheme();
  initMap();
  bindFilters();
  bindRefresh();
  bindModal();
  bindSettings();
  bindConfig();
  bindDashboard();
  document.getElementById('map-toggle-btn').addEventListener('click', toggleMap);
  document.getElementById('theme-toggle-btn').addEventListener('click', toggleTheme);
  await loadStatus();
  await loadProperties();
  // Auto-refresh status every 60s
  setInterval(loadStatus, 60_000);
});

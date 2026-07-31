/* Panel grafikleri — bağımlılıksız SVG.
 *
 * Renkler doğrulanmış kategorik paletten gelir (açık ve koyu tema için ayrı
 * adımlar). Metin hiçbir zaman seri rengini giymez; kimlik, metnin yanındaki
 * renkli işaretten okunur.
 */

import { escapeHtml, fmtNumber } from './ui.js';

const NS = 'http://www.w3.org/2000/svg';

/* ------------------------------------------------------------------ ortak */

/* Uygulamanın genel `svg { … }` kuralı ikonlar için boyut ve çizgi tanımlar;
 * stil kuralları sunum özniteliklerini (fill="…") ezdiği için grafik
 * işaretlerinin boya bilgisi satır içi stil olarak verilir — satır içi stil
 * her zaman kazanır. */
const STYLE_PROPS = new Set([
  'fill', 'stroke', 'stroke-width', 'stroke-linecap', 'stroke-linejoin',
  'font-size', 'font-weight', 'font-variant-numeric', 'font-family',
]);

function svgEl(name, attrs = {}) {
  const node = document.createElementNS(NS, name);
  const style = [];
  Object.entries(attrs).forEach(([key, value]) => {
    if (value === null || value === undefined) return;
    if (key === 'style') { style.push(String(value)); return; }
    if (STYLE_PROPS.has(key)) style.push(`${key}:${value}`);
    else node.setAttribute(key, String(value));
  });
  if (style.length) node.setAttribute('style', style.join(';'));
  return node;
}

function cssVar(name, fallback = '') {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

/** Seri rengi — tema değişince otomatik olarak doğru adımı verir. */
export function seriesColor(index) {
  return cssVar(`--series-${(index % 5) + 1}`, '#2a78d6');
}

/** Eksen için okunaklı üst sınır (0 / 25 / 50 / 100 / 250 …). */
function niceMax(value) {
  if (!(value > 0)) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const normalized = value / magnitude;
  const step = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 2.5 ? 2.5
    : normalized <= 5 ? 5 : 10;
  return step * magnitude;
}

function ticks(max, count = 4) {
  return Array.from({ length: count + 1 }, (_, i) => (max / count) * i);
}

/* --------------------------------------------------------------- ipucu */

function tooltipLayer(host) {
  let node = host.querySelector('.viz-tip');
  if (!node) {
    node = document.createElement('div');
    node.className = 'viz-tip';
    node.hidden = true;
    host.appendChild(node);
  }
  return {
    show(html, x, y) {
      node.innerHTML = html;
      node.hidden = false;
      const bounds = host.getBoundingClientRect();
      const width = node.offsetWidth;
      const left = Math.max(6, Math.min(x - width / 2, bounds.width - width - 6));
      node.style.left = `${left}px`;
      node.style.top = `${Math.max(4, y - node.offsetHeight - 12)}px`;
    },
    hide() { node.hidden = true; },
  };
}

/* ====================================================================
   1) Tank doluluk ölçerleri — stok / lisanslı kapasite
   ==================================================================== */

export function tankFillChart(host, tanks) {
  host.innerHTML = '';
  const tip = tooltipLayer(host);

  const rowHeight = 46;
  const labelWidth = 108;
  const width = host.clientWidth || 520;
  const trackWidth = Math.max(120, width - labelWidth - 92);

  const chartHeight = tanks.length * rowHeight + 8;
  const svg = svgEl('svg', {
    viewBox: `0 0 ${width} ${chartHeight}`, role: 'img',
    style: `display:block;width:100%;height:${chartHeight}px;overflow:visible;`
         + 'stroke:none;fill:none',
  });

  const track = cssVar('--viz-track', '#cde2fb');
  const inkSoft = cssVar('--text-soft');
  const inkMuted = cssVar('--text-muted');

  tanks.forEach((tank, index) => {
    const y = index * rowHeight + 6;
    const ratio = tank.capacity > 0 ? Math.min(1, tank.stock / tank.capacity) : 0;
    // Doluluk eşiği bir durumdur (dolmak üzere / dolu). Durum rengi tek başına
    // bırakılmaz; eşiği aşan tanklar ayrıca bir uyarı işareti taşır.
    const level = ratio >= 0.9 ? 'full' : ratio >= 0.75 ? 'high' : 'normal';
    const fill = level === 'full' ? cssVar('--viz-full', '#e34948')
      : level === 'high' ? cssVar('--viz-high', '#eda100')
        : seriesColor(0);
    const statusMark = level === 'full' ? '▲' : level === 'high' ? '△' : '';
    const statusText = level === 'full' ? 'Kapasite dolu'
      : level === 'high' ? 'Kapasiteye yaklaşıyor' : '';

    svg.appendChild(Object.assign(svgEl('text', {
      x: 0, y: y + 19, fill: inkSoft, 'font-size': 12.5, 'font-weight': 600,
    }), { textContent: tank.tankNo }));

    svg.appendChild(Object.assign(svgEl('text', {
      x: 0, y: y + 34, fill: inkMuted, 'font-size': 10.5,
    }), { textContent: tank.product || tank.fuel || '' }));

    // Ölçer yolu: aynı rampanın açık adımı
    svg.appendChild(svgEl('rect', {
      x: labelWidth, y: y + 8, width: trackWidth, height: 14, rx: 4, fill: track,
    }));

    const barWidth = Math.max(ratio > 0 ? 4 : 0, trackWidth * ratio);
    if (barWidth > 0) {
      const bar = svgEl('rect', {
        x: labelWidth, y: y + 8, width: barWidth, height: 14, rx: 4, fill,
      });
      svg.appendChild(bar);
    }

    // Değer, çubuğun ucunda — doğrudan etiket (renk kontrastından bağımsız)
    svg.appendChild(Object.assign(svgEl('text', {
      x: labelWidth + trackWidth + 8, y: y + 19,
      fill: inkSoft, 'font-size': 12, 'font-weight': 650,
      'font-variant-numeric': 'tabular-nums',
    }), { textContent: `${statusMark ? `${statusMark} ` : ''}%${(ratio * 100).toFixed(0)}` }));

    const hit = svgEl('rect', {
      x: 0, y, width, height: rowHeight, fill: 'transparent', style: 'cursor:pointer',
    });
    hit.addEventListener('mousemove', (event) => {
      const bounds = host.getBoundingClientRect();
      tip.show(
        `<b>${escapeHtml(tank.tankNo)}</b>`
        + `${tank.product ? `<div class="viz-tip__sub">${escapeHtml(tank.product)}</div>` : ''}`
        + `<div>${fmtNumber(tank.stock, 3)} / ${fmtNumber(tank.capacity, 2)} m³ `
        + `(%${(ratio * 100).toFixed(0)})</div>`
        + (statusText ? `<div><b>${escapeHtml(statusText)}</b></div>` : '')
        + (tank.reportedAt ? `<div class="viz-tip__sub">${escapeHtml(tank.reportedAt)}</div>` : ''),
        event.clientX - bounds.left, event.clientY - bounds.top,
      );
    });
    hit.addEventListener('mouseleave', tip.hide);
    svg.appendChild(hit);
  });

  host.appendChild(svg);
}

/* ====================================================================
   2) Son 24 saat stok seyri — tank başına çizgi
   ==================================================================== */

export function trendChart(host, series, { unit = 'ton' } = {}) {
  host.innerHTML = '';
  const tip = tooltipLayer(host);

  const width = host.clientWidth || 640;
  const height = 260;
  const pad = { top: 16, right: 56, bottom: 30, left: 54 };
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;

  const points = series.flatMap((s) => s.points);
  const times = points.map((p) => p.t);
  const minTime = Math.min(...times);
  const maxTime = Math.max(...times);
  const span = Math.max(1, maxTime - minTime);
  const max = niceMax(Math.max(...points.map((p) => p.v)));

  const x = (t) => pad.left + ((t - minTime) / span) * plotWidth;
  const y = (v) => pad.top + plotHeight - (v / max) * plotHeight;

  const svg = svgEl('svg', {
    viewBox: `0 0 ${width} ${height}`, role: 'img',
    style: `display:block;width:100%;height:${height}px;overflow:visible;`
         + 'stroke:none;fill:none',
  });

  const grid = cssVar('--viz-grid', '#e2e8f0');
  const inkMuted = cssVar('--text-muted');
  const surface = cssVar('--surface', '#fff');

  // Izgara: ince, düz, geri planda
  ticks(max).forEach((value) => {
    svg.appendChild(svgEl('line', {
      x1: pad.left, x2: pad.left + plotWidth, y1: y(value), y2: y(value),
      stroke: grid, 'stroke-width': 1, fill: 'none',
    }));
    svg.appendChild(Object.assign(svgEl('text', {
      x: pad.left - 8, y: y(value) + 4, 'text-anchor': 'end',
      fill: inkMuted, 'font-size': 10.5, 'font-variant-numeric': 'tabular-nums',
    }), { textContent: fmtNumber(value, value >= 100 ? 0 : 1) }));
  });

  // Zaman ekseni: birkaç okunaklı işaret
  const stepCount = Math.min(6, Math.max(2, series[0]?.points.length || 2));
  for (let i = 0; i <= stepCount; i += 1) {
    const t = minTime + (span / stepCount) * i;
    const label = new Date(t).toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' });
    svg.appendChild(Object.assign(svgEl('text', {
      x: x(t), y: height - 10, 'text-anchor': 'middle',
      fill: inkMuted, 'font-size': 10.5,
    }), { textContent: label }));
  }

  // Uç etiketleri çakıştığında üst üste bindirilmez; çakışan seriyi gösterge
  // taşır (etiketleri dikey kaydırmak onları çizgilerinden koparır).
  const placedLabels = [];
  const canLabel = (yValue) => {
    if (placedLabels.some((other) => Math.abs(other - yValue) < 14)) return false;
    placedLabels.push(yValue);
    return true;
  };

  series.forEach((s, index) => {
    const color = seriesColor(index);
    const sorted = [...s.points].sort((a, b) => a.t - b.t);
    const d = sorted.map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)} ${y(p.v).toFixed(1)}`).join(' ');

    svg.appendChild(svgEl('path', {
      d, fill: 'none', stroke: color, 'stroke-width': 2,
      'stroke-linejoin': 'round', 'stroke-linecap': 'round',
    }));

    // Uç işareti: yüzey renginde 2px halka ile
    const last = sorted[sorted.length - 1];
    svg.appendChild(svgEl('circle', {
      cx: x(last.t), cy: y(last.v), r: 4.5,
      fill: color, stroke: surface, 'stroke-width': 2,
    }));

    // Doğrudan uç etiketi (4 seriye kadar, yalnızca çakışmıyorsa)
    if (series.length <= 4 && canLabel(y(last.v))) {
      svg.appendChild(Object.assign(svgEl('text', {
        x: Math.min(x(last.t) + 9, width - 4), y: y(last.v) + 4,
        fill: cssVar('--text-soft'), 'font-size': 11, 'font-weight': 600,
      }), { textContent: s.name }));
    }

    sorted.forEach((point) => {
      const hit = svgEl('circle', {
        cx: x(point.t), cy: y(point.v), r: 10, fill: 'transparent', style: 'cursor:pointer',
      });
      hit.addEventListener('mousemove', (event) => {
        const bounds = host.getBoundingClientRect();
        tip.show(
          `<b>${escapeHtml(s.name)}</b>`
          + `<div>${fmtNumber(point.v, 3)} ${escapeHtml(unit)}</div>`
          + `<div class="viz-tip__sub">${new Date(point.t).toLocaleString('tr-TR', {
            day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}</div>`,
          event.clientX - bounds.left, event.clientY - bounds.top,
        );
      });
      hit.addEventListener('mouseleave', tip.hide);
      svg.appendChild(hit);
    });
  });

  host.appendChild(svg);
}

/* ====================================================================
   3) Petrol türüne göre stok — gruplanmış yatay çubuk
   ==================================================================== */

export function groupedBarChart(host, rows, seriesNames, { unit = 'ton' } = {}) {
  host.innerHTML = '';
  const tip = tooltipLayer(host);

  const width = host.clientWidth || 620;
  const labelWidth = 176;
  const barHeight = 14;
  const gap = 2;                            // yüzey boşluğu: komşu çubuklar arası
  const groupHeight = seriesNames.length * (barHeight + gap) + 22;
  const height = rows.length * groupHeight + 12;
  const plotWidth = Math.max(120, width - labelWidth - 74);

  const max = niceMax(Math.max(...rows.flatMap((row) => row.values)));
  const svg = svgEl('svg', {
    viewBox: `0 0 ${width} ${height}`, role: 'img',
    style: `display:block;width:100%;height:${height}px;overflow:visible;`
         + 'stroke:none;fill:none',
  });

  const inkSoft = cssVar('--text-soft');
  const inkMuted = cssVar('--text-muted');

  rows.forEach((row, rowIndex) => {
    const top = rowIndex * groupHeight + 6;

    svg.appendChild(Object.assign(svgEl('text', {
      x: 0, y: top + 12, fill: inkSoft, 'font-size': 12, 'font-weight': 600,
    }), { textContent: row.label.length > 20 ? `${row.label.slice(0, 19)}…` : row.label }));

    svg.appendChild(Object.assign(svgEl('text', {
      x: 0, y: top + 26, fill: inkMuted, 'font-size': 10,
    }), { textContent: row.sub || '' }));

    row.values.forEach((value, seriesIndex) => {
      const y = top + seriesIndex * (barHeight + gap);
      const barWidth = max > 0 ? (value / max) * plotWidth : 0;
      if (value > 0) {
        svg.appendChild(svgEl('rect', {
          x: labelWidth, y, width: Math.max(barWidth, 3), height: barHeight,
          rx: 4, fill: seriesColor(seriesIndex),
        }));
        svg.appendChild(Object.assign(svgEl('text', {
          x: labelWidth + Math.max(barWidth, 3) + 7, y: y + barHeight - 3,
          fill: inkSoft, 'font-size': 11, 'font-variant-numeric': 'tabular-nums',
        }), { textContent: fmtNumber(value, 2) }));
      }

      const hit = svgEl('rect', {
        x: labelWidth, y: y - 1, width: plotWidth + 60, height: barHeight + 2,
        fill: 'transparent', style: 'cursor:pointer',
      });
      hit.addEventListener('mousemove', (event) => {
        const bounds = host.getBoundingClientRect();
        tip.show(
          `<b>${escapeHtml(row.label)}</b>`
          + `${row.sub ? `<div class="viz-tip__sub">${escapeHtml(row.sub)}</div>` : ''}`
          + `<div>${escapeHtml(seriesNames[seriesIndex])}: `
          + `${fmtNumber(value, 3)} ${escapeHtml(unit)}</div>`,
          event.clientX - bounds.left, event.clientY - bounds.top,
        );
      });
      hit.addEventListener('mouseleave', tip.hide);
      svg.appendChild(hit);
    });
  });

  host.appendChild(svg);
}

/* --------------------------------------------------------------- gösterge */

export function legendHtml(names) {
  if (names.length < 2) return '';
  return `<div class="viz-legend">${names.map((name, index) => `
    <span class="viz-legend__item">
      <span class="viz-legend__swatch" style="background:${seriesColor(index)}"></span>
      ${escapeHtml(name)}
    </span>`).join('')}</div>`;
}

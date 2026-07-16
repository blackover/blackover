"""Self-contained HTML report: latest signal, price chart with trade
markers, RSI panel, equity curve and recent-signal table.

No external resources; the palette is a validated colorblind-safe set with
light and dark variants.  Buy/sell markers are shape-coded (triangle
up/down) as well as colored, so meaning never rides on color alone.
"""

from __future__ import annotations

import html
import json
import math

import pandas as pd

from .backtest import BacktestResult
from .strategy import Signal

CHART_W, CHART_H = 960, 320
PANEL_H = 150
MARGIN = {"left": 56, "right": 16, "top": 14, "bottom": 26}


def _scale(values, lo, hi, out_lo, out_hi):
    span = (hi - lo) or 1.0
    return [out_lo + (v - lo) / span * (out_hi - out_lo) for v in values]


def _nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    if hi <= lo:
        return [lo]
    raw = (hi - lo) / max(n - 1, 1)
    mag = 10.0 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if raw <= m * mag)
    first = math.ceil(lo / step) * step
    ticks, t = [], first
    while t <= hi + step * 1e-9:
        ticks.append(round(t, 10))
        t += step
    return ticks or [lo]


def _axis_and_grid(x0, x1, y0, y1, ticks, fmt, xlabels) -> str:
    parts = [f'<line x1="{x0}" y1="{y1}" x2="{x1}" y2="{y1}" class="axisline"/>']
    lo_px, hi_px = y1, y0
    for val, py in ticks:
        parts.append(f'<line x1="{x0}" y1="{py:.1f}" x2="{x1}" y2="{py:.1f}" class="grid"/>')
        parts.append(f'<text x="{x0 - 8}" y="{py + 4:.1f}" class="ticklabel" '
                     f'text-anchor="end">{fmt(val)}</text>')
    for label, px in xlabels:
        parts.append(f'<text x="{px:.1f}" y="{y1 + 18}" class="ticklabel" '
                     f'text-anchor="middle">{label}</text>')
    _ = (lo_px, hi_px)
    return "".join(parts)


def _line_chart(chart_id: str, times: list[str], series: list[dict],
                height: int = CHART_H, y_fmt=lambda v: f"{v:.4f}",
                markers: list[dict] | None = None,
                bands: list[float] | None = None,
                y_domain: tuple[float, float] | None = None) -> str:
    """Build one SVG line chart.  series = [{name, values, var}]."""
    x0, x1 = MARGIN["left"], CHART_W - MARGIN["right"]
    y0, y1 = MARGIN["top"], height - MARGIN["bottom"]
    n = len(times)

    all_vals = [v for s in series for v in s["values"] if v == v]
    lo, hi = (y_domain if y_domain else (min(all_vals), max(all_vals)))
    pad = (hi - lo) * 0.06 or abs(hi) * 0.01 or 1.0
    if not y_domain:
        lo, hi = lo - pad, hi + pad

    xs = _scale(range(n), 0, max(n - 1, 1), x0, x1)

    def ypx(v):
        return y1 - (v - lo) / ((hi - lo) or 1.0) * (y1 - y0)

    ticks = [(t, ypx(t)) for t in _nice_ticks(lo, hi)]
    label_idx = [round(i * (n - 1) / 5) for i in range(6)] if n > 6 else range(n)
    xlabels = [(times[i][5:16].replace("T", " "), xs[i]) for i in label_idx]

    parts = [f'<svg id="{chart_id}" viewBox="0 0 {CHART_W} {height}" '
             f'class="chart" role="img" aria-label="{chart_id} chart">']
    parts.append(_axis_and_grid(x0, x1, y0, y1, ticks, y_fmt, xlabels))

    for band in bands or []:
        parts.append(f'<line x1="{x0}" y1="{ypx(band):.1f}" x2="{x1}" '
                     f'y2="{ypx(band):.1f}" class="bandline"/>')
        parts.append(f'<text x="{x1 - 4}" y="{ypx(band) - 4:.1f}" '
                     f'class="ticklabel" text-anchor="end">{band:g}</text>')

    for s in series:
        pts = " ".join(f"{x:.1f},{ypx(v):.1f}"
                       for x, v in zip(xs, s["values"]) if v == v)
        parts.append(f'<polyline points="{pts}" fill="none" '
                     f'stroke="var({s["var"]})" stroke-width="2" '
                     f'stroke-linejoin="round" stroke-linecap="round"/>')

    for m in markers or []:
        px, py = xs[m["i"]], ypx(m["value"])
        if m["kind"] == "buy":     # triangle up, status-good
            path = f"M {px:.1f} {py - 7:.1f} L {px - 6:.1f} {py + 5:.1f} L {px + 6:.1f} {py + 5:.1f} Z"
            parts.append(f'<path d="{path}" fill="var(--status-good)" '
                         f'stroke="var(--surface-1)" stroke-width="2"/>')
        else:                       # triangle down, status-critical
            path = f"M {px:.1f} {py + 7:.1f} L {px - 6:.1f} {py - 5:.1f} L {px + 6:.1f} {py - 5:.1f} Z"
            parts.append(f'<path d="{path}" fill="var(--status-critical)" '
                         f'stroke="var(--surface-1)" stroke-width="2"/>')

    # hover layer (crosshair + dot), driven by inline JS
    parts.append(f'<line class="crosshair" x1="0" x2="0" y1="{y0}" y2="{y1}" '
                 f'visibility="hidden"/>')
    parts.append('<circle class="hoverdot" r="4" visibility="hidden"/>')
    parts.append(f'<rect class="hoverzone" x="{x0}" y="{y0}" '
                 f'width="{x1 - x0}" height="{y1 - y0}" fill="transparent"/>')
    parts.append("</svg>")

    payload = {
        "times": times,
        "series": [{"name": s["name"], "values": s["values"], "var": s["var"]}
                   for s in series],
        "x0": x0, "x1": x1, "lo": lo, "hi": hi, "y0": y0, "y1": y1,
        "fmt": 4 if height == CHART_H else 1,
    }
    data = json.dumps(payload)
    return (f'<div class="chartwrap">{"".join(parts)}'
            f'<div class="tooltip" hidden></div>'
            f'<script type="application/json" class="chartdata" '
            f'data-for="{chart_id}">{data}</script></div>')


def _fmt_val(v, digits=4):
    return "n/a" if v is None else f"{v:.{digits}f}"


def _signal_tiles(signal: Signal) -> str:
    icon = {"BUY": "&#9650;", "SELL": "&#9660;", "HOLD": "&#9679;"}[signal.action]
    cls = {"BUY": "good", "SELL": "critical", "HOLD": "neutral"}[signal.action]
    prob = ("n/a" if signal.prob_up is None
            else f"{signal.prob_up * 100:.1f}%")
    return f"""
<div class="tiles">
  <div class="tile action {cls}">
    <div class="tile-label">Signal</div>
    <div class="tile-value"><span class="sig-icon" aria-hidden="true">{icon}</span> {signal.action}</div>
  </div>
  <div class="tile">
    <div class="tile-label">XRP price</div>
    <div class="tile-value">${signal.price:.4f}</div>
  </div>
  <div class="tile">
    <div class="tile-label">Confidence</div>
    <div class="tile-value">{signal.confidence:.0f}%</div>
  </div>
  <div class="tile">
    <div class="tile-label">ML&nbsp;P(next candle up)</div>
    <div class="tile-value">{prob}</div>
  </div>
</div>
<p class="meta">Candle {signal.time:%Y-%m-%d %H:%M} UTC &middot; interval {signal.interval}
 &middot; data source: {html.escape(signal.source or "n/a")} &middot; score {signal.score:+.3f}</p>
"""


def _reasons(signal: Signal) -> str:
    items = "".join(f"<li>{html.escape(r)}</li>" for r in signal.reasons)
    return f'<h2>Why</h2><ul class="reasons">{items}</ul>'


def _stats_table(result: BacktestResult) -> str:
    wr = "n/a" if result.win_rate is None else f"{result.win_rate:.1%}"
    rows = [
        ("Period", f"{result.start:%Y-%m-%d %H:%M} &rarr; {result.end:%Y-%m-%d %H:%M} UTC"),
        ("Bars", f"{result.bars} ({result.interval})"),
        ("Strategy return", f"{result.total_return:+.2%}"),
        ("Buy &amp; hold return", f"{result.buy_hold_return:+.2%}"),
        ("Max drawdown", f"{result.max_drawdown:.2%}"),
        ("Sharpe (annualized)", f"{result.sharpe:.2f}"),
        ("Round trips / win rate", f"{result.n_trades} / {wr}"),
        ("Fee per side", f"{result.fee:.2%}"),
    ]
    body = "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in rows)
    return f'<h2>Backtest (walk-forward)</h2><table class="kv">{body}</table>'


def _signals_table(result: BacktestResult, n: int = 15) -> str:
    tail = result.signals.tail(n).iloc[::-1]
    rows = []
    for _, r in tail.iterrows():
        prob = "n/a" if pd.isna(r["prob_up"]) else f"{r['prob_up'] * 100:.0f}%"
        rows.append(
            f"<tr><td>{r['time']:%Y-%m-%d %H:%M}</td>"
            f"<td>${r['close']:.4f}</td>"
            f"<td class='act-{r['action'].lower()}'>{r['action']}</td>"
            f"<td>{r['score']:+.2f}</td><td>{prob}</td></tr>")
    return ("<h2>Recent signals</h2>"
            "<table class='sig'><thead><tr><th>Time (UTC)</th><th>Price</th>"
            "<th>Action</th><th>Score</th><th>P(up)</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>")


CSS = """
:root { color-scheme: light dark; }
body { margin: 0; padding: 24px; background: var(--page); color: var(--text-primary);
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
.viz-root {
  --page: #f9f9f7; --surface-1: #fcfcfb;
  --text-primary: #0b0b0b; --text-secondary: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --series-1: #2a78d6; --series-2: #008300; --series-rsi: #4a3aa7;
  --status-good: #0ca30c; --status-critical: #d03b3b;
  max-width: 1020px; margin: 0 auto;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) .viz-root {
    --page: #0d0d0d; --surface-1: #1a1a19;
    --text-primary: #ffffff; --text-secondary: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
    --series-1: #3987e5; --series-2: #008300; --series-rsi: #9085e9;
  }
}
:root[data-theme="dark"] .viz-root {
  --page: #0d0d0d; --surface-1: #1a1a19;
  --text-primary: #ffffff; --text-secondary: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
  --series-1: #3987e5; --series-2: #008300; --series-rsi: #9085e9;
}
body { background: var(--page); }
h1 { font-size: 1.4rem; margin: 0 0 4px; }
h2 { font-size: 1.05rem; margin: 28px 0 10px; }
.meta, .disclaimer { color: var(--text-secondary); font-size: 0.85rem; }
.disclaimer { border: 1px solid var(--border); border-radius: 8px;
  padding: 10px 14px; background: var(--surface-1); margin-top: 28px; }
.card { background: var(--surface-1); border: 1px solid var(--border);
  border-radius: 10px; padding: 16px 18px; margin-top: 14px; }
.tiles { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 12px; }
.tile { flex: 1 1 150px; background: var(--surface-1);
  border: 1px solid var(--border); border-radius: 10px; padding: 12px 16px; }
.tile-label { color: var(--text-secondary); font-size: 0.78rem;
  text-transform: uppercase; letter-spacing: 0.04em; }
.tile-value { font-size: 1.5rem; font-weight: 650; margin-top: 4px; }
.tile.action .sig-icon { font-size: 1.1rem; vertical-align: 2px; }
.tile.action.good .sig-icon { color: var(--status-good); }
.tile.action.critical .sig-icon { color: var(--status-critical); }
.tile.action.neutral .sig-icon { color: var(--muted); }
.chartwrap { position: relative; background: var(--surface-1);
  border: 1px solid var(--border); border-radius: 10px; padding: 10px;
  margin-top: 10px; }
.chart { width: 100%; height: auto; display: block; }
.grid { stroke: var(--grid); stroke-width: 1; }
.axisline { stroke: var(--axis); stroke-width: 1; }
.bandline { stroke: var(--grid); stroke-width: 1; stroke-dasharray: 4 4; }
.ticklabel { fill: var(--muted); font-size: 11px;
  font-family: system-ui, sans-serif; font-variant-numeric: tabular-nums; }
.crosshair { stroke: var(--axis); stroke-width: 1; }
.hoverdot { fill: var(--series-1); stroke: var(--surface-1); stroke-width: 2; }
.tooltip { position: absolute; pointer-events: none; background: var(--surface-1);
  border: 1px solid var(--border); border-radius: 6px; padding: 6px 9px;
  font-size: 12px; color: var(--text-primary); box-shadow: 0 2px 8px rgba(0,0,0,0.12);
  white-space: nowrap; }
.legend { display: flex; gap: 18px; margin: 8px 2px 0; font-size: 0.82rem;
  color: var(--text-secondary); flex-wrap: wrap; }
.legend .chip { display: inline-block; width: 10px; height: 10px;
  border-radius: 2px; margin-right: 6px; vertical-align: -1px; }
.legend .tri { display: inline-block; margin-right: 6px; }
table { border-collapse: collapse; width: 100%; background: var(--surface-1);
  border: 1px solid var(--border); border-radius: 10px; overflow: hidden;
  font-size: 0.88rem; }
th, td { text-align: left; padding: 7px 12px;
  border-bottom: 1px solid var(--grid); font-variant-numeric: tabular-nums; }
thead th { color: var(--text-secondary); font-weight: 600; }
table.kv th { width: 40%; color: var(--text-secondary); font-weight: 500; }
tr:last-child th, tr:last-child td { border-bottom: none; }
.act-buy, .act-sell, .act-hold { font-weight: 650; }
ul.reasons { background: var(--surface-1); border: 1px solid var(--border);
  border-radius: 10px; margin: 0; padding: 12px 12px 12px 32px;
  font-size: 0.9rem; line-height: 1.6; }
.overflow { overflow-x: auto; }
"""

JS = """
document.querySelectorAll('.chartdata').forEach(function (node) {
  var data = JSON.parse(node.textContent);
  var svg = document.getElementById(node.dataset.for);
  var wrap = svg.closest('.chartwrap');
  var tip = wrap.querySelector('.tooltip');
  var cross = svg.querySelector('.crosshair');
  var dot = svg.querySelector('.hoverdot');
  var zone = svg.querySelector('.hoverzone');
  var n = data.times.length;
  function ypx(v) {
    return data.y1 - (v - data.lo) / ((data.hi - data.lo) || 1) * (data.y1 - data.y0);
  }
  zone.addEventListener('mousemove', function (ev) {
    var pt = svg.createSVGPoint();
    pt.x = ev.clientX; pt.y = ev.clientY;
    var loc = pt.matrixTransform(svg.getScreenCTM().inverse());
    var frac = (loc.x - data.x0) / (data.x1 - data.x0);
    var i = Math.max(0, Math.min(n - 1, Math.round(frac * (n - 1))));
    var px = data.x0 + (i / Math.max(n - 1, 1)) * (data.x1 - data.x0);
    cross.setAttribute('x1', px); cross.setAttribute('x2', px);
    cross.removeAttribute('visibility');
    var first = data.series[0].values[i];
    if (first === null || first === undefined || isNaN(first)) {
      dot.setAttribute('visibility', 'hidden');
    } else {
      dot.setAttribute('cx', px); dot.setAttribute('cy', ypx(first));
      dot.style.fill = 'var(' + data.series[0].var + ')';
      dot.removeAttribute('visibility');
    }
    var lines = ['<strong>' + data.times[i].slice(0, 16).replace('T', ' ') + '</strong>'];
    data.series.forEach(function (s) {
      var v = s.values[i];
      if (v === null || v === undefined || isNaN(v)) return;
      lines.push(s.name + ': ' + v.toFixed(data.fmt));
    });
    tip.innerHTML = lines.join('<br>');
    tip.hidden = false;
    var box = wrap.getBoundingClientRect();
    var sx = box.width / svg.viewBox.baseVal.width;
    var left = px * sx + 14;
    if (left + tip.offsetWidth > box.width - 8) left = px * sx - tip.offsetWidth - 14;
    tip.style.left = left + 'px';
    tip.style.top = '18px';
  });
  zone.addEventListener('mouseleave', function () {
    tip.hidden = true;
    cross.setAttribute('visibility', 'hidden');
    dot.setAttribute('visibility', 'hidden');
  });
});
"""


def generate_report(signal: Signal, ind: pd.DataFrame,
                    result: BacktestResult | None = None,
                    out_path: str = "report.html",
                    window: int = 300) -> str:
    """Write the HTML report; returns the path."""
    tail = ind.tail(window).reset_index(drop=True)
    times = [t.isoformat() for t in tail["time"]]
    closes = [round(float(v), 6) for v in tail["close"]]
    rsi_vals = [None if pd.isna(v) else round(float(v), 2)
                for v in tail["rsi_14"]]

    markers = []
    if result is not None:
        time_to_idx = {t: i for i, t in enumerate(tail["time"])}
        for tr in result.trades:
            i = time_to_idx.get(tr.entry_time)
            if i is not None:
                markers.append({"i": i, "value": tr.entry_price, "kind": "buy"})
            if tr.exit_time is not None:
                j = time_to_idx.get(tr.exit_time)
                if j is not None:
                    markers.append({"i": j, "value": tr.exit_price, "kind": "sell"})

    price_chart = _line_chart(
        "price", times, [{"name": "XRP close (USD)", "values": closes,
                          "var": "--series-1"}], markers=markers)
    price_legend = """
<div class="legend">
  <span><span class="chip" style="background:var(--series-1)"></span>XRP close (USD)</span>
  <span><span class="tri" style="color:var(--status-good)">&#9650;</span>paper buy</span>
  <span><span class="tri" style="color:var(--status-critical)">&#9660;</span>paper sell</span>
</div>"""

    rsi_chart = _line_chart(
        "rsi", times, [{"name": "RSI(14)", "values": rsi_vals,
                        "var": "--series-rsi"}],
        height=PANEL_H, y_fmt=lambda v: f"{v:g}", bands=[30, 70],
        y_domain=(0, 100))

    equity_section = ""
    if result is not None and result.equity is not None:
        eq = result.equity.tail(window).reset_index(drop=True)
        base_s = float(eq["equity"].iloc[0]) or 1.0
        base_b = float(eq["buy_hold"].iloc[0]) or 1.0
        eq_times = [t.isoformat() for t in eq["time"]]
        strat = [round(float(v) / base_s * 100.0, 2) for v in eq["equity"]]
        hold = [round(float(v) / base_b * 100.0, 2) for v in eq["buy_hold"]]
        eq_chart = _line_chart(
            "equity", eq_times,
            [{"name": "Strategy", "values": strat, "var": "--series-1"},
             {"name": "Buy & hold", "values": hold, "var": "--series-2"}],
            height=220, y_fmt=lambda v: f"{v:g}")
        equity_section = f"""
<h2>Strategy vs buy &amp; hold (indexed to 100)</h2>
{eq_chart}
<div class="legend">
  <span><span class="chip" style="background:var(--series-1)"></span>Strategy</span>
  <span><span class="chip" style="background:var(--series-2)"></span>Buy &amp; hold</span>
</div>"""

    stats = _stats_table(result) if result is not None else ""
    sig_table = (f'<div class="overflow">{_signals_table(result)}</div>'
                 if result is not None else "")

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>XRP signal report</title>
<style>{CSS}</style>
</head>
<body>
<div class="viz-root">
  <h1>XRP short-term signal</h1>
  <p class="meta">Generated {pd.Timestamp.now(tz="UTC"):%Y-%m-%d %H:%M} UTC</p>
  {_signal_tiles(signal)}
  {_reasons(signal)}
  <h2>Price &mdash; last {len(tail)} candles ({signal.interval})</h2>
  {price_chart}
  {price_legend}
  <h2>RSI (14)</h2>
  {rsi_chart}
  {equity_section}
  {stats}
  {sig_table}
  <div class="disclaimer"><strong>Disclaimer:</strong> educational tool, not financial
  advice. Signals are statistical guesses about a highly volatile asset and will
  often be wrong. All trading here is simulated (paper) &mdash; no real orders are
  placed. Never risk money you cannot afford to lose.</div>
</div>
<script>{JS}</script>
</body>
</html>"""

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(page)
    return out_path

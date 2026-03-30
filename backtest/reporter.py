"""
Backtest HTML report generator.
Pure HTML + inline CSS — no external dependencies.
"""
from __future__ import annotations

import html
import json
import os
from datetime import datetime, timezone
from typing import Any


def generate_backtest_report(
    results: dict,
    output_path: str = "data/backtest_report.html",
) -> None:
    """Generate a backtest report HTML file."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    gate_passed = results.get("gate_passed")
    sigma_table = results.get("sigma_table", {})
    has_split = "train" in results and "test" in results
    full = results.get("full")

    body_parts: list[str] = []

    # Header
    body_parts.append("""
    <div style="text-align:center; margin-bottom:30px;">
        <h1>Polymarket Weather Bot — Backtest Report</h1>
        <p style="color:#888;">Generated: {ts}</p>
    </div>
    """.format(ts=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")))

    # Gate status
    if gate_passed is not None:
        color = "#2ecc71" if gate_passed else "#e74c3c"
        text = "PASSED" if gate_passed else "FAILED"
        body_parts.append(f"""
        <div style="background:{color}; color:white; padding:20px; border-radius:8px;
                    text-align:center; font-size:24px; margin-bottom:30px;">
            Out-of-Sample Gate: <strong>{text}</strong>
        </div>
        """)

    # Summary tables
    if has_split:
        for label, data in [("Train Window", results["train"]), ("Test Window (Out-of-Sample)", results["test"])]:
            body_parts.append(_summary_table(label, data))
    elif full:
        body_parts.append(_summary_table("Full Backtest", full))

    # Sigma calibration
    if sigma_table:
        body_parts.append("<h2>Sigma Calibration (per month)</h2>")
        body_parts.append("<table><tr><th>Month</th><th>Sigma (C)</th></tr>")
        for month in sorted(sigma_table.keys(), key=lambda x: int(x)):
            body_parts.append(f"<tr><td>{month}</td><td>{sigma_table[month]:.2f}</td></tr>")
        body_parts.append("</table>")

    # Per-city breakdown
    all_results = []
    if has_split:
        for key in ("train", "test"):
            all_results.extend(results[key].get("results", []))
    elif full:
        all_results = full.get("results", [])

    if all_results:
        body_parts.append(_city_breakdown(all_results))

    # Trade log
    if all_results:
        body_parts.append(_trade_log(all_results[:50]))

    content = _wrap_html("Backtest Report", "\n".join(body_parts))
    with open(output_path, "w") as f:
        f.write(content)


def generate_crowding_report(
    reports: list[dict],
    output_path: str = "data/crowding_report.html",
) -> None:
    """Generate a crowding analysis HTML report."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    body_parts: list[str] = []
    body_parts.append("""
    <div style="text-align:center; margin-bottom:30px;">
        <h1>City Crowding Analysis</h1>
        <p style="color:#888;">Generated: {ts}</p>
    </div>
    """.format(ts=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")))

    # Ranked table
    body_parts.append("""
    <table>
        <tr>
            <th>Rank</th><th>City</th><th>Score</th><th>TTR</th><th>DWC</th>
            <th>EDR</th><th>VCR</th><th>BAS</th><th>Window</th><th>Trend</th>
        </tr>
    """)
    for i, r in enumerate(reports, 1):
        score = r.get("opportunity_score", 0)
        color = _score_color(score)
        metrics = r.get("metrics", {})
        trend_arrow = {"improving": "&#x2191;", "degrading": "&#x2193;"}.get(
            r.get("trend", "stable"), "&#x2194;"
        )
        body_parts.append(f"""
        <tr>
            <td>{i}</td>
            <td><strong>{html.escape(r.get('city', ''))}</strong></td>
            <td style="background:{color}; color:white; font-weight:bold;">{score:.1f}</td>
            <td>{metrics.get('ttr', 0):.0f}</td>
            <td>{metrics.get('dwc', 0):.0f}</td>
            <td>{metrics.get('edr', 0):.0f}</td>
            <td>{metrics.get('vcr', 0):.0f}</td>
            <td>{metrics.get('bas', 0):.0f}</td>
            <td>{r.get('entry_window_minutes', '-')}min</td>
            <td>{trend_arrow}</td>
        </tr>
        """)
    body_parts.append("</table>")

    # Per-city radar charts (simple bar representation)
    for r in reports:
        body_parts.append(_city_card(r))

    content = _wrap_html("Crowding Report", "\n".join(body_parts))
    with open(output_path, "w") as f:
        f.write(content)


def _summary_table(label: str, data: dict) -> str:
    rows = [
        ("Markets entered", data.get("entered", 0)),
        ("Markets skipped", data.get("skipped", 0)),
        ("Wins", data.get("wins", 0)),
        ("Win rate", f"{data.get('win_rate', 0):.0%}"),
        ("Net P&L", f"${data.get('net_pnl', 0):+.2f}"),
        ("Avg P&L per ladder", f"${data.get('avg_pnl', 0):+.2f}"),
        ("ROI", f"{data.get('avg_roi', 0):.1%}"),
        ("Avg edge at entry", f"{data.get('avg_edge', 0):.1%}"),
        ("Total capital risked", f"${data.get('total_cost', 0):.2f}"),
    ]
    html_rows = "".join(f"<tr><td>{k}</td><td><strong>{v}</strong></td></tr>" for k, v in rows)
    return f"<h2>{html.escape(label)}</h2><table>{html_rows}</table>"


def _city_breakdown(results: list[dict]) -> str:
    cities: dict[str, list[dict]] = {}
    for r in results:
        c = r.get("city", "Unknown")
        cities.setdefault(c, []).append(r)

    parts = ["<h2>Per-City Breakdown</h2><table>",
             "<tr><th>City</th><th>Trades</th><th>Win Rate</th><th>Net P&L</th><th>Avg Edge</th></tr>"]
    for city, trades in sorted(cities.items()):
        wins = sum(1 for t in trades if t.get("any_won"))
        pnl = sum(t.get("net_pnl", 0) for t in trades)
        edges = [p["edge"] for t in trades for p in t.get("positions", [])]
        avg_edge = sum(edges) / len(edges) if edges else 0
        wr = wins / len(trades) if trades else 0
        parts.append(
            f"<tr><td>{html.escape(city)}</td><td>{len(trades)}</td>"
            f"<td>{wr:.0%}</td><td>${pnl:+.2f}</td><td>{avg_edge:.1%}</td></tr>"
        )
    parts.append("</table>")
    return "\n".join(parts)


def _trade_log(results: list[dict]) -> str:
    parts = ["<h2>Trade Log (last 50)</h2><table>",
             "<tr><th>City</th><th>Date</th><th>Forecast</th><th>Actual</th>"
             "<th>Cost</th><th>Payout</th><th>P&L</th><th>Won?</th></tr>"]
    for r in results:
        won = "Yes" if r.get("any_won") else "No"
        color = "#2ecc71" if r.get("any_won") else "#e74c3c"
        parts.append(
            f"<tr><td>{html.escape(r.get('city', ''))}</td>"
            f"<td>{r.get('date', '')}</td>"
            f"<td>{r.get('forecast_centre', 0):.1f}C</td>"
            f"<td>{r.get('actual_high') or 'N/A'}</td>"
            f"<td>${r.get('total_cost', 0):.2f}</td>"
            f"<td>${r.get('total_payout', 0):.2f}</td>"
            f"<td style='color:{color}'>${r.get('net_pnl', 0):+.2f}</td>"
            f"<td style='color:{color}'>{won}</td></tr>"
        )
    parts.append("</table>")
    return "\n".join(parts)


def _city_card(report: dict) -> str:
    metrics = report.get("metrics", {})
    bars = ""
    for name in ("ttr", "dwc", "edr", "vcr", "bas"):
        val = metrics.get(name, 0)
        color = _score_color(val)
        bars += (
            f"<div style='margin:4px 0;'>"
            f"<span style='display:inline-block;width:40px;'>{name.upper()}</span>"
            f"<div style='display:inline-block;width:{val*2}px;height:16px;"
            f"background:{color};border-radius:3px;'></div>"
            f" <span>{val:.0f}</span></div>"
        )
    return f"""
    <div style="border:1px solid #ddd; border-radius:8px; padding:16px; margin:16px 0;">
        <h3>{html.escape(report.get('city', ''))}
            <span style="float:right; font-size:14px; color:#888;">
            score: {report.get('opportunity_score', 0):.1f}
            </span>
        </h3>
        {bars}
        <p style="color:#888; font-size:12px;">
            Station: {report.get('resolution_station', 'N/A')} |
            Window: {report.get('entry_window_minutes', '-')}min |
            Markets: {report.get('markets_analysed', 0)}
        </p>
    </div>
    """


def _score_color(score: float) -> str:
    if score >= 75:
        return "#2ecc71"
    if score >= 55:
        return "#f39c12"
    if score >= 35:
        return "#e67e22"
    return "#e74c3c"


def _wrap_html(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         max-width: 1000px; margin: 40px auto; padding: 0 20px;
         background: #fafafa; color: #333; }}
  h1 {{ color: #2c3e50; }}
  h2 {{ color: #34495e; margin-top: 40px; border-bottom: 2px solid #ecf0f1; padding-bottom: 8px; }}
  table {{ border-collapse: collapse; width: 100%; margin: 16px 0; }}
  th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
  th {{ background: #34495e; color: white; }}
  tr:nth-child(even) {{ background: #f9f9f9; }}
</style>
</head>
<body>
{body}
</body>
</html>"""

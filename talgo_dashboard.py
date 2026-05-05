"""Render the Talgo planner dashboard as a standalone HTML page (no external deps)."""
from __future__ import annotations
import html as _html
import json as _json
from typing import Any, Dict


def _e(s) -> str:
    return _html.escape(str(s))


def render_dashboard(algorithm: str, ao: Dict[str, Any], objective_value: float) -> str:
    es = ao.get("executive_summary", {})
    sc = ao.get("scorecard", [])
    pd = ao.get("per_depot_kpis", {})
    sla = ao.get("sla_risk", [])[:20]
    gantt = ao.get("gantt_absolute", [])
    bom = ao.get("parts_bom", [])
    rep = ao.get("replenishment_alerts", [])
    cv = (ao.get("solver_extras") or {}).get("energy_curve") or []
    rag_color = {"green": "#16a34a", "amber": "#f59e0b", "red": "#dc2626", "info": "#64748b"}

    # Gantt SVG
    gantt_svg = ""
    if gantt:
        techs = sorted({g["technician_id"] for g in gantt})
        max_h = max(g["end_hour"] for g in gantt)
        row_h = 28
        width = 800
        unit = (width - 120) / max(1, max_h)
        rows = []
        for i, tech in enumerate(techs):
            y = 18 + i * row_h
            rows.append(f'<text x="0" y="{y+18}" font-size="12" fill="#1e293b">{_e(tech)}</text>')
            for g in [g for g in gantt if g["technician_id"] == tech]:
                x0 = 120 + g["start_hour"] * unit
                w = (g["end_hour"] - g["start_hour"]) * unit
                col = "#0ea5e9" if g.get("priority", 3) <= 3 else "#f59e0b" if g["priority"] == 4 else "#dc2626"
                rows.append(
                    f'<g><rect x="{x0:.1f}" y="{y}" width="{w:.1f}" height="{row_h-6}" rx="4" fill="{col}" opacity="0.85"/>'
                    f'<text x="{x0+4:.1f}" y="{y+18}" font-size="11" fill="white">{_e(g["task_id"])} • {_e(g.get("start_clock",""))}</text></g>'
                )
        # axis ticks
        for h in range(0, int(max_h) + 1, 1):
            x = 120 + h * unit
            rows.append(f'<line x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{18+len(techs)*row_h}" stroke="#e2e8f0" stroke-width="1"/>'
                        f'<text x="{x-6:.1f}" y="{18+len(techs)*row_h+12}" font-size="10" fill="#64748b">+{h}h</text>')
        gantt_svg = (
            f'<svg viewBox="0 0 {width} {18+len(techs)*row_h+24}" width="100%" '
            f'style="max-width:{width}px;background:#fff;border:1px solid #e2e8f0;border-radius:8px;">'
            + "".join(rows) + '</svg>'
        )

    # SA convergence curve
    curve_svg = ""
    if cv:
        cs = cv[:200] if len(cv) > 200 else cv
        mn, mx = min(cs), max(cs)
        rng = (mx - mn) or 1
        pts = " ".join(f"{i*(800/max(1,len(cs)-1)):.1f},{120-(v-mn)/rng*100:.1f}" for i, v in enumerate(cs))
        curve_svg = (
            '<svg viewBox="0 0 820 130" width="100%" style="background:#fff;border:1px solid #e2e8f0;border-radius:8px;">'
            f'<polyline fill="none" stroke="#0ea5e9" stroke-width="2" points="{pts}"/>'
            f'<text x="6" y="12" font-size="10" fill="#64748b">best energy (max={mx:.0f}, min={mn:.0f})</text>'
            '</svg>'
        )

    # Sankey-ish summary (we render as a per-depot list rather than a true Sankey for portability)
    depot_rows = "".join(
        f'<tr><td>{_e(row["depot_name"])}</td><td>{row["tech_count"]}</td><td>{row["tasks_assigned"]}</td>'
        f'<td>{row["utilisation_pct"]:.0f}%</td><td>€{row["labor_eur"]:.0f}</td><td>€{row["travel_eur"]:.0f}</td></tr>'
        for row in pd.values()
    )

    sla_rows = "".join(
        f'<tr style="background:{rag_color.get(r["status"],"#fff")}10;">'
        f'<td>{_e(r["task_id"])}</td><td>{_e(r["site_id"])}</td>'
        f'<td>{r["priority"]}</td><td>{r["deadline_hours"]}h</td><td>{r["eta_hours"]}h</td>'
        f'<td>{r["slack_hours"]:+.1f}h</td>'
        f'<td><span style="display:inline-block;width:10px;height:10px;border-radius:5px;background:{rag_color.get(r["status"],"#999")};"></span> {r["status"]}</td>'
        f'</tr>'
        for r in sla
    )

    bom_rows = "".join(
        f'<tr><td>{_e(b["task_id"])}</td><td>{_e(b["part_id"])}</td><td>{b["qty"]}</td><td>{_e(b["supplied_by_depot"])}</td><td>{_e(b["executed_at_depot"])}</td></tr>'
        for b in bom
    )

    rep_rows = "".join(
        f'<tr><td>{_e(r["depot_id"])}</td><td>{_e(r["part_id"])}</td><td>{r["stock_before"]}→{r["stock_after"]}</td><td>{_e(r["level"])}</td></tr>'
        for r in rep
    )

    sc_rows = "".join(
        f'<tr><td>{_e(r["kpi"])}</td><td style="text-align:right">{r["actual"]}</td>'
        f'<td><span style="display:inline-block;width:10px;height:10px;border-radius:5px;background:{rag_color.get(r["rag"],"#999")};"></span> {r["rag"]}</td></tr>'
        for r in sc
    )

    style = """
    body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:#0f172a;background:#f8fafc;margin:0;padding:24px;}
    h1{font-size:22px;margin:0 0 4px;} h2{font-size:16px;margin:24px 0 8px;color:#334155;}
    .card{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:14px 16px;margin:8px 0;}
    .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;}
    .kpi{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:10px 14px;}
    .kpi b{font-size:20px;display:block;color:#0ea5e9;}
    table{border-collapse:collapse;width:100%;background:#fff;}
    th,td{padding:6px 10px;border-bottom:1px solid #e2e8f0;text-align:left;font-size:13px;}
    th{background:#f1f5f9;color:#475569;font-weight:600;}
    .muted{color:#64748b;font-size:12px;}
    """

    bp = ao.get("boss_pitch", "")
    bottom = es.get("bottom_line", "")
    hk = es.get("headline_kpis", {})
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Talgo Dashboard — {_e(algorithm)}</title>
<style>{style}</style></head><body>
<h1>Talgo MRO Dashboard — {_e(algorithm)}</h1>
<p class="muted">QCentroid use case 792 • objective {objective_value:.2f} EUR • {_e(ao.get("solution_status",""))}</p>

<div class="card"><b>Bottom line:</b> {_e(bottom)}</div>
<div class="card"><b>Boss pitch:</b> {_e(bp)}</div>

<div class="grid">
  <div class="kpi"><span class="muted">Tasks assigned</span><b>{hk.get("tasks_assigned_pct",0):.0f}%</b></div>
  <div class="kpi"><span class="muted">SLA on-time</span><b>{hk.get("sla_on_time_pct",0):.0f}%</b></div>
  <div class="kpi"><span class="muted">Total cost</span><b>€{hk.get("total_cost_eur",0):.0f}</b></div>
  <div class="kpi"><span class="muted">Busiest depot</span><b>{_e(hk.get("busiest_depot",""))}</b><span class="muted">{hk.get("busiest_depot_utilisation_pct",0):.0f}% util</span></div>
</div>

<h2>Scorecard vs Talgo targets</h2>
<table><thead><tr><th>KPI</th><th>Actual</th><th>RAG</th></tr></thead><tbody>{sc_rows}</tbody></table>

<h2>Per-depot snapshot</h2>
<table><thead><tr><th>Depot</th><th>Headcount</th><th>Tasks today</th><th>Utilisation</th><th>Labor</th><th>Travel</th></tr></thead><tbody>{depot_rows}</tbody></table>

<h2>Schedule (Gantt — colour by priority)</h2>
{gantt_svg}

<h2>SLA risk per task (priority-weighted)</h2>
<table><thead><tr><th>Task</th><th>Site</th><th>Priority</th><th>Deadline</th><th>ETA</th><th>Slack</th><th>Status</th></tr></thead><tbody>{sla_rows}</tbody></table>

<h2>Bill of parts</h2>
<table><thead><tr><th>Task</th><th>Part</th><th>Qty</th><th>Supplied by</th><th>Used at</th></tr></thead><tbody>{bom_rows}</tbody></table>

<h2>Replenishment alerts</h2>
<table><thead><tr><th>Depot</th><th>Part</th><th>Stock before → after</th><th>Level</th></tr></thead><tbody>{rep_rows}</tbody></table>

{f'<h2>SA convergence (best energy per outer step)</h2>{curve_svg}' if curve_svg else ''}

<p class="muted">Generated by Talgo / QCentroid solver pipeline (v6) • {_e(ao.get("compliance",{}).get("run_started_at_utc",""))} • dataset_sha {_e((ao.get("compliance",{}).get("dataset_sha256") or "")[:12])}…</p>
</body></html>"""

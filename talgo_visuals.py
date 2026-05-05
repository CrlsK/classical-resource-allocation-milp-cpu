"""
Standalone SVG generators for Talgo MRO operations review.
All outputs are self-contained inline SVG strings (no external deps).

Visualisations included:
- spain_depot_map_svg(...)        — peninsular Spain outline with depot pins + task pins
                                     and arrows from each depot to the tasks it serves.
- cost_waterfall_svg(cost_breakdown) — labor + travel + SLA penalty + unassigned waterfall.
- skill_coverage_heatmap_svg(...)  — depot × skill heatmap (cells = number of techs).
- wrap_svg_in_html(title, svg)    — wraps an SVG into a self-contained HTML page so the
                                     QCentroid additional-output viewer can preview it
                                     inline (the viewer only previews .html and .json).
- markdown_to_html(title, md)     — minimal in-place markdown → HTML conversion (no deps)
                                     so the presentation-pack file is also previewable.
"""
from __future__ import annotations
import html as _html
from typing import Any, Dict, List


def _e(s) -> str:
    return _html.escape(str(s))


# --- Spain map -------------------------------------------------------------
# Bounding box for peninsular Spain (with margin for Balearics/Canaries excluded)
_SPAIN_BBOX = {"lat_min": 35.5, "lat_max": 44.0, "lon_min": -10.0, "lon_max": 4.5}
# Rough outline of mainland Spain (lat, lon) traced very loosely — purely illustrative.
_SPAIN_OUTLINE = [
    (43.7, -7.7), (43.6, -5.3), (43.5, -3.0), (43.3, -1.8), (42.7, -1.5),
    (42.6, 0.7), (42.4, 1.7), (42.0, 3.2), (41.0, 3.3), (40.0, 1.0),
    (38.7, 0.2), (37.5, -0.7), (36.7, -2.1), (36.7, -4.4), (36.1, -5.4),
    (36.6, -6.5), (37.2, -7.4), (38.4, -7.3), (40.0, -6.8), (41.5, -8.6),
    (42.5, -8.9), (43.7, -7.7),
]


def _proj(lat: float, lon: float, w: int, h: int, pad: int = 24) -> tuple[float, float]:
    bb = _SPAIN_BBOX
    x = pad + (lon - bb["lon_min"]) / (bb["lon_max"] - bb["lon_min"]) * (w - 2 * pad)
    y = pad + (bb["lat_max"] - lat) / (bb["lat_max"] - bb["lat_min"]) * (h - 2 * pad)
    return x, y


def spain_depot_map_svg(depots: List[Dict[str, Any]], tasks: List[Dict[str, Any]],
                        assignments: List[Dict[str, Any]]) -> str:
    w, h = 720, 520
    poly = " ".join(f"{x:.1f},{y:.1f}" for (lat, lon) in _SPAIN_OUTLINE for x, y in [_proj(lat, lon, w, h)])
    # depots
    depot_pos = {}
    depot_pins = []
    for d in depots:
        loc = d.get("location") or {}
        x, y = _proj(loc.get("lat", 40), loc.get("lon", -3), w, h)
        depot_pos[d["id"]] = (x, y)
        depot_pins.append(
            f'<g><circle cx="{x:.1f}" cy="{y:.1f}" r="9" fill="#0ea5e9" stroke="#0c4a6e" stroke-width="2"/>'
            f'<text x="{x+12:.1f}" y="{y+5:.1f}" font-size="12" fill="#0f172a">{_e(d.get("name", d["id"]))}</text></g>'
        )
    # tasks
    task_pos = {}
    task_pins = []
    for t in tasks:
        sl = t.get("site_location") or {}
        if "lat" not in sl:
            continue
        x, y = _proj(sl["lat"], sl["lon"], w, h)
        task_pos[t["id"]] = (x, y)
        prio_color = {1: "#94a3b8", 2: "#94a3b8", 3: "#0ea5e9", 4: "#f59e0b", 5: "#dc2626"}.get(int(t.get("priority", 3)), "#0ea5e9")
        task_pins.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{prio_color}" opacity="0.85"/>'
            f'<text x="{x+6:.1f}" y="{y-6:.1f}" font-size="9" fill="#475569">{_e(t["id"])}</text>'
        )
    # arrows depot → task for each assignment
    arrows = []
    for a in assignments:
        d_xy = depot_pos.get(a["depot_id"])
        t_xy = task_pos.get(a["task_id"])
        if not (d_xy and t_xy):
            continue
        x1, y1 = d_xy
        x2, y2 = t_xy
        arrows.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#0ea5e9" stroke-width="1" opacity="0.45" stroke-dasharray="3,2"/>'
        )
    legend = (
        '<g transform="translate(20,20)">'
        '<rect width="170" height="80" rx="6" fill="white" opacity="0.85" stroke="#cbd5e1"/>'
        '<text x="10" y="18" font-size="12" font-weight="600" fill="#1e293b">Map legend</text>'
        '<circle cx="20" cy="36" r="6" fill="#0ea5e9" stroke="#0c4a6e"/>'
        '<text x="32" y="40" font-size="11" fill="#334155">Depot</text>'
        '<circle cx="20" cy="55" r="4" fill="#dc2626"/>'
        '<text x="32" y="59" font-size="11" fill="#334155">Critical task (P5)</text>'
        '<circle cx="20" cy="72" r="4" fill="#0ea5e9"/>'
        '<text x="32" y="76" font-size="11" fill="#334155">Routine task</text>'
        '</g>'
    )
    return (
        f'<svg viewBox="0 0 {w} {h}" width="100%" style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;">'
        f'<polygon points="{poly}" fill="#e0f2fe" stroke="#94a3b8" stroke-width="1.2"/>'
        + "".join(arrows) + "".join(task_pins) + "".join(depot_pins) + legend
        + '</svg>'
    )


# --- Cost waterfall ------------------------------------------------------
def cost_waterfall_svg(cost_breakdown: Dict[str, float]) -> str:
    parts = [
        ("Labor",       float(cost_breakdown.get("labor_cost_eur", 0))),
        ("Travel",      float(cost_breakdown.get("travel_cost_eur", 0))),
        ("SLA penalty", float(cost_breakdown.get("sla_penalty_eur", 0))),
        ("Unassigned",  float(cost_breakdown.get("unassigned_penalty_eur", 0))),
    ]
    total = sum(v for _, v in parts)
    w, h = 640, 280
    pad_l, pad_b = 80, 40
    bar_w = (w - pad_l - 40) / (len(parts) + 1)
    max_v = max([total] + [v for _, v in parts]) or 1
    cum = 0
    bars = []
    for i, (label, v) in enumerate(parts):
        x = pad_l + i * bar_w
        y_top = (h - pad_b) - (v / max_v) * (h - pad_b - 30)
        y_bot = (h - pad_b) - (cum / max_v) * (h - pad_b - 30)
        # the floating bar starts at cum
        bar_y = y_top - ((cum) / max_v) * 0  # convert
        rect_y = (h - pad_b) - ((cum + v) / max_v) * (h - pad_b - 30)
        rect_h = (v / max_v) * (h - pad_b - 30)
        col = "#0ea5e9" if v >= 0 else "#dc2626"
        bars.append(
            f'<rect x="{x:.1f}" y="{rect_y:.1f}" width="{bar_w-12:.1f}" height="{rect_h:.1f}" fill="{col}" opacity="0.85"/>'
            f'<text x="{x+(bar_w-12)/2:.1f}" y="{rect_y-4:.1f}" font-size="11" text-anchor="middle" fill="#0f172a">€{v:.0f}</text>'
            f'<text x="{x+(bar_w-12)/2:.1f}" y="{h-pad_b+18:.1f}" font-size="11" text-anchor="middle" fill="#334155">{_e(label)}</text>'
        )
        cum += v
    # Total bar
    x = pad_l + len(parts) * bar_w
    rect_h = (total / max_v) * (h - pad_b - 30)
    rect_y = (h - pad_b) - rect_h
    bars.append(
        f'<rect x="{x:.1f}" y="{rect_y:.1f}" width="{bar_w-12:.1f}" height="{rect_h:.1f}" fill="#0f172a" opacity="0.9"/>'
        f'<text x="{x+(bar_w-12)/2:.1f}" y="{rect_y-4:.1f}" font-size="12" font-weight="600" text-anchor="middle" fill="#0f172a">€{total:.0f}</text>'
        f'<text x="{x+(bar_w-12)/2:.1f}" y="{h-pad_b+18:.1f}" font-size="11" font-weight="600" text-anchor="middle" fill="#334155">TOTAL</text>'
    )
    return (
        f'<svg viewBox="0 0 {w} {h}" width="100%" style="background:#fff;border:1px solid #e2e8f0;border-radius:10px;">'
        f'<text x="20" y="22" font-size="13" font-weight="600" fill="#1e293b">Cost waterfall (€)</text>'
        + "".join(bars)
        + f'<line x1="{pad_l-10}" y1="{h-pad_b}" x2="{w-20}" y2="{h-pad_b}" stroke="#cbd5e1"/>'
        + '</svg>'
    )


# --- Skill coverage heatmap ----------------------------------------------
def skill_coverage_heatmap_svg(depots: List[Dict[str, Any]], technicians: List[Dict[str, Any]]) -> str:
    skills = sorted({s for r in technicians for s in (r.get("skills") or [])})
    matrix = {(d["id"], s): 0 for d in depots for s in skills}
    for r in technicians:
        for s in r.get("skills") or []:
            matrix[(r["depot_id"], s)] = matrix.get((r["depot_id"], s), 0) + 1
    cell_w, cell_h = 80, 36
    w = 140 + cell_w * len(skills)
    h = 60 + cell_h * len(depots)
    rows = []
    # header
    for j, s in enumerate(skills):
        rows.append(f'<text x="{140 + j*cell_w + cell_w/2:.1f}" y="36" font-size="11" text-anchor="middle" fill="#334155">{_e(s)}</text>')
    for i, d in enumerate(depots):
        rows.append(f'<text x="130" y="{60 + i*cell_h + cell_h/2 + 4:.1f}" font-size="12" text-anchor="end" fill="#1e293b">{_e(d.get("name", d["id"]))}</text>')
        for j, s in enumerate(skills):
            v = matrix.get((d["id"], s), 0)
            col = "#e2e8f0" if v == 0 else "#bae6fd" if v == 1 else "#7dd3fc" if v == 2 else "#0ea5e9"
            rows.append(
                f'<rect x="{140+j*cell_w:.1f}" y="{60+i*cell_h:.1f}" width="{cell_w-4:.1f}" height="{cell_h-4:.1f}" rx="4" fill="{col}"/>'
                f'<text x="{140+j*cell_w+cell_w/2:.1f}" y="{60+i*cell_h+cell_h/2+4:.1f}" font-size="12" text-anchor="middle" fill="#0f172a">{v}</text>'
            )
    return (
        f'<svg viewBox="0 0 {w} {h}" width="100%" style="background:#fff;border:1px solid #e2e8f0;border-radius:10px;">'
        f'<text x="20" y="22" font-size="13" font-weight="600" fill="#1e293b">Skill coverage per depot (technician headcount)</text>'
        + "".join(rows)
        + '</svg>'
    )


# --- Wrappers so platform webview previews the artefacts -------------------
def wrap_svg_in_html(title: str, svg: str, subtitle: str = "") -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{_e(title)}</title>
<style>body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:18px;background:#f8fafc;color:#0f172a;}}
h1{{font-size:18px;margin:0 0 4px;}} p{{color:#64748b;font-size:12px;margin:0 0 12px;}}
.frame{{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:12px;}}</style></head>
<body><h1>{_e(title)}</h1>{('<p>' + _e(subtitle) + '</p>') if subtitle else ''}<div class="frame">{svg}</div></body></html>"""


def markdown_to_html(title: str, md: str) -> str:
    """Tiny markdown → HTML converter for the presentation pack file.

    Supports: # / ## / ### headings, **bold**, *italic*, paragraphs, tables (pipe-style),
    fenced code blocks, simple lists. Good enough for the artefact we generate."""
    lines = md.splitlines()
    out = []
    in_table = False
    table_buf: list = []
    in_list = False

    def flush_table():
        nonlocal table_buf
        if not table_buf:
            return
        rows = [r for r in table_buf if r.strip().startswith("|") and not set(r.strip().strip("|").strip()) <= set("-: ")]
        if not rows:
            table_buf = []
            return
        head = [c.strip() for c in rows[0].strip().strip("|").split("|")]
        body = []
        for r in rows[1:]:
            body.append([c.strip() for c in r.strip().strip("|").split("|")])
        out.append("<table><thead><tr>" + "".join(f"<th>{_e(c)}</th>" for c in head) + "</tr></thead><tbody>"
                   + "".join("<tr>" + "".join(f"<td>{_e(c)}</td>" for c in r) + "</tr>" for r in body)
                   + "</tbody></table>")
        table_buf = []

    def fmt_inline(s: str) -> str:
        s = _e(s)
        # bold + italic (very simple)
        import re as _re
        s = _re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = _re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*", r"<em>\1</em>", s)
        s = _re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        return s

    for raw in lines:
        line = raw.rstrip()
        if line.startswith("|"):
            in_table = True
            table_buf.append(line)
            continue
        if in_table:
            in_table = False
            flush_table()
        if line.startswith("### "):
            out.append(f"<h3>{fmt_inline(line[4:])}</h3>")
        elif line.startswith("## "):
            out.append(f"<h2>{fmt_inline(line[3:])}</h2>")
        elif line.startswith("# "):
            out.append(f"<h1>{fmt_inline(line[2:])}</h1>")
        elif line.startswith("- "):
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append(f"<li>{fmt_inline(line[2:])}</li>")
        elif line.strip() == "":
            if in_list:
                out.append("</ul>"); in_list = False
            out.append("")
        else:
            if in_list:
                out.append("</ul>"); in_list = False
            out.append(f"<p>{fmt_inline(line)}</p>")
    if in_list:
        out.append("</ul>")
    if in_table:
        flush_table()

    body = "\n".join(out)
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{_e(title)}</title>
<style>body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:24px;background:#f8fafc;color:#0f172a;max-width:920px;}}
h1{{font-size:22px;margin:0 0 6px;}} h2{{font-size:16px;margin:18px 0 6px;}} h3{{font-size:14px;margin:14px 0 4px;color:#334155;}}
p{{margin:6px 0;}} table{{border-collapse:collapse;width:100%;background:#fff;margin:8px 0;}}
th,td{{padding:6px 10px;border-bottom:1px solid #e2e8f0;text-align:left;font-size:13px;}}
th{{background:#f1f5f9;color:#475569;font-weight:600;}}
ul{{padding-left:22px;}} li{{margin:2px 0;}} code{{background:#f1f5f9;padding:1px 4px;border-radius:3px;font-size:12px;}}</style>
</head><body>{body}</body></html>"""

"""
QCentroid solver — Classical MILP for resource allocation across maintenance depots.

Use case: resource-allocation-for-maintenance-depots-using-variational-quantum-algorithms-teksem
Problem ID: 792 (dev)

Approach
--------
Mixed-Integer Linear Program solved with PuLP (CBC backend, embedded in pulp wheel).
Decision variables:
    x[t,r] in {0,1}  — 1 if technician r is assigned to task t
Constraints:
    - Each task is assigned to at most 1 technician
    - Technician's required-skill must match the task's required_skill
    - Technician must hold all required certifications
    - Sum of task durations per technician <= available_hours + max_overtime
    - Spare-part demand cannot exceed total network stock for any part
Objective (minimise, EUR):
    labor_cost + travel_cost + sla_penalty + unassigned_penalty
where:
    labor      = sum_{t,r} duration[t] * hourly_rate[r] * x[t,r]
    travel     = sum_{t,r} 2 * dist(depot_of(r), site_of(t)) * cost_per_km * x[t,r]
    sla_pen    = sum_{t,r} max(0, eta[t,r] - deadline[t]) * sla_penalty[t] * x[t,r]
    unassigned = sum_t (1 - sum_r x[t,r]) * unassigned_penalty
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Dict, List

logger = logging.getLogger("qcentroid-user-log")


# ----------------------------- helpers --------------------------------------- #

def _haversine_km(a: Dict[str, float], b: Dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    R = 6371.0
    lat1, lon1 = math.radians(a.get("lat", 0.0)), math.radians(a.get("lon", 0.0))
    lat2, lon2 = math.radians(b.get("lat", 0.0)), math.radians(b.get("lon", 0.0))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def _depot_index(depots: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {d["id"]: d for d in depots}


def _stock_index(spares: List[Dict[str, Any]]) -> Dict[str, int]:
    """Total network stock per part_id."""
    out: Dict[str, int] = {}
    for s in spares:
        out[s["part_id"]] = out.get(s["part_id"], 0) + int(s.get("stock", 0))
    return out


# ----------------------------- main entry ------------------------------------ #

def run(input_data: dict, solver_params: dict, extra_arguments: dict) -> dict:
    """
    Parameters
    ----------
    input_data : dict
        Conforms to the use case input schema (depots, technicians, tasks,
        spare_parts, objective_weights, ...).
    solver_params : dict
        Optional. Supported keys:
          - "time_limit_s": int (CBC time limit, default 30)
          - "msg": int (1 to enable solver chatter, default 0)
    extra_arguments : dict
        Reserved.

    Returns
    -------
    dict — full output conforming to the use case output schema, including
           a `benchmark` key so the platform charts render.
    """
    start = time.time()
    logger.info("=== Classical MILP solver start ===")

    # ---- Imports kept inside `run` so the platform's static analyzer is happy
    import pulp  # noqa: WPS433

    # ---- Parse + index --------------------------------------------------------
    depots = input_data.get("depots", [])
    techs = input_data.get("technicians", [])
    tasks = input_data.get("tasks", [])
    spares = input_data.get("spare_parts", [])
    travel_cost_km = float(input_data.get("travel_cost_per_km_eur", 0.6))
    travel_speed = float(input_data.get("travel_speed_kmh", 50)) or 50.0
    weights = input_data.get("objective_weights", {}) or {}
    w_labor = float(weights.get("labor", 1.0))
    w_travel = float(weights.get("travel", 1.0))
    w_sla = float(weights.get("sla_penalty", 1.0))
    w_un = float(weights.get("unassigned_penalty", 1500.0))

    depot_by_id = _depot_index(depots)
    stock_total = _stock_index(spares)

    n_t, n_r = len(tasks), len(techs)
    logger.info(f"Problem size: tasks={n_t}, technicians={n_r}, depots={len(depots)}")

    # ---- Compatibility & cost coefficients -----------------------------------
    coef: Dict[tuple, Dict[str, float]] = {}
    compat: List[tuple] = []
    for t in tasks:
        t_id = t["id"]
        t_skill = t["required_skill"]
        t_certs = set(t.get("required_certifications", []) or [])
        t_dur = float(t["duration_hours"])
        site_loc = t.get("site_location", {})
        t_deadline = float(t.get("deadline_hours", 24))
        t_sla = float(t.get("sla_penalty_eur_per_hour", 50))
        for r in techs:
            r_id = r["id"]
            r_skills = set(r.get("skills", []))
            r_certs = set(r.get("certifications", []))
            if t_skill not in r_skills:
                continue
            if t_certs and not t_certs.issubset(r_certs):
                continue
            depot = depot_by_id.get(r["depot_id"], {})
            depot_loc = depot.get("location", {})
            dist_km = _haversine_km(depot_loc, site_loc)
            travel_h = dist_km / travel_speed
            eta_finish = travel_h + t_dur
            late_h = max(0.0, eta_finish - t_deadline)
            labor = t_dur * float(r.get("hourly_rate_eur", 35.0))
            travel = 2.0 * dist_km * travel_cost_km
            sla = late_h * t_sla
            cost = w_labor * labor + w_travel * travel + w_sla * sla
            coef[(t_id, r_id)] = {
                "cost": cost, "labor": labor, "travel": travel,
                "sla_pen": sla, "dist_km": dist_km, "travel_h": travel_h,
                "eta_finish": eta_finish, "late_h": late_h,
            }
            compat.append((t_id, r_id))

    logger.info(f"Compatible (task, tech) pairs: {len(compat)}")

    # ---- Build MILP -----------------------------------------------------------
    prob = pulp.LpProblem("ResourceAllocationDepots", pulp.LpMinimize)
    x = {pair: pulp.LpVariable(f"x_{pair[0]}_{pair[1]}", lowBound=0, upBound=1, cat=pulp.LpBinary)
         for pair in compat}
    u = {t["id"]: pulp.LpVariable(f"u_{t['id']}", lowBound=0, upBound=1, cat=pulp.LpBinary)
         for t in tasks}  # 1 if task is unassigned

    # objective
    prob += (
        pulp.lpSum(coef[p]["cost"] * x[p] for p in compat)
        + w_un * pulp.lpSum(u[t["id"]] for t in tasks)
    )

    # each task: assigned + unassigned = 1
    for t in tasks:
        t_id = t["id"]
        relevant = [x[p] for p in compat if p[0] == t_id]
        prob += pulp.lpSum(relevant) + u[t_id] == 1, f"assign_{t_id}"

    # technician capacity
    tech_dur = {r["id"]: float(r.get("available_hours", 8.0))
                + float(depot_by_id.get(r["depot_id"], {}).get("max_overtime_hours", 2.0))
                for r in techs}
    for r in techs:
        r_id = r["id"]
        terms = [float(next(t for t in tasks if t["id"] == p[0])["duration_hours"]) * x[p]
                 for p in compat if p[1] == r_id]
        if terms:
            prob += pulp.lpSum(terms) <= tech_dur[r_id], f"cap_{r_id}"

    # spare-part network stock
    part_demand: Dict[str, list] = {}
    for t in tasks:
        for need in t.get("required_parts", []) or []:
            part_demand.setdefault(need["part_id"], []).append((t["id"], int(need["qty"])))
    for part_id, items in part_demand.items():
        cap = stock_total.get(part_id, 0)
        prob += (
            pulp.lpSum(qty * (1 - u[t_id]) for t_id, qty in items) <= cap,
            f"stock_{part_id}",
        )

    # ---- Solve ----------------------------------------------------------------
    time_limit = int(solver_params.get("time_limit_s", 30))
    msg = int(solver_params.get("msg", 0))
    solver = pulp.PULP_CBC_CMD(msg=msg, timeLimit=time_limit)
    pulp_status = prob.solve(solver)
    status_map = {1: "optimal", 0: "feasible", -1: "infeasible", -2: "unbounded", -3: "undefined"}
    status = status_map.get(pulp_status, "feasible")
    if status == "undefined":
        status = "timeout"

    # ---- Build assignments + breakdown ---------------------------------------
    assignments: List[Dict[str, Any]] = []
    labor_total = travel_total = sla_total = 0.0
    on_time = 0
    used_hours: Dict[str, float] = {}
    unassigned: List[str] = []
    parts_used_per_depot: Dict[str, Dict[str, int]] = {}
    stock_remaining = {(s["part_id"], s["depot_id"]): int(s.get("stock", 0)) for s in spares}

    for t in tasks:
        t_id = t["id"]
        if pulp.value(u[t_id]) > 0.5:
            unassigned.append(t_id)
            continue
        chosen = None
        for p in compat:
            if p[0] == t_id and pulp.value(x[p]) > 0.5:
                chosen = p
                break
        if chosen is None:
            unassigned.append(t_id)
            continue
        _, r_id = chosen
        c = coef[chosen]
        r = next(rr for rr in techs if rr["id"] == r_id)
        depot_id = r["depot_id"]
        start_h = used_hours.get(r_id, 0.0)
        used_hours[r_id] = start_h + float(t["duration_hours"])
        # parts allocation: greedy from technician's depot first
        parts_alloc = []
        for need in t.get("required_parts", []) or []:
            qty_needed = int(need["qty"])
            for d_id in [depot_id] + [d["id"] for d in depots if d["id"] != depot_id]:
                if qty_needed <= 0:
                    break
                k = (need["part_id"], d_id)
                avail = stock_remaining.get(k, 0)
                take = min(avail, qty_needed)
                if take > 0:
                    stock_remaining[k] = avail - take
                    qty_needed -= take
                    parts_alloc.append({"part_id": need["part_id"], "depot_id": d_id, "qty": take})
                    parts_used_per_depot.setdefault(d_id, {}).setdefault(need["part_id"], 0)
                    parts_used_per_depot[d_id][need["part_id"]] += take
        labor_total += c["labor"]
        travel_total += c["travel"]
        sla_total += c["sla_pen"]
        if c["late_h"] <= 0:
            on_time += 1
        assignments.append({
            "task_id": t_id,
            "technician_id": r_id,
            "depot_id": depot_id,
            "start_hour": round(start_h, 3),
            "end_hour": round(start_h + float(t["duration_hours"]), 3),
            "travel_km": round(c["dist_km"], 3),
            "parts_allocated": parts_alloc,
        })

    unassigned_pen = w_un * len(unassigned)
    objective = w_labor * labor_total + w_travel * travel_total + w_sla * sla_total + unassigned_pen
    util = (sum(used_hours.values()) / max(1.0, sum(tech_dur.values()))) if tech_dur else 0.0
    elapsed = round(time.time() - start, 4)

    logger.info(
        f"Solve done in {elapsed}s — status={status}, objective={objective:.2f} EUR, "
        f"assigned={len(assignments)}/{len(tasks)}, on_time={on_time}/{len(tasks)}"
    )

    cost_breakdown = {
        "labor_cost_eur": round(labor_total, 4),
        "travel_cost_eur": round(travel_total, 4),
        "sla_penalty_eur": round(sla_total, 4),
        "unassigned_penalty_eur": round(unassigned_pen, 4),
        "total_cost_eur": round(float(objective), 4),
    }
    kpis = {
        "tasks_total": len(tasks),
        "tasks_assigned": len(assignments),
        "sla_on_time_rate": round(on_time / max(1, len(tasks)), 4),
        "technician_utilization": round(util, 4),
        "stockouts": 0,
    }

    # Talgo-friendly additional output (per-depot KPIs, Gantt, SLA risk, BOM)
    try:
        from talgo_outputs import build_additional  # noqa: WPS433
        additional_output = build_additional(
            algorithm="MILP_CBC",
            objective_value=round(float(objective), 4),
            solution_status=status,
            cost_breakdown=cost_breakdown,
            kpis=kpis,
            assignments=assignments,
            unassigned_tasks=unassigned,
            depots=depots, technicians=techs, tasks=tasks, spare_parts=spares,
            extras={"travel_cost_per_km_eur": travel_cost_km},
        )
    except Exception as e:  # pragma: no cover
        logger.warning(f"build_additional failed: {e}")
        additional_output = {"error": str(e)}

    headline = additional_output.get("headline_numerics", {}) if isinstance(additional_output, dict) else {}

    # Emit additional-output FILES — every artefact as .html or .json so the
    # platform's webview previews them inline (preview only renders those types).
    try:
        from talgo_dashboard import render_dashboard  # noqa: WPS433
        from talgo_files import emit_files  # noqa: WPS433
        from talgo_visuals import (
            spain_depot_map_svg, cost_waterfall_svg, skill_coverage_heatmap_svg,
            wrap_svg_in_html, markdown_to_html,
        )  # noqa: WPS433
        files = [
            {"name": "00_executive_summary.json",
             "content": additional_output.get("executive_summary", {})},
            {"name": "01_talgo_dashboard.html",
             "content": render_dashboard("MILP_CBC", additional_output, float(objective))},
            {"name": "02_presentation_pack.html",
             "content": markdown_to_html("Presentation pack — MILP_CBC",
                                         additional_output.get("presentation_pack", ""))},
            {"name": "03_shift_handover.json",
             "content": additional_output.get("shift_handover", {})},
            {"name": "04_compliance.json",
             "content": additional_output.get("compliance", {})},
            {"name": "06_spain_depot_map.html",
             "content": wrap_svg_in_html("Spain depot map — assignments",
                                         spain_depot_map_svg(depots, tasks, assignments),
                                         "Blue circles = depots, dots = task sites (red = critical), dashed lines = depot→task assignments.")},
            {"name": "07_cost_waterfall.html",
             "content": wrap_svg_in_html("Cost waterfall — €",
                                         cost_waterfall_svg(cost_breakdown),
                                         "Labor + Travel + SLA penalties + Unassigned-task penalty = TOTAL.")},
            {"name": "08_skill_coverage_heatmap.html",
             "content": wrap_svg_in_html("Skill coverage per depot",
                                         skill_coverage_heatmap_svg(depots, techs),
                                         "Cells show technician headcount per (depot, skill).")},
        ]
        additional_output["uploaded_files"] = emit_files(files)
    except Exception as _e:  # pragma: no cover
        logger.warning(f"emit_files failed: {_e}")

    return {
        "objective_value": round(float(objective), 4),
        "sla_on_time_rate": kpis["sla_on_time_rate"],
        "technician_utilization": kpis["technician_utilization"],
        "total_travel_km": float(headline.get("total_travel_km", 0.0)),
        "replenishment_alerts_count": int(headline.get("replenishment_alerts_count", 0)),
        "solution_status": status,
        "assignments": assignments,
        "unassigned_tasks": unassigned,
        "cost_breakdown": cost_breakdown,
        "kpis": kpis,
        "computation_metrics": {
            "wall_time_s": elapsed,
            "algorithm": "MILP_CBC",
            "iterations": int(getattr(prob, "numIterations", lambda: 0)() if callable(getattr(prob, "numIterations", None)) else 0),
            "num_variables": len(x) + len(u),
            "num_qubits": 0,
            "best_iter": 0,
            "energy_curve": [],
        },
        "additional_output": additional_output,
        "benchmark": {
            "execution_cost": {"value": 1.0, "unit": "credits"},
            "time_elapsed": f"{elapsed}s",
            "energy_consumption": round(0.05 * elapsed, 6),
        },
    }

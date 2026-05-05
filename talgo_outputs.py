"""
Shared post-solve helpers that build the `additional_output` block Talgo
planners want to see in the QCentroid additional-output viewer.

Both the classical and the quantum-inspired solver call `build_additional`
with the same arguments so the viewer is identical across solvers.
"""
from __future__ import annotations
from typing import Any, Dict, List


def _amber_red_green(slack_h: float) -> str:
    if slack_h < 0:
        return "red"
    if slack_h < 1.0:
        return "amber"
    return "green"


def build_additional(
    *,
    algorithm: str,
    objective_value: float,
    solution_status: str,
    cost_breakdown: Dict[str, float],
    kpis: Dict[str, Any],
    assignments: List[Dict[str, Any]],
    unassigned_tasks: List[str],
    depots: List[Dict[str, Any]],
    technicians: List[Dict[str, Any]],
    tasks: List[Dict[str, Any]],
    spare_parts: List[Dict[str, Any]],
    extras: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    depot_by_id = {d["id"]: d for d in depots}
    tech_by_id = {t["id"]: t for t in technicians}
    task_by_id = {t["id"]: t for t in tasks}

    # ---- Per-depot KPIs ------------------------------------------------------
    per_depot: Dict[str, Dict[str, Any]] = {
        d["id"]: {
            "depot_name": d.get("name", d["id"]),
            "tech_count": 0,
            "tech_hours_used": 0.0,
            "tech_hours_capacity": 0.0,
            "tasks_assigned": 0,
            "labor_eur": 0.0,
            "travel_eur": 0.0,
            "parts_used": {},
        }
        for d in depots
    }
    for r in technicians:
        d_id = r["depot_id"]
        cap = float(r.get("available_hours", 8.0)) + float(
            depot_by_id.get(d_id, {}).get("max_overtime_hours", 2.0)
        )
        per_depot[d_id]["tech_count"] += 1
        per_depot[d_id]["tech_hours_capacity"] += cap

    travel_cost_km = 0.6  # in case caller doesn't pass it through extras
    if extras and "travel_cost_per_km_eur" in extras:
        travel_cost_km = float(extras["travel_cost_per_km_eur"])

    for a in assignments:
        d_id = a["depot_id"]
        dur = float(a["end_hour"] - a["start_hour"])
        r = tech_by_id.get(a["technician_id"], {})
        per_depot[d_id]["tasks_assigned"] += 1
        per_depot[d_id]["tech_hours_used"] += dur
        per_depot[d_id]["labor_eur"] += dur * float(r.get("hourly_rate_eur", 35.0))
        per_depot[d_id]["travel_eur"] += 2.0 * float(a.get("travel_km", 0.0)) * travel_cost_km
        for p in a.get("parts_allocated", []) or []:
            d2 = p["depot_id"]
            pid = p["part_id"]
            per_depot[d2]["parts_used"][pid] = per_depot[d2]["parts_used"].get(pid, 0) + int(p["qty"])

    for d_id, row in per_depot.items():
        cap = row["tech_hours_capacity"] or 1.0
        row["utilisation_pct"] = round(100.0 * row["tech_hours_used"] / cap, 2)
        row["labor_eur"] = round(row["labor_eur"], 2)
        row["travel_eur"] = round(row["travel_eur"], 2)
        row["tech_hours_used"] = round(row["tech_hours_used"], 2)

    # ---- SLA risk per task ---------------------------------------------------
    sla_risk: List[Dict[str, Any]] = []
    on_time = 0
    for a in assignments:
        t = task_by_id.get(a["task_id"], {})
        deadline = float(t.get("deadline_hours", 24))
        eta = float(a["end_hour"])  # technician-relative; first-task ETA is its end_hour
        slack = round(deadline - eta, 2)
        if slack >= 0:
            on_time += 1
        sla_risk.append({
            "task_id": a["task_id"],
            "site_id": t.get("site_id"),
            "deadline_hours": deadline,
            "eta_hours": round(eta, 2),
            "slack_hours": slack,
            "status": _amber_red_green(slack),
            "priority": int(t.get("priority", 3)),
        })
    sla_risk.sort(key=lambda r: r["slack_hours"])

    # ---- Gantt rows (one per assignment) ------------------------------------
    gantt = [
        {
            "technician_id": a["technician_id"],
            "depot_id": a["depot_id"],
            "task_id": a["task_id"],
            "site_id": task_by_id.get(a["task_id"], {}).get("site_id"),
            "start_hour": a["start_hour"],
            "end_hour": a["end_hour"],
            "duration_hours": round(a["end_hour"] - a["start_hour"], 2),
            "priority": int(task_by_id.get(a["task_id"], {}).get("priority", 3)),
        }
        for a in assignments
    ]
    gantt.sort(key=lambda g: (g["technician_id"], g["start_hour"]))

    # ---- Bill of parts ------------------------------------------------------
    parts_bom: List[Dict[str, Any]] = []
    for a in assignments:
        for p in a.get("parts_allocated", []) or []:
            parts_bom.append({
                "task_id": a["task_id"],
                "part_id": p["part_id"],
                "qty": int(p["qty"]),
                "supplied_by_depot": p["depot_id"],
                "executed_at_depot": a["depot_id"],
            })

    # ---- Replenishment alerts -----------------------------------------------
    stock_remaining = {(s["part_id"], s["depot_id"]): int(s.get("stock", 0)) for s in spare_parts}
    used = {(p["part_id"], p["supplied_by_depot"]): 0 for p in parts_bom}
    for p in parts_bom:
        used[(p["part_id"], p["supplied_by_depot"])] += p["qty"]
    replen_alerts: List[Dict[str, Any]] = []
    for (pid, did), u in used.items():
        before = stock_remaining.get((pid, did), 0)
        after = before - u
        if after <= 1:  # low-stock threshold
            replen_alerts.append({
                "depot_id": did,
                "part_id": pid,
                "stock_before": before,
                "stock_after": max(0, after),
                "level": "critical" if after <= 0 else "low",
            })

    # ---- Human-readable summary --------------------------------------------
    line1 = (
        f"{algorithm}: {len(assignments)}/{len(tasks)} tasks assigned, "
        f"{on_time}/{len(tasks)} on time, total cost {objective_value:.2f} EUR "
        f"(labor {cost_breakdown.get('labor_cost_eur',0):.0f} EUR, "
        f"travel {cost_breakdown.get('travel_cost_eur',0):.0f} EUR, "
        f"SLA penalty {cost_breakdown.get('sla_penalty_eur',0):.0f} EUR)."
    )
    busiest = max(per_depot.values(), key=lambda r: r["utilisation_pct"], default=None)
    line2 = (
        f"Busiest depot: {busiest['depot_name']} at {busiest['utilisation_pct']}% utilisation."
        if busiest else ""
    )
    line3 = (
        f"{len(replen_alerts)} part(s) need replenishment after this plan."
        if replen_alerts else "Spare-parts inventory is healthy after this plan."
    )
    summary = " ".join(s for s in [line1, line2, line3] if s)

    out = {
        "summary": summary,
        "cost_components": {
            "labor_eur": float(cost_breakdown.get("labor_cost_eur", 0)),
            "travel_eur": float(cost_breakdown.get("travel_cost_eur", 0)),
            "sla_penalty_eur": float(cost_breakdown.get("sla_penalty_eur", 0)),
            "unassigned_penalty_eur": float(cost_breakdown.get("unassigned_penalty_eur", 0)),
        },
        "per_depot_kpis": per_depot,
        "gantt": gantt,
        "sla_risk": sla_risk,
        "parts_bom": parts_bom,
        "replenishment_alerts": replen_alerts,
        "kpis": kpis,
        "solution_status": solution_status,
    }
    if extras:
        out["solver_extras"] = extras
    return out

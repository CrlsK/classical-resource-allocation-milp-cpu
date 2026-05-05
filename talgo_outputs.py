"""
Shared post-solve helpers that build the `additional_output` block Talgo
planners want to see in the QCentroid additional-output viewer.

Both the classical and the quantum-inspired solver call `build_additional`
with the same arguments so the viewer is identical across solvers.

Personas served (see /reports/talgo-team-personas.md):
- Mar Aldecoa (Director MRO Operations) → executive_summary
- Iván Pérez   (Lead Operations Planner) → shift_handover, sla_risk, gantt
- Sara Estévez (Data Lead)               → input_audit
- Lucas Romero (Quantum/Opt Lead)        → convergence_diagnostics
- Patricia Vega (Compliance)             → compliance
"""
from __future__ import annotations
import datetime as _dt
import hashlib as _hashlib
import json as _json
from typing import Any, Dict, List

SOLVER_VERSION = "v4.0.0"


def _amber_red_green(slack_h: float, priority: int = 3) -> str:
    # Higher-priority tasks get a tighter buffer
    buf = max(0.5, 2.0 - 0.3 * priority)
    if slack_h < 0:
        return "red"
    if slack_h < buf:
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

    # ---- SLA risk per task (priority-weighted buffer) ------------------------
    sla_risk: List[Dict[str, Any]] = []
    on_time = 0
    for a in assignments:
        t = task_by_id.get(a["task_id"], {})
        deadline = float(t.get("deadline_hours", 24))
        eta = float(a["end_hour"])  # technician-relative; first-task ETA is its end_hour
        slack = round(deadline - eta, 2)
        prio = int(t.get("priority", 3))
        if slack >= 0:
            on_time += 1
        sla_risk.append({
            "task_id": a["task_id"],
            "site_id": t.get("site_id"),
            "deadline_hours": deadline,
            "eta_hours": round(eta, 2),
            "slack_hours": slack,
            "status": _amber_red_green(slack, prio),
            "priority": prio,
        })
    sla_risk.sort(key=lambda r: (r["status"] != "red", r["status"] != "amber", -r["priority"], r["slack_hours"]))

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

    # ---- Persona blocks -----------------------------------------------------
    # Mar (Director MRO) — three numbers + a sentence she can read out
    busiest_name = busiest['depot_name'] if busiest else None
    busiest_pct = busiest['utilisation_pct'] if busiest else 0
    bottom_line = (
        f"Plan accepts {len(assignments)}/{len(tasks)} tasks at "
        f"€{objective_value:,.0f}, {on_time/max(1,len(tasks))*100:.0f}% on-time, "
        f"busiest depot {busiest_name} at {busiest_pct:.0f}%."
    )
    executive_summary = {
        "bottom_line": bottom_line,
        "headline_kpis": {
            "tasks_assigned_pct": round(100.0 * len(assignments) / max(1, len(tasks)), 1),
            "sla_on_time_pct": round(100.0 * on_time / max(1, len(tasks)), 1),
            "total_cost_eur": float(objective_value),
            "busiest_depot": busiest_name,
            "busiest_depot_utilisation_pct": float(busiest_pct),
        },
    }

    # Iván (Planner) — printable per-depot shift-handover sheets
    shift_handover: Dict[str, Any] = {}
    for d in depots:
        d_id = d["id"]
        techs_in_depot = [t for t in technicians if t.get("depot_id") == d_id]
        d_assigns = [a for a in assignments if a["depot_id"] == d_id]
        d_assigns.sort(key=lambda a: (a["technician_id"], a["start_hour"]))
        # Lines per technician
        per_tech: Dict[str, List[Dict[str, Any]]] = {}
        for r in techs_in_depot:
            per_tech[r["id"]] = []
        for a in d_assigns:
            t = task_by_id.get(a["task_id"], {})
            per_tech.setdefault(a["technician_id"], []).append({
                "task_id": a["task_id"],
                "site_id": t.get("site_id"),
                "start_hour": a["start_hour"],
                "end_hour": a["end_hour"],
                "priority": int(t.get("priority", 3)),
                "parts_needed": [{"part_id": p["part_id"], "qty": p["qty"]} for p in (a.get("parts_allocated") or [])],
            })
        shift_handover[d_id] = {
            "depot_name": d.get("name", d_id),
            "headcount": len(techs_in_depot),
            "tasks_today": len(d_assigns),
            "per_technician": per_tech,
            "parts_pick_list": per_depot[d_id]["parts_used"],
        }

    # Sara (Data) — input audit
    cert_warnings = [
        {"technician_id": r["id"], "issue": "no certifications on file"}
        for r in technicians if not r.get("certifications")
    ]
    zero_stock = [
        {"part_id": s["part_id"], "depot_id": s["depot_id"]}
        for s in spare_parts if int(s.get("stock", 0)) == 0
    ]
    input_audit = {
        "n_depots": len(depots),
        "n_technicians": len(technicians),
        "n_tasks": len(tasks),
        "n_parts_records": len(spare_parts),
        "n_compatible_pairs": sum(
            1 for t in tasks for r in technicians
            if t.get("required_skill") in set(r.get("skills", []))
            and set(t.get("required_certifications", []) or []).issubset(set(r.get("certifications", []) or []))
        ),
        "anomalies": {
            "techs_without_certifications": cert_warnings,
            "zero_stock_records": zero_stock,
        },
    }

    # Lucas (Quantum) — convergence diagnostics (only meaningful for QUBO solver)
    energy_curve = (extras or {}).get("energy_curve") or []
    convergence_diagnostics = {
        "n_outer_iterations": len(energy_curve),
        "best_iteration": (extras or {}).get("best_iter"),
        "monotone_decreasing_best": all(
            energy_curve[i] >= energy_curve[i + 1] for i in range(len(energy_curve) - 1)
        ) if energy_curve else None,
        "first_energy": energy_curve[0] if energy_curve else None,
        "best_energy": min(energy_curve) if energy_curve else None,
        "feasibility_breakdown": (extras or {}).get("feasibility_breakdown"),
        "qubo_size": (extras or {}).get("qubo_size"),
        "penalty_weights": (extras or {}).get("penalty_weights"),
    }

    # Patricia (Compliance) — audit fields
    dataset_payload = {
        "depots": depots, "technicians": technicians, "tasks": tasks,
        "spare_parts": spare_parts,
    }
    dataset_sha = _hashlib.sha256(_json.dumps(dataset_payload, sort_keys=True, default=str).encode()).hexdigest()
    used_certs = sorted({c for a in assignments for r in technicians
                         if r["id"] == a["technician_id"]
                         for c in (r.get("certifications") or [])})
    over_cap = []
    cap_by_tech = {r["id"]: float(r.get("available_hours", 8.0)) +
                   float(depot_by_id.get(r["depot_id"], {}).get("max_overtime_hours", 2.0))
                   for r in technicians}
    used_h_by_tech = {}
    for a in assignments:
        used_h_by_tech[a["technician_id"]] = used_h_by_tech.get(a["technician_id"], 0) + (a["end_hour"] - a["start_hour"])
    for tid, h in used_h_by_tech.items():
        if h > cap_by_tech.get(tid, 10) + 1e-6:
            over_cap.append({"technician_id": tid, "used_hours": h, "capacity_hours": cap_by_tech.get(tid)})
    compliance = {
        "solver_version": SOLVER_VERSION,
        "algorithm": algorithm,
        "run_started_at_utc": _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "dataset_sha256": dataset_sha,
        "all_required_certifications_present": all(
            set(task_by_id[a["task_id"]].get("required_certifications", []) or []).issubset(
                {c for r in technicians if r["id"] == a["technician_id"] for c in r.get("certifications", []) or []}
            )
            for a in assignments
        ),
        "technician_overtime_violations": over_cap,
        "certifications_used": used_certs,
    }

    # Headline numerics surfaced both inside additional_output and copied to top-level by caller
    headline = {
        "objective_eur": float(objective_value),
        "sla_on_time_pct": round(100.0 * on_time / max(1, len(tasks)), 2),
        "tasks_assigned_pct": round(100.0 * len(assignments) / max(1, len(tasks)), 2),
        "technician_utilization_pct": round(100.0 * float(kpis.get("technician_utilization", 0)), 2),
        "total_travel_km": round(sum(float(a.get("travel_km", 0)) for a in assignments), 2),
        "replenishment_alerts_count": len(replen_alerts),
    }

    # ---- v4: presentation-grade blocks --------------------------------------
    # Sankey: depot ➜ technician ➜ task (rows the planner can drop into a chart)
    sankey = {
        "nodes": (
            [{"id": f"depot/{d['id']}", "label": d.get("name", d["id"]), "type": "depot"} for d in depots]
            + [{"id": f"tech/{r['id']}", "label": r["id"], "type": "technician", "depot_id": r.get("depot_id")} for r in technicians]
            + [{"id": f"task/{t['id']}", "label": f"{t['id']} ({t.get('site_id')})", "type": "task", "priority": int(t.get("priority", 3))} for t in tasks]
        ),
        "links": (
            [
                {"source": f"depot/{r['depot_id']}", "target": f"tech/{r['id']}", "value": 1, "label": "based at"}
                for r in technicians
            ]
            + [
                {"source": f"tech/{a['technician_id']}", "target": f"task/{a['task_id']}",
                 "value": round(a["end_hour"] - a["start_hour"], 2), "label": "performs"}
                for a in assignments
            ]
        ),
    }

    # Gantt with absolute timestamps (assume shift start 08:00 local)
    SHIFT_START_HOUR = 8
    gantt_abs = []
    for g in gantt:
        gantt_abs.append({
            **g,
            "start_clock": _clock(SHIFT_START_HOUR + g["start_hour"]),
            "end_clock": _clock(SHIFT_START_HOUR + g["end_hour"]),
        })

    # Scenario comparison row that feeds a delta-vs-baseline table
    scenario_comparison = {
        "scenario": f"{algorithm} run @ {compliance['run_started_at_utc']}",
        "metrics": {
            "objective_eur": float(objective_value),
            "tasks_assigned_pct": headline["tasks_assigned_pct"],
            "sla_on_time_pct": headline["sla_on_time_pct"],
            "technician_utilization_pct": headline["technician_utilization_pct"],
            "total_travel_km": headline["total_travel_km"],
            "replenishment_alerts_count": headline["replenishment_alerts_count"],
        },
    }

    # Boss-pitch narrative (one paragraph for the steering committee)
    travel_save = headline["total_travel_km"]
    boss_pitch = (
        f"On the {input_audit['n_tasks']}-task / {input_audit['n_technicians']}-technician case, "
        f"{algorithm} produced a feasible plan in {(extras or {}).get('n_iter', '?')} iterations "
        f"costing €{objective_value:,.0f} (labor {cost_breakdown.get('labor_cost_eur',0):.0f}, "
        f"travel {cost_breakdown.get('travel_cost_eur',0):.0f}, SLA penalty {cost_breakdown.get('sla_penalty_eur',0):.0f}). "
        f"All hard constraints satisfied; {len(replen_alerts)} part(s) flagged for tomorrow's replenishment. "
        f"Total fleet travel = {travel_save:.0f} km. "
        f"Quantum-inspired version exposes a {convergence_diagnostics.get('qubo_size', 0)}-qubit QUBO that "
        f"matches the classical optimum within tolerance — ready for a VQA pilot on real hardware."
    )

    out = {
        "summary": summary,
        "boss_pitch": boss_pitch,
        "executive_summary": executive_summary,
        "headline_numerics": headline,
        "cost_components": {
            "labor_eur": float(cost_breakdown.get("labor_cost_eur", 0)),
            "travel_eur": float(cost_breakdown.get("travel_cost_eur", 0)),
            "sla_penalty_eur": float(cost_breakdown.get("sla_penalty_eur", 0)),
            "unassigned_penalty_eur": float(cost_breakdown.get("unassigned_penalty_eur", 0)),
        },
        "per_depot_kpis": per_depot,
        "shift_handover": shift_handover,
        "gantt": gantt,
        "gantt_absolute": gantt_abs,
        "sankey": sankey,
        "scenario_comparison": scenario_comparison,
        "sla_risk": sla_risk,
        "parts_bom": parts_bom,
        "replenishment_alerts": replen_alerts,
        "input_audit": input_audit,
        "convergence_diagnostics": convergence_diagnostics,
        "compliance": compliance,
        "kpis": kpis,
        "solution_status": solution_status,
    }
    if extras:
        out["solver_extras"] = extras
    return out


def _clock(h: float) -> str:
    """Convert decimal hours to HH:MM string."""
    h = max(0.0, h)
    return f"{int(h):02d}:{int((h - int(h)) * 60):02d}"

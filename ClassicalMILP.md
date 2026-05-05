# classical-resource-allocation-milp-cpu

Classical MILP solver for the **Resource Allocation for Maintenance Depots (TEKSEM)** use case.
Solved with PuLP using the embedded CBC backend.

## Algorithm
Mixed-Integer Linear Program. Decision variables: `x[task, technician] ∈ {0,1}`. Objective:
minimise total EUR cost (labor + travel + SLA-late penalties + unassigned-task penalty).
Constraints enforce skill match, certifications, technician hour capacity, and network-wide
spare-part stock availability.

## solver_params
- `time_limit_s` *(int, default 30)* — CBC wall-clock budget in seconds.
- `msg` *(int 0|1, default 0)* — print CBC solver chatter to stdout.

## Output contract
Returns the standard QCentroid output dict for this use case:
`objective_value` (EUR), `solution_status`, `assignments`, `unassigned_tasks`,
`cost_breakdown`, `kpis`, `computation_metrics`, and `benchmark`. The `benchmark`
key is required by the platform for charts to render.

## Notes
- Suitable as the deterministic baseline for any quantum / quantum-inspired
  comparison on this use case; CBC produces an optimality certificate when not
  hitting the time limit.
- Comparable units (`objective_value` in EUR) with the
  `quantum-inspired-resource-allocation-qubo-sa-cpu` solver.

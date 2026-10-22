# S4 strategy comparison — R101

Started with 25 customers. Broke vehicle 14 at t=90 and added order 26 at t=95.

Each strategy received the same events and began with the same initial plan.

## break_vehicle at t=90

| Strategy | Distance before | Distance after | Delta | Unassigned before | Unassigned after | Late | Recovery seconds |
|---|---:|---:|---:|---:|---:|---:|---:|
| do_nothing | 619.0 | 567.6 | -51.4 | 0 | 3 | 0 | 0.000002 |
| cheapest_insertion | 619.0 | 636.1 | +17.1 | 0 | 1 | 0 | 0.001710 |
| replan | 619.0 | 626.2 | +7.2 | 0 | 1 | 0 | 2.005611 |

## add_order at t=95

| Strategy | Distance before | Distance after | Delta | Unassigned before | Unassigned after | Late | Recovery seconds |
|---|---:|---:|---:|---:|---:|---:|---:|
| do_nothing | 567.6 | 567.6 | +0.0 | 3 | 4 | 0 | 0.000003 |
| cheapest_insertion | 636.1 | 637.1 | +1.0 | 1 | 1 | 0 | 0.001340 |
| replan | 626.2 | 630.2 | +4.0 | 1 | 1 | 0 | 2.010872 |

## End of day (t=230)

| Strategy | Delivered | Unassigned | Distance | Late |
|---|---:|---:|---:|---:|
| do_nothing | 22 | 4 | 567.6 | 0 |
| cheapest_insertion | 25 | 1 | 637.1 | 0 |
| replan | 25 | 1 | 630.2 | 0 |

All demo checks passed: history locking, vehicle positions, travelled distance, capacity, windows, and order accounting.

Lower distance alone does not mean better performance: a strategy may travel less because it leaves more deliveries unassigned.

Recovery runtime is measured consistently for each strategy and excludes state cloning and event advancement. Before-event runtime is zero for that event. Search is capped at two seconds; preparation adds small overhead.

Late counts include actual and projected deliveries; at end of day they are actual. Orders left unassigned are counted separately. Re-planning is a time-limited heuristic, not guaranteed to outperform insertion on every run.

The capacity/cargo-transfer assumptions are the same as S3. This is a terminal comparison, not the future map/dashboard.

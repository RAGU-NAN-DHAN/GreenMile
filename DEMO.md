# GreenMile demo script (about 90 seconds)

1. Open the dashboard, choose **R101**, press **Load & solve** (25 orders, 8 vehicles, about 3 s).
   Say: "Benchmark check: our static solve is within 0.3% of the best-known value."
2. Click the map button for the **street map** (Vellore). Wait for the road legs; the **Road** chip shows real road km.
3. Press **Play** (2x) or drag the slider to **t = 90**.
4. Click a truck to open its card (route progress, load, alerts).
5. Press **Take selected vehicle offline**. All three strategies re-plan.
6. Open the **strategy comparison** (third sidebar icon):
   OR-Tools re-plan and cheapest insertion leave 0 unassigned; do nothing leaves 1.
7. Add a **new order** (map button, click the map, Add order) and show the event log.
8. Close: "Served stops stay locked, broken vehicles get no new work, re-plan takes about 2 s."

Recording: Windows key + G (Xbox Game Bar), or OBS. Keep the browser at 1400 px wide.

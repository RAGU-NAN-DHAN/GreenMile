# GreenMile demo video: Google Flow prompt pack

Google Flow (Veo) makes short AI clips, roughly 8 seconds each. It cannot reproduce a real dashboard:
interface text, numbers and buttons come out garbled. So the plan is a hybrid:

| Time | Source | What |
|---|---|---|
| 0:00 - 0:08 | **Flow clip 1** | Intro: city at night, glowing routes |
| 0:08 - 1:15 | **Real screen recording** | The actual dashboard, following the steps in [DEMO.md](../DEMO.md) |
| 1:15 - 1:23 | **Flow clip 4** | Outro: trucks return to the depot at dawn |

Add the title cards and captions in your video editor (Clipchamp on Windows, or CapCut), not in Flow.
Flow menu names change; if something below is called something else, pick the closest option
(text to video, frames to video, ingredients to video).

## Global settings for every clip
- Aspect ratio 16:9, 8 seconds, highest quality your plan allows, audio off (add music in the editor).
- Add to every prompt: `No text, no logos, no watermarks, no readable signs.`
- Keep the look consistent: night or dawn, deep black and teal shadows, warm yellow-green accent light
  (the dashboard accent is #CEDE62).

## Clip 1: intro (text to video)
```
Slow aerial push-in over an Indian city at night, a university campus and its main road in the center.
Thin glowing yellow-green lines trace delivery routes along the streets and connect small delivery
trucks that glide along them. Warm street lights, light haze, cinematic, shallow depth of field,
smooth camera. No text, no logos, no watermarks, no readable signs.
```

## Clip 2: the problem (text to video, optional B-roll)
```
Night street scene: one white delivery truck stops at the roadside with hazard lights blinking while
two other delivery trucks drive past in the background. Rain-slick road reflects the lights.
Handheld-free, locked-off camera, cinematic. No text, no logos, no watermarks, no readable signs.
```

## Clip 3: re-planning (text to video, abstract)
```
Top-down abstract map visualization at night: glowing yellow-green route lines on a dark city grid,
one line fades out and the other lines smoothly bend to take over its stops, small truck icons keep
moving. Clean motion-graphics look, dark background. No text, no letters, no numbers, no logos.
```

## Clip 4: outro (text to video)
```
Dawn over a delivery depot yard: several delivery trucks roll in and park in a neat row as soft golden
light spreads. Calm, slow dolly forward. Leave the upper third of the frame empty and uncluttered for
a title. No text, no logos, no watermarks, no readable signs.
```

## Ready-made frames (docs/flow-frames/, 1920x1080, from the real app)
Upload these in Flow's **Frames to Video** (start frame, optional end frame) or as reference images.

| File | What it shows | Best use |
|---|---|---|
| `01_map_start_depot_t000.png` | Street map of the VIT area, trucks stacked at the depot, routes drawn | Start frame of clip A |
| `02_map_moving_t060.png` | Same map at minute 60, trucks 1, 17, 18, 20, 21, 24 out on their routes | End frame of clip A, start frame of clip B |
| `03_map_after_breakdown_t100.png` | After truck 17 breaks down at minute 90 and the plan is re-made | End frame of clip B |
| `04_dashboard_t060.png` | Full dashboard at minute 60 | Title-card or still background |
| `05_dashboard_truck_card_t060.png` | Truck 1's card open over the map | Still background |
| `06_strategy_comparison_t100.png` | The three strategies after the breakdown | Still background |
| `07_vehicle_cargo_t100.png` | Cargo layout of a truck | Still background |

### Clip A: trucks set off (frames 01 to 02)
```
Smooth cinematic aerial view of the map. Small delivery trucks leave the central depot and glide along
the glowing yellow-green roads to their stops. Keep the map layout, roads and colours unchanged.
```

### Clip B: breakdown and re-plan (frames 02 to 03)
```
One truck stops and turns red while the glowing routes of the other trucks bend to take over its stops.
Keep the map layout unchanged, smooth motion.
```

The frames contain small map labels and interface text, which the AI will probably smear while animating.
Use clips A and B only as quick background shots with the real recording as the main content, and keep
frames 04 to 07 as still images (no animation).

## Re-creating the frames
Start the server, load R101 in road mode, then open URLs such as
`http://127.0.0.1:8000/?t=60&clean=1` (map only), `?t=60&truck=1` (truck card),
`?t=100&view=compare` or `?t=100&view=vehicle&truck=1`, and capture them at 1920x1080
(for example with `chrome --headless=new --window-size=1920,1080 --virtual-time-budget=30000
--screenshot=out.png URL`).

## Recording the real demo (about 70 seconds)
Windows key + G opens Xbox Game Bar (or use OBS). Record the browser window, then follow DEMO.md:

| Seconds | Do | Say |
|---|---|---|
| 0-10 | Select R101, press Load & solve | "GreenMile plans routes for a delivery fleet using real road distances around our campus." |
| 10-20 | Show the street map, point at the Road chip | "Every stop is snapped to a real road, and times are in minutes at a 25 km/h average." |
| 20-35 | Press Play, point at moving trucks and the Fleet panel | "Each truck shows what it is doing, its ETA and its load, and moves live." |
| 35-50 | Take a truck offline | "A truck breaks down mid-day. All three recovery strategies react to the same event." |
| 50-62 | Open the strategy comparison | "Do nothing leaves orders undelivered; cheapest insertion and OR-Tools re-plan keep every order, with no late deliveries." |
| 62-70 | Back to the map, add a new order | "A new order arrives and the plan adapts again. Completed deliveries stay locked." |

## Voice-over and titles
- Open with the title card "GreenMile - live last-mile re-planning" over clip 1.
- End on "GreenMile" with the repo name over clip 4.
- Record the voice-over on your phone or in the editor and keep the music low.

## Checklist before exporting
- No AI clip shows readable text or a fake interface.
- The dashboard footage is the real app, recorded after a hard refresh (Ctrl + Shift + R).
- Total length about 90 seconds, 1080p, MP4.

import "leaflet/dist/leaflet.css";

import L from "leaflet";
import { useEffect, useRef, useState } from "react";

import { type Forecast, type HeightMap, loadSheet, loopUrl, nearestCase, type Sheet, type WaveLoop } from "./sheets";
import { useAsync } from "./useAsync";

// The map is Portugal's 2023 satellite mosaic (DGT, 30 cm; its service states no fees and no
// access constraints), with Esri's world imagery to switch to. Tiles come straight from them:
// they see which part of the map a viewer looks at, and nothing else.
function baseLayers() {
  const dgt = L.tileLayer.wms("https://ortos.dgterritorio.gov.pt/wms/ortosat2023", {
    layers: "ortoSat2023-CorVerdadeira",
    format: "image/jpeg",
    version: "1.3.0",
    maxZoom: 20,
    attribution: "OrtoSat2023 © DGT",
  });
  const esri = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { maxNativeZoom: 19, maxZoom: 20, attribution: "Imagery © Esri, Maxar, Earthstar Geographics" },
  );
  return { "DGT 2023": dgt, Esri: esri };
}

const ZONE = "#d4b483";
const BREAK = "#9cc7e8";
const ROCK = "#e0605a";

// Wave height is magnitude, so one hue, gogo's gold, dark and faint to light and solid: on a
// dark sea photo the bigger waves are the brighter ones.
// At the swell's own height the photo shows through; the bigger, the brighter and more opaque.
const RAMP: [number, [number, number, number], number][] = [
  [0, [120, 90, 45], 0.04],
  [0.5, [212, 180, 131], 0.5],
  [1, [255, 246, 228], 0.88],
];
const LOW = 1; // ratios at or below this take the ramp's first colour
const HIGH = 2; // and at or above this its last

function ramp(ratio: number): [number, number, number, number] {
  const t = Math.min(1, Math.max(0, (ratio - LOW) / (HIGH - LOW)));
  for (let k = 1; k < RAMP.length; k++) {
    const [t1, c1, a1] = RAMP[k]!;
    const [t0, c0, a0] = RAMP[k - 1]!;
    if (t <= t1) {
      const f = (t - t0) / (t1 - t0);
      return [
        c0[0] + f * (c1[0] - c0[0]),
        c0[1] + f * (c1[1] - c0[1]),
        c0[2] + f * (c1[2] - c0[2]),
        a0 + f * (a1 - a0),
      ];
    }
  }
  const [, c, a] = RAMP[RAMP.length - 1]!;
  return [c[0], c[1], c[2], a];
}

function rampCss() {
  const stops = [0, 0.25, 0.5, 0.75, 1].map((t) => {
    const [r, g, b, a] = ramp(LOW + t * (HIGH - LOW));
    return `rgba(${r | 0},${g | 0},${b | 0},${a.toFixed(2)}) ${t * 100}%`;
  });
  return `linear-gradient(90deg, ${stops.join(", ")})`;
}

type Decoded = { url: string; values: Float32Array; w: number; h: number };
const decoded = new WeakMap<HeightMap, Promise<Decoded>>();

// The grey PNG to a coloured image, and its values kept for reading one off under a tap.
function decode(hm: HeightMap): Promise<Decoded> {
  let p = decoded.get(hm);
  if (!p) {
    p = (async () => {
      const img = await loadImage(`data:image/png;base64,${hm.png}`);
      const w = img.naturalWidth;
      const h = img.naturalHeight;
      const canvas = document.createElement("canvas");
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext("2d");
      if (!ctx) throw new Error("no canvas");
      ctx.drawImage(img, 0, 0);
      const px = ctx.getImageData(0, 0, w, h);
      const values = new Float32Array(w * h);
      for (let i = 0; i < w * h; i++) {
        const grey = px.data[i * 4]!;
        if (grey === 0) {
          values[i] = NaN;
          px.data[i * 4 + 3] = 0;
          continue;
        }
        const v = ((grey - 1) / 254) * hm.top;
        values[i] = v;
        const [r, g, b, a] = ramp(v);
        px.data[i * 4] = r;
        px.data[i * 4 + 1] = g;
        px.data[i * 4 + 2] = b;
        px.data[i * 4 + 3] = Math.round(a * 255);
      }
      ctx.putImageData(px, 0, 0);
      return { url: canvas.toDataURL(), values, w, h };
    })();
    decoded.set(hm, p);
    p.catch(() => decoded.delete(hm)); // a failure is tried again next time, not remembered
  }
  return p;
}

type Loop = { frames: Uint8ClampedArray[]; foam: Uint8ClampedArray[]; w: number; h: number };

// The model flags only the front that is breaking; behind it the foam stays a few seconds,
// fading. FOAM_LIFE_S is how long it takes to fade to a fifth.
const FOAM_LIFE_S = 4;

// Foam per frame (0-255): breaking now, or what was left of it a frame ago, fading. Round the
// loop twice, so the first frames get the foam the last ones left.
function foamOf(frames: Uint8ClampedArray[], periodS: number): Uint8ClampedArray[] {
  const n = frames.length;
  const keep = Math.pow(0.2, periodS / n / FOAM_LIFE_S);
  const size = frames[0]?.length ?? 0;
  const foam = frames.map(() => new Uint8ClampedArray(size));
  let prev = new Float32Array(size);
  for (let pass = 0; pass < 2; pass++) {
    for (let k = 0; k < n; k++) {
      const grey = frames[k]!;
      const out = foam[k]!;
      const next = new Float32Array(size);
      for (let p = 0; p < size; p++) {
        const g = grey[p]!;
        const v = g === 0 ? 0 : g >= 128 ? 1 : prev[p]! * keep;
        next[p] = v;
        out[p] = Math.round(v * 255);
      }
      prev = next;
    }
  }
  return foam;
}
const loops = new WeakMap<WaveLoop, Promise<Loop>>();

// The loop's PNG, fetched on first play, split into its frames' grey values.
function decodeLoop(loop: WaveLoop): Promise<Loop> {
  let p = loops.get(loop);
  if (!p) {
    p = (async () => {
      const img = await loadImage(await loopUrl(loop.file));
      const w = img.naturalWidth;
      const h = img.naturalHeight / loop.frames;
      const canvas = document.createElement("canvas");
      canvas.width = w;
      canvas.height = img.naturalHeight;
      const ctx = canvas.getContext("2d");
      if (!ctx) throw new Error("no canvas");
      ctx.drawImage(img, 0, 0);
      const all = ctx.getImageData(0, 0, w, img.naturalHeight).data;
      const frames = Array.from({ length: loop.frames }, (_, n) => {
        const grey = new Uint8ClampedArray(w * h);
        for (let k = 0; k < w * h; k++) grey[k] = all[(n * w * h + k) * 4]!;
        return grey;
      });
      return { frames, foam: foamOf(frames, loop.period_s), w, h };
    })();
    loops.set(loop, p);
    p.catch(() => loops.delete(loop));
  }
  return p;
}

// Leaflet's image overlay, drawing a canvas instead of loading an image.
const CanvasOverlay = L.ImageOverlay.extend({
  _initImage(this: { _url: unknown; _image: HTMLCanvasElement; _zoomAnimated: boolean }) {
    const c = this._url as HTMLCanvasElement;
    this._image = c;
    L.DomUtil.addClass(c, "leaflet-image-layer");
    if (this._zoomAnimated) L.DomUtil.addClass(c, "leaflet-zoom-animated");
  },
}) as unknown as new (canvas: HTMLCanvasElement, bounds: L.LatLngBoundsExpression, options?: L.ImageOverlayOptions) => L.ImageOverlay;

// One frame of the moving wave: crests pale blue, troughs dark, white water and its foam white.
// In between two saved frames everything is blended, so the loop runs smoothly at any speed.
function paintWave(
  out: ImageData,
  a: Uint8ClampedArray,
  b: Uint8ClampedArray,
  foamA: Uint8ClampedArray,
  foamB: Uint8ClampedArray,
  f: number,
  top: number,
  h0: number,
) {
  const d = out.data;
  for (let k = 0; k < a.length; k++) {
    const va = a[k]!;
    const vb = b[k]!;
    const o = k * 4;
    if (va === 0 || vb === 0) {
      d[o + 3] = 0;
      continue;
    }
    const level = (1 - f) * (va & 127) + f * (vb & 127);
    const eta = ((level - 1) / 126) * 2 * top - top;
    let r: number, g: number, bl: number, al: number;
    if (eta >= 0) {
      r = 178;
      g = 222;
      bl = 240;
      al = Math.min(190, (eta / (0.45 * h0)) * 160);
    } else {
      r = 2;
      g = 14;
      bl = 26;
      al = Math.min(150, (-eta / (0.5 * h0)) * 130);
    }
    // White water over it, fading with the foam.
    const foam = ((1 - f) * foamA[k]! + f * foamB[k]!) / 255;
    d[o] = r + (252 - r) * foam;
    d[o + 1] = g + (253 - g) * foam;
    d[o + 2] = bl + (255 - bl) * foam;
    d[o + 3] = Math.max(al, foam * 240);
  }
}

// An image, loaded: the load event rather than img.decode(), which some browsers reject for a
// page that is not on screen.
function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("image did not load"));
    img.src = src;
  });
}

function pct(x: number | null | undefined) {
  return x == null ? "—" : `${Math.round(100 * x)}%`;
}

// `forecast`, when given, is the hour the map opens on: it shows the run nearest that swell.
export default function SpotSheet({ spotId, forecast }: { spotId: string; forecast?: Forecast }) {
  const result = useAsync(() => loadSheet(spotId), [spotId]);
  if (result.state === "loading") return null;
  if (result.state === "error") return null; // the page is whole without it
  return <SheetMap sheet={result.data} forecast={forecast} />;
}

const fmt = (n: number) => (Math.round(n * 10) / 10).toString();

type View = "breaks" | "height" | "wave";

function SheetMap({ sheet, forecast }: { sheet: Sheet; forecast?: Forecast }) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const layer = useRef<L.LayerGroup | null>(null);
  const fitted = useRef(false);

  const near = forecast ? nearestCase(sheet, forecast) : null;
  const [swellIdx, setSwellIdx] = useState(near?.swellIdx ?? 0);
  const [deg, setDeg] = useState(near?.deg ?? 320);
  const [tide, setTide] = useState(near?.tide ?? "mid");
  // Another hour picked on the page: move to its nearest run. Picks made on the map stay
  // until then.
  const nearKey = near && forecast ? `${forecast.at_local}|${near.swellIdx}|${near.deg}|${near.tide}` : "";
  useEffect(() => {
    if (!near) return;
    setSwellIdx(near.swellIdx);
    setDeg(near.deg);
    setTide(near.tide);
  }, [nearKey]);
  const [view, setView] = useState<View>("breaks");
  const [heights, setHeights] = useState<Decoded | null>(null);
  const [reading, setReading] = useState<string | null>(null);
  const [loop, setLoop] = useState<Loop | null>(null);
  const [playing, setPlaying] = useState(
    () => !window.matchMedia?.("(prefers-reduced-motion: reduce)").matches,
  );
  const [speed, setSpeed] = useState(1);
  const phase = useRef(0); // where in the wave's period the loop is, kept across pause and speed

  const swell = sheet.swells[swellIdx] ?? sheet.swells[0];
  const row = swell?.rows.find((r) => r.from_deg === deg) ?? swell?.rows[0];
  const tideName = row && row.tides[tide] ? tide : Object.keys(row?.tides ?? {})[0] ?? "mid";
  const t = row?.tides[tideName];
  const hm = t?.height_map ?? null;
  const wl = t?.loop ?? null;

  useEffect(() => {
    if (!el.current) return;
    // Quarter zoom steps, so a spot a few hundred metres across can fill the map.
    const m = L.map(el.current, { scrollWheelZoom: false, zoomControl: true, zoomSnap: 0.25 });
    const bases = baseLayers();
    bases["DGT 2023"].addTo(m);
    L.control.layers(bases, undefined, { position: "topright" }).addTo(m);
    // The height map and the moving wave go under the outline, dots and rings.
    m.createPane("sheetImages").style.zIndex = "350";
    layer.current = L.layerGroup().addTo(m);
    map.current = m;
    return () => {
      m.remove();
      map.current = null;
      fitted.current = false;
    };
  }, []);

  // Decode the height map of the run on show, ready for the height view.
  useEffect(() => {
    let live = true;
    setHeights(null);
    setReading(null);
    if (hm) {
      decode(hm)
        .then((d) => live && setHeights(d))
        .catch(() => live && setHeights(null));
    }
    return () => {
      live = false;
    };
  }, [hm]);

  // Fetch and split the loop of the run on show, once the wave view is open.
  useEffect(() => {
    let live = true;
    setLoop(null);
    if (view === "wave" && wl) {
      decodeLoop(wl)
        .then((d) => live && setLoop(d))
        .catch(() => live && setLoop(null));
    }
    return () => {
      live = false;
    };
  }, [view, wl]);

  // Play it: one period of the swell every period_s / speed seconds.
  useEffect(() => {
    const m = map.current;
    if (!m || view !== "wave" || !wl || !loop || !swell) return;
    const canvas = document.createElement("canvas");
    canvas.width = loop.w;
    canvas.height = loop.h;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const img = ctx.createImageData(loop.w, loop.h);
    const overlay = new CanvasOverlay(canvas, wl.bounds, { interactive: false, className: "sheet-wave", pane: "sheetImages" }).addTo(m);
    let raf = 0;
    let last = performance.now();
    const draw = (now: number) => {
      // The first animation frame can be stamped a little before `last`: never step backwards.
      const dt = Math.max(0, now - last) / 1000;
      last = Math.max(last, now);
      if (playing) phase.current = (phase.current + dt * (speed / wl.period_s)) % 1;
      const x = phase.current * loop.frames.length;
      const n0 = Math.floor(x) % loop.frames.length;
      const n1 = (n0 + 1) % loop.frames.length;
      const a = loop.frames[n0];
      const b = loop.frames[n1];
      const fa = loop.foam[n0];
      const fb = loop.foam[n1];
      if (a && b && fa && fb) {
        paintWave(img, a, b, fa, fb, x - Math.floor(x), wl.eta_top_m, swell.height_m);
        ctx.putImageData(img, 0, 0);
      }
      if (playing) raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(raf);
      overlay.remove();
    };
  }, [view, wl, loop, swell, playing, speed]);

  // Tap or hover the height map to read it.
  useEffect(() => {
    const m = map.current;
    if (!m || view !== "height" || !hm || !heights || !swell) return;
    const [[s, w], [n, e]] = hm.bounds;
    const read = (ev: L.LeafletMouseEvent) => {
      const col = Math.floor(((ev.latlng.lng - w) / (e - w)) * heights.w);
      const r = Math.floor(((n - ev.latlng.lat) / (n - s)) * heights.h);
      const v = col >= 0 && col < heights.w && r >= 0 && r < heights.h ? heights.values[r * heights.w + col] : NaN;
      setReading(
        v === undefined || Number.isNaN(v)
          ? null
          : `${v.toFixed(1)}× the swell here: about ${(v * swell.height_m).toFixed(1)} m for this ${swell.height_m} m swell`,
      );
    };
    m.on("click", read);
    m.on("mousemove", read);
    return () => {
      m.off("click", read);
      m.off("mousemove", read);
    };
  }, [view, hm, heights, swell]);

  useEffect(() => {
    const m = map.current;
    const g = layer.current;
    if (!m || !g || !swell || !t) return;
    g.clearLayers();

    if (view === "height" && hm && heights) {
      L.imageOverlay(heights.url, hm.bounds, { interactive: false, className: "sheet-heights", pane: "sheetImages" }).addTo(g);
    }
    if (sheet.area) {
      L.polygon(sheet.area, { color: "#fff", weight: 1.2, dashArray: "5 6", fill: false, interactive: false }).addTo(g);
    }
    if (view === "breaks") {
      for (const poly of t.zone) {
        L.polygon(
          poly.map((ring) => ring.map(([lon, lat]) => [lat, lon] as [number, number])),
          { color: ZONE, weight: 1, fillColor: ZONE, fillOpacity: 0.38 },
        ).addTo(g);
      }
      t.breaks.forEach(([lon, lat, h, d, rock], i) => {
        if (i % 2) return; // every other row is plenty on a phone
        L.circleMarker([lat, lon], {
          radius: 1.5 + (1.4 * h) / swell.height_m,
          weight: 0,
          fillOpacity: 0.9,
          fillColor: rock ? ROCK : BREAK,
        })
          .bindTooltip(`${h.toFixed(1)} m in ${d.toFixed(1)} m of water${rock ? ", onto the rock" : ""}`)
          .addTo(g);
      });
    }
    // The reef's peaks, fainter the fewer runs they break first in.
    const runs = sheet.swells.reduce((n, s) => n + s.rows.reduce((k, r) => k + Object.keys(r.tides).length, 0), 0);
    for (const p of sheet.peaks.filter((p) => p.share >= 0.25)) {
      const strength = Math.min(1, 0.3 + ((p.share - 0.25) / 0.3) * 0.7);
      L.circleMarker([p.lat, p.lon], {
        radius: 11,
        color: ZONE,
        weight: 2,
        opacity: strength,
        dashArray: "3 3",
        fill: false,
      })
        .bindTooltip(`a peak of the reef: breaks first here in ${Math.round(p.share * runs)} of ${runs} runs`)
        .addTo(g);
    }
    t.takeoffs
      .filter((p) => (p.stands_out_s ?? 0) >= 5)
      .forEach((p, i) => {
        L.circleMarker([p.lat, p.lon], {
          radius: i ? 6 : 8,
          color: "#071018",
          weight: 2,
          fillColor: "#fff",
          fillOpacity: 1,
        })
          .bindTooltip(`takeoff at this swell, in ${p.depth_m} m of water`)
          .addTo(g);
      });

    if (!fitted.current) {
      const bounds = sheet.area ? L.latLngBounds(sheet.area) : L.featureGroup(g.getLayers()).getBounds();
      m.fitBounds(bounds.pad(0.05));
      fitted.current = true;
    }
  }, [sheet, swell, t, view, hm, heights]);

  if (!swell || !row || !t) return null;
  const n = t.near_takeoff;
  const shift = row.low_tide_seaward_m;
  const takeoffs = t.takeoffs.filter((p) => (p.stands_out_s ?? 0) >= 5).length;

  const onNear = near !== null && near.swellIdx === swellIdx && near.deg === row.from_deg && near.tide === tideName;
  const nearSwell = near ? sheet.swells[near.swellIdx] : undefined;
  const degs = swell.rows.map((r) => r.from_deg);
  const sizes = sheet.swells.map((s) => s.height_m);
  const notes = forecast
    ? [
        forecast.from_deg < Math.min(...degs) - 10 || forecast.from_deg > Math.max(...degs) + 10
          ? `the runs cover ${Math.min(...degs)}–${Math.max(...degs)}°`
          : null,
        forecast.height_m > 1.3 * Math.max(...sizes) ? "bigger than any run" : null,
        forecast.height_m < 0.75 * Math.min(...sizes) ? "smaller than any run" : null,
      ].filter(Boolean)
    : [];

  return (
    <section className="sheet" aria-label="How the spot breaks">
      <p className="kicker">How it breaks</p>
      {forecast && near && nearSwell && (
        <p className="sheet-forecast">
          Forecast at {forecast.at_local}: {fmt(forecast.height_m)} m, {Math.round(forecast.period_s)} s from{" "}
          {Math.round(forecast.from_deg)}°{forecast.tide ? `, ${forecast.tide} tide` : ""}.{" "}
          {onNear ? (
            <>
              The map shows the closest run: {nearSwell.name} from {near.deg}°, {near.tide} tide
              {notes.length ? ` (${notes.join("; ")})` : ""}.
            </>
          ) : (
            <button
              type="button"
              className="sheet-link"
              onClick={() => {
                setSwellIdx(near.swellIdx);
                setDeg(near.deg);
                setTide(near.tide);
              }}
            >
              Show the closest run
            </button>
          )}
        </p>
      )}
      <div className="sheet-picks" role="group" aria-label="Show">
        <button type="button" aria-pressed={view === "breaks"} onClick={() => setView("breaks")}>
          Where it breaks
        </button>
        <button
          type="button"
          aria-pressed={view === "height"}
          disabled={!hm}
          onClick={() => setView("height")}
        >
          Wave height
        </button>
        <button type="button" aria-pressed={view === "wave"} disabled={!wl} onClick={() => setView("wave")}>
          Moving wave
        </button>
      </div>
      <div className="sheet-map" ref={el} />
      {view === "height" && (
        <p className="sheet-reading">{reading ?? "Tap the map to read the height there."}</p>
      )}
      {view === "wave" && (
        <div className="sheet-picks" role="group" aria-label="Play">
          <button type="button" className="sheet-play" onClick={() => setPlaying((p) => !p)}>
            {playing ? "Pause" : "Play"}
          </button>
          <button type="button" aria-pressed={speed === 1} onClick={() => setSpeed(1)}>
            Real time
          </button>
          <button type="button" aria-pressed={speed === 0.5} onClick={() => setSpeed(0.5)}>
            Half speed
          </button>
          {!loop && <small className="sheet-loading">loading…</small>}
        </div>
      )}

      {sheet.swells.length > 1 && (
        <div className="sheet-picks" role="group" aria-label="Swell">
          {/* Smallest first; the sheet's first swell stays the one the map opens on. */}
          {sheet.swells
            .map((s, i) => [s, i] as const)
            .sort(([a], [b]) => a.height_m - b.height_m)
            .map(([s, i]) => (
              <button key={s.name} type="button" aria-pressed={i === swellIdx} onClick={() => setSwellIdx(i)}>
                {s.name}
              </button>
            ))}
        </div>
      )}
      <div className="sheet-picks" role="group" aria-label="Swell from">
        {swell.rows.map((r) => (
          <button key={r.from_deg} type="button" aria-pressed={r.from_deg === row.from_deg} onClick={() => setDeg(r.from_deg)}>
            {r.from_deg}°
          </button>
        ))}
      </div>
      <div className="sheet-picks" role="group" aria-label="Tide">
        {Object.entries(row.tides).map(([name, e]) => (
          <button key={name} type="button" aria-pressed={name === tideName} onClick={() => setTide(name)}>
            {name} tide <small>{e.tide_m >= 0 ? "+" : ""}{e.tide_m} m</small>
          </button>
        ))}
      </div>

      <dl className="sheet-facts">
        <dt>Swell</dt>
        <dd>
          {swell.name} from {row.from_deg}°; the shelf bends it to {row.at_maker_deg}° by the reef
        </dd>
        <dt>Takeoff</dt>
        <dd>{takeoffs ? `${takeoffs} (white)` : "no clear peak"}</dd>
        <dt>At the break</dt>
        <dd>
          {n?.height_over_offshore != null ? `${n.height_over_offshore}× the swell` : "—"}
          {n?.depth_m != null ? `, in ${n.depth_m} m of water` : ""}
          {n?.from_shore_m != null ? `, ${Math.round(n.from_shore_m)} m out` : ""}
        </dd>
        <dt>Onto the rock</dt>
        <dd>
          {pct(t.rock_share)} of the spot{n ? `, ${pct(n.rock_share)} near the takeoff` : ""}
        </dd>
        <dt>Low tide</dt>
        <dd>
          {shift == null
            ? Object.keys(row.tides).length === 1
              ? `this swell is only modelled at ${tideName} tide`
              : "—"
            : Math.abs(shift) < 5
              ? "breaks in about the same place"
              : `breaks ${Math.abs(Math.round(shift))} m further ${shift > 0 ? "out" : "in"}`}
        </dd>
      </dl>

      {view === "wave" ? (
        <div className="sheet-legend">
          <p>
            Crests pale blue, troughs dark, white water and its foam white. One wave of a perfectly regular {swell.name} swell,
            repeated: the real sea comes in sets, and this shows how each wave of it moves across the reef.
          </p>
        </div>
      ) : view === "breaks" ? (
        <ul className="sheet-legend">
          <li><i style={{ background: ZONE, opacity: 0.6 }} />breaks on most waves</li>
          <li><i className="dot" />takeoff at this swell</li>
          <li><i className="ring" />a peak of the reef; fainter, at fewer swells</li>
          <li><i style={{ background: BREAK }} />break points, bigger dot = bigger wave</li>
          <li><i style={{ background: ROCK }} />reaches the rock unbroken</li>
        </ul>
      ) : (
        <div className="sheet-legend">
          <div className="sheet-scale" style={{ background: rampCss() }} aria-hidden="true" />
          <div className="sheet-scale-ticks">
            <span>as big as the swell</span>
            <span>1.5×</span>
            <span>{HIGH}× or more</span>
          </div>
          <p>
            How big the waves get, over the swell offshore. Smoothed over about 30 m, because the swell
            bouncing back off the rock lays stripes over it; so it reads a little lower than the height at the
            break, which includes that bounce.
          </p>
        </div>
      )}
      <p className="sheet-note">
        {sheet.note}. wavesim, run on {sheet.seabed.cell_m} m cells over the {sheet.seabed.source}; {sheet.generated}.
        Whether it barrels and which way it peels are left out until a finer seabed. Seabed:{" "}
        {sheet.seabed.attribution}.
      </p>
    </section>
  );
}

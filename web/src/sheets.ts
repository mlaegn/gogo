// Spot sheets: how a reef or point spot breaks at each swell, from wavesim's runs. Each is a
// static file, src/sheets/<spot id>.json, copied from wavesim's tools/spot_sheet.py output
// (out/sheets/<spot>.json). Model, not observation; the score never reads them.

// [longitude, latitude, height at the break (m), depth there (m), reaches the rock unbroken]
export type BreakPoint = [number, number, number, number, boolean];

export type Takeoff = { lat: number; lon: number; depth_m: number; stands_out_s?: number };

export type TideEntry = {
  tide_m: number;
  takeoff: Takeoff | null;
  takeoffs: Takeoff[];
  near_takeoff: {
    height_over_offshore: number | null;
    reflection: number | null;
    depth_m: number | null;
    from_shore_m: number | null;
    rock_share: number;
  } | null;
  rock_share: number | null;
  breaks: BreakPoint[];
  // Polygons, each an outer ring then its holes, of [longitude, latitude] corners.
  zone: [number, number][][][];
  height_map: HeightMap | null;
  loop?: WaveLoop | null;
};

// One period of the settled sea, rebuilt from the run's frames: `frames` grey tiles stacked top
// to bottom in one PNG (src/sheets/<file>). Each pixel: 0 = no data; the surface level over
// +-eta_top_m quantised to 1..127; plus 128 where it is breaking.
export type WaveLoop = {
  file: string;
  frames: number;
  period_s: number;
  eta_top_m: number;
  harmonics: number;
  fit_rms: number;
  bounds: [[number, number], [number, number]];
};

// The wave height over the swell's offshore height, smoothed over the reflection off the rock:
// an 8-bit grey PNG on a latitude-longitude grid, 0 = no data, else 1 + ratio / top * 254.
export type HeightMap = { bounds: [[number, number], [number, number]]; top: number; png: string };

export type SheetRow = {
  from_deg: number;
  at_maker_deg: number;
  low_tide_seaward_m: number | null;
  tides: Record<string, TideEntry>;
};

export type Swell = { name: string; height_m: number; period_s: number; rows: SheetRow[] };

export type Sheet = {
  spot: string;
  note: string;
  seabed: { source: string | null; cell_m: number | null; attribution: string | null };
  area: [number, number][] | null; // [latitude, longitude] corners
  generated: string;
  peaks: { lat: number; lon: number; share: number }[];
  swells: Swell[];
};

// Loaded only when a spot page asks for one, so the other pages never carry them.
const files = import.meta.glob<Sheet>("./sheets/*.json", { import: "default" });

export function hasSheet(spotId: string): boolean {
  return `./sheets/${spotId}.json` in files;
}

// The loops are separate files, each fetched only when its wave is played.
const loopFiles = import.meta.glob<string>("./sheets/*/*.png", { query: "?url", import: "default" });

export function loopUrl(file: string): Promise<string> {
  const load = loopFiles[`./sheets/${file}`];
  return load ? load() : Promise.reject(new Error(`no loop ${file}`));
}

export function loadSheet(spotId: string): Promise<Sheet> {
  const load = files[`./sheets/${spotId}.json`];
  return load ? load() : Promise.reject(new Error(`no sheet for ${spotId}`));
}

// One forecast hour, as the spot page hands it to the map.
export type Forecast = {
  at_local: string;
  height_m: number;
  period_s: number;
  from_deg: number;
  tide: "low" | "mid" | "high" | null;
};

// The run the map shows: a swell (by its index in the sheet), a direction and a tide.
export type Case = { swellIdx: number; deg: number; tide: string };

// The run closest to a forecast hour. Size by ratio (1.5 m is nearer 1.2 than 2), the period
// only to break a tie; the direction the nearest the swell has rows for; the tide the same
// one, else mid for high (the runs stop at mid), else whichever the row has.
export function nearestCase(sheet: Sheet, f: Forecast): Case {
  const off = (s: Swell) => Math.abs(Math.log(f.height_m / s.height_m)) + 1e-6 * Math.abs(f.period_s - s.period_s);
  let swellIdx = 0;
  let best = Infinity;
  sheet.swells.forEach((s, i) => {
    if (off(s) < best) [swellIdx, best] = [i, off(s)];
  });
  const rows = sheet.swells[swellIdx]?.rows ?? [];
  const row = rows.reduce<SheetRow | undefined>(
    (best, r) => (!best || Math.abs(r.from_deg - f.from_deg) < Math.abs(best.from_deg - f.from_deg) ? r : best),
    undefined,
  );
  const tides = Object.keys(row?.tides ?? {});
  const want = f.tide === "high" ? "mid" : f.tide ?? "mid";
  const tide = tides.includes(want) ? want : tides.includes("mid") ? "mid" : tides[0] ?? "mid";
  return { swellIdx, deg: row?.from_deg ?? f.from_deg, tide };
}

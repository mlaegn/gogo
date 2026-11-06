import "leaflet/dist/leaflet.css";

import L from "leaflet";
import { useEffect, useRef, useState } from "react";

import { loadSheet, type Sheet } from "./sheets";
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

function pct(x: number | null | undefined) {
  return x == null ? "—" : `${Math.round(100 * x)}%`;
}

export default function SpotSheet({ spotId }: { spotId: string }) {
  const result = useAsync(() => loadSheet(spotId), [spotId]);
  if (result.state === "loading") return null;
  if (result.state === "error") return null; // the page is whole without it
  return <SheetMap sheet={result.data} />;
}

function SheetMap({ sheet }: { sheet: Sheet }) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const layer = useRef<L.LayerGroup | null>(null);
  const fitted = useRef(false);

  const [swellIdx, setSwellIdx] = useState(0);
  const [deg, setDeg] = useState(320);
  const [tide, setTide] = useState("mid");

  const swell = sheet.swells[swellIdx] ?? sheet.swells[0];
  const row = swell?.rows.find((r) => r.from_deg === deg) ?? swell?.rows[0];
  const tideName = row && row.tides[tide] ? tide : Object.keys(row?.tides ?? {})[0] ?? "mid";
  const t = row?.tides[tideName];

  useEffect(() => {
    if (!el.current) return;
    // Quarter zoom steps, so a spot a few hundred metres across can fill the map.
    const m = L.map(el.current, { scrollWheelZoom: false, zoomControl: true, zoomSnap: 0.25 });
    const bases = baseLayers();
    bases["DGT 2023"].addTo(m);
    L.control.layers(bases, undefined, { position: "topright" }).addTo(m);
    layer.current = L.layerGroup().addTo(m);
    map.current = m;
    return () => {
      m.remove();
      map.current = null;
      fitted.current = false;
    };
  }, []);

  useEffect(() => {
    const m = map.current;
    const g = layer.current;
    if (!m || !g || !swell || !t) return;
    g.clearLayers();

    if (sheet.area) {
      L.polygon(sheet.area, { color: "#fff", weight: 1.2, dashArray: "5 6", fill: false, interactive: false }).addTo(g);
    }
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
    const runs = sheet.swells.reduce((n, s) => n + s.rows.reduce((k, r) => k + Object.keys(r.tides).length, 0), 0);
    for (const p of sheet.peaks.filter((p) => p.share >= 0.25)) {
      L.circleMarker([p.lat, p.lon], { radius: 11, color: ZONE, weight: 2, dashArray: "3 3", fill: false })
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
  }, [sheet, swell, t]);

  if (!swell || !row || !t) return null;
  const n = t.near_takeoff;
  const shift = row.low_tide_seaward_m;
  const takeoffs = t.takeoffs.filter((p) => (p.stands_out_s ?? 0) >= 5).length;

  return (
    <section className="sheet" aria-label="How the spot breaks">
      <p className="kicker">How it breaks</p>
      <div className="sheet-map" ref={el} />

      {sheet.swells.length > 1 && (
        <div className="sheet-picks" role="group" aria-label="Swell">
          {sheet.swells.map((s, i) => (
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
            ? "—"
            : Math.abs(shift) < 5
              ? "breaks in about the same place"
              : `breaks ${Math.abs(Math.round(shift))} m further ${shift > 0 ? "out" : "in"}`}
        </dd>
      </dl>

      <ul className="sheet-legend">
        <li><i style={{ background: ZONE, opacity: 0.6 }} />breaks on most waves</li>
        <li><i className="dot" />takeoff at this swell</li>
        <li><i className="ring" />a peak of the reef at many swells</li>
        <li><i style={{ background: BREAK }} />break points, bigger dot = bigger wave</li>
        <li><i style={{ background: ROCK }} />reaches the rock unbroken</li>
      </ul>
      <p className="sheet-note">
        {sheet.note}. wavesim, run on {sheet.seabed.cell_m} m cells over the {sheet.seabed.source}; {sheet.generated}.
        Whether it barrels and which way it peels are left out until a finer seabed. Seabed:{" "}
        {sheet.seabed.attribution}.
      </p>
    </section>
  );
}

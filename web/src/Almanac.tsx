import { Link } from "react-router-dom";

import type { SpotAlmanac, TideNudge } from "./api/client";
import { clock } from "./format";

const metres = (m: number) => `${m.toFixed(2)} m`;

const CLASS_NOTE: Record<string, string> = {
  king: "king tides",
  spring: "springs",
  neap: "neaps",
};

/**
 * One spot's day as the tide table and the sky have it: first and last light, the
 * station's high and low waters (heights above chart datum, as printed tables give
 * them), and whether it is a big or a small tide.
 */
export function SpotAlmanacLine({ almanac }: { almanac: SpotAlmanac }) {
  const { daylight, tides, station, tide_class } = almanac;
  const note = tide_class ? CLASS_NOTE[tide_class] : undefined;
  return (
    <section className="almanac" aria-label="Tide and light">
      <ol className="tides">
        {tides.map((t) => (
          <li key={t.at} className={`tide tide-${t.kind}${t.king ? " king" : ""}`}>
            <span className="tide-kind">{t.kind === "high" ? "High" : "Low"}</span>
            <b>{clock(t.at_local)}</b>
            <small>{metres(t.height_m)}</small>
          </li>
        ))}
      </ol>
      <p className="almanac-note">
        Light {clock(daylight.civil_dawn_local)}–{clock(daylight.civil_dusk_local)} · sun{" "}
        {clock(daylight.sunrise_local)}–{clock(daylight.sunset_local)} · {station} tides
        {note ? <span className={`tide-class tc-${tide_class}`}> · {note}</span> : null}
      </p>
    </section>
  );
}

/**
 * A king tide in daylight, put to the person as a reason to go and look. The spots are
 * the ones whose tide gate shuts at this phase, where the most extreme tide of the
 * season is the hardest test of that call. Several, because the headline metric counts
 * same-day pairs and a lone label is not one.
 */
export function TideNudgeCard({ nudge, day }: { nudge: TideNudge; day: string }) {
  const first = nudge.tides[0];
  if (!first) return null;
  const phase = nudge.kind === "high" ? "high" : "low";
  const heights = nudge.tides.map((t, i) => `${metres(t.height_m)} at ${nudge.stations[i]}`);
  return (
    <section className="nudge" aria-label={`King ${phase} tide`}>
      <p className="kicker">
        King {phase} · {clock(first.at_local)}
      </p>
      <p className="nudge-text">
        One of the year's {phase === "high" ? "highest" : "lowest"} tides ({heights.join(", ")}). The
        score shuts these at {phase} tide: check two and log what you find, surfed or not.
      </p>
      <ul className="nudge-spots">
        {nudge.spots.map((s) => (
          <li key={s.id}>
            <Link to={`/log?spot=${s.id}&day=${day}`}>{s.name}</Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

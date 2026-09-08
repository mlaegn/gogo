import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { DayStrip } from "../DayStrip";
import { api } from "../api/client";
import { dayLabel, lisbonClock, lisbonNowLocal, nextClock, regionLabel, todayInLisbon } from "../format";
import { useAsync } from "../useAsync";

export function SpotScreen() {
  const { spotId } = useParams();
  const [params] = useSearchParams();
  const day = params.get("day") ?? undefined;
  const [openHour, setOpenHour] = useState<string | null>(null);
  const result = useAsync(
    () => (spotId ? api.spotDay(spotId, day) : Promise.reject(new Error("no spot"))),
    [spotId, day],
  );

  if (result.state === "loading") return <p className="sub">Loading…</p>;
  if (result.state === "error") {
    return (
      <>
        <h1 className="spot-title">Nothing to show</h1>
        <p className="sub">{result.message}</p>
      </>
    );
  }

  const { spot, day: shown, hours } = result.data;
  const today = todayInLisbon();
  const listTo = shown === today ? "/" : `/?day=${shown}`;
  const passing = hours.filter((h) => h.verdict !== "no");
  const first = passing[0];
  const last = passing[passing.length - 1];
  const nowLocal = shown === today ? lisbonNowLocal(shown) : undefined;
  const nowHour = shown === today ? lisbonClock().slice(0, 2) : null;
  const stripVerdict = passing.some((h) => h.verdict === "go")
    ? "go"
    : passing.length
      ? "maybe"
      : "no";
  const peak = passing.reduce<(typeof passing)[number] | undefined>(
    (best, hour) => (best && best.score >= hour.score ? best : hour),
    undefined,
  );

  return (
    <div className={result.state === "refreshing" ? "refreshing" : undefined}>
      <p className="crumb">
        <Link to={listTo}>← {dayLabel(shown, today)}</Link>
      </p>
      <h1 className="spot-title">{spot.name}</h1>
      <p className="sub spec">
        {regionLabel(spot.region)}
        <span>
          {spot.size_min_m}–{spot.size_max_m} m · {spot.swell_from_min}–{spot.swell_from_max}° ·
          off {spot.offshore_from}°
        </span>
      </p>

      <DayStrip
        startsLocal={
          first ? `${shown}T${first.at_local}:00` : `${shown}T06:00:00`
        }
        endsLocal={
          first && last ? `${shown}T${nextClock(last.at_local)}:00` : `${shown}T06:00:00`
        }
        peakLocal={peak ? `${shown}T${peak.at_local}:00` : undefined}
        nowLocal={nowLocal}
        verdict={stripVerdict}
        size="hero"
      />

      <ol className="hours">
        {hours.map((h) => {
          const open = openHour === h.valid_at;
          const isNow = nowHour !== null && h.at_local.startsWith(nowHour);
          return (
            <li
              key={h.valid_at}
              className={`v-${h.verdict}${open ? " open" : ""}${isNow ? " now" : ""}`}
            >
              <button
                type="button"
                className="hour-row"
                onClick={() => setOpenHour(open ? null : h.valid_at)}
                aria-expanded={open}
              >
                <span className="at">{h.at_local}</span>
                <span className="bar" aria-hidden="true">
                  <i style={{ width: `${h.score}%` }} />
                </span>
                <span className="score">{h.score}</span>
              </button>
              {open && (
                <p className="why">{h.reasons.map((r) => r.detail).join("; ")}</p>
              )}
            </li>
          );
        })}
      </ol>

      <Link className="cta" to={`/log?spot=${spot.id}&day=${shown}`}>
        Log a session here
      </Link>
    </div>
  );
}

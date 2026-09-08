import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import { dayLabel, regionLabel, todayInLisbon } from "../format";
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
  const listTo = shown === todayInLisbon() ? "/" : `/?day=${shown}`;

  return (
    <div className={result.state === "refreshing" ? "refreshing" : undefined}>
      <p className="crumb">
        <Link to={listTo}>← {dayLabel(shown, todayInLisbon())}</Link>
      </p>
      <h1 className="spot-title">{spot.name}</h1>
      <p className="sub">
        {regionLabel(spot.region)} · {spot.size_min_m}–{spot.size_max_m} m from{" "}
        {spot.swell_from_min}–{spot.swell_from_max}° · offshore {spot.offshore_from}°
      </p>

      <ol className="hours">
        {hours.map((h) => {
          const open = openHour === h.valid_at;
          return (
            <li key={h.valid_at} className={`v-${h.verdict}${open ? " open" : ""}`}>
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

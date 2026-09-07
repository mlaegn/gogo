import { Link, useParams, useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import { dayLabel, todayInLisbon } from "../format";
import { useAsync } from "../useAsync";

export function SpotScreen() {
  const { spotId } = useParams();
  const [params] = useSearchParams();
  const day = params.get("day") ?? undefined;
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

  return (
    <>
      <h1 className="spot-title">{spot.name}</h1>
      <p className="sub">
        {dayLabel(shown, todayInLisbon())} · {spot.region} · wants {spot.size_min_m}–
        {spot.size_max_m} m from {spot.swell_from_min}–{spot.swell_from_max}°, offshore{" "}
        {spot.offshore_from}°
      </p>

      <table className="hours">
        <tbody>
          {hours.map((h) => (
            <tr key={h.valid_at} className={`v-${h.verdict}`}>
              <td className="at">{h.at_local}</td>
              <td className="score">{h.score}</td>
              {/* Every term here, not the headline's summary: this is the "show me why" view. */}
              <td className="why">{h.reasons.map((r) => r.detail).join("; ")}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <Link className="cta" to={`/log?spot=${spot.id}&day=${shown}`}>
        Log a session here
      </Link>
    </>
  );
}

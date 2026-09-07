import { Link, useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import { clock, dayLabel, span, todayInLisbon, why } from "../format";
import { useAsync } from "../useAsync";

export function WindowsScreen() {
  const [params] = useSearchParams();
  const day = params.get("day") ?? undefined;
  const result = useAsync(() => api.windows(day), [day]);

  if (result.state === "loading") return <p className="sub">Reading the forecast…</p>;
  if (result.state === "error") {
    return (
      <>
        <h1 className="spot-title">Nothing to show</h1>
        <p className="sub">{result.message}</p>
      </>
    );
  }

  const { day: shown, days, windows, as_of } = result.data;
  const today = todayInLisbon();
  // Windows arrive ranked, so the first that is not a "no" is the day's call.
  const best = windows.find((w) => w.verdict !== "no");
  const rest = windows.filter((w) => w.spot_id !== best?.spot_id);

  return (
    <>
      <div className="days">
        {days.map((d) => (
          <Link
            key={d}
            className={`day${d === shown ? " on" : ""}`}
            to={d === today ? "/" : `/?day=${d}`}
          >
            {dayLabel(d, today)}
          </Link>
        ))}
      </div>

      {best ? (
        <>
          <Link className={`headline v-${best.verdict}`} to={`/spot/${best.spot_id}?day=${shown}`}>
            <div className="verdict">{best.verdict}</div>
            <h1>{best.spot_name}</h1>
            <div className="window">{span(best)}</div>
            <div className="meta">
              {best.score}
              {best.hours > 1 && ` · ${best.hours}h · best ${clock(best.peak_at_local)}`}
            </div>
            <p className="why">{why(best.reasons)}</p>
          </Link>
          <Link
            className="cta"
            to={`/log?spot=${best.spot_id}&day=${shown}&start=${clock(best.starts_at_local)}&end=${clock(best.ends_at_local)}`}
          >
            Been there? Log it
          </Link>
        </>
      ) : (
        <div className="headline v-no">
          <div className="verdict">no</div>
          <h1>Nowhere, {dayLabel(shown, today).toLowerCase()}</h1>
          <p className="why">
            Every spot is out. That is an answer too — and if it turns out to be wrong, log
            a session anyway so the veto gets caught.
          </p>
        </div>
      )}

      <ol className="ranked">
        {rest.map((w) => (
          <li key={w.spot_id} className={`v-${w.verdict}`}>
            <Link to={`/spot/${w.spot_id}?day=${shown}`}>
              <span className="score">{w.score}</span>
              <span className="name">{w.spot_name}</span>
              <span className="span">{w.verdict === "no" ? "—" : span(w)}</span>
            </Link>
          </li>
        ))}
      </ol>

      {as_of && <p className="asof">Forecast fetched {as_of.slice(5, 16).replace("T", " ")}</p>}
    </>
  );
}

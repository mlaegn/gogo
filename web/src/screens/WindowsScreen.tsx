import { Link, useSearchParams } from "react-router-dom";

import { DayStrip } from "../DayStrip";
import { api } from "../api/client";
import {
  clock,
  dayLabel,
  fetchedAt,
  lisbonNowLocal,
  regionLabel,
  span,
  todayInLisbon,
  why,
} from "../format";
import { useAsync } from "../useAsync";

export function WindowsScreen() {
  const [params] = useSearchParams();
  const day = params.get("day") ?? undefined;
  const result = useAsync(() => api.windows(day), [day]);

  if (result.state === "loading") {
    return (
      <>
        <div className="days skeleton-days" aria-hidden="true">
          <span className="day" />
          <span className="day" />
          <span className="day" />
        </div>
        <div className="headline skeleton-card" aria-busy="true">
          <p className="sub">Reading the forecast…</p>
        </div>
      </>
    );
  }
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
  const nowLocal = shown === today ? lisbonNowLocal(shown) : undefined;
  const best = windows.find((w) => w.verdict !== "no");
  const rest = windows.filter((w) => w.spot_id !== best?.spot_id);
  const refreshing = result.state === "refreshing";

  return (
    <div className={refreshing ? "refreshing" : undefined}>
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
            <div className="eyebrow">
              <span className="verdict">{best.verdict}</span>
              <span className="region">{regionLabel(best.region)}</span>
            </div>
            <div className="window">{span(best)}</div>
            <h1>{best.spot_name}</h1>
            <DayStrip
              startsLocal={best.starts_at_local}
              endsLocal={best.ends_at_local}
              peakLocal={best.peak_at_local}
              nowLocal={nowLocal}
              verdict={best.verdict}
              size="hero"
            />
            <div className="meta">
              <span className="score-pill">{best.score}</span>
              {best.hours > 1 && (
                <span>
                  {best.hours}h · peak {clock(best.peak_at_local)}
                </span>
              )}
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
          <div className="eyebrow">
            <span className="verdict">no</span>
          </div>
          <h1>Nowhere, {dayLabel(shown, today).toLowerCase()}</h1>
          <DayStrip
            startsLocal={`${shown}T06:00:00`}
            endsLocal={`${shown}T06:00:00`}
            nowLocal={nowLocal}
            verdict="no"
            size="hero"
          />
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
              <span className="place">
                <span className="who">
                  <span className="name">{w.spot_name}</span>
                  <span className="where">{regionLabel(w.region)}</span>
                </span>
                <DayStrip
                  startsLocal={w.starts_at_local}
                  endsLocal={w.ends_at_local}
                  peakLocal={w.peak_at_local}
                  nowLocal={nowLocal}
                  verdict={w.verdict}
                  size="row"
                />
              </span>
              <span className="span">{w.verdict === "no" ? "out" : span(w)}</span>
            </Link>
          </li>
        ))}
      </ol>

      {as_of && <p className="asof">Updated {fetchedAt(as_of)}</p>}
    </div>
  );
}

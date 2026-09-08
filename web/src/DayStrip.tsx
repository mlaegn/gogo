import type { Verdict } from "./api/client";

import { clock, localHour } from "./format";

/** Must match assemble.SURFABLE_FROM_HOUR / UNTIL_HOUR — the day the run finder searches. */
export const DAY_FROM = 6;
export const DAY_UNTIL = 20;
const SPAN = DAY_UNTIL - DAY_FROM;
const TICKS = [8, 10, 12, 14, 16, 18] as const;

export function windowOffsets(
  startsLocal: string,
  endsLocal: string,
): { left: number; width: number } | null {
  const start = Math.max(DAY_FROM, Math.min(DAY_UNTIL, localHour(startsLocal)));
  const end = Math.max(DAY_FROM, Math.min(DAY_UNTIL, localHour(endsLocal)));
  if (end <= start) return null;
  return {
    left: ((start - DAY_FROM) / SPAN) * 100,
    width: ((end - start) / SPAN) * 100,
  };
}

function alongDay(localIso: string): number {
  const hour = Math.max(DAY_FROM, Math.min(DAY_UNTIL, localHour(localIso)));
  return ((hour - DAY_FROM) / SPAN) * 100;
}

function inSurfableDay(localIso: string): boolean {
  const hour = localHour(localIso);
  return hour >= DAY_FROM && hour < DAY_UNTIL;
}

type Props = {
  startsLocal: string;
  endsLocal: string;
  peakLocal?: string;
  nowLocal?: string;
  verdict: Verdict;
  size?: "hero" | "row";
};

/**
 * The day's surfable hours as a strip, with the passing window lit.
 * This is the product, drawn: a window is a run of hours, not a timestamp.
 */
export function DayStrip({
  startsLocal,
  endsLocal,
  peakLocal,
  nowLocal,
  verdict,
  size = "row",
}: Props) {
  const lit = verdict !== "no" ? windowOffsets(startsLocal, endsLocal) : null;
  const peak = verdict !== "no" && peakLocal ? alongDay(peakLocal) : null;
  const now = nowLocal && inSurfableDay(nowLocal) ? alongDay(nowLocal) : null;
  const label =
    size === "hero"
      ? lit
        ? `${clock(startsLocal)} to ${clock(endsLocal)}, ${verdict}`
        : "no passing hours"
      : undefined;

  return (
    <div
      className={`strip strip-${size} v-${verdict}`}
      aria-hidden={size === "row" ? true : undefined}
      aria-label={label}
    >
      <div className="strip-frame">
        <div className="strip-track">
          {TICKS.map((hour) => (
            <i
              key={hour}
              className="strip-tick"
              style={{ left: `${((hour - DAY_FROM) / SPAN) * 100}%` }}
            />
          ))}
          {lit && (
            <i className="strip-lit" style={{ left: `${lit.left}%`, width: `${lit.width}%` }} />
          )}
        </div>
        {peak !== null && <b className="strip-peak" style={{ left: `${peak}%` }} />}
        {now !== null && <b className="strip-now" style={{ left: `${now}%` }} />}
      </div>
      {size === "hero" && (
        <div className="strip-scale">
          <span>06</span>
          <span>12</span>
          <span>20</span>
        </div>
      )}
    </div>
  );
}

import type { Daylight, Verdict } from "./api/client";

import { clock, localHour } from "./format";

/**
 * The span the strip draws: civil dawn to civil dusk, the hours the run finder searches
 * since score v5 (`assemble._is_surfable`). Without a daylight block from the server it
 * falls back to the old fixed 06–20, which is only ever a loading or error state.
 */
type Span = { from: number; until: number };

function spanOf(daylight?: Daylight | null): Span {
  if (!daylight) return { from: 6, until: 20 };
  return {
    from: localHour(daylight.civil_dawn_local),
    until: localHour(daylight.civil_dusk_local),
  };
}

function along(hour: number, s: Span): number {
  const clamped = Math.max(s.from, Math.min(s.until, hour));
  return ((clamped - s.from) / (s.until - s.from)) * 100;
}

export function windowOffsets(
  startsLocal: string,
  endsLocal: string,
  daylight?: Daylight | null,
): { left: number; width: number } | null {
  const s = spanOf(daylight);
  const left = along(localHour(startsLocal), s);
  const right = along(localHour(endsLocal), s);
  if (right <= left) return null;
  return { left, width: right - left };
}

/** Even hours strictly inside the span: 08, 10, … */
function ticks(s: Span): number[] {
  const out: number[] = [];
  for (let h = Math.ceil(s.from / 2) * 2; h < s.until; h += 2) {
    if (h > s.from) out.push(h);
  }
  return out;
}

type Props = {
  startsLocal: string;
  endsLocal: string;
  peakLocal?: string;
  nowLocal?: string;
  verdict: Verdict;
  daylight?: Daylight | null;
  size?: "hero" | "row";
};

/**
 * The day's light as a strip, with the passing window lit.
 * This is the product, drawn: a window is a run of hours, not a timestamp.
 */
export function DayStrip({
  startsLocal,
  endsLocal,
  peakLocal,
  nowLocal,
  verdict,
  daylight,
  size = "row",
}: Props) {
  const s = spanOf(daylight);
  const lit = verdict !== "no" ? windowOffsets(startsLocal, endsLocal, daylight) : null;
  const peak = verdict !== "no" && peakLocal ? along(localHour(peakLocal), s) : null;
  const nowHour = nowLocal ? localHour(nowLocal) : null;
  const now = nowHour !== null && nowHour >= s.from && nowHour < s.until ? along(nowHour, s) : null;
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
          {ticks(s).map((hour) => (
            <i key={hour} className="strip-tick" style={{ left: `${along(hour, s)}%` }} />
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
          <span>{daylight ? clock(daylight.civil_dawn_local) : "06"}</span>
          <span>12</span>
          <span>{daylight ? clock(daylight.civil_dusk_local) : "20"}</span>
        </div>
      )}
    </div>
  );
}

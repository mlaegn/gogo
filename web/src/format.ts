import type { Reason, Window } from "./api/client";

/**
 * The server sends both UTC and local for every instant, so these only ever slice the
 * pre-formatted local string. Nothing here constructs a Date from a wall clock, because
 * that is where "Saturday 08:00" quietly becomes 07:00.
 */
export const clock = (localIso: string) => localIso.slice(11, 16);

export const span = (w: Window) =>
  `${clock(w.starts_at_local)}–${clock(w.ends_at_local)}`;

/** The one-sentence why: failures always, plus the terms a person actually asks about. */
export function why(reasons: Reason[]): string {
  return reasons
    .filter((r) => r.points === 0 || ["wind", "size", "period"].includes(r.code))
    .map((r) => r.detail)
    .join("; ");
}

const WEEKDAY = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"] as const;

/** "Today" / "Tomorrow" / "Sat 12", from an ISO date, in local terms. */
export function dayLabel(iso: string, todayIso: string): string {
  if (iso === todayIso) return "Today";

  const [y, m, d] = iso.split("-").map(Number);
  const [ty, tm, td] = todayIso.split("-").map(Number);
  if (!y || !m || !d || !ty || !tm || !td) return iso;

  // Compared as UTC midnights so a DST change cannot shift the difference.
  const target = Date.UTC(y, m - 1, d);
  const today = Date.UTC(ty, tm - 1, td);
  if (target - today === 86_400_000) return "Tomorrow";

  const weekday = WEEKDAY[new Date(target).getUTCDay()] ?? "";
  return `${weekday} ${String(d).padStart(2, "0")}`;
}

/** Wall-clock HH:MM in Lisbon, optionally shifted, for form defaults. */
export const lisbonClock = (offsetHours = 0): string =>
  new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/Lisbon",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(Date.now() + offsetHours * 3_600_000));

export const todayInLisbon = (): string =>
  new Intl.DateTimeFormat("en-CA", {
    timeZone: "Europe/Lisbon",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());

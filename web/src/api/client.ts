import type { components } from "./schema";

/**
 * Types come from FastAPI's OpenAPI schema via `npm run types`, so a field renamed in
 * Python fails the frontend build instead of rendering blank. CI regenerates and diffs
 * this file to catch the two drifting apart.
 */
type Schemas = components["schemas"];

export type Window = Schemas["WindowOut"];
export type Windows = Schemas["WindowsOut"];
export type Days = Schemas["DaysOut"];
export type Spot = Schemas["SpotOut"];
export type SpotDay = Schemas["SpotDayOut"];
export type Hour = Schemas["HourOut"];
export type Reason = Schemas["ReasonOut"];
export type NewObservation = Schemas["ObservationIn"];
export type SavedObservation = Schemas["ObservationOut"];
export type Verdict = Window["verdict"];
export type FaultCode = NonNullable<NewObservation["faults"]>[number];
export type CrowdReport = NonNullable<NewObservation["crowd"]>;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function detail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object" && "detail" in body) {
      const value = (body as { detail: unknown }).detail;
      if (typeof value === "string") return value;
      return JSON.stringify(value);
    }
  } catch {
    // A non-JSON error body is not worth a second failure.
  }
  return `${response.status} ${response.statusText}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });

  // The cookie is httponly, so the app cannot inspect it and only finds out here. The
  // login form is server-rendered, so hand the whole page back to the server.
  if (response.status === 401) {
    window.location.assign("/enter");
    throw new ApiError(401, "Not signed in");
  }
  if (!response.ok) throw new ApiError(response.status, await detail(response));
  return (await response.json()) as T;
}

const withDay = (path: string, day?: string) =>
  day ? `${path}?day=${encodeURIComponent(day)}` : path;

export const api = {
  windows: (day?: string) => request<Windows>(withDay("/api/windows", day)),
  days: () => request<Days>("/api/days"),
  /** No scores in this payload; see SpotOut in schemas.py for why that is deliberate. */
  spots: () => request<Spot[]>("/api/spots"),
  spotDay: (spotId: string, day?: string) =>
    request<SpotDay>(withDay(`/api/spots/${encodeURIComponent(spotId)}`, day)),
  logSession: (body: NewObservation) =>
    request<SavedObservation>("/api/observations", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};

import { useEffect, useState } from "react";

import { ApiError } from "./api/client";

export type Async<T> =
  | { state: "loading" }
  | { state: "error"; message: string; status?: number }
  | { state: "ready"; data: T };

/**
 * Enough data fetching for four screens. No cache and no retries on purpose: every
 * screen here reads once, and a query library would be more machinery than the whole
 * app. If offline label queueing lands, this is the seam to replace.
 */
export function useAsync<T>(load: () => Promise<T>, deps: readonly unknown[]): Async<T> {
  const [result, setResult] = useState<Async<T>>({ state: "loading" });

  useEffect(() => {
    let live = true;
    setResult({ state: "loading" });
    load()
      .then((data) => {
        if (live) setResult({ state: "ready", data });
      })
      .catch((error: unknown) => {
        if (!live) return;
        if (error instanceof ApiError) {
          setResult({ state: "error", message: error.message, status: error.status });
        } else {
          setResult({ state: "error", message: String(error) });
        }
      });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return result;
}

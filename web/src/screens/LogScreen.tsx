import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import type {
  CrowdReport,
  FaultCode,
  NewObservation,
  SavedObservation,
} from "../api/client";
import { ApiError, api } from "../api/client";
import { lisbonClock, regionLabel, todayInLisbon } from "../format";
import { useAsync } from "../useAsync";

const RATINGS = [
  [1, "awful"],
  [2, "poor"],
  [3, "ok"],
  [4, "good"],
  [5, "epic"],
] as const;

const RETURN_CHOICES: ReadonlyArray<readonly [boolean, string]> = [
  [true, "yes"],
  [false, "no"],
];

const CROWDS: ReadonlyArray<readonly [CrowdReport | "", string]> = [
  ["", "not saying"],
  ["empty", "empty"],
  ["ok", "ok"],
  ["busy", "busy"],
  ["zoo", "zoo"],
];

// A fault names the gate we got wrong. The card only offers "worse than you said",
// because that is the direction a person volunteers; `gogo log` can record either.
const FAULTS: ReadonlyArray<readonly [FaultCode, string]> = [
  ["size", "smaller / bigger than you said"],
  ["wind", "wind was worse"],
  ["period", "no power"],
  ["swell_dir", "wrong direction"],
  ["tide", "wrong tide"],
];

function Reveal({
  saved,
  onAgain,
}: {
  saved: SavedObservation;
  onAgain: () => void;
}) {
  return (
    <div className="notebook-page">
      {saved.duplicate ? (
        <>
          <h1 className="spot-title">Already logged</h1>
          <p className="sub">
            {saved.spot_name}, {saved.day} {saved.span} is on record. Nothing was changed —
            one session is one label.
          </p>
        </>
      ) : (
        <>
          <h1 className="spot-title">Saved</h1>
          <p className="sub">
            {saved.spot_name}, {saved.day} {saved.span} — you said{" "}
            <strong>{saved.rating}</strong>.
          </p>
        </>
      )}

      <div className="reveal">
        {saved.shown ? (
          <>
            <p>
              We had said <strong>{saved.shown.score}</strong> ({saved.shown.verdict}).
            </p>
            <p className="sub">Your rating was recorded before you saw this.</p>
          </>
        ) : (
          <>
            <p>We had nothing on screen for that session.</p>
            <p className="sub">
              It still counts for whether the score reads conditions right, but it cannot
              say whether we would have sent you to the right place.
            </p>
          </>
        )}
      </div>

      <button type="button" className="cta" onClick={onAgain}>
        Log another spot this day
      </button>
      <Link className="cta quiet" to="/">
        Back to today
      </Link>
    </div>
  );
}

export function LogScreen() {
  const [params] = useSearchParams();

  // The only fetch on this screen, and it carries no scores. Our prediction is not on
  // the device while you decide how it went — see SpotOut in schemas.py.
  const spots = useAsync(() => api.spots(), []);

  // Rating is held apart from the rest and starts unset, so there is no moment where a
  // half-filled card claims someone rated a session 0.
  const [rating, setRating] = useState<number | null>(null);
  const [form, setForm] = useState<Omit<NewObservation, "rating">>({
    spot_id: params.get("spot") ?? "",
    day: params.get("day") ?? todayInLisbon(),
    start: params.get("start") ?? lisbonClock(-2),
    end: params.get("end") ?? lisbonClock(),
    kind: "surfed",
    would_return: null,
    crowd: null,
    faults: [],
    note: null,
  });
  const [saved, setSaved] = useState<SavedObservation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  if (saved) {
    return (
      <Reveal
        saved={saved}
        onAgain={() => {
          setSaved(null);
          setRating(null);
          setError(null);
          setForm((prev) => ({
            ...prev,
            spot_id: "",
            kind: "surfed",
            would_return: null,
            crowd: null,
            faults: [],
            note: null,
          }));
        }}
      />
    );
  }

  type Field = Omit<NewObservation, "rating">;
  const set = <K extends keyof Field>(key: K, value: Field[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  const toggleFault = (code: FaultCode) =>
    setForm((prev) => ({
      ...prev,
      faults: prev.faults?.includes(code)
        ? prev.faults.filter((c) => c !== code)
        : [...(prev.faults ?? []), code],
    }));

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (rating === null) {
      setError("Pick how the waves were.");
      return;
    }
    setError(null);
    setSending(true);
    try {
      setSaved(await api.logSession({ ...form, rating }));
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "Could not save that. Try again.",
      );
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="notebook-page">
      <p className="kicker">Leave the forecast behind</p>
      <h1 className="spot-title">How was it?</h1>
      <p className="sub">
        Answer before you look at what we predicted — that is the whole point. We show you
        our number after you save.
      </p>

      {error && <p className="error">{error}</p>}

      <form onSubmit={submit}>
        <label>
          Spot
          <select
            name="spot"
            required
            value={form.spot_id}
            onChange={(e) => set("spot_id", e.target.value)}
          >
            <option value="">choose…</option>
            {spots.state === "ready" &&
              (["ericeira", "lisbon", "peniche"] as const).map((region) => (
                <optgroup key={region} label={regionLabel(region)}>
                  {spots.data
                    .filter((s) => s.region === region)
                    .map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                </optgroup>
              ))}
          </select>
        </label>

        <div className="row">
          <label>
            Day
            <input
              type="date"
              required
              value={form.day}
              onChange={(e) => set("day", e.target.value)}
            />
          </label>
          <label>
            From
            <input
              type="time"
              required
              value={form.start}
              onChange={(e) => set("start", e.target.value)}
            />
          </label>
          <label>
            To
            <input
              type="time"
              required
              value={form.end}
              onChange={(e) => set("end", e.target.value)}
            />
          </label>
        </div>

        <label>
          What happened
          <select
            value={form.kind ?? "surfed"}
            onChange={(e) => set("kind", e.target.value as NewObservation["kind"])}
          >
            <option value="surfed">I surfed</option>
            <option value="checked">I looked and did not go in</option>
            <option value="cam">I saw it on a cam</option>
          </select>
        </label>

        <fieldset className="rating">
          <legend>How were the waves?</legend>
          {RATINGS.map(([value, word]) => (
            <label className="pick" key={value}>
              <input
                type="radio"
                name="rating"
                required
                checked={rating === value}
                onChange={() => setRating(value)}
              />
              <span>
                {value}
                <small>{word}</small>
              </span>
            </label>
          ))}
        </fieldset>

        <fieldset className="inline">
          <legend>Go back in the same conditions?</legend>
          {RETURN_CHOICES.map(([value, word]) => (
            <label className="pick wide" key={word}>
              <input
                type="radio"
                name="would_return"
                checked={form.would_return === value}
                onChange={() => set("would_return", value)}
              />
              <span>{word}</span>
            </label>
          ))}
        </fieldset>

        <details className="optional">
          <summary>Crowd, faults, note</summary>

          <label>
            Crowd
            <select
              value={form.crowd ?? ""}
              onChange={(e) => set("crowd", (e.target.value || null) as CrowdReport | null)}
            >
              {CROWDS.map(([value, word]) => (
                <option key={value} value={value}>
                  {word}
                </option>
              ))}
            </select>
          </label>

          <fieldset className="faults">
            <legend>Anything we got wrong?</legend>
            {FAULTS.map(([code, word]) => (
              <label className="check" key={code}>
                <input
                  type="checkbox"
                  checked={form.faults?.includes(code) ?? false}
                  onChange={() => toggleFault(code)}
                />
                <span>{word}</span>
              </label>
            ))}
          </fieldset>

          <label>
            Note
            <textarea
              rows={2}
              placeholder="sandbar shifted, only worked on the push…"
              value={form.note ?? ""}
              onChange={(e) => set("note", e.target.value || null)}
            />
          </label>
        </details>

        <button type="submit" disabled={sending}>
          {sending ? "Saving…" : "Save"}
        </button>
      </form>
    </div>
  );
}

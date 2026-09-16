"""One command, one number, and the working behind it written down.

The Stage 2 gate is that `make backtest` prints pairwise accuracy with an interval, for
the current score against every baseline, under both as-of policies. This is that.

Three properties it has to have, and each is easy to lose.

**Deterministic.** The same arguments over the same database produce byte-identical
output. That is why the bootstrap seed is an argument rather than a clock, and why the
report body carries no timestamp: the point of a diffable report is that changing the
score shows up as the numbers that moved and nothing else.

**Offline.** Nothing here touches Open-Meteo. Everything comes from what the worker
already stored, which is also the only way a backtest can be honest about lead time.

**Unable to damage the record.** Runs write to `eval_runs` and `eval_predictions` and
nothing else. `window_impressions` is what was actually served and an experiment must
never be able to edit it, which is why these are separate tables rather than columns.

On spec modes. `current` scores old days with today's spot file, which is what you want
when asking "is the score I have now any good". `as_of` reproduces what a spot looked
like at the time, and can only reach back as far as `spot_specs` does — anything older
is genuinely gone, and this says so rather than quietly substituting today's. `pinned`
applies one recorded version to every day, which is how a proposal gets tested against
history before it ships.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import psycopg
from psycopg.types.json import Jsonb

from gogo.eval import baselines as base
from gogo.eval import metrics as metric
from gogo.eval.dataset import Dataset, build_dataset
from gogo.features import BEST_KNOWN, EVENING_BEFORE, LEAD_24H, POLICIES
from gogo.models import Spot
from gogo.score import SCORE_VERSION
from gogo.spots import load_spots
from gogo.store import load_spec_versions

#: Both questions, every run. Reporting only one is how a harness ends up answering the
#: easy one by habit: `best_known` asks whether the score reads the water right, the
#: other two ask whether we would have said so in time.
DEFAULT_POLICIES = (BEST_KNOWN, EVENING_BEFORE, LEAD_24H)

CURRENT = "current"
AS_OF = "as_of"
PINNED = "pinned"

#: Fixed by default so two runs are comparable. Override it to see how much of a
#: difference between runs is just the resampling.
DEFAULT_SEED = 20260915


@dataclass(frozen=True)
class SpecResolution:
    spots: list[Spot]
    mode: str
    #: Spot ids whose historical spec was asked for and not found. Named rather than
    #: counted, because "we silently used today's spec for Coxos" is the kind of detail
    #: that turns a backtest into fiction.
    unresolved: list[str] = field(default_factory=list)
    #: Which spot a `pinned:` mode actually swapped. The rest stay on the current file
    #: on purpose, so a proposal is measured with everything else held still.
    pinned_spot: str | None = None


def resolve_spots(
    conn: psycopg.Connection, spec_mode: str = CURRENT
) -> SpecResolution:
    """Which version of each spot's definition this run should score with."""
    spots = load_spots()
    if spec_mode == CURRENT:
        return SpecResolution(spots=spots, mode=CURRENT)

    if spec_mode == AS_OF:
        # There is no per-day spec history, only a set of versions ever seen. Reproducing
        # history exactly needs a valid_from column that Stage 4 introduces for overlays;
        # until then this means "the oldest spec we have on record", which is the closest
        # honest thing and is stated as such in the report.
        known = load_spec_versions(conn)
        oldest: dict[str, dict] = {}
        for (spot_id, _), spec in known.items():
            oldest.setdefault(spot_id, spec)
        resolved, missing = [], []
        for spot in spots:
            if spot.id in oldest:
                resolved.append(Spot.model_validate(oldest[spot.id]))
            else:
                resolved.append(spot)
                missing.append(spot.id)
        return SpecResolution(spots=resolved, mode=AS_OF, unresolved=missing)

    if spec_mode.startswith(f"{PINNED}:"):
        # A spec version is a digest of *one* spot, so a pin names one spot and leaves
        # the other fifteen on the current file. That is the Stage 3 shape anyway: you
        # change what Coxos needs and ask whether that one change helps, with everything
        # else held still.
        wanted = spec_mode.split(":", 1)[1]
        known = load_spec_versions(conn, {wanted})
        if not known:
            return SpecResolution(
                spots=spots, mode=spec_mode, unresolved=[f"version {wanted} not on record"]
            )
        pinned_id, spec = next(iter(known.items()))[0][0], next(iter(known.values()))
        return SpecResolution(
            spots=[
                Spot.model_validate(spec) if spot.id == pinned_id else spot
                for spot in spots
            ],
            mode=spec_mode,
            pinned_spot=pinned_id,
        )

    raise ValueError(
        f"unknown spec mode {spec_mode!r}; expected {CURRENT!r}, {AS_OF!r} "
        f"or '{PINNED}:<version>'"
    )


@dataclass(frozen=True)
class PolicyResult:
    policy: str
    data: Dataset
    accuracy: dict[str, metric.Estimate]
    veto: metric.VetoQuality
    ndcg: metric.Estimate
    brier: metric.Estimate
    calibration: list[metric.Bucket]


@dataclass(frozen=True)
class Backtest:
    score_version: str
    spec: SpecResolution
    seed: int
    synthetic: bool
    from_day: date | None
    to_day: date | None
    results: list[PolicyResult]


def run(
    conn: psycopg.Connection,
    policies: tuple[str, ...] = DEFAULT_POLICIES,
    spec_mode: str = CURRENT,
    seed: int = DEFAULT_SEED,
    only_synthetic: bool = False,
    from_day: date | None = None,
    to_day: date | None = None,
) -> Backtest:
    """Build the dataset under each policy and measure it against every baseline."""
    for policy in policies:
        if policy not in POLICIES:
            raise ValueError(f"unknown as-of policy {policy!r}; expected {POLICIES}")

    spec = resolve_spots(conn, spec_mode)
    suite = base.standard(spec.spots)
    results = []
    for policy in policies:
        data = build_dataset(
            conn, policy, only_synthetic=only_synthetic, spots=spec.spots
        )
        if from_day or to_day:
            kept = [
                s
                for s in data.samples
                if (from_day is None or s.day >= from_day)
                and (to_day is None or s.day <= to_day)
            ]
            data = Dataset(policy=policy, samples=kept, dropped=data.dropped)
        results.append(
            PolicyResult(
                policy=policy,
                data=data,
                accuracy=metric.compare(data, suite, seed),
                veto=metric.veto_quality(data, seed),
                ndcg=metric.ndcg_at_3(data, base.incumbent, seed),
                brier=metric.brier_would_return(data, base.incumbent, seed),
                calibration=metric.calibration(data),
            )
        )

    return Backtest(
        score_version=SCORE_VERSION,
        spec=spec,
        seed=seed,
        synthetic=only_synthetic,
        from_day=from_day,
        to_day=to_day,
        results=results,
    )


def report(result: Backtest) -> str:
    """Markdown, deliberately free of anything that changes between identical runs.

    No wall clock, no row ids, no dictionary iteration order. Two runs of the same
    arguments diff to nothing; a score change diffs to exactly the numbers it moved.
    """
    out = ["# Backtest", ""]
    out.append(f"- score: `{result.score_version}`")
    out.append(f"- spec mode: `{result.spec.mode}`")
    out.append(f"- seed: `{result.seed}`")
    if result.from_day or result.to_day:
        out.append(f"- range: `{result.from_day or '...'}` .. `{result.to_day or '...'}`")
    if result.spec.pinned_spot:
        out.append(
            f"- pinned `{result.spec.pinned_spot}` to that spec; every other spot is on "
            "the current file, so this measures one change and nothing else"
        )
    if result.spec.unresolved:
        out.append(
            f"- **spec not on record**, scored with today's instead: "
            f"{', '.join(sorted(result.spec.unresolved))}"
        )
    if result.synthetic:
        out += [
            "",
            "> **SYNTHETIC LABELS.** These ratings were generated from this same score, "
            "so every number below measures our own assumptions. Never quote it as a "
            "result; the only legitimate use is checking the harness reacts.",
        ]

    for policy in result.results:
        data = policy.data
        out += ["", f"## {policy.policy}", ""]
        out.append(
            f"{len(data)} samples, {data.events} events, {data.pairs} same-day pairs"
        )
        for reason, count in sorted(data.dropped.items()):
            out.append(f"- dropped {count}: {reason}")

        out += ["", "### Pairwise ranking accuracy", "",
                "| predictor | accuracy | 95% interval | pairs | events |",
                "|---|---|---|---|---|"]
        for name in sorted(policy.accuracy):
            est = policy.accuracy[name]
            value = "n/a" if est.value is None else f"{est.value:.3f}"
            interval = (
                "n/a" if est.lo is None else f"{est.lo:.3f} – {est.hi:.3f}"
            )
            mark = " **(incumbent)**" if name == "incumbent" else ""
            out.append(f"| `{name}`{mark} | {value} | {interval} | {est.n} | {est.events} |")

        out += ["", "### Other metrics", "",
                "| metric | value | n |", "|---|---|---|"]
        for label, est in (
            ("veto precision", policy.veto.precision),
            ("veto recall", policy.veto.recall),
            ("ndcg@3", policy.ndcg),
            ("brier (would return)", policy.brier),
        ):
            value = "n/a" if est.value is None else f"{est.value:.3f}"
            out.append(f"| {label} | {value} | {est.n} |")

        if policy.calibration:
            out += ["", "### Calibration", "",
                    "| predicted | n | mean rating |", "|---|---|---|"]
            for band in policy.calibration:
                out.append(f"| {band.low}–{band.high} | {band.n} | {band.mean_rating:.2f} |")

    out += ["", "---", ""]
    headline = next(
        (p for p in result.results if p.policy == BEST_KNOWN), result.results[0]
    )
    incumbent = headline.accuracy.get("incumbent")
    if incumbent is None or incumbent.value is None:
        out.append(
            "**No comparable pairs.** Not a bad score — no evidence either way. The "
            "headline metric needs two spots judged differently on one day."
        )
    else:
        best_baseline = max(
            (
                (name, est)
                for name, est in headline.accuracy.items()
                if name != "incumbent" and est.value is not None
            ),
            key=lambda kv: kv[1].value,
            default=None,
        )
        if best_baseline and incumbent.value <= best_baseline[1].value:
            out.append(
                f"**The score does not beat `{best_baseline[0]}`** "
                f"({incumbent.value:.3f} vs {best_baseline[1].value:.3f}). "
                "That is the finding, not a failure."
            )
        else:
            out.append(f"Incumbent leads on `{BEST_KNOWN}`: {incumbent}.")
    return "\n".join(out) + "\n"


def store(conn: psycopg.Connection, result: Backtest) -> list[int]:
    """Persist each policy's run and its per-label detail. Returns the run ids."""
    ids = []
    with conn.cursor() as cur:
        for policy in result.results:
            metrics_json = {
                "pairwise": {
                    name: est.value for name, est in sorted(policy.accuracy.items())
                },
                "pairwise_lo": {
                    name: est.lo for name, est in sorted(policy.accuracy.items())
                },
                "pairwise_hi": {
                    name: est.hi for name, est in sorted(policy.accuracy.items())
                },
                "veto_precision": policy.veto.precision.value,
                "veto_recall": policy.veto.recall.value,
                "ndcg_at_3": policy.ndcg.value,
                "brier_would_return": policy.brier.value,
            }
            cur.execute(
                """
                INSERT INTO eval_runs
                    (score_version, spec_mode, as_of_policy, from_day, to_day,
                     synthetic, samples, events, pairs, metrics, seed)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    result.score_version,
                    result.spec.mode,
                    policy.policy,
                    result.from_day,
                    result.to_day,
                    result.synthetic,
                    len(policy.data),
                    policy.data.events,
                    policy.data.pairs,
                    Jsonb(metrics_json),
                    result.seed,
                ),
            )
            run_id = cur.fetchone()["id"]
            ids.append(run_id)
            for sample in policy.data.samples:
                cur.execute(
                    """
                    INSERT INTO eval_predictions
                        (run_id, observation_id, spot_id, day, event_id,
                         predicted_score, predicted_verdict, rating)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id, observation_id) DO NOTHING
                    """,
                    (
                        run_id,
                        sample.observation_id,
                        sample.spot_id,
                        sample.day,
                        sample.event_id,
                        sample.predicted_score,
                        sample.predicted_verdict,
                        sample.rating,
                    ),
                )
    conn.commit()
    return ids

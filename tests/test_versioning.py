from gogo.models import Spot
from gogo.spots import load_spots
from gogo.versioning import canonical_spec, spec_version


def _spot(**kwargs) -> Spot:
    base = dict(
        id="fixture",
        name="Fixture Reef",
        lat=38.9,
        lon=-9.4,
        region="ericeira",
        swell_from_min=250,
        swell_from_max=330,
        size_min_m=0.8,
        size_max_m=3.2,
        period_min_s=8,
        offshore_from=80,
        max_onshore_kn=12,
        tides=["high", "mid"],
        skill_min="intermediate",
        skill_max="advanced",
        crowd="high",
    )
    base.update(kwargs)
    return Spot.model_validate(base)


def test_digest_is_pinned():
    """The canary. If this fails, every stored row pointing at an old version is orphaned.

    Changing it is allowed — deliberately, with a note in docs/plan.md — never by accident.
    """
    assert spec_version(_spot()) == "9cfd22ce1fe4"


def test_numbers_are_normalised_by_the_declared_types():
    """`period_min_s: 8` in YAML and `8.0` are the same spec."""
    assert '"period_min_s":8.0' in canonical_spec(_spot(period_min_s=8))
    assert spec_version(_spot(period_min_s=8)) == spec_version(_spot(period_min_s=8.0))


def test_renaming_a_spot_keeps_its_version():
    """Ribeira d'Ilhas does not break differently because we spelled it differently."""
    assert spec_version(_spot(name="Something Else")) == spec_version(_spot())


def test_tide_order_does_not_matter():
    assert spec_version(_spot(tides=["mid", "high"])) == spec_version(
        _spot(tides=["high", "mid"])
    )


def test_behaviour_changes_change_the_version():
    unchanged = spec_version(_spot())
    assert spec_version(_spot(tides=["low", "mid"])) != unchanged
    assert spec_version(_spot(size_max_m=2.8)) != unchanged
    assert spec_version(_spot(max_onshore_kn=15)) != unchanged
    assert spec_version(_spot(lat=39.0)) != unchanged


def test_every_spot_has_a_distinct_version():
    spots = load_spots()
    versions = {spot.id: spec_version(spot) for spot in spots}
    assert len(set(versions.values())) == len(spots)
    assert all(len(v) == 12 for v in versions.values())

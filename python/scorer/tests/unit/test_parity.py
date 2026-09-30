import numpy as np
import pytest
from scorer_support import observation_records, stay_frame
from wardwatch_ml.bundle import ServingBundle
from wardwatch_ml.features import build_features
from wardwatch_scorer.engine import ScoringEngine
from wardwatch_scorer.online import OnlineScorer

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("stay", ["p000001.psv", "p000009.psv", "p000004.psv"])
def test_online_scores_equal_offline_scores(bundle: ServingBundle, stay: str) -> None:
    """Replaying a stay as Observations gives the offline model's numbers."""
    frame = stay_frame(stay)
    now = [0.0]
    scoring = ScoringEngine(
        OnlineScorer(bundle), settle_seconds=1.0, monotonic=lambda: now[0], record_scores=True
    )
    for record in observation_records(frame, "E"):
        now[0] += 0.01
        scoring.ingest(record)
    now[0] += 10
    scoring.tick()

    online = {score.icu_hour: score for _, score in scoring.scored}
    offline = build_features(frame, bundle.spec).set_index("hour")
    hours = sorted(online)
    assert hours == list(range(1, max(hours) + 1))
    offline_rows = offline.loc[hours].reset_index()
    offline_margin = bundle.margin(offline_rows)
    offline_probability = bundle.calibrator.probability(offline_margin)
    online_margin = np.array([online[h].raw_score for h in hours])
    online_probability = np.array([online[h].calibrated_probability for h in hours])
    np.testing.assert_allclose(online_margin, offline_margin, rtol=0, atol=1e-6)
    np.testing.assert_allclose(online_probability, offline_probability, rtol=0, atol=1e-6)
    assert [online[h].news2_total for h in hours] == offline_rows["news2_total"].astype(
        int
    ).tolist()
    names = bundle.spec.feature_names()
    online_features = np.vstack([online[h].features[names].to_numpy() for h in hours])
    np.testing.assert_allclose(
        online_features, offline_rows[names].to_numpy(), rtol=0, atol=1e-9, equal_nan=True
    )

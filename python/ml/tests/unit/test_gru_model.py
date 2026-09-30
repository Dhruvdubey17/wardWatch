from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import torch
from wardwatch_ml.data import LABEL, load_site
from wardwatch_ml.features import FeatureSpec, build_features
from wardwatch_ml.gru_model import (
    GruParams,
    GruScaler,
    SepsisGru,
    masked_loss,
    pad_batch,
    predict_logits,
    to_sequences,
    train_gru,
)

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "physionet"
SPEC = FeatureSpec()


@pytest.fixture(scope="module")
def features() -> pd.DataFrame:
    frame = load_site(FIXTURES / "training_setA", "A")
    table = build_features(frame, SPEC)
    table[LABEL] = frame[LABEL].to_numpy()
    return table


def test_scaler_uses_training_statistics(features: pd.DataFrame) -> None:
    scaler = GruScaler.fit(features, SPEC)
    heart_rate = features["HR_last"].dropna()
    assert scaler.means[0] == pytest.approx(heart_rate.mean())
    matrix = scaler.transform(features)
    assert matrix.shape == (len(features), 3 * len(SPEC.variables))
    assert matrix.dtype == np.float32
    assert not np.isnan(matrix).any()
    # Scaled heart rate over the observed rows is centred on zero.
    observed = ~features["HR_last"].isna().to_numpy()
    assert matrix[observed, 0].mean() == pytest.approx(0.0, abs=1e-5)
    # Hours since last is 1 where blank (never seen or past the cap).
    assert set(np.unique(matrix[~observed, 2])) <= {1.0}


def test_sequences_keep_row_positions(features: pd.DataFrame) -> None:
    sequences = to_sequences(features, GruScaler.fit(features, SPEC))
    assert len(sequences.inputs) == 9
    assert sum(len(rows) for rows in sequences.row_index) == len(features)
    assert sorted(np.concatenate(sequences.row_index).tolist()) == list(range(len(features)))
    assert sequences.inputs[0].shape == (40, 39)


def test_padding_and_mask_shapes() -> None:
    inputs = [np.ones((3, 2), dtype=np.float32), np.ones((5, 2), dtype=np.float32)]
    labels = [np.zeros(3, dtype=np.float32), np.ones(5, dtype=np.float32)]
    batch, targets, mask = pad_batch(inputs, labels)
    assert batch.shape == (2, 5, 2)
    assert targets.shape == mask.shape == (2, 5)
    assert mask.sum().item() == 8
    assert batch[0, 3:].abs().sum().item() == 0


def test_padded_hours_do_not_change_the_loss() -> None:
    torch.manual_seed(0)
    model = SepsisGru(2, 4)
    inputs = [np.random.default_rng(1).normal(size=(4, 2)).astype(np.float32)]
    labels = [np.array([0, 0, 1, 1], dtype=np.float32)]
    alone = masked_loss(model(pad_batch(inputs, labels)[0]), *pad_batch(inputs, labels)[1:], 2.0)
    longer_inputs = [*inputs, np.zeros((9, 2), dtype=np.float32)]
    longer_labels = [*labels, np.zeros(9, dtype=np.float32)]
    batch, targets, mask = pad_batch(longer_inputs, longer_labels)
    logits = model(batch)
    first_only = masked_loss(logits[:1, :4], targets[:1, :4], mask[:1, :4], 2.0)
    assert first_only.item() == pytest.approx(alone.item(), rel=1e-6)


def test_outputs_are_causal() -> None:
    torch.manual_seed(0)
    model = SepsisGru(3, 8).eval()
    sequence = torch.randn(1, 10, 3)
    altered = sequence.clone()
    altered[0, 6:] = torch.randn(4, 3)
    with torch.no_grad():
        before = model(sequence)[0, :6]
        after = model(altered)[0, :6]
    torch.testing.assert_close(before, after)


def test_loss_decreases_over_a_few_steps(features: pd.DataFrame) -> None:
    params = GruParams(hidden_size=16, learning_rate=0.01, epochs=30, batch_size=9, threads=1)
    result = train_gru(features, features, SPEC, params)
    first, last = np.mean(result.losses[:3]), np.mean(result.losses[-3:])
    assert last < first
    predictions = predict_logits(result.model, to_sequences(features, result.scaler), len(features))
    assert not np.isnan(predictions).any()


def test_smoke_training_stops_after_max_steps(features: pd.DataFrame) -> None:
    params = GruParams(hidden_size=8, epochs=5, batch_size=3, max_steps=4, threads=1)
    result = train_gru(features, features, SPEC, params)
    assert len(result.losses) == 4
    assert len(result.validation_auprc) == 2


def test_training_is_deterministic(features: pd.DataFrame) -> None:
    params = GruParams(hidden_size=8, epochs=2, batch_size=4, threads=1)
    first = train_gru(features, features, SPEC, params)
    second = train_gru(features, features, SPEC, params)
    assert first.losses == second.losses

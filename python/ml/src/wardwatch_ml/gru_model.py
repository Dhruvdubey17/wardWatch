"""A small GRU over hourly sequences of values, masks and time since last observation.

Inputs per hour and variable: the staleness-limited last value standardized
with training-site statistics (0 when blank), whether it was measured this
hour, and hours since the last value divided by the cap (1 when blank). The
GRU runs forward in time only, so the output at hour t depends on hours 1..t.
"""

import math
from dataclasses import asdict, dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd
import torch
from sklearn.metrics import average_precision_score
from torch import nn

from wardwatch_ml.data import LABEL
from wardwatch_ml.features import FeatureSpec

STAY_KEYS = ["site", "patient_id"]


@dataclass(frozen=True)
class GruParams:
    hidden_size: int = 64
    learning_rate: float = 1e-3
    epochs: int = 8
    batch_size: int = 64
    max_steps: int | None = None
    seed: int = 2019
    threads: int = 4


@dataclass(frozen=True)
class GruScaler:
    """Per-variable mean and standard deviation of last values, from the training site."""

    variables: tuple[str, ...]
    means: tuple[float, ...]
    stds: tuple[float, ...]
    hours_since_cap: int

    @classmethod
    def fit(cls, features: pd.DataFrame, spec: FeatureSpec) -> "GruScaler":
        means, stds = [], []
        for variable in spec.variables:
            values = features[f"{variable}_last"].to_numpy(dtype=np.float64)
            present = values[~np.isnan(values)]
            mean = float(present.mean()) if len(present) else 0.0
            std = float(present.std()) if len(present) > 1 else 1.0
            means.append(mean)
            stds.append(std if std > 0 else 1.0)
        return cls(spec.variables, tuple(means), tuple(stds), spec.hours_since_cap)

    def transform(self, features: pd.DataFrame) -> npt.NDArray[np.float32]:
        """A (rows, 3 * variables) matrix of scaled value, mask and time since."""
        columns = []
        for variable, mean, std in zip(self.variables, self.means, self.stds, strict=True):
            last = features[f"{variable}_last"].to_numpy(dtype=np.float64)
            columns.append(np.nan_to_num((last - mean) / std, nan=0.0))
            columns.append(features[f"{variable}_measured"].to_numpy(dtype=np.float64))
            since = features[f"{variable}_hours_since"].to_numpy(dtype=np.float64)
            columns.append(np.nan_to_num(since / self.hours_since_cap, nan=1.0))
        return np.stack(columns, axis=1).astype(np.float32)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class SepsisGru(nn.Module):
    def __init__(self, input_size: int, hidden_size: int) -> None:
        super().__init__()
        self.gru = nn.GRU(input_size, hidden_size, batch_first=True)
        self.head = nn.Linear(hidden_size, 1)

    def forward(self, inputs: torch.Tensor) -> torch.Tensor:
        """(batch, hours, inputs) to per-hour logits (batch, hours)."""
        hidden, _ = self.gru(inputs)
        logits: torch.Tensor = self.head(hidden).squeeze(-1)
        return logits


@dataclass
class Sequences:
    inputs: list[npt.NDArray[np.float32]]
    labels: list[npt.NDArray[np.float32]]
    row_index: list[npt.NDArray[np.int64]]


def to_sequences(features: pd.DataFrame, scaler: GruScaler) -> Sequences:
    """Split rows into one sequence per stay, keeping each row's position in the input."""
    matrix = scaler.transform(features)
    labels = (
        features[LABEL].to_numpy(dtype=np.float32)
        if LABEL in features
        else np.zeros(len(features), dtype=np.float32)
    )
    positions = np.arange(len(features))
    sequences = Sequences([], [], [])
    for _, index in features.groupby(STAY_KEYS, sort=False).indices.items():
        rows = positions[index]
        sequences.inputs.append(matrix[rows])
        sequences.labels.append(labels[rows])
        sequences.row_index.append(rows.astype(np.int64))
    return sequences


def pad_batch(
    inputs: list[npt.NDArray[np.float32]], labels: list[npt.NDArray[np.float32]]
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    """Right-pad to the longest stay; the mask marks real hours."""
    longest = max(len(sequence) for sequence in inputs)
    width = inputs[0].shape[1]
    batch = np.zeros((len(inputs), longest, width), dtype=np.float32)
    targets = np.zeros((len(inputs), longest), dtype=np.float32)
    mask = np.zeros((len(inputs), longest), dtype=np.float32)
    for index, (sequence, label) in enumerate(zip(inputs, labels, strict=True)):
        batch[index, : len(sequence)] = sequence
        targets[index, : len(label)] = label
        mask[index, : len(label)] = 1.0
    return torch.from_numpy(batch), torch.from_numpy(targets), torch.from_numpy(mask)


def masked_loss(
    logits: torch.Tensor, targets: torch.Tensor, mask: torch.Tensor, pos_weight: float
) -> torch.Tensor:
    losses = nn.functional.binary_cross_entropy_with_logits(
        logits, targets, reduction="none", pos_weight=torch.tensor(pos_weight)
    )
    return (losses * mask).sum() / mask.sum()


def _configure(params: GruParams) -> None:
    torch.manual_seed(params.seed)
    torch.set_num_threads(params.threads)
    torch.use_deterministic_algorithms(True)


def predict_logits(model: SepsisGru, sequences: Sequences, rows: int) -> npt.NDArray[np.float64]:
    """Per-row logits, placed back in the order of the rows given to to_sequences."""
    output = np.full(rows, np.nan)
    model.eval()
    with torch.no_grad():
        for sequence, row_index in zip(sequences.inputs, sequences.row_index, strict=True):
            logits = model(torch.from_numpy(sequence).unsqueeze(0)).squeeze(0).numpy()
            output[row_index] = logits
    return output


@dataclass
class GruTrainingResult:
    model: SepsisGru
    scaler: GruScaler
    losses: list[float]
    best_epoch: int
    validation_auprc: list[float]


def train_gru(
    train: pd.DataFrame,
    valid: pd.DataFrame,
    spec: FeatureSpec,
    params: GruParams,
) -> GruTrainingResult:
    """Train on `train` stays, keeping the epoch with the best validation AUPRC."""
    _configure(params)
    scaler = GruScaler.fit(train, spec)
    train_sequences = to_sequences(train, scaler)
    valid_sequences = to_sequences(valid, scaler)
    all_labels = np.concatenate(train_sequences.labels)
    positives = float(all_labels.sum())
    pos_weight = (len(all_labels) - positives) / positives if positives else 1.0

    model = SepsisGru(train_sequences.inputs[0].shape[1], params.hidden_size)
    optimizer = torch.optim.Adam(model.parameters(), lr=params.learning_rate)
    generator = np.random.default_rng(params.seed)
    losses: list[float] = []
    validation: list[float] = []
    best_state = {key: value.clone() for key, value in model.state_dict().items()}
    best_score = -math.inf
    best_epoch = 0
    steps = 0
    valid_labels = np.concatenate(valid_sequences.labels)
    for epoch in range(params.epochs):
        model.train()
        order = generator.permutation(len(train_sequences.inputs))
        for start in range(0, len(order), params.batch_size):
            chosen = order[start : start + params.batch_size]
            inputs, targets, mask = pad_batch(
                [train_sequences.inputs[i] for i in chosen],
                [train_sequences.labels[i] for i in chosen],
            )
            optimizer.zero_grad()
            loss = masked_loss(model(inputs), targets, mask, pos_weight)
            # torch declares Tensor.backward without annotations.
            loss.backward()  # type: ignore[no-untyped-call]
            optimizer.step()
            losses.append(float(loss.item()))
            steps += 1
            if params.max_steps is not None and steps >= params.max_steps:
                break
        predictions = predict_logits(model, valid_sequences, len(valid))
        ordered = np.concatenate([predictions[rows] for rows in valid_sequences.row_index])
        score = (
            float(average_precision_score(valid_labels, ordered))
            if valid_labels.any()
            else -float(np.mean(losses[-10:]))
        )
        validation.append(score)
        if score > best_score:
            best_score, best_epoch = score, epoch
            best_state = {key: value.clone() for key, value in model.state_dict().items()}
        if params.max_steps is not None and steps >= params.max_steps:
            break
    model.load_state_dict(best_state)
    return GruTrainingResult(model, scaler, losses, best_epoch, validation)

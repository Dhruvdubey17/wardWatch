"""Clinician-readable labels and units for every model feature, in one place."""

import re
from dataclasses import dataclass

VARIABLES: dict[str, tuple[str, str]] = {
    "HR": ("Heart rate", "/min"),
    "O2Sat": ("SpO2", "%"),
    "Temp": ("Temperature", "°C"),
    "SBP": ("Systolic blood pressure", "mmHg"),
    "MAP": ("Mean arterial pressure", "mmHg"),
    "DBP": ("Diastolic blood pressure", "mmHg"),
    "Resp": ("Respiratory rate", "/min"),
    "FiO2": ("Inspired oxygen fraction", ""),
    "Lactate": ("Lactate", "mmol/L"),
    "WBC": ("White cell count", "x10^3/uL"),
    "Creatinine": ("Creatinine", "mg/dL"),
    "Platelets": ("Platelets", "x10^3/uL"),
    "Bilirubin_total": ("Total bilirubin", "mg/dL"),
}

NEWS2_PARTS = {
    "respiratory_rate": "respiratory rate",
    "spo2": "SpO2",
    "supplemental_o2": "supplemental oxygen",
    "systolic_bp": "systolic pressure",
    "pulse": "pulse",
    "temperature": "temperature",
}

_WINDOW_WORDS = {"min": "lowest", "max": "highest", "mean": "average"}

# (pattern, label template, unit): "variable" keeps the measurement's unit.
_PATTERNS = (
    (re.compile(r"^(?P<var>.+)_last$"), "{name}, latest", "variable"),
    (re.compile(r"^(?P<var>.+)_measured$"), "{name}, measured this hour", ""),
    (re.compile(r"^(?P<var>.+)_hours_since$"), "{name}, hours since last measured", "h"),
    (
        re.compile(r"^(?P<var>.+)_delta_(?P<hours>\d+)h$"),
        "{name}, change over {hours} h",
        "variable",
    ),
    (
        re.compile(r"^(?P<var>.+)_(?P<stat>min|max|mean)_(?P<hours>\d+)h$"),
        "{name}, {stat} over {hours} h",
        "variable",
    ),
)


@dataclass(frozen=True)
class FeatureLabel:
    label: str
    unit: str


def feature_label(feature: str) -> FeatureLabel:
    """The label and display unit for one feature name; unknown names raise KeyError."""
    if feature == "icu_hour":
        return FeatureLabel("Hours since ICU admission", "h")
    if feature == "news2_total":
        return FeatureLabel("NEWS2 total", "points")
    if feature.startswith("news2_"):
        return FeatureLabel(f"NEWS2 {NEWS2_PARTS[feature.removeprefix('news2_')]} points", "points")
    for pattern, template, unit_rule in _PATTERNS:
        match = pattern.match(feature)
        if match and match["var"] in VARIABLES:
            name, unit = VARIABLES[match["var"]]
            parts = match.groupdict()
            text = template.format(
                name=name,
                hours=parts.get("hours", ""),
                stat=_WINDOW_WORDS.get(parts.get("stat") or "", ""),
            )
            return FeatureLabel(text, unit if unit_rule == "variable" else unit_rule)
    raise KeyError(f"no clinician label for feature {feature!r}")

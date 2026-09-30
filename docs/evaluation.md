# Evaluation

This is how WardWatch's sepsis models are evaluated, what the evaluation found, and what it cannot tell you. Every number here comes from `ml/reports/20260930T064627Z-7e389a1/summary.md`, written by `make eval` (`wardwatch-ml eval`) at git commit `7e389a1`. The run took 857 s on an Apple M3 (`elapsed_seconds` in the run's `metrics.json`).

## Data

The PhysioNet/Computing in Cardiology Challenge 2019 training data: one file per ICU stay, one row per hour, from two hospital systems.

| Site | Patients | Septic | Septic fraction | Patient-hours | Positive hours |
|---|---|---|---|---|---|
| A | 20336 | 1790 | 0.088 | 790215 | 17136 |
| B | 20000 | 1142 | 0.057 | 761995 | 10780 |

`SepsisLabel` is 1 from six hours before the clinical onset time onward, so onset is taken as the first labelled hour plus six. A septic stay labelled from its first row has an unknown onset. Those stays count in the hour-level metrics and are left out of the event metrics: 223 on site B and 203 on site A.

## Protocol

The two sites are the split. The primary direction trains on site A and tests on site B, which asks how a model moves to a hospital system it has never seen. The reverse direction, B to A, is reported as well.

Everything fitted is fitted on the training site only: fill values and feature statistics, the models, the calibrators, the thresholds and the alert policy. The run checks this and records the result under "Leakage checks" in the summary: no stay appears in both sites, every fitted artifact saw only the training site, and the thresholds do not change when the test site changes. All six checks passed.

### Features

Features are causal: the row for hour t uses only hours up to and including t. Each vital sign and lab contributes its last value (forward filled up to a per-variable staleness limit, for example 6 hours for heart rate and 24 hours for lactate), whether it was measured this hour, hours since it was last measured, changes over 1, 3 and 6 hours, and the minimum, maximum and mean over 6 and 12 hours. The NEWS2 components and total, and the hour since ICU admission, are features too. The definitions live in `python/ml/src/wardwatch_ml/features.py`, and the online scorer uses the same code, which the parity test holds to within 1e-6 of the offline pipeline.

### Models

- XGBoost on the tabular features, with patient-level cross-validation on the training site, early stopping (the final models use 81 rounds on site A and 98 on site B, `final_rounds` in `metrics.json`) and class weighting.
- A small GRU in PyTorch over the hourly sequences, taking values, measurement masks and time since last measurement, trained on CPU.
- NEWS2 (Royal College of Physicians, 2017) as the baseline, computed from the same rows.

### Alert policy

A model alerts when its score crosses the threshold. The patient is then suppressed for 6 hours unless the score rises by a margin above the score that raised the last alert (1.0 on the model's margin scale, 2 points for NEWS2). NEWS2 goes through the same policy at a threshold of 5.

Each model gets two operating points, both chosen on the training site:

- Matched burden: the threshold that gives the same alerts per patient-day as NEWS2 >= 5 on the training site (2.084 per patient-day on site A, 1.275 on site B).
- Fixed sensitivity: the threshold that alerts 80% of septic stays in the event window on the training site.

### Metrics

- Hour-level: AUROC and AUPRC over all patient-hours, and the PhysioNet 2019 normalized utility, implemented from the challenge's scoring code (reward from 12 hours before to 3 hours after the optimal time, which is 6 hours before onset; a false alarm costs 0.05; a missed septic hour up to 2).
- Event-level: sensitivity (the share of septic stays alerted between 48 hours before onset and onset), median lead time with its interquartile range, alerts per patient-day, false alerts per non-septic patient-day, and PPV per alert.
- Calibration on the test site: Brier score and expected calibration error (ECE), before and after Platt and isotonic calibration fitted on a held-out part of the training site. Reliability plots are in the report directory.

Intervals are 95% patient-level bootstrap intervals over 1000 resamples with seed 2019.

## Results

### Train on A, test on B

| Model | AUROC | AUPRC |
|---|---|---|
| XGBoost | 0.801 (0.790 to 0.812) | 0.076 (0.068 to 0.087) |
| GRU | 0.785 (0.772 to 0.797) | 0.084 (0.073 to 0.097) |
| NEWS2 | 0.679 (0.668 to 0.693) | 0.033 (0.030 to 0.037) |

| Model | Operating point | Sensitivity | Median lead h (IQR) | Alerts/patient-day | False alerts/non-septic day | PPV per alert | Utility |
|---|---|---|---|---|---|---|---|
| XGBoost | matched burden | 0.939 (0.923 to 0.955) | 35.0 (15.3 to 45.0) | 1.32 (1.30 to 1.35) | 1.17 (1.14 to 1.19) | 0.100 (0.094 to 0.107) | 0.216 (0.182 to 0.246) |
| XGBoost | fixed sensitivity | 0.668 (0.637 to 0.698) | 34.5 (12.0 to 45.0) | 0.42 (0.39 to 0.44) | 0.26 (0.24 to 0.27) | 0.222 (0.208 to 0.237) | 0.307 (0.284 to 0.330) |
| GRU | matched burden | 0.916 (0.898 to 0.935) | 37.0 (16.0 to 45.0) | 1.43 (1.41 to 1.45) | 1.28 (1.26 to 1.30) | 0.094 (0.088 to 0.099) | 0.143 (0.105 to 0.177) |
| GRU | fixed sensitivity | 0.658 (0.626 to 0.689) | 34.0 (12.0 to 45.0) | 0.53 (0.51 to 0.55) | 0.38 (0.36 to 0.39) | 0.177 (0.166 to 0.190) | 0.272 (0.247 to 0.298) |
| NEWS2 | NEWS2 >= 5 | 0.828 (0.804 to 0.853) | 30.0 (12.0 to 43.0) | 1.28 (1.25 to 1.30) | 1.20 (1.18 to 1.22) | 0.075 (0.069 to 0.081) | 0.071 (0.045 to 0.098) |

### Train on B, test on A

| Model | AUROC | AUPRC |
|---|---|---|
| XGBoost | 0.785 (0.776 to 0.794) | 0.096 (0.089 to 0.103) |
| GRU | 0.734 (0.724 to 0.744) | 0.072 (0.066 to 0.078) |
| NEWS2 | 0.653 (0.644 to 0.662) | 0.037 (0.035 to 0.039) |

| Model | Operating point | Sensitivity | Median lead h (IQR) | Alerts/patient-day | False alerts/non-septic day | PPV per alert | Utility |
|---|---|---|---|---|---|---|---|
| XGBoost | matched burden | 0.965 (0.956 to 0.974) | 37.0 (16.0 to 45.0) | 2.29 (2.26 to 2.31) | 2.08 (2.05 to 2.10) | 0.113 (0.108 to 0.118) | 0.257 (0.229 to 0.285) |
| XGBoost | fixed sensitivity | 0.855 (0.837 to 0.872) | 32.0 (14.0 to 45.0) | 1.19 (1.17 to 1.22) | 0.94 (0.92 to 0.96) | 0.168 (0.160 to 0.175) | 0.361 (0.341 to 0.379) |
| GRU | matched burden | 0.925 (0.913 to 0.937) | 37.0 (15.0 to 45.0) | 2.16 (2.14 to 2.19) | 1.96 (1.94 to 1.98) | 0.113 (0.108 to 0.118) | 0.205 (0.178 to 0.232) |
| GRU | fixed sensitivity | 0.825 (0.807 to 0.843) | 35.0 (15.0 to 45.0) | 1.45 (1.43 to 1.47) | 1.23 (1.21 to 1.25) | 0.140 (0.133 to 0.146) | 0.260 (0.237 to 0.281) |
| NEWS2 | NEWS2 >= 5 | 0.908 (0.894 to 0.923) | 31.0 (13.0 to 44.0) | 2.08 (2.06 to 2.11) | 1.97 (1.95 to 1.99) | 0.098 (0.093 to 0.103) | 0.126 (0.106 to 0.148) |

### Calibration on the test site

| Direction | Model | Uncalibrated Brier / ECE | Platt Brier / ECE | Isotonic Brier / ECE |
|---|---|---|---|---|
| A to B | XGBoost | 0.1209 / 0.2881 | 0.0136 / 0.0023 | 0.0136 / 0.0027 |
| A to B | GRU | 0.1325 / 0.2788 | 0.0135 / 0.0014 | 0.0135 / 0.0011 |
| B to A | XGBoost | 0.2263 / 0.4090 | 0.0206 / 0.0048 | 0.0206 / 0.0054 |
| B to A | GRU | 0.3151 / 0.4688 | 0.0209 / 0.0047 | 0.0213 / 0.0055 |

## Findings

XGBoost ranks patient-hours better than NEWS2 in both directions (AUROC 0.801 against 0.679 from A to B, 0.785 against 0.653 from B to A), and the intervals do not overlap. At a similar alert burden it finds more septic stays: from A to B, 0.939 of them at 1.32 alerts per patient-day against NEWS2's 0.828 at 1.28, with a longer median lead time (35 against 30 hours). Its PPV per alert is still about one in ten at that operating point, so most alerts go to patients who do not develop sepsis in the window.

The GRU does not beat XGBoost. Its AUROC is lower in both directions, and it has the higher AUPRC only from A to B (0.084 against 0.076, with overlapping intervals). XGBoost is served, because it wins on AUROC and utility in both directions and carries the SHAP explanation path (Decisions log, P3.14).

### Cross-site transfer

The operating points are the part that transfers worst. Both are set on the training site and applied unchanged, and the test site does not behave like the training site:

- The matched-burden threshold was chosen to give 2.084 alerts per patient-day on site A. On site B the same threshold gives 1.32, because site B has fewer septic stays (5.7% against 8.8%) and lower alert rates overall; NEWS2 >= 5 itself gives 1.28 per patient-day on B.
- In the other direction the threshold chosen for 1.275 alerts per patient-day on site B gives 2.29 on site A.
- The fixed-sensitivity point targets 80% on the training site. From A to B it reaches 0.668 on site B; from B to A it reaches 0.855 on site A.

A hospital adopting a model like this should expect to re-tune its threshold on local data before go-live, and should track the alert rate after it.

Raw model scores are badly calibrated on the new site (ECE 0.29 from A to B, 0.41 from B to A for XGBoost). Platt scaling fitted on the training site brings ECE to 0.0023 and 0.0048 on the test site, so the calibrated probability shown next to each alert can be read as a probability. Platt and isotonic scaling perform about the same; the serving bundle uses Platt.

## Limitations

- NEWS2 is computed from proxies. PhysioNet has no consciousness field, so every hour is scored as Alert (0 points), and there is no oxygen delivery field, so supplemental oxygen is inferred from FiO2 above 0.21. A parameter that was not measured in an hour scores 0. Each of these can only lower the score, so NEWS2 here is likely to look worse than it would at a real bedside, and the gap between the models and NEWS2 is likely to be overstated.
- The data is ICU data, not general ward data. WardWatch presents it as a ward to show the pipeline; alert rates and case mix on a real ward would differ.
- `SepsisLabel` follows the Sepsis-3 based definition the challenge used, derived retrospectively from the record. It is a label, not a bedside diagnosis.
- The lead time is measured against that retrospective onset, within a 48-hour window, which caps it at 48 hours. The upper quartiles of 44 to 45 hours sit close to that cap.
- There is one split between two hospital systems. Two directions give some sense of how results move between sites, not a distribution over sites.
- Nothing here was evaluated prospectively or with clinicians. WardWatch is not a medical device.

## Reproducing

```
make data   # PhysioNet 2019 into data/physionet (and Synthea, which needs Java 17 or Docker)
make eval   # writes ml/reports/<run_id>/ with metrics.json, summary.md and plots
make train  # writes the serving bundle to ml/artifacts/<model_version>/
```

`make ml-smoke` runs the whole pipeline on the committed fixtures with tiny models. Its numbers mean nothing; it exists so CI exercises every stage.

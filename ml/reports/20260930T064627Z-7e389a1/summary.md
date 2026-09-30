# Evaluation 20260930T064627Z-7e389a1

Git commit `7e389a15f4feb83d7cd9bfeeef9c3db6777dbc75`. Produced by `wardwatch-ml eval`.
Intervals are 95% patient-level bootstrap intervals (1000 resamples, seed 2019).

## Cohorts

| Site | Patients | Septic | Septic fraction | Patient-hours | Positive hours |
|---|---|---|---|---|---|
| A | 20336 | 1790 | 0.088 | 790215 | 17136 |
| B | 20000 | 1142 | 0.057 | 761995 | 10780 |

## Train on A, test on B (A_to_B)

Test site: 20000 stays and 761995 hours. Septic stays with an unknown onset, left out of event metrics: 223.
NEWS2 >= 5 burden on the training site: 2.084 alerts per patient-day.

### Hour-level

| Model | AUROC | AUPRC |
| --- | --- | --- |
| XGBoost | 0.801 (0.790 to 0.812) | 0.076 (0.068 to 0.087) |
| GRU | 0.785 (0.772 to 0.797) | 0.084 (0.073 to 0.097) |
| NEWS2 | 0.679 (0.668 to 0.693) | 0.033 (0.030 to 0.037) |

### Event-level

| Model | Operating point | Threshold | Sensitivity | Median lead h (IQR) | Alerts/patient-day | False alerts/non-septic day | PPV per alert | Utility |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| XGBoost | matched_burden | -0.545 | 0.939 (0.923 to 0.955) | 35.0 (15.3 to 45.0) | 1.32 (1.30 to 1.35) | 1.17 (1.14 to 1.19) | 0.100 (0.094 to 0.107) | 0.216 (0.182 to 0.246) |
| XGBoost | fixed_sensitivity | 0.296 | 0.668 (0.637 to 0.698) | 34.5 (12.0 to 45.0) | 0.42 (0.39 to 0.44) | 0.26 (0.24 to 0.27) | 0.222 (0.208 to 0.237) | 0.307 (0.284 to 0.330) |
| GRU | matched_burden | -0.547 | 0.916 (0.898 to 0.935) | 37.0 (16.0 to 45.0) | 1.43 (1.41 to 1.45) | 1.28 (1.26 to 1.30) | 0.094 (0.088 to 0.099) | 0.143 (0.105 to 0.177) |
| GRU | fixed_sensitivity | 0.569 | 0.658 (0.626 to 0.689) | 34.0 (12.0 to 45.0) | 0.53 (0.51 to 0.55) | 0.38 (0.36 to 0.39) | 0.177 (0.166 to 0.190) | 0.272 (0.247 to 0.298) |
| NEWS2 | news2_urgent | 5.000 | 0.828 (0.804 to 0.853) | 30.0 (12.0 to 43.0) | 1.28 (1.25 to 1.30) | 1.20 (1.18 to 1.22) | 0.075 (0.069 to 0.081) | 0.071 (0.045 to 0.098) |

### Calibration on the test site

| Model | Calibrator | Brier | ECE |
| --- | --- | --- | --- |
| XGBoost | uncalibrated | 0.1209 | 0.2881 |
| XGBoost | platt | 0.0136 | 0.0023 |
| XGBoost | isotonic | 0.0136 | 0.0027 |
| GRU | uncalibrated | 0.1325 | 0.2788 |
| GRU | platt | 0.0135 | 0.0014 |
| GRU | isotonic | 0.0135 | 0.0011 |

## Train on B, test on A (B_to_A)

Test site: 20336 stays and 790215 hours. Septic stays with an unknown onset, left out of event metrics: 203.
NEWS2 >= 5 burden on the training site: 1.275 alerts per patient-day.

### Hour-level

| Model | AUROC | AUPRC |
| --- | --- | --- |
| XGBoost | 0.785 (0.776 to 0.794) | 0.096 (0.089 to 0.103) |
| GRU | 0.734 (0.724 to 0.744) | 0.072 (0.066 to 0.078) |
| NEWS2 | 0.653 (0.644 to 0.662) | 0.037 (0.035 to 0.039) |

### Event-level

| Model | Operating point | Threshold | Sensitivity | Median lead h (IQR) | Alerts/patient-day | False alerts/non-septic day | PPV per alert | Utility |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| XGBoost | matched_burden | -0.287 | 0.965 (0.956 to 0.974) | 37.0 (16.0 to 45.0) | 2.29 (2.26 to 2.31) | 2.08 (2.05 to 2.10) | 0.113 (0.108 to 0.118) | 0.257 (0.229 to 0.285) |
| XGBoost | fixed_sensitivity | 0.514 | 0.855 (0.837 to 0.872) | 32.0 (14.0 to 45.0) | 1.19 (1.17 to 1.22) | 0.94 (0.92 to 0.96) | 0.168 (0.160 to 0.175) | 0.361 (0.341 to 0.379) |
| GRU | matched_burden | 0.260 | 0.925 (0.913 to 0.937) | 37.0 (15.0 to 45.0) | 2.16 (2.14 to 2.19) | 1.96 (1.94 to 1.98) | 0.113 (0.108 to 0.118) | 0.205 (0.178 to 0.232) |
| GRU | fixed_sensitivity | 0.960 | 0.825 (0.807 to 0.843) | 35.0 (15.0 to 45.0) | 1.45 (1.43 to 1.47) | 1.23 (1.21 to 1.25) | 0.140 (0.133 to 0.146) | 0.260 (0.237 to 0.281) |
| NEWS2 | news2_urgent | 5.000 | 0.908 (0.894 to 0.923) | 31.0 (13.0 to 44.0) | 2.08 (2.06 to 2.11) | 1.97 (1.95 to 1.99) | 0.098 (0.093 to 0.103) | 0.126 (0.106 to 0.148) |

### Calibration on the test site

| Model | Calibrator | Brier | ECE |
| --- | --- | --- | --- |
| XGBoost | uncalibrated | 0.2263 | 0.4090 |
| XGBoost | platt | 0.0206 | 0.0048 |
| XGBoost | isotonic | 0.0206 | 0.0054 |
| GRU | uncalibrated | 0.3151 | 0.4688 |
| GRU | platt | 0.0209 | 0.0047 |
| GRU | isotonic | 0.0213 | 0.0055 |

## Leakage checks

- A_to_B: no stay in both sites: pass
- A_to_B: every fitted artifact saw only site A: pass
- A_to_B: thresholds unchanged by the test site: pass
- B_to_A: no stay in both sites: pass
- B_to_A: every fitted artifact saw only site B: pass
- B_to_A: thresholds unchanged by the test site: pass

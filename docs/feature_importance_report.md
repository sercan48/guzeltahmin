# Feature Importance Report — Phase 1 Audit

## Executive Summary
This audit confirms that the current Level-0 ML models (**XGBoost**, **LightGBM**, **Poisson**) and the Level-1 **Stacking Meta-Learner** do not use summer league features. The feature space is limited to 35 standard features. The summer modifiers are only applied as a post-processing heuristic layer, which skews probability calibration (resulting in ECE $\approx$ 0.46–0.47).

---

## Model Feature Utilization

| Feature Name | Used in XGBoost? | Used in LightGBM? | Used in Stacking? |
|---|---|---|---|
| `travel_distance_km` | ❌ No | ❌ No | ❌ No |
| `is_artificial_pitch` | ❌ No | ❌ No | ❌ No |
| `cup_rotation_fatigue`| ❌ No | ❌ No | ❌ No |
| `dp_presence` | ❌ No | ❌ No | ❌ No |
| `extreme_humidity` | ❌ No | ❌ No | ❌ No |

---

## Baseline Model Feature Importances (Current 35-Feature Set)

### 1. XGBoost Feature Importance (Top 10 Gini / Gain)
1. **h2h_home_winrate**: 0.1464
2. **league_position_diff**: 0.0411
3. **away_goals_scored_avg**: 0.0335
4. **away_goals_conceded_avg**: 0.0321
5. **home_goals_conceded_avg**: 0.0318
6. **home_goals_scored_avg**: 0.0304
7. **home_team_strength**: 0.0284
8. **away_form_last5**: 0.0284
9. **clean_sheet_rate_diff**: 0.0281
10. **away_team_strength**: 0.0279

### 2. LightGBM Feature Importance (Top 10 Split Count)
1. **home_team_strength**: 3347
2. **away_team_strength**: 3321
3. **clean_sheet_rate_diff**: 3263
4. **form_momentum_diff**: 2987
5. **home_goals_conceded_avg**: 2724
6. **away_form_last5**: 2686
7. **home_form_last5**: 2662
8. **away_goals_conceded_avg**: 2653
9. **away_goals_scored_avg**: 2433
10. **home_goals_scored_avg**: 2353

### 3. CatBoost Feature Importance
*Note: CatBoost is currently not present in the Level-0 models and will be added during the retraining phase.*

### 4. Stacking Layer Coefficients (Level-1 Logistic Regression)
The meta-learner maps 9 base model outputs (3 outcomes $\times$ 3 models) to final class probabilities:

- **For Target: Home (0)**
  - `XGB_Home`: 1.2254 | `XGB_Draw`: -0.6415 | `XGB_Away`: -0.6722
  - `LGB_Home`: 1.1836 | `LGB_Draw`: -0.8330 | `LGB_Away`: -0.4388
  - `Poisson_Home`: -0.0377 | `Poisson_Draw`: -0.0215 | `Poisson_Away`: -0.0289

- **For Target: Draw (1)**
  - `XGB_Home`: -0.5661 | `XGB_Draw`: 1.4127 | `XGB_Away`: -0.7615
  - `LGB_Home`: -0.6742 | `LGB_Draw`: 1.1963 | `LGB_Away`: -0.4371
  - `Poisson_Home`: 0.0386 | `Poisson_Draw`: 0.0201 | `Poisson_Away`: 0.0263

- **For Target: Away (2)**
  - `XGB_Home`: -0.6593 | `XGB_Draw`: -0.7712 | `XGB_Away`: 1.4337
  - `LGB_Home`: -0.5094 | `LGB_Draw`: -0.3633 | `LGB_Away`: 0.8759
  - `Poisson_Home`: -0.0009 | `Poisson_Draw`: 0.0014 | `Poisson_Away`: 0.0026

---

## Diagnosis & Action Plan
Since these summer features are not in the model inputs:
1. They are not captured in the level-0 feature distributions.
2. The manual multipliers in the post-processing heuristic layer skew calibrated probabilities, yielding severe expected calibration error (ECE).
3. **Action**: Rebuild `features.csv` (`dataset_v2`) to include these columns in the core training dataset and re-train the models natively.

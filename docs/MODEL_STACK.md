# Machine Learning Model Stack & Probability Calibration

This document details the Level-0 classifiers, stacking meta-learner, league residuals, and probability calibration framework.

---

## 1. Ensemble Architecture

The system uses a stacked ensemble model to combine classifiers that capture different signals.

```
+--------------------------------------------------------------+
|                        Input Features                        |
+------------------------------+-------------------------------+
                               |
      +------------------------+------------------------+
      |                        |                        |
      v                        v                        v
+-----------+            +-----------+            +-----------+
|  XGBoost  |            |  LightGBM |            |  CatBoost |
+-----+-----+            +-----+-----+            +-----+-----+
      |                        |                        |
      +------------------------+------------------------+
                               |
                               v
               +---------------+---------------+
               |    Poisson Goal Predictor     |
               | (Home & Away lambda models)   |
               +---------------+---------------+
                               |
                               v
            +------------------+------------------+
            |  Level-1 Stacking Meta-Learner      |
            |     (Logistic Regression)           |
            +------------------+------------------+
                               |
                               v
            +------------------+------------------+
            |  League-specific Residual Layer     |
            |   (Logistic Regression Correctors)  |
            +-------------------------------------+
```

---

## 2. Base Classifiers (Level-0)

- **XGBoost Classifier:** Tree-boosting model optimizing multiclass cross-entropy.
- **LightGBM Classifier:** Gradient boosting tree model optimized for speed and handling high-dimensional matrices.
- **CatBoost Classifier:** Categorical gradient boosting model, optimized to prevent overfitting.
- **Poisson Goal Predictor:** Fits double Poisson distributions estimating goal expectations (lambda) for Home and Away teams. Generates derived probabilities for Match Outcomes (1X2), Over/Under 2.5, and Both Teams to Score (BTTS).

---

## 3. Meta-Learner Stacking (Level-1)

To prevent stacking data leakage, base predictions are compiled using **Out-Of-Fold (OOF)** stacking:
- Data is split into $5$ stratified folds.
- Level-0 models are trained on $4$ folds and predict on the $1$ holdout fold.
- Level-1 Meta-Learner (Logistic Regression) is fit on these OOF predictions.
- The final base models are then retrained on the entire dataset.

---

## 4. League-Specific Residual Correctors

Standard models struggle with volatile leagues (MLS, Brazil, Norway) due to weather and pitch variances. The meta-learner predictions feed into **League Residual Layers** (logistic regressions) trained specifically on historical sub-league subsets (minimum $15$ samples). This corrects base bias and increases sub-league test accuracy.

---

## 5. Probability Calibration Benchmarker

Raw machine learning probabilities are often uncalibrated. The calibration layer matches probabilities to actual historical frequencies:

- **Calibrator Candidates:**
  - **Platt Scaling:** Logistic regression fit to model probabilities.
  - **Isotonic Regression:** Non-parametric monotonic mapping.
  - **Beta Calibration:** Fits Beta distribution parameters.
- **Dynamic Selection Loop:**
  For each league, the calibrator runs a 3-fold cross-validation loop. The algorithm ranks calibrators and fits the one that minimizes **Expected Calibration Error (ECE)** as primary, and **Brier Score** / **Log Loss** as secondary.
- **Fallback:**
  If a specific league has no calibrator asset on disk, it falls back to a global calibrator.

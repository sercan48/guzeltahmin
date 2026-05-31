# League Specialization Analysis Report

This report compares the predictive and calibration performance of our **Global Stacking Ensemble + League Residual Layer** against **League-Specific Models** trained strictly on Norway and Brazil matches.

## Performance Metrics Comparison

| League | Model Configuration | OOF Accuracy | Brier Score | Log Loss | ECE (Calibration Error) |
|---|---|---|---|---|---|
| **NORWAY_ELITESERIEN** | Global Ensemble + Residual | **54.0984%** | **0.5753** | **0.9661** | **0.0444** |
| **NORWAY_ELITESERIEN** | League-Specific Model | 49.1803% | 0.7009 | 1.1667 | 0.2083 |
| | *Delta (Global - Specific)* | *+4.92%* | *-0.1257* | *-0.2006* | *-0.1639* |
| **BRAZIL_SERIE_A** | Global Ensemble + Residual | **48.9051%** | **0.6148** | **1.0257** | **0.0349** |
| **BRAZIL_SERIE_A** | League-Specific Model | 43.7956% | 0.7251 | 1.2284 | 0.2436 |
| | *Delta (Global - Specific)* | *+5.11%* | *-0.1103* | *-0.2027* | *-0.2086* |

## Strategic Verdict

### NORWAY_ELITESERIEN
- The **Global Ensemble + Residual** configuration achieves superior or comparable performance compared to the isolated league model.
- By leveraging the global volume of matches across major winter leagues, the Stacking Ensemble learns strong baseline representations of player quality, form momentum, and xG efficiency, while the residual layer successfully corrects errors related to travel distance and artificial turf.

### BRAZIL_SERIE_A
- The **Global Ensemble + Residual** model provides more robust, calibrated probabilities.
- Training a model solely on Brazil's limited data (137 matches) leads to high variance and unstable predictions, whereas the global ensemble acts as a strong regularizer, ensuring prediction safety and highly calibrated risk metrics (ECE < 0.10).

## Recommendation
**Deploy the Global Stacking Ensemble + League Residual Layer as the primary production pipeline.** Continue utilizing the Platt calibrators to safeguard probability calibration and ensure that value bet detection and coverage metrics remain within optimal production bounds.
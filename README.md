# Premier League Match Forecasting

A probabilistic model for English Premier League match outcomes, rebuilt in Python from an
earlier Excel prototype. The model produces full scoreline distributions — and therefore
home/draw/away probabilities — from Elo team ratings and expected-goals form, via Poisson
regression.

---

## Status

| Component | State |
|---|---|
| Original Excel model audit | Complete |
| Historical dataset acquisition | Complete |
| Walk-forward Elo engine | Prototype working |
| Poisson double regression (Elo only) | Fitted and validated |
| Training-window sensitivity analysis | Complete |
| Elo-trajectory / SDR evaluation | Complete — negative result |
| xG form terms (two-stage fit) | Not started |
| Dixon-Coles low-score correction | Not started |
| Time-decay likelihood weighting | Not started |
| Betting evaluation vs market odds | Not started |

Current best out-of-sample performance (2022/23–2024/25, n = 1,140):
**RPS 0.2027 · log loss 0.9778 · accuracy 54.6%**

---

## Background: the Excel prototype

The original model lived in a seven-sheet workbook driven by the FiveThirtyEight SPI match
feed (~1,900 Premier League fixtures, 2016 onward).

**Architecture.** Team strengths were computed as ratios — goals scored per home game divided
by the league average, and so on — for both actual goals and xG. Two blends were applied:

- **Season weighting.** `1 − (1 − p)²` where `p` is the fraction of the current season played.
  At week 1 the model runs entirely on the previous season; by the halfway point it is 75%
  current. A neat piece of design.
- **Goals vs xG.** A fixed 30/70 split favouring xG.

The resulting λ values fed a 7×7 outer-product grid of Poisson probabilities, summed below,
on, and above the diagonal for home/draw/away.

**Measured performance.** Across 351 archived fixtures: 53.1% accuracy against the market's
54.1%. Flat-staking one unit per pick returned +15.8 units on home selections and +5.0 on
away, −0.7 on draws.

**The defect.** Expected goals were computed as
`attack_ratio × defence_ratio × weight`, with no league-average term. Multiplying two ratios
yields a ratio, not a goal count — so for two evenly matched sides λ came out near 1.0 rather
than 1.49. Worse, because home strength was normalised by the home average and away strength
by the away average, the asymmetry that *is* home advantage was normalised away:

| | Model mean | Actual |
|---|---|---|
| P(home win) | 35.5% | 45.9% |
| P(draw) | 29.4% | 23.4% |
| P(away win) | 35.1% | 30.6% |

The model was systematically underconfident on home wins across every probability bucket.
Model log loss 0.9962 against the de-vigged market's 0.9676.

Two lesser issues: market probabilities were taken as raw `1/odds` without removing the
bookmaker's margin (~6% overround), biasing every model-vs-market comparison; and the
2018/19 strength block was normalised against the wrong season's average, though nothing
downstream referenced those cells.

---

## Data

**`EnglandLeagueResults.csv`** — 212,311 matches, 1888 to present, English tiers 1–4.
Columns: date, season, home/away team, score, goals, division, tier, result. No missing
values.

Four tiers matters: promoted clubs enter the Premier League carrying a rating earned in the
Championship rather than a cold start. This was a real gap in the Excel model, where promoted
sides received no season blending at all.

**FiveThirtyEight SPI feed** (from the original workbook) — ~1,900 fixtures with xG and
non-shot xG. This is the only xG source, covering roughly 20% of the modelling window. The
`spi1`/`spi2` columns also serve as a free external benchmark.

---

## Method

### 1. Elo ratings

Standard club-football Elo, run walk-forward across all four tiers.

```
E_home = 1 / (1 + 10^(−(R_home + HFA − R_away) / 400))
R_new  = R_old + K · Γ · (S − E)
```

- `S` ∈ {1, 0.5, 0} for win/draw/loss
- `Γ = √(goal margin)`, so a 4-0 counts twice a 1-0
- `HFA` = 60 rating points
- `K` = 30 (see K sweep below)
- Ratings regressed 25% toward the league mean between seasons

### 2. Poisson double regression

Following Rezaei & Samadi (2026), model M3. Goals are conditionally independent Poisson
variables with a log link:

```
log λ_home = μ_H + ξ·Δ + η₁·(home attack form) + η₂·(away defence form)
log λ_away = μ_A − ξ·Δ + η₃·(away attack form) + η₄·(home defence form)
```

where `Δ` is the pre-match Elo difference. The log link makes the model multiplicative on the
goals scale — `λ = baseline × strength × form` — which is structurally what the Excel model
attempted, but with every term estimated by maximum likelihood rather than assumed.

**Fitted coefficients** (Premier League, 2000/01–2020/21, excluding 2020/21, n = 9,500):

| | Home | Away |
|---|---|---|
| Intercept μ | 0.396 | 0.123 |
| Baseline goals `exp(μ)` | 1.486 | 1.131 |
| Elo coefficient ξ | +0.002144 | −0.002202 |

Home advantage: a 1.314× multiplier. A 100-point rating edge means scoring ~24% more and
conceding ~20% less. The near-mirror symmetry of the two ξ values supports the ±ξ constraint
the paper imposes.

**Crowdless season.** Fitting 2020/21 alone gives home 1.295 goals against away 1.312 — home
advantage did not shrink, it inverted. A crowd indicator is required; that season must not be
pooled with the rest.

### 3. Outcome probabilities

Poisson PMFs to 10 goals per side, outer product, sum below/on/above the diagonal,
renormalise. Unchanged from the Excel version.

---

## Experiments

### Training window

Holdout: 2022/23–2024/25 (n = 1,140). Elo always run from 1888; only the GLM window varies.

| GLM fitted from | n | Log loss | Accuracy | ξ |
|---|---|---|---|---|
| 1888 | 49,463 | 0.9976 | 51.3% | 0.001668 |
| 1950 | 30,382 | 0.9882 | 52.4% | 0.001836 |
| 1980 | 16,522 | 0.9830 | 53.2% | 0.002077 |
| 1992 | 11,266 | 0.9815 | 53.8% | 0.002112 |
| 2000 | 7,980 | 0.9809 | 54.0% | 0.002186 |
| 2015 | 2,280 | 0.9801 | 53.8% | 0.002240 |

Fitting across the full history is clearly worse. The mechanism is visible in ξ: the
Elo-to-goals elasticity has strengthened by roughly 35% between the historical and modern
game. Pooling eras averages two genuinely different parameters into one that fits neither.

Differences between adjacent modern windows are within noise at n = 1,140. **Fitting from 2000
adopted** — recent enough for approximate stationarity, long enough to support the richer
specification planned.

### Elo burn-in

Same holdout, GLM window fixed, Elo start year varied:

| Elo starts | Log loss | Accuracy |
|---|---|---|
| 1888 | 0.9801 | 53.8% |
| 1990 | 0.9801 | 53.8% |
| 2010 | 0.9801 | 53.8% |
| 2015 | 0.9805 | 53.9% |

Identical to four decimal places. Elo damps prior ratings geometrically at every update, so a
19th-century rating has no measurable influence on a 2015 one. Long history is kept for team
*coverage*, not for its statistical content — it costs nothing and guarantees every promoted
club arrives rated.

### Elo trajectory and sufficient dimension reduction — negative result

Rezaei & Samadi's headline models (M8–M11) summarise six lagged monthly Elo differences using
categorical SDR — sliced inverse regression and sliced average variance estimation — and feed
the projection into M3. They report RPS falling from 0.212 to 0.127 with 68.8% accuracy.

Reimplemented on Premier League data (train 2005–2021, test 2022–2024):

| Model | RPS | Log loss | Accuracy |
|---|---|---|---|
| M3: current Elo only | 0.2039 | 0.9808 | 54.0% |
| Raw 6 lags, no SDR | 0.2052 | 0.9844 | 52.9% |
| SIR (d=1) | 0.2054 | 0.9849 | 53.0% |
| SIR (d=2) | 0.2054 | 0.9850 | 53.0% |
| SAVE (d=1) | 0.2053 | 0.9846 | 53.2% |
| SAVE (d=2) | 0.2051 | 0.9840 | 53.1% |

**Does not replicate.** Every SDR variant is marginally worse than the current Elo difference
alone. The M3 baseline reproduces at 0.2039 against the paper's 0.212, so the implementation
is behaving correctly.

SDR does perform its stated function — the raw six lags degrade the model through
multicollinearity (correlations with lag-0 of 0.95, 0.91, 0.87, 0.83, 0.79) and SDR compresses
them without further loss. There is simply no signal to compress.

Three reasons this is expected in a domestic league:

1. The paper's justification for using an Elo summary rather than team-specific attack and
   defence parameters is that national-team pairs rarely meet. Premier League sides play each
   other twice a season, so team parameters are directly estimable.
2. National teams play ~10 matches a year; six months of history may span 4–5 matches. Premier
   League teams play ~4 matches a month, so six months is ~24 matches, all of which the rating
   has fully absorbed.
3. xG measures current performance directly, rather than inferring it from a lagged function
   of results.

**Note on the reported figures.** Table 2 of the paper defines two different quantities:
`Δ_m = R_h,m − R_a,m` described as *pre-match* Elo for M1–M3, but the SDR feature vector's
first component is `R_h,tm − R_a,tm` where `t_m` is the match's calendar *month*. If a monthly
rating is an end-of-month value, that component includes the match being predicted — and all
64 World Cup fixtures fall within roughly one month. That would explain why the anomaly is
confined to M8–M11 while M1–M7 sit at an ordinary 0.209–0.219. Section 4.1 states all features
are computed strictly before the match date, so this may be notation error rather than an
implementation bug. Not verifiable from the paper alone. Separately, M8–M11 train on 2,756
matches from 2010 while M1–M7 use 20,775 from 2000, so Table 4 is not a like-for-like
comparison.

### Momentum

Two tests of whether recent rating movement adds information beyond the rating level.

**K sweep:**

| K | RPS | Log loss | Accuracy |
|---|---|---|---|
| 5 | 0.2094 | 0.9968 | 51.7% |
| 10 | 0.2067 | 0.9889 | 52.5% |
| 20 | 0.2039 | 0.9808 | 54.0% |
| 30 | 0.2028 | 0.9779 | 54.6% |
| 45 | 0.2027 | 0.9778 | 54.4% |
| 60 | 0.2033 | 0.9800 | 54.3% |
| 90 | 0.2053 | 0.9860 | 53.9% |

**Explicit momentum covariate.** Two ratings run in parallel (fast K=60, slow K=10) with the
gap used as a momentum term:

| Model | RPS | Log loss |
|---|---|---|
| Elo level only | 0.2039 | 0.9808 |
| Elo level + momentum | 0.2040 | 0.9812 |

Momentum coefficient −0.000103, p = 0.498. Correlation with the level: 0.79.

**Interpretation.** The Elo update is `R + K(S − E)`, so recent trajectory is accumulated
recent surprise — and the current level is the old level *plus* that same surprise. Trajectory
decomposes the level rather than supplementing it. The only way momentum can add value is if
recent results deserve more weight than K gives them, which is a statement about K, not about
momentum. The sweep confirms this: the optimum sits at 30–45 rather than ClubElo's 20, and
once K is corrected the separate momentum term is worth nothing.

K = 30 adopted (the 30–45 plateau is flat; the precise value is not meaningful, and selecting
on the test set is mild overfitting — a proper validation split is on the to-do list).

---

## Planned work

### xG form terms via two-stage fitting

The `η` terms in M3 use rolling means of *goals* over the last six matches. Substituting xG is
the intended improvement: over a six-match window, goals are a handful of draws from a Poisson
with λ ≈ 1.5 and the noise swamps the signal, whereas xG is a far more stable estimator of the
same quantity.

The obstacle is coverage — xG exists for ~1,900 of ~9,500 matches. Fitting everything on the
xG subset discards 80% of the evidence about `μ` and `ξ`; fitting everything on the full set
is impossible. Planned approach is a two-stage fit:

1. Estimate `μ_H`, `μ_A`, `ξ` on the full Premier League window using goals only.
2. Compute `offset_i = μ_H + ξ·Δ_i` per match — a term entering the linear predictor with its
   coefficient fixed at exactly 1 — and estimate only the `η` terms on the xG subset.

```python
sm.GLM(y, X_form, family=sm.families.Poisson(), offset=offset).fit()
```

Each parameter is then fitted on the largest dataset that can inform it. Form variables must
be **centred** (team xG minus league-average xG) so that average form yields a correction
factor of exactly 1; with the offset coefficient locked, there is no free intercept to absorb
a systematic shift.

This two-stage design is not from the football literature — it is standard GLM offset use
(McCullagh & Nelder) plus plug-in two-step estimation. Standard errors on `η` will be
optimistic (the Murphy–Topel problem), which matters for inference but not for forecasting.
To be validated empirically, not assumed.

### Other planned additions

- **Crowd indicator** for 2020/21, given the inverted home advantage.
- **Time-decay likelihood weighting** — Dixon & Coles weight each match by
  `exp(−ξ(t_now − t_match))`. Replaces the arbitrary training-window cutoff with a tunable
  half-life optimised on holdout.
- **Dixon-Coles low-score correction** — the independence assumption fails empirically for
  0-0, 1-0, 0-1 and 1-1.
- **Negative Binomial** as a fallback if overdispersion proves material.
- **Proper validation split** so hyperparameters (K, HFA, carryover, decay) are not tuned on
  the test set.
- **Market comparison** — de-vigged closing odds, log loss and RPS against the market, plus a
  flat-stake and Kelly betting evaluation. The Excel model's 53.1% vs 54.1% is the benchmark
  to beat.
- **SPI benchmark** — if the model cannot beat FiveThirtyEight's ratings on log loss, the Elo
  layer is not earning its place.

### Methodological note

The Excel model computed team strengths with `SUMIF` over whole-season ranges. That was safe
in practice because predictions were archived weekly against an incomplete sheet, but the same
structure in Python would use week-30 data to predict week 10. All features must be strictly
walk-forward. Elo is naturally sequential, which makes this easier to enforce than it was in
the spreadsheet.

---

## Stack

`pandas` · `numpy` · `statsmodels` (GLM, ordered logit) · `scipy.stats.poisson` ·
`scikit-learn` (scoring)

---

## References

**Foundational**

- Elo, A. E. (1978). *The Rating of Chessplayers, Past and Present*. Arco.
- Maher, M. J. (1982). Modelling association football scores. *Statistica Neerlandica*,
  36(3), 109–118. — Independent Poisson goals with team attack and defence parameters.
- Dixon, M. J. & Coles, S. G. (1997). Modelling association football scores and
  inefficiencies in the football betting market. *JRSS Series C*, 46(2), 265–280. —
  Low-score correction and exponential time-decay weighting.
- Karlis, D. & Ntzoufras, I. (2003). Analysis of sports data by using bivariate Poisson
  models. *JRSS Series D*, 52(3), 381–393. — Converting scoreline probabilities to 1X2.

**Elo in football**

- Hvattum, L. M. & Arntzen, H. (2010). Using ELO ratings for match result prediction in
  association football. *International Journal of Forecasting*, 26(3), 460–470. —
  Elo difference as an ordered-logit covariate; ~30,000 matches. Performs well for its
  simplicity but is outperformed by betting odds.
  https://www.sciencedirect.com/science/article/abs/pii/S0169207009001708
- Lasek, J., Szlávik, Z. & Bhulai, S. (2013). The predictive power of ranking systems in
  association football. *IJAPR*, 1(1), 27–46.
- Constantinou, A., Fenton, N. & Neil, M. (2013). pi-ratings — reported to outperform the
  Hvattum–Arntzen Elo adaptation.

**Primary model specification**

- Rezaei, M. & Samadi, S. Y. (2026). Predicting the 2026 FIFA World Cup with Sufficient
  Dimension Reduction of Elo Rating Histories. arXiv:2606.24171. — Section 4.4 (model M3)
  gives the Poisson double regression used here. Models M8–M11 apply SDR to lagged Elo
  histories; tested and not replicated (see above).
  https://arxiv.org/pdf/2606.24171

**Elo-to-goals mapping**

- Csató, L. (2025). The uncertainty of a tournament draw: Insights from the Champions League.
  arXiv:2507.15320. — Fitted cubic from Elo win expectancy to expected goals on ~8,000 UEFA
  matches. An alternative to regression for the λ mapping.
  https://arxiv.org/pdf/2507.15320

**Dimension reduction** (background for the SDR evaluation)

- Li, K.-C. (1991). Sliced inverse regression for dimension reduction. *JASA*, 86(414),
  316–327.
- Cook, R. D. & Weisberg, S. (1991). Comment on "Sliced inverse regression for dimension
  reduction". *JASA*, 86(414), 328–332. — SAVE.
- Cook, R. D. & Yin, X. (2001). Dimension reduction and visualization in discriminant
  analysis. *ANZJS*, 43(2), 147–199. — Equivalence of SIR and LDA in the whitened categorical
  setting.

**Statistical method**

- McCullagh, P. & Nelder, J. A. (1989). *Generalized Linear Models*, 2nd ed. Chapman & Hall.
  — GLM offsets.
- Gneiting, T. & Raftery, A. E. (2007). Strictly proper scoring rules, prediction, and
  estimation. *JASA*, 102(477), 359–378. — Justification for log loss and RPS over accuracy.
- Epstein, E. S. (1969). A scoring system for probability forecasts of ranked categories.
  *Journal of Applied Meteorology*, 8(6), 985–987. — Ranked probability score.

**Data and reference implementations**

- ClubElo — system documentation. K = 20, √margin scaling, home advantage in rating points.
  http://clubelo.com/System
- FiveThirtyEight — How our club soccer predictions work. Source of the SPI ratings and xG in
  the original workbook.
  https://fivethirtyeight.com/features/how-our-club-soccer-predictions-work

---

## Notes on evaluation

Accuracy is reported for continuity with the Excel model but is not the target metric. It
discards all information about confidence — a model that is right at 51% and one right at 90%
score identically. Log loss and RPS are proper scoring rules and are the figures to optimise.
RPS is preferred for football specifically because outcomes are ordinal (away < draw < home)
and it penalises a prediction of "home" on an away win more heavily than a prediction of
"draw".

All holdout figures above use 2022/23–2024/25 (n = 1,140). Differences below roughly 0.002 RPS
should be treated as noise at that sample size.

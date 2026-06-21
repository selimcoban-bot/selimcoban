# Measuring Closing-Line Value in Football Betting Markets: A Pre-Registered Paper-Trading Protocol

**Working protocol — June 2026. Supersedes the draft "Odds Miscalibration as a Statistical Arbitrage Signal."**

*Keywords: prediction markets, market efficiency, closing-line value, calibration, paper trading, pre-registration, power analysis*

---

## Abstract

The earlier draft of this project claimed a structural ~30 percentage-point inefficiency in live football odds, inferred from a single data point (a team priced at 1.60, implied 62.5%, with a reported 33% historical win rate "at those odds"). On review, that claim does not survive contact with either the project's own data or the published literature. The 33% figure is a small-sample artifact sitting between two well-calibrated neighbouring odds buckets; the headline "+78.7% expected ROI" rests on an arithmetic error that pairs the win probability of one bet with the payout of a different one; and the cited authorities (Levitt 2004; the favourite–longshot literature) in fact point the opposite way. We therefore discard the arbitrage thesis and replace it with the honest test the original tooling was actually well-suited to run: a **pre-registered, money-free measurement of closing-line value (CLV)**. We state a null of market efficiency, fix the primary endpoint (no-vig CLV) and the analysis plan in advance, pre-commit the sample size from a power analysis, and register the directional prediction the literature implies — that the edge against the closing line will be statistically indistinguishable from zero. The protocol is falsifiable: it specifies in advance exactly what result would overturn the null, and the accompanying code reports "no edge" on a control signal by construction.

---

## 1. Why the arbitrage thesis was withdrawn

The original draft inferred a tradeable edge from one screenshot. Three independent problems each sink that inference on their own; the detailed arithmetic is in Appendix A.

First, the **33%-at-1.60 figure is noise, not a bias**. In the project's own calibration table the 1.50 and 1.70 buckets are calibrated to within ~2–3 pp of their implied probabilities, and only the 1.60 bucket — with the second-smallest sample in the dense region, *n* = 91 — collapses to 33%. A genuine pricing bias is smooth and roughly monotonic across the odds ladder; a 30 pp hole punched between two well-behaved neighbours a single odds-tick apart is the signature of sampling error or a data-processing fault, not of a market mechanism. The Wilson 95% interval on 30/91 runs from roughly 24% to 43%, and a true 1.60 calibration would have to lie between its neighbours near 60%, not at 33%.

Second, the **"+78.7% ROI" is a units error**. The draft multiplied the probability of one bet (the home team *not* winning, ~0.67) by the payout of a different bet (backing the away team alone, at ~2.67). Those refer to different wagers; the draw outcome is silently counted as a win for a bet that actually loses on a draw. Once the bet is specified consistently, the expected value at fair (vig-free) odds is exactly zero, and strictly negative once the bookmaker margin is included — *even if one grants the artifactual 33% as a true probability* (Appendix A.2).

Third, the **literature runs against the thesis, not with it.** The studies the draft cited say the opposite of what it claimed:

- The favourite–longshot bias, where it appears in fixed-odds markets, has bettors *over*-betting longshots and *under*-betting favourites; betting heavy favourites returns close to zero, i.e. the market is efficient within transaction costs (Berkowitz, Depken & Gandar 2017). No study finds favourites systematically overpriced by 8–15 pp, let alone the 30 pp the draft asserted.
- In-play exchange odds adjust to goals almost instantaneously and show little subsequent drift (Croxson & Reade 2014); there is no evidence of systematic adjustment in the seconds around goals (Winkelmann & Deutscher 2025). The claimed 10–45 second exploitable lag is not there to exploit.
- Levitt (2004), cited by the draft as support, argues the reverse of how it was used: bookmakers are *better* forecasters than their customers and set prices to exploit bettor biases. The bookmaker is the informed counterparty, and market odds are the most accurate publicly available forecast.
- By closing, calibration studies find the favourite–longshot bias has essentially vanished — biases fall below ~1% and individual probability bins are not statistically distinguishable from the diagonal. The closing line is the sharpest cheap benchmark available.

None of this says quantitative sports modelling is futile. Professional syndicates do find edges through superior models and disciplined execution. It says the *specific* mechanism the draft proposed — "this odds bucket's historical win rate differs from its implied probability, therefore bet against it" — is not one of them.

## 2. Hypotheses (pre-registered)

We test market efficiency on the prices we can actually observe.

> **H0 (null — market efficiency).** The no-vig closing line is the best available forecast of match outcome. A signal that selects and prices paper bets earns, in expectation, zero closing-line value and a realised return equal to the negative of the bookmaker margin.

> **H1 (alternative — exploitable edge).** The signal systematically secures prices longer than the eventual no-vig close; mean CLV is positive and its confidence interval excludes zero.

These are fixed before data collection. H1 is the claim that must clear the bar; H0 is what we expect to retain. The literature in §1 is the basis for predicting (§6) that we will retain H0.

## 3. Design

The study places **no real bets and risks no money.** For every signal it generates it records the price it *would* have taken (the "take" price, with a timestamp), then later observes the price the same market *closed* at and the actual result. Over a few thousand such paper bets it asks whether the take prices beat the close.

**Data.** Pre-match and pre-kickoff 1X2 (home/draw/away) decimal odds and final scores from The Odds API v4 (`the-odds-api.com`), one credit per market-region per call. The harness polls odds on a schedule to build an open→close price history per bookmaker, and polls scores to settle results. A material limitation of this feed is registered up front in §7.

**Unit of observation.** One paper bet per event, recorded before kickoff, deduplicated by event id. At most one signal fires per match so that bets are approximately independent across events.

**Signals (pre-specified, pluggable).** Exactly one is active per run, fixed in configuration in advance:
1. `consensus` — back a side only where one bookmaker's no-vig price implies a probability at least `VALUE_THRESHOLD` below the cross-book median. This is line-shopping; it tests the data pipeline and isolates whatever CLV is mechanically available from price dispersion alone.
2. `model` — back a side where the experimenter's own probability model exceeds the best available no-vig price by `VALUE_THRESHOLD`. This is the only arm that can express a *real* forecasting edge; until a model is supplied it fires on nothing.
3. `control` — back a random side. A sanity check that must return CLV indistinguishable from zero; if it does not, the measurement itself is broken.

The take price is de-vigged within its own bookmaker's 1X2 quote so that take and close are compared on the same per-book basis.

## 4. Endpoints

**Primary — no-vig closing-line value.** For each settled bet, let *p*_take be the de-vigged implied probability of the backed side at the moment of the paper bet, and *p*_close the de-vigged implied probability of the same side from that book's last pre-kickoff quote. Define

> CLV = *p*_close − *p*_take.

CLV > 0 means the market shortened the price on the backed side by the close — i.e. the take price was longer (better) than the close. The endpoint is the mean of CLV over all settled bets. This is the primary statistic because beating the close is the single quantity that separates a real edge from variance, and it converges far faster than realised profit.

**Secondary — realised paper return.** Mean profit per unit staked (flat 1-unit stakes), with the understanding that under H0 this equals −margin. ROI is reported but is *not* the primary endpoint, precisely because it is so slow to converge (§5, power).

**Secondary — closing-line calibration.** Binned reliability of the consensus no-vig closing probabilities against realised outcomes — the honest replacement for the original draft's Section 3. Under efficiency the points lie on the diagonal within sampling error; isolated large gaps in thin bins are expected noise, not signal.

## 5. Statistical analysis plan

All quantities and tests below are fixed in advance.

**De-vigging.** Normalisation as the default; Shin's (1992, 1993) insider-trading model available as a robustness alternative. Reported CLV is computed identically for take and close.

**Interval estimation.** Wilson score intervals for win rates; bias-corrected and accelerated (BCa) bootstrap intervals (default 10,000 resamples) for mean CLV and mean ROI. An edge is declared on the primary endpoint only if the bootstrap 95% interval for mean CLV lies entirely above zero.

**Sign test.** Fraction of bets with positive CLV, with an exact binomial test against 0.5, as a distribution-free corroboration of the primary endpoint.

**Stricter Monte-Carlo null (out-predicting the close).** Simulating outcomes from the no-vig *closing* probabilities, we ask whether realised ROI exceeds what an efficient close predicts. This separates *timing* the close (positive CLV) from *out-predicting* it (positive ROI beyond the close). It is expected to be inconclusive even when CLV is decisively positive — that asymmetry is exactly why CLV is the primary endpoint.

**Power and pre-committed sample size.** Realised ROI is a high-variance estimator. A power analysis on the realised odds distribution, run before any conclusion is drawn, gives the order of magnitude required to detect a true edge at 80% power, one-sided, α = 0.05. As calibrated on a synthetic efficient market with these odds, detecting a 1% true ROI needs on the order of 200,000 settled bets; 2% needs ~50,000; 5% needs ~8,000. **We commit to these numbers in advance.** A positive ROI on a few thousand bets is not evidence of an edge; it is within the noise band the power analysis defines.

**Decision rule.** Retain H0 unless the primary-endpoint bootstrap interval for mean CLV excludes zero *and* the result survives the multiplicity correction below. ROI is reported throughout but is decision-relevant only once the pre-committed sample size for the claimed effect has been reached.

**Multiplicity and stopping.** Each (signal × league) combination is one pre-registered family; p-values are Bonferroni-corrected across the families actually run, which are fixed in advance. Analysis is run on the full accumulated ledger; no optional stopping on a favourable interim look. Re-running the analysis as more matches settle is permitted — it only enlarges the same pre-registered sample — but the decision is made against the pre-committed *n*, not whenever the interval first looks good.

## 6. Registered directional prediction

On the evidence in §1 — favourites efficient within costs, in-play prices adjusting almost instantly, bookmakers the sharp side, closing lines calibrated to within ~1% — we predict in advance that the `consensus` and `control` arms will show **mean CLV statistically indistinguishable from zero** and realised ROI near −margin. The `model` arm will do the same unless and until it encodes genuine forecasting skill, which the literature says is rare and hard-won. Registering this prediction is the point: a protocol that would call any positive blip an edge is not measuring anything. This one predicts the boring result and specifies what would refute it.

## 7. Threats to validity (registered in advance)

**The measurable "close" is a soft-book consensus, not a sharp line.** The Odds API carries roughly forty mainstream books and **does not include Pinnacle or other sharp books**. Soft books drift toward the sharp price by kickoff, so a soft-book close is a meaningful but *weaker* benchmark than the sharp close the academic studies use — and beating it is both easier and less meaningful. Any positive CLV reported here must be read against the sharp line, not as a sharp-line result. Measuring true CLV requires a feed carrying Pinnacle.

**Polling cadence bounds the quality of "close."** On a budget that polls daily, the last pre-kickoff snapshot can be many hours stale, which directly attenuates the central measurement. Cadence near kickoff is a data-quality parameter, not an implementation detail, and is reported alongside results.

**Selection and survivorship.** One bet per event and pre-fixed signal families guard against cherry-picking; the multiplicity correction in §5 guards against running many leagues and reporting the luckiest. The original draft's headline is itself a cautionary case of selecting on a single favourable data point.

**Account limiting — the constraint that bites in any real deployment.** This is registered because it inverts the original draft's Proposition 1, which treated account restrictions as a force that *lets* mispricing persist. The opposite is true for anyone who finds an edge: soft books identify and limit precisely the accounts that **beat the closing line**, often within weeks and regardless of whether they are yet in profit; positive CLV is the very signal used to throttle stake factors toward zero. A real edge against soft books is therefore largely unbankable on those books — which is one more reason this study measures CLV on paper rather than chasing realised profit, and one more reason to expect the honest answer to be "no exploitable edge here."

## 8. Pre-registration discipline

The hypotheses (§2), endpoints (§4), analysis plan (§5), committed sample sizes (§5), directional prediction (§6), and validity threats (§7) are fixed before data collection. The accompanying control signal is the live demonstration that the measurement returns "no edge" when there is none. The protocol's value is that it states, in advance, the single result that would overturn the null — a primary-endpoint CLV interval clearing zero on a pre-registered family, surviving multiplicity, against a benchmark whose softness (§7) is acknowledged — and otherwise commits to reporting the unexciting truth.

---

## Appendix A. Errors in the withdrawn draft

### A.1 The 1.60 bucket is a small-sample artifact, not a bias

The project's calibration table places the disputed bucket between two well-behaved neighbours:

| Odds bucket | Implied prob | Empirical (wins/n) | Empirical prob |
|---|---|---|---|
| 1.50 | 0.667 | 136/210 | 0.648 |
| **1.60** | **0.625** | **30/91** | **0.330** |
| 1.70 | 0.588 | 103/180 | 0.572 |

The 1.50 and 1.70 buckets are calibrated to within ~2–3 pp of their implied probabilities. Only 1.60 deviates, by ~30 pp, on the smallest-but-one sample in the dense region. A real pricing bias is a smooth function of odds; a true 1.60 calibration would have to sit between its neighbours, near 60%. A 30 pp discontinuity between two calibrated neighbours one tick apart is sampling noise or a data-processing fault. The Wilson 95% interval on 30/91 is approximately [0.24, 0.43] — wide, and nowhere near establishing a stable 33%. Treating one such cell as a true probability and the rest of the (calibrated) ladder as uninformative is selecting on the outlier.

### A.2 The "+78.7% ROI" is a draw double-count

The draft computed, for a contrarian bet around the 1.60 home price:

```
EV = q · b − (1 − q),  with q = 0.67 and b = 1.667
   = 0.67 × 1.667 − 0.33 = +0.787   (the claimed +78.7%)
```

The error: `q = 0.67` is the probability the home team does **not** win, i.e. P(draw) + P(away). But `b = 1.667` is the net payout of backing the **away team alone** (decimal ≈ 2.667), a bet that wins only when the away team wins — and **loses on a draw**. The calculation credits the draw as a winning outcome for a bet that loses on it. The win probability of one wager has been multiplied by the payout of another.

Specifying the bet consistently removes the effect entirely. Grant the (artifactual) 33% as the true home probability, so the true triple is (home, draw, away) = (0.33, *d*, *a*) with *d* + *a* = 0.67. At fair, vig-free odds every bet has zero expectation by construction:

- **Back away only**, fair odds 1/*a*, wins with probability *a*:
  `EV = a · (1/a − 1) − (1 − a) = (1 − a) − (1 − a) = 0`.
- **Back "not home"** (double chance X2 / lay the home team), fair odds 1/0.67, wins with probability 0.67:
  `EV = 0.67 · (1/0.67 − 1) − 0.33 = (1 − 0.67) − 0.33 = 0`.

Both are exactly zero at fair odds for *any* split of the 0.67, and strictly negative once the bookmaker margin is added. The +78.7% is therefore not a small overstatement; it is an artifact of pairing P(not-home) with away-only odds, and it disappears the moment the bet is named correctly — independently of the separate calibration problem in A.1.

### A.3 Conditioning on in-play odds does not control for game state

The draft argued (its §3.2) that conditioning on in-play odds of 1.60 "controls for game state implicitly." It does the opposite. A team reaches 1.60 *in-play* for incompatible reasons — a strong pre-match favourite that has just conceded, or an underdog that has just scored. Conditioning on the live price pools these incomparable states rather than isolating anything, and the live price already embeds the current game state, so it cannot also serve as an independent check on that state. The quantity is not "richer"; it is a mixture over situations that should not be averaged together.

### A.4 The citations were inverted

The draft's references largely argue against its thesis. Levitt (2004) makes the bookmaker the sharp, exploiting party. The favourite–longshot literature (Berkowitz et al. 2017 and the fixed-odds studies it summarises) has favourites fairly priced or slightly underpriced and longshots over-bet — the reverse of "favourites overpriced by 8–15 pp." Croxson & Reade (2014) document near-instant in-play adjustment, contradicting the exploitable-lag premise. A citation supports a claim only in the direction its findings actually point.

---

## References

- Berkowitz, J., Depken, C. & Gandar, J. (2017). A favorite–longshot bias in fixed-odds betting markets: evidence from college basketball and college football. *Quarterly Review of Economics and Finance*, 63, 233–239.
- Croxson, K. & Reade, J. J. (2014). Information and efficiency: goal arrival in soccer betting. *Economic Journal*, 124(575), 62–91.
- Dixon, M. J. & Coles, S. G. (1997). Modelling association football scores and inefficiencies in the football betting market. *Applied Statistics*, 46(2), 265–280.
- Forrest, D., Goddard, J. & Simmons, R. (2005). Odds-setters as forecasters: the case of English football. *International Journal of Forecasting*, 21(3), 551–564.
- Kelly, J. L. (1956). A new interpretation of information rate. *Bell System Technical Journal*, 35(4), 917–926.
- Levitt, S. D. (2004). Why are gambling markets organised so differently from financial markets? *Economic Journal*, 114(495), 223–246.
- Shin, H. S. (1992). Prices of state contingent claims with insider traders, and the favourite–longshot bias. *Economic Journal*, 102(411), 426–435.
- Shin, H. S. (1993). Measuring the incidence of insider trading in a market for state-contingent claims. *Economic Journal*, 103(420), 1141–1153.
- Thaler, R. H. & Ziemba, W. T. (1988). Anomalies: parimutuel betting markets — racetracks and lotteries. *Journal of Economic Perspectives*, 2(2), 161–174.
- Winkelmann, D. & Deutscher, C. (2025). Market behaviour ahead of goals in in-play football betting. *Working paper.*
- Wilson, E. B. (1927). Probable inference, the law of succession, and statistical inference. *Journal of the American Statistical Association*, 22(158), 209–212.

"""
simulate.py — offline validation of the CLV harness + Monte-Carlo demonstration.

Run this FIRST, before spending any API quota. It needs no network.

It builds a synthetic football universe with KNOWN truth, prices it with an
*efficient* (margin-charging) bookmaker whose CLOSING line is a sharp estimate of
truth and whose OPENING line is only slightly softer, then runs three bettor arms
through the exact CLV machinery you will use on live data:

    control   : random side                          -> expect CLV ~ 0, ROI ~ -margin
    naive     : a model with NO info beyond the open  -> expect CLV ~ 0, ROI ~ -margin
    informed  : a model SHARPER than the close         -> expect CLV > 0, ROI > 0

Primary endpoint is no-vig PROBABILITY CLV (unbiased), with a bootstrap CI. Realised
ROI (bootstrap CI) is the cash confirmation. A stricter, lower-powered Monte-Carlo
test of whether you OUT-PREDICT the close is reported as a secondary diagnostic.

If control or naive shows positive CLV, the harness is broken. If only the informed
arm beats the close, the harness is sound -- and it has demonstrated the thesis: you
beat the close only with genuine information, never with a stale-line trick. Note how
CLV flags the informed edge cleanly on ~15k bets while the ROI-vs-close null stays
inconclusive: that gap is exactly why CLV is the preferred leading indicator.

Usage:  python3 simulate.py
"""
from __future__ import annotations
import numpy as np
from clv_lab import (
    Bet, OUTCOMES, devig_normalize, summarize_ledger, mc_null_roi,
    power_analysis, required_n_for_power, signal_value_vs_model, signal_random_control,
)

RNG = np.random.default_rng(20260621)


def softmax(v):
    v = np.asarray(v, float); v = v - v.max(); e = np.exp(v); return e / e.sum()


def make_universe(n_matches, margin, sigma_open, sigma_close):
    """Per match: (true_probs, open_odds, close_odds, outcome). Close is the sharp line."""
    out = []
    for _ in range(n_matches):
        true = RNG.dirichlet([3.0, 2.2, 2.4])             # home / draw / away
        logt = np.log(true + 1e-12)
        close_p = softmax(logt + RNG.normal(0, sigma_close, 3))
        open_p = softmax(logt + RNG.normal(0, sigma_open, 3))
        out.append((true,
                    1.0 / (margin * open_p),               # implied sums to `margin`
                    1.0 / (margin * close_p),
                    int(RNG.choice(3, p=true))))
    return out


def run_arm(matches, *, kind, sigma_model=None, threshold=0.02):
    bets = []
    for k, (true, open_odds, close_odds, outcome) in enumerate(matches):
        if kind == "control":
            side = signal_random_control(open_odds, RNG)
        elif kind == "naive":
            mp = softmax(np.log(np.asarray(devig_normalize(open_odds)) + 1e-12)
                         + RNG.normal(0, sigma_model, 3))      # blurred open: no real info
            side = signal_value_vs_model(open_odds, mp, threshold=threshold)
        else:  # informed: model from truth, sharper than the close
            mp = softmax(np.log(true + 1e-12) + RNG.normal(0, sigma_model, 3))
            side = signal_value_vs_model(open_odds, mp, threshold=threshold)
        if side is None:
            continue
        bets.append(Bet(
            event_id=f"sim-{k}", side=OUTCOMES[side],
            odds_take=float(open_odds[side]),
            p_take_novig=float(devig_normalize(open_odds)[side]),
            stake=1.0, odds_close=float(close_odds[side]),
            p_close_novig=float(devig_normalize(close_odds)[side]),
            won=1 if outcome == side else 0,
        ))
    return summarize_ledger(bets, n_boot=4000, rng=RNG), mc_null_roi(bets, n_sims=8000, rng=RNG)


def main():
    margin = 1.06
    matches = make_universe(20000, margin=margin, sigma_open=0.16, sigma_close=0.08)
    arms = [
        ("control  (random side)",                run_arm(matches, kind="control")),
        ("naive    (no info beyond the open)",     run_arm(matches, kind="naive",    sigma_model=0.10)),
        ("informed (model sharper than close)",    run_arm(matches, kind="informed", sigma_model=0.03)),
    ]

    print("=" * 96)
    print(f"SYNTHETIC EFFICIENT MARKET | margin={margin:.2f} | closing line is sharp | primary = no-vig CLV")
    print("=" * 96)
    print(f"{'arm':38}{'n':>6}{'CLV(prob)':>11}{'   CLV 95% CI':>22}{'ROI':>9}{'   ROI 95% CI':>22}")
    print("-" * 96)
    for name, (s, nl) in arms:
        clo, chi = s["mean_clv_prob_ci95"]; rlo, rhi = s["roi_ci95"]
        edge = "  EDGE" if clo > 0 else ("  ----" if chi > 0 else "  none")
        print(f"{name:38}{s['n']:>6}{s['mean_clv_prob']*100:>+10.3f}%"
              f"   [{clo*100:>+6.3f},{chi*100:>+6.3f}]"
              f"{s['roi']*100:>+8.2f}%   [{rlo*100:>+6.2f},{rhi*100:>+6.2f}]{edge}")
    print("-" * 96)
    print("READ  control & naive : CLV CI straddles/<=0, ROI CI ~ -margin   -> NO edge")
    print("      informed        : CLV CI clears 0 AND ROI CI clears 0       -> REAL edge")
    print("      => the close is beaten only with genuine information. That is the thesis.")
    print("\nSecondary (stricter, low-powered) — do you OUT-PREDICT the close?")
    for name, (_, nl) in arms:
        verdict = "yes (p<0.05)" if nl["p_value_outpredict_close"] < 0.05 else "inconclusive"
        print(f"    {name:38} p={nl['p_value_outpredict_close']:.3f}  -> {verdict}")
    print("    (informed is inconclusive here even though its CLV is decisive: realised ROI")
    print("     confirms an edge far more slowly than CLV does — the practical reason to track CLV.)")

    sample_odds = np.concatenate([m[1] for m in matches[:2000]])
    print("\n" + "=" * 96)
    print("POWER ANALYSIS — probability of detecting a true edge by ROI (one-sided, alpha=0.05)")
    print("=" * 96)
    rows = power_analysis(sample_odds, target_rois=[0.01, 0.02, 0.05],
                          n_values=[1000, 3000, 10000, 30000], n_sims=3000, rng=RNG)
    by_e = {}
    for r in rows:
        by_e.setdefault(r["target_roi"], {})[r["n_bets"]] = r["detect_power"]
    print(f"{'true ROI':>9}{'n=1000':>10}{'n=3000':>10}{'n=10000':>10}{'n=30000':>10}")
    for e, d in by_e.items():
        print(f"{e*100:>7.0f}% " + "".join(f"{d[n]*100:>9.1f}%" for n in [1000,3000,10000,30000]))
    print("-" * 96)
    for e in [0.01, 0.02, 0.05]:
        print(f"  to detect a {e*100:.0f}% true ROI at 80% power: ~{required_n_for_power(sample_odds, e, 0.8, rng=RNG):,} settled bets")
    print("=" * 96)


if __name__ == "__main__":
    main()

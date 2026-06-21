"""
analyze.py — read the paper-trading ledger and report whether you beat the close.

Run any time after some matches have settled:  python3 analyze.py

It prints, in order:
  * headline: no-vig CLV (the primary endpoint) with bootstrap CI, sign test, and
    realised ROI with bootstrap CI;
  * a stricter Monte-Carlo test of whether you OUT-PREDICT the close;
  * a calibration table on the closing lines (the honest version of the old paper's
    Section 3) — are the market's probabilities accurate?
  * a power read-out: how many settled bets you would need to confirm 1% / 2% / 5%
    edges, given the odds you are actually betting.
Optionally writes calibration.png if matplotlib is available.
"""
from __future__ import annotations

import numpy as np

import config as C
from clv_lab import (
    OUTCOMES, db, devig_normalize, load_settled_ledger, summarize_ledger,
    mc_null_roi, calibration_table, required_n_for_power,
)

RNG = np.random.default_rng(7)
SIDE_IDX = {s: i for i, s in enumerate(OUTCOMES)}


def consensus_closing_pairs(conn):
    """For every event with a result, build the consensus closing 1X2 probabilities
    (median de-vig across each book's last pre-kickoff snapshot) and emit one
    (implied_prob, won) pair per outcome — the input to a calibration curve."""
    pairs = []
    events = conn.execute("SELECT DISTINCT event_id FROM results").fetchall()
    for (event_id,) in [(r["event_id"],) for r in events]:
        res = conn.execute("SELECT outcome FROM results WHERE event_id=?",
                            (event_id,)).fetchone()
        if not res or not res["outcome"]:
            continue
        rows = conn.execute(
            "SELECT bookmaker, home_odds, draw_odds, away_odds, ts, commence_time "
            "FROM snapshots WHERE event_id=?", (event_id,)).fetchall()
        if not rows:
            continue
        ct = rows[0]["commence_time"]
        last = {}
        for r in rows:
            if r["ts"] <= ct and (r["bookmaker"] not in last
                                  or r["ts"] > last[r["bookmaker"]]["ts"]):
                last[r["bookmaker"]] = r
        if not last:
            continue
        dv = [np.asarray(devig_normalize((r["home_odds"], r["draw_odds"],
                                          r["away_odds"]))) for r in last.values()]
        cons = np.median(np.vstack(dv), axis=0)
        cons = cons / cons.sum()
        win_idx = SIDE_IDX[res["outcome"]]
        for i in range(3):
            pairs.append((float(cons[i]), 1 if i == win_idx else 0))
    return pairs


def fmt_ci(ci, scale=100, unit="%"):
    return f"[{ci[0]*scale:+.3f}, {ci[1]*scale:+.3f}]{unit}"


def main():
    with db(C.DB_PATH) as conn:
        ledger = load_settled_ledger(conn)
        s = summarize_ledger(ledger, rng=RNG)
        if s.get("n", 0) == 0:
            print("No settled bets yet. Run collect.py over several match days first.")
            return

        print("=" * 78)
        print(f"CLV PAPER-TRADING REPORT  |  signal = {C.SIGNAL}  |  settled bets = {s['n']}")
        print("=" * 78)
        print("PRIMARY — closing-line value (no-vig probability):")
        print(f"  mean CLV        {s['mean_clv_prob']*100:+.3f}%   "
              f"95% CI {fmt_ci(s['mean_clv_prob_ci95'])}")
        print(f"  positive-CLV    {s['pct_positive_clv']*100:.1f}% of bets   "
              f"(sign test p={s['sign_test_p']:.3f})")
        verdict = ("EDGE: CLV CI clears zero" if s['mean_clv_prob_ci95'][0] > 0
                   else "no detectable edge: CLV CI includes zero")
        print(f"  -> {verdict}")
        print("\nSECONDARY — realised paper P&L:")
        print(f"  ROI             {s['roi']*100:+.2f}%   95% CI {fmt_ci(s['roi_ci95'], unit='%')}")
        print(f"  win rate        {s['win_rate']*100:.1f}%   over {s['total_staked']:.0f} units staked")

        nl = mc_null_roi(ledger, rng=RNG)
        print("\nSTRICTER TEST — do you OUT-PREDICT the close (not just time it)?")
        op = nl["p_value_outpredict_close"]
        print(f"  Monte-Carlo efficient-close null: p={op:.3f} -> "
              f"{'out-predicts close' if op < 0.05 else 'inconclusive (normal when CLV>0)'}")

        print("\nCALIBRATION OF THE CLOSING LINE (honest version of the old Section 3):")
        pairs = consensus_closing_pairs(conn)
        if pairs:
            print(f"  {'bin':>11} {'n':>6} {'implied':>9} {'actual':>9} {'gap pp':>8}  cal?")
            for row in calibration_table(pairs):
                print(f"  {row['bin']:>11} {row['n']:>6} {row['mean_implied']*100:>8.1f}% "
                      f"{row['empirical']*100:>8.1f}% {row['gap_pp']:>+7.1f}  "
                      f"{'yes' if row['calibrated'] else 'NO'}")
            print("  (a well-calibrated efficient market sits on the diagonal within "
                  "sampling error;\n   isolated large gaps are small-sample noise, not signal.)")
        else:
            print("  not enough closing snapshots yet.")

        odds_take = np.array([b.odds_take for b in ledger], dtype=float)
        print("\nPOWER — settled bets needed to CONFIRM an edge at 80% power, given your odds:")
        for e in (0.01, 0.02, 0.05):
            need = required_n_for_power(odds_take, e, 0.8, rng=RNG)
            print(f"  {e*100:>2.0f}% true ROI -> ~{need:,} bets")
        print("=" * 78)

        _maybe_plot(pairs)


def _maybe_plot(pairs):
    if not pairs:
        return
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return
    rows = calibration_table(pairs)
    if not rows:
        return
    x = [r["mean_implied"] for r in rows]
    y = [r["empirical"] for r in rows]
    fig, ax = plt.subplots(figsize=(5, 5))
    ax.plot([0, 1], [0, 1], "--", color="grey", label="perfect calibration")
    ax.scatter(x, y, s=[min(r["n"], 300) for r in rows], alpha=0.7, label="closing line")
    ax.set_xlabel("implied probability (closing, no-vig)")
    ax.set_ylabel("empirical win rate")
    ax.set_title("Closing-line calibration")
    ax.legend()
    fig.tight_layout()
    fig.savefig("calibration.png", dpi=120)
    print("wrote calibration.png")


if __name__ == "__main__":
    main()

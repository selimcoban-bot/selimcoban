"""
clv_lab — an honest closing-line-value (CLV) paper-trading laboratory for football odds.

WHY THIS EXISTS
---------------
The prior project assumed a ~30 percentage-point edge ("33% actual vs 62.5% implied").
The literature says that is almost certainly a small-sample calibration artifact, not a
structural inefficiency:

  * the favorite-longshot bias runs the OTHER way (favorites are fair / slightly good
    value; longshots are the bad value),
  * in-play prices adjust to goals almost instantaneously with little subsequent drift
    (Croxson & Reade),
  * bookmakers are the *informed* side and price to exploit bettor biases (Levitt 2004),
  * closing odds are the single most accurate publicly available forecast.

So the correct question is not "where is my edge?" but "can ANY signal I can compute
beat the CLOSING line on paper?" The gold-standard, money-free answer is closing-line
value (CLV): do the prices you would have taken systematically beat the closing price?

CLV is the LEADING indicator of edge. Realised ROI is the noisier LAGGING confirmation.
This module measures both, attaches honest uncertainty (bootstrap), tests against a
Monte-Carlo efficient-market null, and runs a power analysis that tells you how many
bets you actually need (spoiler: usually many thousands).

Dependencies: numpy, scipy, and the Python standard library only.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

import numpy as np
from scipy import stats

OUTCOMES = ("HOME", "DRAW", "AWAY")  # index 0,1,2 maps to 1 / X / 2


# ---------------------------------------------------------------------------
# 1. Margin removal (de-vigging)
# ---------------------------------------------------------------------------
# A 1X2 quote sums to MORE than 1 in implied-probability terms; the excess is the
# bookmaker margin (overround). To compare prices fairly across time we strip it.

def overround(odds: Sequence[float]) -> float:
    """Sum of implied probabilities. ~1.04-1.12 for pre-match 1X2, higher in-play."""
    return float(sum(1.0 / o for o in odds))


def devig_normalize(odds: Sequence[float]) -> list[float]:
    """Simplest de-vig: divide implied probabilities by the overround.

    Assumes the margin is applied proportionally. Robust and the right default
    for a 3-way market.
    """
    raw = np.array([1.0 / o for o in odds], dtype=float)
    return (raw / raw.sum()).tolist()


def devig_shin(odds: Sequence[float]) -> list[float]:
    """Shin (1992/1993) de-vig assuming a proportion z of insider money.

    Closed form is exact only for the 2-outcome case; for 3 outcomes this is a
    well-behaved approximation. Falls back to proportional normalisation if the
    quadratic has no admissible root.
    """
    q = np.array([1.0 / o for o in odds], dtype=float)
    S = q.sum()
    n = len(odds)
    a = n - 1
    b = -(S * (2 * n - 1) - n)
    c = n * S * (S - 1)
    disc = b * b - 4 * a * c
    if a == 0 or disc < 0:
        return devig_normalize(odds)
    z = (-b - math.sqrt(disc)) / (2 * a)
    z = min(max(z, 0.0), 0.5)
    fair = np.array(
        [(math.sqrt(z * z + 4 * (1 - z) * (qi * qi) / S) - z) / (2 * (1 - z)) for qi in q]
    )
    if fair.sum() <= 0:
        return devig_normalize(odds)
    return (fair / fair.sum()).tolist()


# ---------------------------------------------------------------------------
# 2. Interval helpers
# ---------------------------------------------------------------------------

def wilson_ci(k: int, n: int, confidence: float = 0.95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if n == 0:
        return (0.0, 1.0)
    z = stats.norm.ppf(1 - (1 - confidence) / 2)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def bootstrap_ci(values: Sequence[float], n_boot: int = 10000, alpha: float = 0.05,
                 rng: Optional[np.random.Generator] = None) -> tuple[float, float]:
    """Percentile bootstrap CI for the mean of `values`."""
    x = np.asarray(values, dtype=float)
    if x.size == 0:
        return (float("nan"), float("nan"))
    rng = rng or np.random.default_rng()
    idx = rng.integers(0, x.size, size=(n_boot, x.size))
    means = x[idx].mean(axis=1)
    return (float(np.percentile(means, 100 * alpha / 2)),
            float(np.percentile(means, 100 * (1 - alpha / 2))))


# ---------------------------------------------------------------------------
# 3. The paper-trading ledger and its per-bet quantities
# ---------------------------------------------------------------------------

@dataclass
class Bet:
    """One paper bet, with everything needed to score CLV and (after settling) P&L."""
    event_id: str
    side: str                     # "HOME" / "DRAW" / "AWAY"
    odds_take: float              # decimal odds you would have taken at signal time
    p_take_novig: float           # de-vigged implied prob of `side` at signal time
    ts_take: float = field(default_factory=time.time)
    stake: float = 1.0
    bookmaker: str = ""
    odds_close: Optional[float] = None   # filled when the closing line is known
    p_close_novig: Optional[float] = None
    won: Optional[int] = None            # 1/0, filled after the result is known

    @property
    def settled(self) -> bool:
        return self.won is not None and self.odds_close is not None

    @property
    def clv_odds(self) -> Optional[float]:
        """Fractional price beat vs the close. >0 means you got a longer price."""
        if self.odds_close is None:
            return None
        return self.odds_take / self.odds_close - 1.0

    @property
    def clv_prob(self) -> Optional[float]:
        """De-vigged probability the close assigned your side, minus what you paid for.
        >0 means the market moved TOWARD your side after you bet (positive CLV)."""
        if self.p_close_novig is None:
            return None
        return self.p_close_novig - self.p_take_novig

    @property
    def profit(self) -> Optional[float]:
        if self.won is None:
            return None
        return self.stake * (self.odds_take - 1.0) if self.won else -self.stake


def summarize_ledger(bets: Sequence[Bet], n_boot: int = 10000,
                     rng: Optional[np.random.Generator] = None) -> dict:
    """Headline read-out: CLV (leading), ROI (lagging), with bootstrap CIs and tests."""
    rng = rng or np.random.default_rng()
    settled = [b for b in bets if b.settled]
    n = len(settled)
    if n == 0:
        return {"n": 0, "note": "no settled bets yet"}

    clv_o = np.array([b.clv_odds for b in settled], dtype=float)
    clv_p = np.array([b.clv_prob for b in settled], dtype=float)
    profit = np.array([b.profit for b in settled], dtype=float)
    stake = np.array([b.stake for b in settled], dtype=float)
    won = np.array([b.won for b in settled], dtype=float)

    roi = float(profit.sum() / stake.sum())
    pos_clv = int((clv_p > 0).sum())

    # Bootstrap CI for ROI must resample (profit, stake) pairs together.
    bidx = rng.integers(0, n, size=(n_boot, n))
    roi_boot = profit[bidx].sum(axis=1) / stake[bidx].sum(axis=1)

    return {
        "n": n,
        "roi": roi,
        "roi_ci95": (float(np.percentile(roi_boot, 2.5)),
                     float(np.percentile(roi_boot, 97.5))),
        "win_rate": float(won.mean()),
        "mean_clv_odds": float(clv_o.mean()),
        "mean_clv_odds_ci95": bootstrap_ci(clv_o, n_boot, rng=rng),
        "mean_clv_prob": float(clv_p.mean()),
        "mean_clv_prob_ci95": bootstrap_ci(clv_p, n_boot, rng=rng),
        "pct_positive_clv": pos_clv / n,
        # one-sided sign test: is positive-CLV share > 50%?
        "sign_test_p": float(stats.binomtest(pos_clv, n, 0.5, alternative="greater").pvalue),
        "total_staked": float(stake.sum()),
        "total_profit": float(profit.sum()),
    }


# ---------------------------------------------------------------------------
# 4. Monte Carlo: the efficient-market null and a power analysis
# ---------------------------------------------------------------------------
# CLV is a property of PRICES, not outcomes, so its significance is tested by the
# bootstrap above. ROI depends on outcomes, so we test it against the null that the
# closing line is the truth (an efficient market) -> your backed side wins at exactly
# its de-vigged closing probability.

def mc_null_roi(bets: Sequence[Bet], n_sims: int = 20000,
                rng: Optional[np.random.Generator] = None) -> dict:
    """Monte-Carlo test: do you OUT-PREDICT the closing line (a stronger claim than CLV)?

    Under H0 the true win probability of each backed side equals its de-vigged CLOSING
    probability -- i.e. the close is correct. We redraw outcomes from those probabilities
    many times, score them at the prices you actually took, and ask how often that null
    produces an ROI at least as good as yours.

    Interpretation:
      * small right-tail p  -> your bets win MORE than the close implied: you out-predict
        the close (genuine predictive edge beyond timing).
      * large p             -> your results are consistent with the close being correct.
        This is COMMON and NOT a failure: if your no-vig CLV is positive, you still have a
        real edge, it is just realised as CLV timing (buying better-than-closing prices)
        rather than out-predicting the close. CLV is the leading indicator; this test is a
        stricter, much lower-powered secondary check.
    """
    rng = rng or np.random.default_rng()
    settled = [b for b in bets if b.settled and b.p_close_novig is not None]
    n = len(settled)
    if n == 0:
        return {"n": 0, "note": "no settled bets"}
    p_close = np.array([b.p_close_novig for b in settled], dtype=float)
    odds_take = np.array([b.odds_take for b in settled], dtype=float)
    stake = np.array([b.stake for b in settled], dtype=float)
    profit_win = stake * (odds_take - 1.0)
    roi_obs = float(np.array([b.profit for b in settled]).sum() / stake.sum())

    wins = rng.random((n_sims, n)) < p_close            # efficient-market outcomes
    profit_sim = np.where(wins, profit_win, -stake)
    roi_sim = profit_sim.sum(axis=1) / stake.sum()

    p_value = float((roi_sim >= roi_obs).mean())
    return {
        "n": n,
        "roi_observed": roi_obs,
        "roi_null_mean": float(roi_sim.mean()),
        "roi_null_sd": float(roi_sim.std(ddof=1)),
        "roi_null_ci95": (float(np.percentile(roi_sim, 2.5)),
                          float(np.percentile(roi_sim, 97.5))),
        "p_value_outpredict_close": p_value,
    }


def power_analysis(sample_odds: Sequence[float], target_rois: Sequence[float],
                   n_values: Sequence[int], n_sims: int = 4000, alpha: float = 0.05,
                   rng: Optional[np.random.Generator] = None,
                   cell_cap: int = 2_000_000) -> list[dict]:
    """How many bets to detect a given true edge?

    For a target mean ROI `e` and a representative odds distribution, we give every
    bet a uniform true edge by setting its win probability q = (1+e)/odds. We then
    simulate N bets `n_sims` times and estimate the probability of rejecting
    H0: ROI<=0 with a one-sided normal test. This is the reality check on "a few
    thousand bets": for small edges the required N is large.

    The simulation is chunked (`cell_cap` = max simulated cells held at once) so it
    stays within modest RAM even for large N.
    """
    rng = rng or np.random.default_rng()
    odds = np.asarray(sample_odds, dtype=float)
    zc = stats.norm.ppf(1 - alpha)
    rows = []
    for e in target_rois:
        for N in n_values:
            chunk = max(1, cell_cap // max(N, 1))
            rejects = 0
            mean_acc = 0.0
            se_acc = 0.0
            done = 0
            while done < n_sims:
                m = min(chunk, n_sims - done)
                O = rng.choice(odds, size=(m, N))
                q = np.clip((1.0 + e) / O, 0.0, 1.0)
                wins = rng.random((m, N)) < q
                r = np.where(wins, O - 1.0, -1.0)
                mean = r.mean(axis=1)
                se = r.std(axis=1, ddof=1) / math.sqrt(N)
                rejects += int((mean / se > zc).sum())
                mean_acc += float(mean.sum())
                se_acc += float(se.sum())
                done += m
            rows.append({
                "target_roi": e,
                "n_bets": N,
                "detect_power": rejects / n_sims,
                "mean_roi": mean_acc / n_sims,
                "roi_se": se_acc / n_sims,
            })
    return rows


def required_n_for_power(sample_odds: Sequence[float], target_roi: float,
                         power: float = 0.8, alpha: float = 0.05,
                         rng: Optional[np.random.Generator] = None) -> int:
    """Closed-form-ish required N for one-sided detection at given power.

    Uses N ~= ((z_alpha + z_power) * sigma / mu)^2, with sigma estimated from the
    odds distribution at a uniform per-bet edge.
    """
    rng = rng or np.random.default_rng()
    odds = np.asarray(sample_odds, dtype=float)
    q = np.clip((1.0 + target_roi) / odds, 0.0, 1.0)
    # variance of unit return r=(O-1) w.p. q else -1, averaged over the odds dist
    mu = float((q * (odds - 1.0) - (1 - q)).mean())
    if mu <= 0:
        return -1
    var = float((q * (odds - 1.0) ** 2 + (1 - q) * 1.0 - 0.0).mean()  # E[r^2] approx
                - (q * (odds - 1.0) - (1 - q)).mean() ** 2)
    sigma = math.sqrt(max(var, 1e-9))
    z = stats.norm.ppf(1 - alpha) + stats.norm.ppf(power)
    return int(math.ceil((z * sigma / mu) ** 2))


# ---------------------------------------------------------------------------
# 5. Signal rules (pluggable). NONE is assumed to work — they are HYPOTHESES.
# ---------------------------------------------------------------------------

def signal_value_vs_model(odds: Sequence[float], model_probs: Sequence[float],
                          threshold: float = 0.03, devig=devig_normalize) -> Optional[int]:
    """Bet the side your model thinks is most underpriced, if the edge clears
    `threshold` (in de-vigged probability terms). Returns the side index or None.

    `model_probs` is YOUR probability estimate for (HOME, DRAW, AWAY). The whole
    experiment is a test of whether your model beats the closing line; the rule
    itself guarantees nothing.
    """
    market = np.asarray(devig(odds))
    model = np.asarray(model_probs, dtype=float)
    edge = model - market                      # positive => model says underpriced
    i = int(np.argmax(edge))
    return i if edge[i] >= threshold else None


def signal_random_control(odds: Sequence[float],
                          rng: Optional[np.random.Generator] = None) -> int:
    """Control arm: pick a side at random. Should show CLV ~ 0 and ROI ~ -margin.
    If a control ever shows positive CLV, your harness has a bug."""
    rng = rng or np.random.default_rng()
    return int(rng.integers(0, len(odds)))


# ---------------------------------------------------------------------------
# 6. Storage (SQLite). One file, append-only snapshots; results and bets settled later.
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL, event_id TEXT, sport TEXT, commence_time INTEGER,
    home TEXT, away TEXT, bookmaker TEXT,
    home_odds REAL, draw_odds REAL, away_odds REAL
);
CREATE INDEX IF NOT EXISTS ix_snap_event ON snapshots(event_id);
CREATE TABLE IF NOT EXISTS results (
    event_id TEXT PRIMARY KEY,
    home_score INTEGER, away_score INTEGER, outcome TEXT, completed_ts REAL
);
CREATE TABLE IF NOT EXISTS bets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT, side TEXT, ts_take REAL, bookmaker TEXT,
    odds_take REAL, p_take_novig REAL, stake REAL,
    odds_close REAL, p_close_novig REAL, won INTEGER, signal TEXT
);
"""


@contextmanager
def db(path: str):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def insert_snapshot(conn, *, ts, event_id, sport, commence_time, home, away,
                    bookmaker, home_odds, draw_odds, away_odds):
    conn.execute(
        "INSERT INTO snapshots(ts,event_id,sport,commence_time,home,away,bookmaker,"
        "home_odds,draw_odds,away_odds) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (ts, event_id, sport, commence_time, home, away, bookmaker,
         home_odds, draw_odds, away_odds))


def insert_result(conn, *, event_id, home_score, away_score, outcome, completed_ts):
    conn.execute(
        "INSERT OR REPLACE INTO results(event_id,home_score,away_score,outcome,"
        "completed_ts) VALUES (?,?,?,?,?)",
        (event_id, home_score, away_score, outcome, completed_ts))


def insert_bet(conn, bet: Bet, signal: str):
    conn.execute(
        "INSERT INTO bets(event_id,side,ts_take,bookmaker,odds_take,p_take_novig,"
        "stake,signal) VALUES (?,?,?,?,?,?,?,?)",
        (bet.event_id, bet.side, bet.ts_take, bet.bookmaker, bet.odds_take,
         bet.p_take_novig, bet.stake, signal))


def closing_line(conn, event_id: str, bookmaker: Optional[str] = None
                 ) -> Optional[tuple[float, float, float]]:
    """Latest snapshot for an event at/just before kickoff -> the closing 1X2 odds."""
    sql = ("SELECT home_odds,draw_odds,away_odds FROM snapshots WHERE event_id=? "
           "AND ts <= COALESCE((SELECT commence_time FROM snapshots WHERE event_id=? "
           "LIMIT 1), ts) ")
    args = [event_id, event_id]
    if bookmaker:
        sql += "AND bookmaker=? "
        args.append(bookmaker)
    sql += "ORDER BY ts DESC LIMIT 1"
    row = conn.execute(sql, args).fetchone()
    if not row:
        return None
    return (row["home_odds"], row["draw_odds"], row["away_odds"])


def load_settled_ledger(conn, devig=devig_normalize) -> list[Bet]:
    """Join bets -> closing line -> result, returning fully-settled Bet objects.

    The closing price and outcome are derived at analysis time, so re-running after
    more results arrive simply settles more bets.
    """
    out: list[Bet] = []
    side_idx = {s: i for i, s in enumerate(OUTCOMES)}
    for r in conn.execute("SELECT * FROM bets").fetchall():
        res = conn.execute("SELECT outcome FROM results WHERE event_id=?",
                            (r["event_id"],)).fetchone()
        if not res:
            continue
        close = closing_line(conn, r["event_id"], r["bookmaker"] or None)
        if not close:
            continue
        i = side_idx[r["side"]]
        p_close = devig(close)[i]
        out.append(Bet(
            event_id=r["event_id"], side=r["side"], odds_take=r["odds_take"],
            p_take_novig=r["p_take_novig"], ts_take=r["ts_take"], stake=r["stake"],
            bookmaker=r["bookmaker"] or "",
            odds_close=close[i], p_close_novig=p_close,
            won=1 if res["outcome"] == r["side"] else 0,
        ))
    return out


# ---------------------------------------------------------------------------
# 7. Calibration table — the HONEST version of the original paper's Section 3.
# ---------------------------------------------------------------------------
# Bin the (de-vigged) closing probabilities and compare each bin's implied
# probability to the empirical win rate. For an efficient market these track the
# diagonal (minus a little margin). A single bucket 30pp off its neighbours is a
# small-sample artifact, not an exploitable signal.

def calibration_table(pairs: Iterable[tuple[float, int]],
                      edges: Sequence[float] = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5,
                                                0.6, 0.7, 0.8, 0.9, 1.0)) -> list[dict]:
    """`pairs` = iterable of (implied_prob_of_side, won 0/1). Returns per-bin stats."""
    p = np.array([a for a, _ in pairs], dtype=float)
    w = np.array([b for _, b in pairs], dtype=float)
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi) if hi < 1.0 else (p >= lo) & (p <= hi)
        n = int(m.sum())
        if n == 0:
            continue
        k = int(w[m].sum())
        ci = wilson_ci(k, n)
        rows.append({
            "bin": f"[{lo:.1f},{hi:.1f})",
            "n": n,
            "mean_implied": float(p[m].mean()),
            "empirical": k / n,
            "wilson_ci95": ci,
            "gap_pp": (k / n - float(p[m].mean())) * 100,
            # well-calibrated within sampling error if implied is inside the Wilson CI
            "calibrated": ci[0] <= float(p[m].mean()) <= ci[1],
        })
    return rows


# ---------------------------------------------------------------------------
# 8. A line-shopping baseline signal (real, testable, no external model needed).
# ---------------------------------------------------------------------------

def signal_consensus_deviation(book_odds: Sequence[tuple[str, Sequence[float]]],
                               threshold: float = 0.02, devig=devig_normalize
                               ) -> Optional[tuple[int, str, float]]:
    """Across several bookmakers' 1X2 quotes, find the book most UNDERPRICING a side
    relative to the multi-book consensus, and bet that side at that book's price.

    `book_odds` = [(bookmaker_name, (home_odds, draw_odds, away_odds)), ...].
    Returns (side_index, bookmaker, take_odds) or None. This is a genuine baseline
    (best-price line shopping); expect only a small CLV and rapid account limiting in
    the real world. It exists to validate the live pipeline and as a yardstick.
    """
    if len(book_odds) < 2:
        return None
    devigged = [np.asarray(devig(o)) for _, o in book_odds]
    consensus = np.median(np.vstack(devigged), axis=0)
    consensus = consensus / consensus.sum()
    best = None  # (value, side, book_name, take_odds)
    for (name, odds), dv in zip(book_odds, devigged):
        value = consensus - dv                      # >0 => this book underprices side
        i = int(np.argmax(value))
        if value[i] >= threshold and (best is None or value[i] > best[0]):
            best = (float(value[i]), i, name, float(odds[i]))
    if best is None:
        return None
    return (best[1], best[2], best[3])

"""
collect.py — one polling run. Wire this to a PythonAnywhere scheduled task.

Each run it:
  1. fetches current 1X2 odds for the configured sports and appends a SNAPSHOT row
     per (event, bookmaker) — this builds the open->close price history;
  2. evaluates the configured SIGNAL and, if it fires and the event has no bet yet,
     records ONE paper bet at the price available right now (the "take" price);
  3. fetches recent SCORES and stores results so bets can be settled at analysis time.

The closing price and the win/lose outcome are derived later by analyze.py, so simply
re-running as results arrive settles more bets. No real money is involved anywhere.

Usage:  python3 collect.py
"""
from __future__ import annotations

import time

import numpy as np

import config as C
from clv_lab import (
    Bet, OUTCOMES, db, devig_normalize, insert_snapshot, insert_result, insert_bet,
    signal_consensus_deviation, signal_value_vs_model, signal_random_control,
)
from odds_client import OddsClient

RNG = np.random.default_rng()
SIDE_IDX = {s: i for i, s in enumerate(OUTCOMES)}


def _event_has_bet(conn, event_id: str) -> bool:
    return conn.execute("SELECT 1 FROM bets WHERE event_id=? LIMIT 1",
                        (event_id,)).fetchone() is not None


def _pick(event: dict):
    """Apply the configured signal. Return (side_idx, bookmaker, take_odds) or None."""
    books = [(b["bookmaker"], (b["home_odds"], b["draw_odds"], b["away_odds"]))
             for b in event["books"]]
    if C.SIGNAL == "consensus":
        return signal_consensus_deviation(books, threshold=C.VALUE_THRESHOLD)
    if C.SIGNAL == "control":
        name, odds = books[RNG.integers(0, len(books))]
        i = signal_random_control(odds, RNG)
        return (i, name, odds[i])
    if C.SIGNAL == "model":
        model = C.MODEL(event)
        if model is None:
            return None
        # evaluate the model against the best (longest) price per side across books
        best = {}
        for name, odds in books:
            for i, o in enumerate(odds):
                if i not in best or o > best[i][1]:
                    best[i] = (name, o)
        ref_odds = [best[i][1] for i in range(3)]
        i = signal_value_vs_model(ref_odds, model, threshold=C.VALUE_THRESHOLD)
        return None if i is None else (i, best[i][0], best[i][1])
    raise ValueError(f"unknown SIGNAL {C.SIGNAL!r}")


def run() -> None:
    client = OddsClient(C.ODDS_API_KEY)
    now = time.time()
    with db(C.DB_PATH) as conn:
        n_snap = n_bet = n_res = 0
        for sport in C.SPORTS:
            for ev in client.get_odds(sport, C.REGIONS, C.MARKETS):
                for b in ev["books"]:
                    insert_snapshot(conn, ts=now, event_id=ev["event_id"],
                                    sport=sport, commence_time=ev["commence_time"],
                                    home=ev["home"], away=ev["away"],
                                    bookmaker=b["bookmaker"], home_odds=b["home_odds"],
                                    draw_odds=b["draw_odds"], away_odds=b["away_odds"])
                    n_snap += 1
                # only bet before kickoff and only once per event
                if ev["commence_time"] > now and not _event_has_bet(conn, ev["event_id"]):
                    pick = _pick(ev)
                    if pick:
                        side_i, book, take = pick
                        # take price's own book quote, for a clean per-book closing line
                        quote = next((bk for bk in ev["books"]
                                      if bk["bookmaker"] == book), None)
                        if quote:
                            p_take = devig_normalize((quote["home_odds"],
                                                      quote["draw_odds"],
                                                      quote["away_odds"]))[side_i]
                            insert_bet(conn, Bet(
                                event_id=ev["event_id"], side=OUTCOMES[side_i],
                                odds_take=take, p_take_novig=float(p_take),
                                ts_take=now, bookmaker=book, stake=1.0), C.SIGNAL)
                            n_bet += 1
            for sc in client.get_scores(sport, C.SCORES_DAYS_FROM):
                if sc["completed"] and sc["outcome"]:
                    insert_result(conn, event_id=sc["event_id"],
                                  home_score=sc["home_score"],
                                  away_score=sc["away_score"],
                                  outcome=sc["outcome"], completed_ts=now)
                    n_res += 1
        rem = client.requests_remaining
        print(f"[{time.strftime('%Y-%m-%d %H:%M')}] snapshots+{n_snap} bets+{n_bet} "
              f"results+{n_res} | quota remaining: {rem if rem is not None else '?'}")


if __name__ == "__main__":
    run()

"""
notify.py — pre-kickoff alerts for the CLV lab.

Around `LEAD_MINUTES` before each match it: (1) fetches and STORES the latest 1X2
odds for the configured sports (this is the automatic odds update, and it also
extends the open->close history analyze.py needs); (2) evaluates the configured
SIGNAL; and (3) if the signal fires for a match kicking off inside the lead window
and that match has not been alerted yet, sends ONE email and records the paper bet.

No real money is involved. The email is an alert about a paper signal, not advice.

Two ways to run it:

  python3 notify.py                 # one pass — wire to a 5-minute scheduler
  python3 notify.py --loop          # self-scheduling daemon (PythonAnywhere always-on)

The loop is quota-aware: between matches it sleeps (re-checking at most hourly), and
near a kickoff it wakes about LEAD_MINUTES before. Each wake costs ~1 Odds API credit
per sport, because one /odds call returns every event for that sport at once.

Secrets come from the environment — never hard-code them, and never put them in the
website (a GitHub Pages site is public). You provide:

  ODDS_API_KEY     your the-odds-api.com key (also used by collect.py)
  SMTP_HOST        e.g. smtp.gmail.com
  SMTP_PORT        587 (STARTTLS) or 465 (SSL); default 587
  SMTP_USER        the mailbox login
  SMTP_PASS        an app password (for Gmail, a 16-char app password, NOT your login)
  EMAIL_FROM       sender address; defaults to SMTP_USER
  EMAIL_TO         recipient(s), comma-separated
"""
from __future__ import annotations

import argparse
import os
import smtplib
import ssl
import sys
import time
from datetime import datetime, timezone
from email.message import EmailMessage

import numpy as np

import config as C
from clv_lab import (
    Bet, OUTCOMES, db, devig_normalize, insert_snapshot, insert_bet,
    signal_consensus_deviation, signal_value_vs_model, signal_random_control,
)
from odds_client import OddsClient

LEAD_MINUTES = float(os.environ.get("LEAD_MINUTES", "5"))    # alert this long before KO
GRACE_MINUTES = 1.0                                          # tolerate a slightly late run
RNG = np.random.default_rng()


# --- dedupe: remember which events were already alerted ---------------------

def _ensure_table(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS notified "
                 "(event_id TEXT PRIMARY KEY, ts REAL)")


def _already_notified(conn, event_id: str) -> bool:
    return conn.execute("SELECT 1 FROM notified WHERE event_id=?",
                        (event_id,)).fetchone() is not None


def _mark_notified(conn, event_id: str):
    conn.execute("INSERT OR REPLACE INTO notified(event_id, ts) VALUES (?,?)",
                 (event_id, time.time()))


# --- the signal (mirrors collect.py so paper bets are consistent) -----------

def _pick(event: dict):
    """Apply C.SIGNAL. Return (side_idx, bookmaker, take_odds) or None."""
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
        best = {}
        for name, odds in books:
            for i, o in enumerate(odds):
                if i not in best or o > best[i][1]:
                    best[i] = (name, o)
        ref = [best[i][1] for i in range(3)]
        i = signal_value_vs_model(ref, model, threshold=C.VALUE_THRESHOLD)
        return None if i is None else (i, best[i][0], best[i][1])
    raise ValueError(f"unknown SIGNAL {C.SIGNAL!r}")


# --- email ------------------------------------------------------------------

def _smtp_config() -> dict:
    cfg = {
        "host": os.environ.get("SMTP_HOST"),
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": os.environ.get("SMTP_USER"),
        "password": os.environ.get("SMTP_PASS"),
        "to": os.environ.get("EMAIL_TO"),
    }
    cfg["from"] = os.environ.get("EMAIL_FROM", cfg["user"])
    missing = [k for k in ("host", "user", "password", "to") if not cfg[k]]
    if missing:
        raise RuntimeError("missing SMTP settings: " + ", ".join(
            {"host": "SMTP_HOST", "user": "SMTP_USER",
             "password": "SMTP_PASS", "to": "EMAIL_TO"}[m] for m in missing))
    return cfg


def send_email(subject: str, body: str) -> None:
    cfg = _smtp_config()
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["from"]
    msg["To"] = cfg["to"]
    msg.set_content(body)
    recipients = [a.strip() for a in cfg["to"].split(",") if a.strip()]
    if cfg["port"] == 465:
        with smtplib.SMTP_SSL(cfg["host"], cfg["port"],
                              context=ssl.create_default_context()) as s:
            s.login(cfg["user"], cfg["password"])
            s.send_message(msg, to_addrs=recipients)
    else:
        with smtplib.SMTP(cfg["host"], cfg["port"]) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(cfg["user"], cfg["password"])
            s.send_message(msg, to_addrs=recipients)


def _compose(event: dict, side_i: int, book: str, take: float,
             minutes: float) -> tuple[str, str]:
    side = OUTCOMES[side_i]
    label = {"HOME": event["home"], "DRAW": "Unentschieden",
             "AWAY": event["away"]}[side]
    quote = next((b for b in event["books"] if b["bookmaker"] == book), None)
    p_take = devig_normalize((quote["home_odds"], quote["draw_odds"],
                              quote["away_odds"]))[side_i] if quote else float("nan")
    # consensus de-vig across all books, for the edge figure
    dv = np.vstack([devig_normalize((b["home_odds"], b["draw_odds"], b["away_odds"]))
                    for b in event["books"]])
    consensus = dv.mean(axis=0)
    edge_pp = (p_take - consensus[side_i]) * 100
    ko = datetime.fromtimestamp(event["commence_time"], timezone.utc)

    subject = (f"⚽ {event['home']} – {event['away']} | Anpfiff in ~{minutes:.0f} Min "
               f"| {label} @ {take:.2f}")
    lines = [
        f"{event['home']} vs {event['away']}",
        f"Anpfiff: {ko:%Y-%m-%d %H:%M} UTC  (in ~{minutes:.0f} Minuten)",
        "",
        f"Signal ({C.SIGNAL}):  {label}",
        f"Bester Preis:  {take:.2f} bei {book}",
        f"Entvigte W'keit (dieser Preis):  {p_take*100:.1f}%",
        f"Konsens (alle Buchmacher):  {consensus[side_i]*100:.1f}%",
        f"Vorteil ggü. Konsens:  {edge_pp:+.1f} Prozentpunkte",
        "",
        "Aktuelle beste Quoten (über alle Buchmacher):",
        f"  {event['home']}: {max(b['home_odds'] for b in event['books']):.2f}",
        f"  Unentschieden: {max(b['draw_odds'] for b in event['books']):.2f}",
        f"  {event['away']}: {max(b['away_odds'] for b in event['books']):.2f}",
        "",
        "Hinweis: Paper-Trading. Dies ist ein Mess-Signal, keine Wettempfehlung und",
        "keine Finanzberatung. Der erwartete realisierbare Edge ist ~0 (siehe Paper).",
    ]
    return subject, "\n".join(lines)


# --- one polling pass -------------------------------------------------------

def run_once(send: bool = True) -> list[dict]:
    """Fetch+store odds for all sports, alert any match inside the lead window.
    Returns the list of all events seen (for the loop scheduler)."""
    client = OddsClient(C.ODDS_API_KEY)
    now = time.time()
    all_events: list[dict] = []
    n_snap = n_alert = 0
    with db(C.DB_PATH) as conn:
        _ensure_table(conn)
        for sport in C.SPORTS:
            events = client.get_odds(sport, C.REGIONS, C.MARKETS)
            all_events.extend(events)
            for ev in events:
                # (1) automatic odds update: store every book's current quote
                for b in ev["books"]:
                    insert_snapshot(conn, ts=now, event_id=ev["event_id"],
                                    sport=sport, commence_time=ev["commence_time"],
                                    home=ev["home"], away=ev["away"],
                                    bookmaker=b["bookmaker"], home_odds=b["home_odds"],
                                    draw_odds=b["draw_odds"], away_odds=b["away_odds"])
                    n_snap += 1
                # (2) is kickoff inside the lead window, and not yet alerted?
                minutes = (ev["commence_time"] - now) / 60.0
                if not (-GRACE_MINUTES < minutes <= LEAD_MINUTES):
                    continue
                if _already_notified(conn, ev["event_id"]):
                    continue
                pick = _pick(ev)
                if not pick:
                    continue
                side_i, book, take = pick
                subject, body = _compose(ev, side_i, book, take, max(minutes, 0))
                if send:
                    send_email(subject, body)
                # record the paper bet at the take price
                quote = next((b for b in ev["books"] if b["bookmaker"] == book), None)
                if quote:
                    p_take = devig_normalize((quote["home_odds"], quote["draw_odds"],
                                              quote["away_odds"]))[side_i]
                    insert_bet(conn, Bet(event_id=ev["event_id"], side=OUTCOMES[side_i],
                                         odds_take=take, p_take_novig=float(p_take),
                                         ts_take=now, bookmaker=book, stake=1.0),
                               C.SIGNAL)
                _mark_notified(conn, ev["event_id"])
                n_alert += 1
        rem = client.requests_remaining
    print(f"[{time.strftime('%Y-%m-%d %H:%M')}] snapshots+{n_snap} alerts+{n_alert} "
          f"| quota remaining: {rem if rem is not None else '?'}")
    return all_events


# --- self-scheduling loop (quota-aware) -------------------------------------

def _seconds_until_next_window(events: list[dict], now: float) -> float | None:
    targets = [ev["commence_time"] - LEAD_MINUTES * 60 for ev in events
               if ev["commence_time"] - LEAD_MINUTES * 60 > now]
    return (min(targets) - now) if targets else None


def run_loop(idle_cap_seconds: float = 3600.0, min_sleep: float = 30.0) -> None:
    print(f"[notify] loop started | sport(s)={list(C.SPORTS)} | "
          f"lead={LEAD_MINUTES:.0f} min | signal={C.SIGNAL}")
    while True:
        try:
            events = run_once(send=True)
        except Exception as e:                       # keep the daemon alive
            print(f"[notify] pass failed: {e}", file=sys.stderr)
            time.sleep(min_sleep * 4)
            continue
        nxt = _seconds_until_next_window(events, time.time())
        sleep = idle_cap_seconds if nxt is None else max(min_sleep,
                                                         min(nxt, idle_cap_seconds))
        print(f"[notify] sleeping {sleep/60:.1f} min")
        time.sleep(sleep)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Pre-kickoff CLV alerts.")
    ap.add_argument("--loop", action="store_true",
                    help="run as a self-scheduling daemon (e.g. PythonAnywhere always-on)")
    ap.add_argument("--dry-run", action="store_true",
                    help="do everything except actually send email")
    args = ap.parse_args()
    if args.loop:
        run_loop()
    else:
        run_once(send=not args.dry_run)

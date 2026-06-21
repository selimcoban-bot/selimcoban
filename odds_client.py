"""
odds_client.py — a small client for The Odds API v4 (the-odds-api.com).

It reads your key from the ODDS_API_KEY environment variable. It is NOT hard-coded:
a key committed to a file should be treated as compromised. Set it once on the host
(see README) and rotate the one you pasted into chat.

Endpoints used (each /odds or /scores call returns ALL events for the sport, so it
costs ~1 request regardless of how many matches come back):
  GET /v4/sports/{sport}/odds   ?regions=eu&markets=h2h&oddsFormat=decimal
  GET /v4/sports/{sport}/scores ?daysFrom=3
Quota is reported back in the x-requests-remaining / x-requests-used headers.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

import requests

BASE = "https://api.the-odds-api.com/v4"


class OddsAPIError(RuntimeError):
    pass


def _iso_to_unix(s: str) -> int:
    return int(datetime.fromisoformat(s.replace("Z", "+00:00"))
               .astimezone(timezone.utc).timestamp())


class OddsClient:
    def __init__(self, api_key: Optional[str] = None, timeout: float = 20.0):
        self.api_key = api_key or os.environ.get("ODDS_API_KEY")
        if not self.api_key:
            raise OddsAPIError(
                "No API key. Set it with:  export ODDS_API_KEY=your_key_here")
        self.timeout = timeout
        self.session = requests.Session()
        self.requests_remaining: Optional[int] = None
        self.requests_used: Optional[int] = None

    def _get(self, path: str, **params) -> list:
        params["apiKey"] = self.api_key
        r = self.session.get(f"{BASE}{path}", params=params, timeout=self.timeout)
        # quota headers come back on every call
        self.requests_remaining = _safe_int(r.headers.get("x-requests-remaining"))
        self.requests_used = _safe_int(r.headers.get("x-requests-used"))
        if r.status_code == 401:
            raise OddsAPIError("401 Unauthorized — bad or revoked API key.")
        if r.status_code == 429:
            raise OddsAPIError("429 — out of quota or rate limited.")
        if not r.ok:
            raise OddsAPIError(f"{r.status_code}: {r.text[:300]}")
        return r.json()

    def list_sports(self) -> list[dict]:
        """All available sport keys (e.g. soccer_epl, soccer_uefa_champs_league)."""
        return self._get("/sports/")

    def get_odds(self, sport: str, regions: str = "eu", markets: str = "h2h",
                 odds_format: str = "decimal") -> list[dict]:
        """Return normalised events with per-bookmaker 1X2 odds.

        Each event: {event_id, sport, commence_time(unix), home, away,
                     books:[{bookmaker, home_odds, draw_odds, away_odds}]}.
        Only bookmakers offering a complete 1X2 (home/draw/away) are kept.
        """
        raw = self._get(f"/sports/{sport}/odds/", regions=regions, markets=markets,
                        oddsFormat=odds_format, dateFormat="iso")
        events = []
        for ev in raw:
            home, away = ev.get("home_team"), ev.get("away_team")
            books = []
            for bk in ev.get("bookmakers", []):
                h2h = next((m for m in bk.get("markets", []) if m.get("key") == "h2h"),
                           None)
                if not h2h:
                    continue
                price = {o["name"]: o["price"] for o in h2h.get("outcomes", [])}
                if home in price and away in price and "Draw" in price:
                    books.append({
                        "bookmaker": bk.get("key", ""),
                        "home_odds": float(price[home]),
                        "draw_odds": float(price["Draw"]),
                        "away_odds": float(price[away]),
                    })
            if books:
                events.append({
                    "event_id": ev["id"], "sport": sport,
                    "commence_time": _iso_to_unix(ev["commence_time"]),
                    "home": home, "away": away, "books": books,
                })
        return events

    def get_scores(self, sport: str, days_from: int = 3) -> list[dict]:
        """Return settled results: {event_id, completed, home_score, away_score,
        outcome in {HOME,DRAW,AWAY} or None, commence_time(unix)}."""
        raw = self._get(f"/sports/{sport}/scores/", daysFrom=days_from,
                        dateFormat="iso")
        out = []
        for ev in raw:
            home, away = ev.get("home_team"), ev.get("away_team")
            scores = {s["name"]: s.get("score") for s in (ev.get("scores") or [])}
            hs = _safe_int(scores.get(home))
            as_ = _safe_int(scores.get(away))
            outcome = None
            if ev.get("completed") and hs is not None and as_ is not None:
                outcome = "HOME" if hs > as_ else "AWAY" if as_ > hs else "DRAW"
            out.append({
                "event_id": ev["id"], "completed": bool(ev.get("completed")),
                "home_score": hs, "away_score": as_, "outcome": outcome,
                "commence_time": _iso_to_unix(ev["commence_time"])
                if ev.get("commence_time") else None,
            })
        return out


def _safe_int(x) -> Optional[int]:
    try:
        return int(x)
    except (TypeError, ValueError):
        return None

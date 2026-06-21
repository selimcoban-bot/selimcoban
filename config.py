"""
config.py — settings for the CLV laboratory. Edit the constants; keep secrets in env.
"""
import os
from typing import Optional, Sequence

# --- secrets & paths (from environment; never commit the key) ---------------
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")        # export ODDS_API_KEY=...
DB_PATH = os.environ.get("CLV_DB", "clv_lab.db")

# --- what to collect --------------------------------------------------------
# Find valid keys by running:  python3 -c "from odds_client import OddsClient; \
#   [print(s['key']) for s in OddsClient().list_sports() if s['key'].startswith('soccer')]"
SPORTS: Sequence[str] = ("soccer_epl",)              # add more leagues as you like
REGIONS = "eu"                                       # eu books quote 1X2 decimal
MARKETS = "h2h"                                      # match-odds (1X2)
SCORES_DAYS_FROM = 3                                 # look back window for results

# --- the signal under test --------------------------------------------------
# "consensus" : line-shopping baseline (no model needed) — validates the pipeline.
# "model"     : your own probabilities via MODEL below — the real experiment.
# "control"   : random side — sanity check; must show CLV ~ 0.
SIGNAL = "consensus"
VALUE_THRESHOLD = 0.02                               # min de-vig edge to fire (prob)


def MODEL(event: dict) -> Optional[Sequence[float]]:
    """Return YOUR (home, draw, away) probability estimate for an event, or None to skip.

    `event` is the normalised dict from OddsClient.get_odds (has home/away/books).
    This is where a real edge would live. Until you fill it in, SIGNAL="model" skips
    everything. Example stub (consensus — i.e. no edge):

        import numpy as np
        from clv_lab import devig_normalize
        dv = [np.asarray(devig_normalize((b['home_odds'], b['draw_odds'], b['away_odds'])))
              for b in event['books']]
        p = np.median(np.vstack(dv), axis=0); return (p / p.sum()).tolist()
    """
    return None

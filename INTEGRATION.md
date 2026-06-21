# Integrating the CLV lab into wm2026-dashboard

This adds a **Modell / Code / Methodik** page to your existing dashboard, plus the
backend that powers the pre-kickoff email alerts. It does not touch real money, and
it does not put your API key anywhere public.

## 1. Drop the files into the repo

Put **all** of these in the repository **root**, next to your existing `index.html`:

```
methodik.html        # the new page (self-contained: its own CSS + JS)
protocol.md          # the paper (the page renders it)
clv_lab.py           # engine
odds_client.py       # The Odds API client
collect.py           # data collection pass
analyze.py           # the CLV report
notify.py            # the email alerter
simulate.py          # offline validation / Monte Carlo
config.py            # settings
.github/workflows/notify.yml   # optional scheduler (see §5, option B)
```

The page loads the `.py` files and `protocol.md` with relative `fetch('./…')`, so they
must be siblings of `methodik.html`. On GitHub Pages the page will live at
`https://godxblazeee.github.io/wm2026-dashboard/methodik.html`.

If you prefer the code in a subfolder, move the `.py`/`.md` there and change the two
`fetch('./…')` paths near the bottom of `methodik.html` (the code loader and the paper
loader) to `fetch('./yourfolder/…')`.

## 2. Add the nav link in your `index.html`

The new page already mirrors your nav (`Dashboard / Alle Spiele / Gruppen / WM-Sieg`).
To link to it from the dashboard, add one item to your existing nav — matching whatever
markup your nav uses, e.g.:

```html
<a href="methodik.html">🔬 Modell</a>
```

That is the only edit to `index.html`. Everything else is additive.

## 3. Match the look exactly (optional)

`methodik.html` ships with a neutral dark dashboard palette defined as CSS variables at
the very top (`:root { --bg, --surface, --green, --blue, --amber … }`) and Inter +
JetBrains Mono. If your dashboard uses different colors or fonts, change those variables
(and the two `@font-face`/Google-Fonts links) to your values — it's a two-minute job. Or
paste me your `index.html`/CSS and I'll set them to match precisely.

## 4. Preview locally

`fetch()` is blocked on `file://`, so opening `methodik.html` by double-click shows empty
code/paper panels. Serve it instead:

```bash
cd path/to/repo
python3 -m http.server 8000
# open http://localhost:8000/methodik.html
```

On GitHub Pages it just works (served over https).

## 5. The email alerts (`notify.py`)

`notify.py` refreshes and stores the odds ~5 minutes before each kickoff, evaluates the
configured signal, and emails one alert per match (deduplicated). It needs an Odds API
key and SMTP credentials, supplied as **environment variables — never in a file, never in
the website** (the site is public; a key in client-side code would be exposed and abused).

Variables:

| Variable | What it is |
|---|---|
| `ODDS_API_KEY` | your the-odds-api.com key (rotate the one you pasted in chat) |
| `SMTP_HOST` | e.g. `smtp.gmail.com` |
| `SMTP_PORT` | `587` (STARTTLS) or `465` (SSL); default `587` |
| `SMTP_USER` | mailbox login |
| `SMTP_PASS` | an **app password** (Gmail: a 16-char app password, not your login) |
| `EMAIL_FROM` | sender; defaults to `SMTP_USER` |
| `EMAIL_TO` | recipient(s), comma-separated |
| `LEAD_MINUTES` | optional; how long before kickoff to alert (default `5`) |

Gmail app password: enable 2-Step Verification, then Google Account → Security → App
passwords → create one for "Mail", and use that 16-character value as `SMTP_PASS`.

Test it without waiting for a match (writes nothing, sends nothing):

```bash
export ODDS_API_KEY=... SMTP_HOST=... SMTP_USER=... SMTP_PASS=... EMAIL_TO=...
python3 notify.py --dry-run
```

### Option A — always-on host (recommended)

On PythonAnywhere (or any small VPS) run the self-scheduling loop as an **always-on task**:

```bash
ODDS_API_KEY=... SMTP_HOST=... SMTP_USER=... SMTP_PASS=... EMAIL_TO=... \
  CLV_DB=/home/youruser/wm2026-dashboard/clv_lab.db \
  python3 /home/youruser/wm2026-dashboard/notify.py --loop
```

The loop sleeps between matches and wakes ~`LEAD_MINUTES` before each kickoff, so it
spends only ~1 Odds API credit per wake and keeps a persistent SQLite ledger (so dedupe
and the open→close history actually accumulate). This is the clean home for it.

### Option B — GitHub Actions (convenience)

`.github/workflows/notify.yml` runs `notify.py` on a cron. Set the seven secrets under
**Settings → Secrets and variables → Actions**. Caveats are in the workflow's header
comments and matter: GitHub cron is best-effort (timing drifts), runners are ephemeral
(state persists only via a best-effort cache, so an alert can occasionally repeat), and
every run costs API credits. The default cron is scoped to 16:00–22:55 UTC every 5
minutes — edit it to your kickoff window, and widen it only on a paid Odds API plan.

## 6. Odds API quota — the arithmetic

Free tier is **500 credits/month**. One `/odds` call (one market, one region) costs **1
credit** and returns every event for that sport at once. So:

- Always-on loop, idle most of the day, a handful of matches: tens of credits/month → free.
- A 5-minute cron running 24/7: ~8,600 credits/month → far over free; needs a paid plan.

Scope your schedule to the hours matches actually kick off, or move to a paid Odds API
tier.

## 7. What this is (and isn't)

The page states it plainly and so will I: this is a **measurement instrument** for closing
-line value, not a profitable betting system. The expected realizable edge is ≈ 0, the
measurable "close" here is a soft-book consensus (no Pinnacle), and soft books limit
exactly the accounts that beat the close. The alerts are research signals, not advice. The
honesty is deliberate — it's also what keeps the public page credible.

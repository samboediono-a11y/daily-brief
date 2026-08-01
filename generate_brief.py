#!/usr/bin/env python3
"""
generate_brief.py  (100%-free version, with archive + real links)

How this stays free, end to end:
  1. Headlines come from Google News RSS feeds — public, free, no API key.
  2. The "analyst take" writing is done by Google's Gemini API free tier
     — free, no credit card, no expiration. This script makes ONE request
     per day, far under the free daily limit.
  3. Hosting is GitHub Pages — free.

How the real links work:
  The AI never invents a URL. Instead, it picks a story by its INDEX from
  the real candidates list, and this script looks up that candidate's
  actual link from the RSS feed. The headline is rewritten for clarity,
  but it always links to the real original article.

How the archive works:
  Each day's edition is saved as its own permanent page
  (daily-brief-YYYY-MM-DD.html). On every run, this script also checks
  out whatever is already published (the existing GitHub Pages site),
  so old editions are never lost — they just accumulate, like a running
  set of tabs across days. A tab strip at the top of every page links
  across all editions.

Requires:
    pip install -r requirements.txt

Environment variables:
    GEMINI_API_KEY   - required, free key from https://aistudio.google.com/apikey

Usage:
    python generate_brief.py
    -> writes ./output/index.html, ./output/daily-brief-YYYY-MM-DD.html,
       and updates the day-tabs strip across all previously published editions.
"""

import os
import re
import time
import json
import shutil
import datetime
import urllib.parse
from pathlib import Path

import feedparser
import requests
from google import genai

STORIES_PER_SECTION = 4  # change to taste
GEMINI_MODEL = "gemini-3-flash-preview"  # free-tier model, current as of July 2026
CANDIDATES_PER_SECTION = 20  # how many raw headlines to fetch before picking the best ones
EARNINGS_LOOKAHEAD_DAYS = 7   # how many calendar days ahead to check for earnings
EARNINGS_MAX_ROWS = 8         # how many upcoming earnings to show, picked by market cap
EARNINGS_ACCENT = "#A78BFA"   # violet, distinct from the four news section colors
MOVERS_ACCENT = "#F472B6"    # pink, distinct from earnings and the four news colors
TRENDING_ACCENT = "#FDE047"   # bright yellow, signals "hot right now"
TRENDING_MAX_ROWS = 5

CALLS_ACCENT = "#34D399"      # emerald, distinct from the other sidebar widgets
MAX_CALLS_TO_CHECK = 6         # how many of the previous edition's predictions to check
TRACK_RECORD_WINDOW_DAYS = 30  # how far back to look for the "% held up" stat

CRYPTO_ACCENT = "#818CF8"     # indigo, distinct from the other sidebar widgets
CRYPTO_IDS = ["bitcoin", "ethereum", "solana"]  # CoinGecko ids; edit to add/remove coins
CRYPTO_LABELS = {"bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL"}

MOVERS_PER_SIDE = 5           # how many gainers / losers to show

# A curated watchlist of major tech names (not just AI-specific) used to
# narrow the "movers" search to relevant companies rather than the whole
# market. Feel free to edit this list.
TECH_WATCHLIST = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "NFLX",
    "AMD", "INTC", "CSCO", "ORCL", "CRM", "ADBE", "IBM", "QCOM",
    "TXN", "PYPL", "UBER", "NOW", "AVGO", "TSM", "PLTR", "SMCI",
    "ARM", "MRVL", "SNOW", "DELL", "MU", "ASML",
]

# Where to look for an already-published site (so old editions aren't lost).
# The workflow checks this branch out into this folder before running the script.
EXISTING_SITE_DIR = Path(__file__).parent / "existing-site"


def _google_news_url(query: str, hl: str, gl: str, ceid: str) -> str:
    q = urllib.parse.quote(query)
    return f"https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={ceid}"


# Each section: (key, label, accent color, RSS URL)
SECTIONS = [
    (
        "tech",
        "TECH",
        "#2DD4BF",
        _google_news_url("technology industry", "en-US", "US", "US:en"),
    ),
    (
        "markets",
        "MARKETS",
        "#F5A623",
        _google_news_url("stock market", "en-US", "US", "US:en"),
    ),
    (
        "us_news",
        "US NEWS",
        "#5FA8FF",
        "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",  # top US headlines
    ),
    (
        "indonesia",
        "INDONESIA",
        "#F87171",
        _google_news_url("Indonesia economy OR Indonesia politics", "en-US", "ID", "ID:en"),
    ),
    (
        "crypto_fx",
        "CRYPTO & FX",
        "#22D3EE",
        _google_news_url("cryptocurrency bitcoin OR forex dollar exchange rate", "en-US", "US", "US:en"),
    ),
]

PROMPT_TEMPLATE = """You are a sharp business/economics analyst writing a daily briefing
for a smart friend who does not want jargon. Below are real candidate headlines
(each with an INDEX number, source, and short snippet) fetched today for each
section. For each section, pick the {n} most important, non-duplicate stories.

For each story, return:
  - "source_index": the INDEX number (integer) of the candidate you picked,
    exactly as shown in brackets before it in that section's list
  - "headline": a punchy plain-English headline (under 12 words), rewritten
    in your own words — do not copy the source headline verbatim
  - "summary": 1-2 plain sentences on what actually happened
  - "meaning": 2-3 sentences of REAL analysis, written like a sharp business/
    economics analyst explaining it to a smart non-expert. Name who wins or
    loses, the actual mechanism (how it moves prices, supply, competition,
    policy, or sentiment), and a concrete thing to watch next. Be specific
    and opinionated about the stakes, not vague or generic — but keep the
    language plain and jargon-free, as if explaining it to a sharp friend
    over coffee, not writing a research note.

Return ONLY valid JSON (no markdown fences, no commentary) in exactly this shape:
{{
  "date": "{date}",
  "throughline": "one sharp sentence tying today's news together",
  "sections": {{
    "tech": [{{"source_index": 0, "headline": "...", "summary": "...", "meaning": "..."}}, ...],
    "markets": [...],
    "us_news": [...],
    "indonesia": [...],
    "crypto_fx": [...]
  }}
}}

Candidate headlines by section:
{candidates}
"""

ANALYST_SNIPPETS_PER_STORY = 5  # how many real "analyst reaction" headlines to look for per story

MEANING_PROMPT_TEMPLATE = """You are a sharp, senior analyst writing your own research note on {count}
already-selected news stories, organized by section. For EACH story you're
given the headline, a summary, and (if any were found) real headlines from
other outlets that reference analyst, researcher, or expert reactions to that
same story or topic.

For each story, write your own analyst note in three parts:

  - "view": your read on what's really going on - 2-3 sentences, confident
    and specific. Name who wins or loses and the actual mechanism (how it
    moves prices, supply, competition, policy, or sentiment). Write this the
    way a sharp analyst thinks out loud in their own notes: opinionated,
    concrete, and textured - never hedgy filler like "this could have
    implications" or "it remains to be seen." Take a real position.

  - "bear_case": the single strongest reason your "view" could be wrong -
    1-2 sentences. A real, specific risk or counter-argument (e.g. a
    competing force, a data point that cuts the other way, a way the
    timeline slips) - not a throwaway "of course, things could change."
    Genuinely try to poke a hole in your own view here.

  - "prediction": one concrete, falsifiable forecast of what happens next -
    a specific thing to watch for, ideally with a rough timeframe (e.g.
    "expect X within the next earnings cycle" or "watch for Y before
    month-end"). Not vague ("things may change") - commit to a real call.

This should read like genuine analyst thinking either way - whether or not
real outside commentary was found below. The bar is the SAME regardless of
whether a source was found: confident, specific, well-reasoned - just be
honest about which kind of thinking it is (see below).

IMPORTANT — be honest about your source, do not fake research:
- If a story's "analyst headlines found" give you a genuine, specific read on
  what real analysts/researchers/experts are actually saying about it, weave
  that into your "view" and set "grounded": true.
- If that list is empty, or is just unrelated news rather than actual expert
  reactions, this becomes YOUR OWN analysis - reasoned with the same rigor
  and confidence, but don't invent or attribute opinions to anyone specific.
  Set "grounded": false. Never claim outside research backing that isn't real.

Return ONLY valid JSON (no markdown fences, no commentary) in exactly this
shape, with one entry per story per section, in the same order they were given:
{{
  "tech": [{{"index": 0, "view": "...", "bear_case": "...", "prediction": "...", "grounded": true}}, ...],
  "markets": [...],
  "us_news": [...],
  "indonesia": [...],
  "crypto_fx": [...]
}}

Stories:
{stories}
"""


CALLCHECK_PROMPT_TEMPLATE = """You are reviewing your own past predictions to check if they held up - like
a disciplined analyst keeping themselves honest. Below are {count} predictions
made in a previous edition (from {prev_date}), each with the original
headline and prediction, plus (if found) fresh headlines from today that
might confirm, contradict, or say nothing new about it yet.

For each one, classify its status:
  - "held_up": today's fresh headlines clearly support that the prediction
    came true or is on track.
  - "missed": today's fresh headlines clearly contradict it, or enough has
    happened that it clearly didn't play out as predicted.
  - "too_early": not enough new information yet to say either way. This is
    the honest default - do NOT guess "held_up" or "missed" just to seem
    decisive. Only call it one way or the other if the evidence genuinely
    shows it.

Also write a one-sentence "note" explaining your call, referencing what you
actually found (or the lack of it) - do not fabricate evidence that isn't in
the fresh headlines given.

Return ONLY valid JSON (no markdown fences, no commentary) in this shape,
one entry per prediction, in the same order given:
{{"checks": [{{"index": 0, "status": "held_up", "note": "..."}}, ...]}}

Predictions to check:
{predictions}
"""


def fetch_upcoming_earnings(days_ahead: int = EARNINGS_LOOKAHEAD_DAYS, max_rows: int = EARNINGS_MAX_ROWS) -> list[dict]:
    """Pulls upcoming earnings dates from Nasdaq's public earnings-calendar
    endpoint (no API key needed). This is an unofficial endpoint, so this
    function fails SAFELY: if it errors for any reason (network hiccup,
    Nasdaq changing their response format, etc.), it just returns an empty
    list rather than breaking the whole daily brief.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }
    session_labels = {
        "time-before-open": "Before Open",
        "time-after-hours": "After Close",
        "time-not-supplied": "Time TBD",
    }

    rows = []
    today = datetime.date.today()
    for i in range(days_ahead):
        day = today + datetime.timedelta(days=i)
        # Nasdaq's calendar is empty on weekends anyway; skip them to save calls.
        if day.weekday() >= 5:
            continue
        date_str = day.isoformat()
        try:
            resp = requests.get(
                "https://api.nasdaq.com/api/calendar/earnings",
                params={"date": date_str},
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            payload = resp.json()
            day_rows = (payload.get("data") or {}).get("rows") or []
        except Exception as e:
            print(f"[earnings] Skipping {date_str}: {e}")
            continue

        for r in day_rows:
            try:
                market_cap_raw = (r.get("marketCap") or "").replace("$", "").replace(",", "")
                try:
                    market_cap = float(market_cap_raw) if market_cap_raw else 0.0
                except ValueError:
                    market_cap = 0.0
                rows.append(
                    {
                        "symbol": (r.get("symbol") or "").strip(),
                        "name": (r.get("name") or "").strip(),
                        "date": date_str,
                        "session": session_labels.get(r.get("time", ""), "Time TBD"),
                        "eps_forecast": r.get("epsForecast") or None,
                        "market_cap": market_cap,
                    }
                )
            except Exception as e:
                print(f"[earnings] Skipping a malformed row: {e}")
                continue

    rows.sort(key=lambda r: r["market_cap"], reverse=True)
    return rows[:max_rows]


def fetch_tech_stock_movers(watchlist: list[str] = TECH_WATCHLIST, per_side: int = MOVERS_PER_SIDE) -> dict:
    """Pulls today's % change for a curated list of major tech stocks from
    Finnhub's free-tier quote API (60 calls/minute, no credit card).

    Note: this used to try Yahoo Finance, then Stooq - both broke. Yahoo
    now requires a session "crumb"/cookie for its quote endpoint. Stooq has
    a well-documented, very low per-IP daily quota ("Exceeded the daily
    hits limit"), and GitHub Actions IPs are shared across huge numbers of
    repos worldwide - very likely already exhausted before this job even
    runs. Finnhub needs one extra free API key (FINNHUB_API_KEY, same setup
    as GEMINI_API_KEY) but is a real, stable, documented API rather than an
    unofficial endpoint - it won't have this whack-a-mole problem.

    Fails safely: if the key isn't set yet, or any request errors, this
    just returns empty gainers/losers rather than breaking the whole run.
    """
    api_key = os.environ.get("FINNHUB_API_KEY")
    if not api_key:
        print("[movers] FINNHUB_API_KEY not set - skipping tech stock movers.")
        return {"gainers": [], "losers": []}

    quotes = []
    for symbol in watchlist:
        try:
            resp = requests.get(
                "https://finnhub.io/api/v1/quote",
                params={"symbol": symbol, "token": api_key},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json() or {}
            change_pct = data.get("dp")
            price = data.get("c")
            if change_pct is None or price in (None, 0):
                continue
            quotes.append(
                {
                    "symbol": symbol,
                    "name": symbol,
                    "change_pct": float(change_pct),
                    "price": float(price),
                }
            )
        except Exception as e:
            print(f"[movers] Skipping {symbol}: {e}")
            continue

    gainers = sorted([q for q in quotes if q["change_pct"] > 0], key=lambda q: q["change_pct"], reverse=True)[:per_side]
    losers = sorted([q for q in quotes if q["change_pct"] < 0], key=lambda q: q["change_pct"])[:per_side]
    return {"gainers": gainers, "losers": losers}


def _format_score(n: int) -> str:
    if n >= 10_000:
        return f"{n / 1000:.0f}k"
    if n >= 1_000:
        return f"{n / 1000:.1f}k"
    return str(n)


def fetch_trending(max_rows: int = TRENDING_MAX_ROWS, pool_size: int = 15, timeout: int = 8) -> list[dict]:
    """Real trending signal (actual Hacker News points today), not an AI
    guess. Pulls the current top stories from Hacker News' official public
    API (free, no key, no auth - it's a Firebase database built for exactly
    this kind of open access) and returns the highest-scored ones.

    Note: this used to pull from several subreddits, but Reddit permanently
    closed unauthenticated .json access on May 28, 2026 (a platform policy
    change, not something fixable with headers or rate-limiting). Hacker
    News skews toward tech/startup/business news rather than Reddit's
    broader mix - a real trade-off worth knowing about, not a bug.
    """
    headers = {"User-Agent": "DailyBriefBot/1.0 (personal daily news aggregator; non-commercial)"}
    try:
        resp = requests.get(
            "https://hacker-news.firebaseio.com/v0/topstories.json",
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()
        ids = (resp.json() or [])[:pool_size]
    except Exception as e:
        print(f"[trending] Skipping Hacker News top stories: {e}")
        return []

    posts = []
    for story_id in ids:
        try:
            r = requests.get(
                f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json",
                headers=headers,
                timeout=timeout,
            )
            r.raise_for_status()
            item = r.json() or {}
            title = (item.get("title") or "").strip()
            if not title:
                continue
            link = item.get("url") or f"https://news.ycombinator.com/item?id={story_id}"
            posts.append(
                {
                    "title": title,
                    "link": link,
                    "source_sub": "Hacker News",
                    "score": item.get("score", 0) or 0,
                }
            )
        except Exception as e:
            print(f"[trending] Skipping a malformed HN item {story_id}: {e}")
            continue

    posts.sort(key=lambda p: p["score"], reverse=True)
    return posts[:max_rows]


def _format_price(p: float | None) -> str:
    if p is None:
        return "n/a"
    if p >= 1000:
        return f"${p:,.0f}"
    return f"${p:,.2f}"


def fetch_crypto_prices(ids: list[str] = CRYPTO_IDS) -> list[dict]:
    """Free, no-key spot prices + 24h change from CoinGecko's public API.
    Fails safely: any error just returns an empty list so the widget shows
    a graceful 'unavailable' message rather than breaking the run."""
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": ",".join(ids), "vs_currencies": "usd", "include_24hr_change": "true"},
            timeout=8,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        print(f"[crypto] Skipping crypto prices: {e}")
        return []

    rows = []
    for cid in ids:
        entry = payload.get(cid)
        if not entry:
            continue
        rows.append(
            {
                "id": cid,
                "symbol": CRYPTO_LABELS.get(cid, cid.upper()[:4]),
                "price": entry.get("usd"),
                "change_pct": entry.get("usd_24h_change"),
            }
        )
    return rows


def fetch_candidates(url: str, limit: int = CANDIDATES_PER_SECTION) -> list[dict]:
    try:
        feed = feedparser.parse(url)
    except Exception as e:
        print(f"[candidates] Skipping feed fetch for {url}: {e}")
        return []
    items = []
    for entry in feed.entries[:limit]:
        items.append(
            {
                "title": getattr(entry, "title", ""),
                "link": getattr(entry, "link", ""),
                "source": getattr(getattr(entry, "source", None), "title", ""),
                "summary": getattr(entry, "summary", "")[:300],
            }
        )
    return items


def build_candidates_block(all_items: dict) -> str:
    blocks = []
    for key, label, _color, _url in SECTIONS:
        items = all_items[key]
        lines = [f"## {label} ({key})"]
        for i, it in enumerate(items):
            lines.append(f"[{i}] {it['title']} — {it['source']} — {it['summary']}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def call_gemini(candidates_block: str, today: str) -> dict:
    api_key = os.environ["GEMINI_API_KEY"]
    client = genai.Client(api_key=api_key)

    prompt = PROMPT_TEMPLATE.format(
        n=STORIES_PER_SECTION, date=today, candidates=candidates_block
    )
    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    raw = response.text.strip()

    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    return json.loads(raw)


def call_gemini_with_retries(candidates_block: str, today: str, max_attempts: int = 3, delay_seconds: int = 10) -> dict:
    """This is the one call with no fallback - if it fails, there's no
    edition to publish. Retries a few times with a short pause first, since
    most failures at this step are transient (a momentary rate limit or
    network blip), before giving up and letting the caller handle it."""
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            return call_gemini(candidates_block, today)
        except Exception as e:
            last_error = e
            print(f"[main] call_gemini attempt {attempt}/{max_attempts} failed: {e}")
            if attempt < max_attempts:
                time.sleep(delay_seconds)
    raise last_error


def resolve_links(data: dict, all_items: dict) -> dict:
    """Attach the real article URL to each story by looking up its source_index."""
    for key, _label, _color, _url in SECTIONS:
        items = all_items[key]
        for story in data["sections"].get(key, []):
            idx = story.get("source_index")
            # Be tolerant of the model returning "0" (string) instead of 0 (int).
            if isinstance(idx, str) and idx.strip().lstrip("-").isdigit():
                idx = int(idx)
            if isinstance(idx, int) and 0 <= idx < len(items):
                story["link"] = items[idx]["link"]
            else:
                story["link"] = ""  # fallback: headline just won't be clickable
    return data


def fetch_analyst_commentary(headline: str, limit: int = ANALYST_SNIPPETS_PER_STORY) -> list[dict]:
    """Free, no-key search for real headlines about analyst/expert reactions
    to a specific story, using the same Google News RSS approach as the main
    news fetch - just a more targeted query."""
    url = _google_news_url(f"{headline} analyst reaction", "en-US", "US", "US:en")
    try:
        return fetch_candidates(url, limit=limit)
    except Exception as e:
        print(f"[research] Skipping analyst search for '{headline[:40]}...': {e}")
        return []


def gather_analyst_snippets(data: dict) -> dict:
    """Returns {(section_key, index_in_section): [snippet dicts]} for every
    selected story, by running a targeted analyst-reaction search on each."""
    snippets = {}
    for key, _label, _color, _url in SECTIONS:
        for i, story in enumerate(data["sections"].get(key, [])):
            snippets[(key, i)] = fetch_analyst_commentary(story.get("headline", ""))
    return snippets


def build_meaning_prompt(data: dict, snippets: dict) -> tuple[str, int]:
    blocks = []
    count = 0
    for key, label, _color, _url in SECTIONS:
        for i, story in enumerate(data["sections"].get(key, [])):
            count += 1
            found = snippets.get((key, i), [])
            if found:
                snippet_lines = "\n".join(f"  - {s['title']} — {s['source']}" for s in found)
            else:
                snippet_lines = "  (none found)"
            blocks.append(
                f"### {key}[{i}]\n"
                f"HEADLINE: {story.get('headline', '')}\n"
                f"SUMMARY: {story.get('summary', '')}\n"
                f"ANALYST HEADLINES FOUND:\n{snippet_lines}\n"
            )
    return "\n".join(blocks), count


def call_gemini_meanings(stories_block: str, count: int) -> dict | None:
    """Second Gemini pass: rewrites 'meaning' grounded in real analyst-reaction
    headlines where genuinely found, honestly marking which stories are
    actually research-grounded vs. the model's own reasoning. Returns None on
    any failure so main() can gracefully keep the original first-pass meaning.
    """
    try:
        api_key = os.environ["GEMINI_API_KEY"]
        client = genai.Client(api_key=api_key)
        prompt = MEANING_PROMPT_TEMPLATE.format(count=count, stories=stories_block)
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        raw = response.text.strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        return json.loads(raw)
    except Exception as e:
        print(f"[research] Grounded-analysis pass failed, keeping original reasoning: {e}")
        return None


def apply_grounded_meanings(data: dict, result: dict | None) -> dict:
    """Merges the structured view/bear_case/prediction back in by (section,
    index). Any story missing from the result, or if the whole pass failed,
    falls back to using its original first-pass 'meaning' as the view, with
    no bear_case/prediction - so nothing ever goes blank."""
    for key, _label, _color, _url in SECTIONS:
        stories = data["sections"].get(key, [])
        updates = (result or {}).get(key, []) if result else []
        by_index = {}
        for u in updates:
            idx = u.get("index")
            if isinstance(idx, str) and idx.strip().lstrip("-").isdigit():
                idx = int(idx)
            if isinstance(idx, int):
                by_index[idx] = u
        for i, story in enumerate(stories):
            update = by_index.get(i)
            if update and update.get("view"):
                story["view"] = update["view"]
                story["bear_case"] = update.get("bear_case", "")
                story["prediction"] = update.get("prediction", "")
                story["grounded"] = bool(update.get("grounded", False))
            else:
                story.setdefault("view", story.get("meaning", ""))
                story.setdefault("bear_case", "")
                story.setdefault("prediction", "")
                story.setdefault("grounded", False)
    return data


OG_IMAGE_RE_LIST = [
    re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', re.IGNORECASE),
]


def fetch_og_image(url: str, timeout: int = 6) -> str:
    """Best-effort: try to grab the article's real preview photo (its
    og:image meta tag). This can fail for plenty of legitimate reasons —
    Google News links are redirects that don't always resolve to the real
    publisher without running JavaScript, some sites block bots, some
    pages are paywalled, etc. Any failure just returns "" so the story
    falls back to a styled placeholder instead of a broken image.
    """
    if not url:
        return ""
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; DailyBriefBot/1.0)"},
            timeout=timeout,
        )
        resp.raise_for_status()
        html = resp.text[:200_000]  # cap how much of the page we scan
    except Exception as e:
        print(f"[image] No thumbnail for {url}: {e}")
        return ""

    for pattern in OG_IMAGE_RE_LIST:
        m = pattern.search(html)
        if m:
            img_url = m.group(1).strip()
            if img_url.startswith("http"):
                return img_url
    return ""


def _placeholder_image_url(seed_text: str, width: int = 480, height: int = 640) -> str:
    """A free, no-signup stand-in photo (Lorem Picsum) for stories where we
    couldn't pull the real article image. Seeded so the same story tends to
    get the same placeholder rather than a random one on every rebuild."""
    seed = re.sub(r"[^a-zA-Z0-9]+", "-", seed_text).strip("-").lower()[:60] or "news"
    return f"https://picsum.photos/seed/{urllib.parse.quote(seed)}/{width}/{height}"


def attach_images(data: dict) -> dict:
    for key, _label, _color, _url in SECTIONS:
        for story in data["sections"].get(key, []):
            real_image = fetch_og_image(story.get("link", ""))
            if real_image:
                story["image"] = real_image
            else:
                story["image"] = _placeholder_image_url(f"{key}-{story.get('headline', '')}")
    return data


def build_day_tabs(all_dates: list[str], active_date: str) -> str:
    tabs = []
    for d in sorted(all_dates, reverse=True):
        label = datetime.datetime.strptime(d, "%Y-%m-%d").strftime("%b %d").upper()
        cls = "tab active" if d == active_date else "tab"
        tabs.append(f'    <a class="{cls}" href="daily-brief-{d}.html">{label}</a>')
    return "\n".join(tabs)


def _domain_from_url(url: str) -> str:
    if not url:
        return ""
    netloc = urllib.parse.urlparse(url).netloc
    return netloc[4:] if netloc.startswith("www.") else netloc


def render_html(
    data: dict,
    day_tabs_html: str,
    earnings: list[dict],
    movers: dict,
    trending: list[dict],
    calls_checked: list[dict],
    prev_date: str | None,
    track_record: dict,
    crypto_prices: list[dict],
) -> str:
    template_path = Path(__file__).parent / "template.html"
    template = template_path.read_text(encoding="utf-8")

    sections_html = ""
    for key, label, color, _url in SECTIONS:
        stories = data["sections"].get(key, [])
        sections_html += (
            f'\n  <div class="section-label" id="{key}">'
            f'<span class="chip" style="background:{color}22;color:{color};'
            f'border-color:{color}55">{label[:2]}</span>'
            f'<h2>{label}</h2><span class="line"></span></div>\n'
        )
        for s in stories:
            link = s.get("link", "")
            domain = _domain_from_url(link)
            domain_badge = f'<span class="domain-badge">{domain}</span>' if domain else ""
            if link:
                headline_html = (
                    f'<a class="headline-link" href="{link}" target="_blank" rel="noopener">'
                    f'<h3 class="headline">{s["headline"]}</h3>'
                    f'<span class="ext-icon">&#8599;</span></a>'
                )
            else:
                headline_html = f'<h3 class="headline">{s["headline"]}</h3>'

            image_url = s.get("image", "")
            img_tag = (
                f'<img src="{image_url}" alt="" loading="lazy" '
                f'onerror="this.style.display=\'none\'">'
                if image_url else ""
            )
            thumb_html = f"""<div class="thumb" style="--accent:{color}">
      {img_tag}
      <span class="thumb-fallback">{label[:2]}</span>
    </div>"""

            angle_tag = (
                '<span class="angle-src">&#128269; Grounded in analyst coverage</span>'
                if s.get("grounded")
                else '<span class="angle-src">&#129504; Independent analysis</span>'
            )
            bear_html = (
                f'<p class="angle-bear"><span class="bear-label">&#9888; Risk to this view —</span> {s["bear_case"]}</p>'
                if s.get("bear_case")
                else ""
            )
            prediction_html = (
                f'<p class="angle-prediction"><span class="pred-label">Prediction —</span> {s["prediction"]}</p>'
                if s.get("prediction")
                else ""
            )

            sections_html += f"""
  <article class="story" style="--accent:{color}">
    {thumb_html}
    <div class="story-body">
    {domain_badge}
    {headline_html}
    <p class="summary">{s['summary']}</p>
    <div class="analyst">
      <span class="label">&#128202; The Angle {angle_tag}</span>
      <p class="angle-view">{s.get('view', s.get('meaning', ''))}</p>
      {bear_html}
      {prediction_html}
    </div>
    </div>
  </article>
"""

    if trending:
        trending_rows = ""
        for i, t in enumerate(trending, start=1):
            trending_rows += f"""
    <div class="trend-row">
      <span class="tr-rank">{i}</span>
      <a class="tr-headline" href="{t['link']}" target="_blank" rel="noopener">{t['title']}</a>
      <span class="tr-meta">
        <span class="tr-source">{t['source_sub']}</span>
        <span class="tr-score">&#9650; {_format_score(t['score'])}</span>
      </span>
    </div>"""
        trending_body = f'<div class="trending-card">{trending_rows}\n  </div>'
    else:
        trending_body = (
            '<div class="trending-card">'
            '<p style="padding:16px 0;margin:0;font-size:12.5px;color:var(--text-faint);">'
            "Trending data unavailable right now.</p></div>"
        )

    trending_html = f"""
  <div class="section-label" id="trending">
    <span class="chip" style="background:{TRENDING_ACCENT}22;color:{TRENDING_ACCENT};border-color:{TRENDING_ACCENT}55">TR</span>
    <h2>TRENDING NOW</h2><span class="line"></span>
  </div>
  {trending_body}
"""

    status_meta = {
        "held_up": ("&#9989;", "var(--green)", "Held up"),
        "missed": ("&#10060;", "var(--red)", "Missed"),
        "too_early": ("&#8987;", "var(--text-dim)", "Too early"),
    }

    if calls_checked:
        rows_html = ""
        for c in calls_checked:
            icon, color_, label_ = status_meta.get(c["status"], status_meta["too_early"])
            rows_html += f"""
    <div class="call-row">
      <span class="call-status" style="color:{color_}">{icon} {label_}</span>
      <p class="call-pred">&ldquo;{c['prediction']}&rdquo;</p>
      <p class="call-note">{c['note']}</p>
    </div>"""
        calls_body = f'<div class="calls-card">{rows_html}\n  </div>'
    elif prev_date:
        calls_body = (
            '<div class="calls-card"><p class="calls-empty">'
            "No predictions were made in the previous edition to check.</p></div>"
        )
    else:
        calls_body = (
            '<div class="calls-card"><p class="calls-empty">'
            "No prior edition yet — this is the first one.</p></div>"
        )

    if track_record.get("pct") is not None:
        track_badge = (
            f'<div class="track-badge">{track_record["pct"]}% held up '
            f'&middot; last {track_record["window_days"]}d '
            f'&middot; {track_record["total_resolved"]} resolved calls</div>'
        )
    else:
        track_badge = '<div class="track-badge track-building">Building track record&hellip;</div>'

    calls_html = f"""
  <div class="section-label" id="calls-checked">
    <span class="chip" style="background:{CALLS_ACCENT}22;color:{CALLS_ACCENT};border-color:{CALLS_ACCENT}55">CC</span>
    <h2>CALLS CHECKED{f' &middot; {prev_date}' if prev_date else ''}</h2><span class="line"></span>
  </div>
  {track_badge}
  {calls_body}
"""

    if crypto_prices:
        crypto_rows = ""
        for c in crypto_prices:
            chg = c.get("change_pct")
            if chg is None:
                chg_html = '<span class="cp-change" style="color:var(--text-faint)">n/a</span>'
            else:
                arrow = "&#9650;" if chg >= 0 else "&#9660;"
                color_ = "var(--green)" if chg >= 0 else "var(--red)"
                chg_html = f'<span class="cp-change" style="color:{color_}">{arrow} {abs(chg):.1f}%</span>'
            crypto_rows += f"""
    <div class="crypto-row">
      <span class="cp-symbol">{c['symbol']}</span>
      <span class="cp-price">{_format_price(c.get('price'))}</span>
      {chg_html}
    </div>"""
        crypto_body = f'<div class="crypto-card">{crypto_rows}\n  </div>'
    else:
        crypto_body = '<div class="crypto-card"><p class="crypto-empty">Crypto prices unavailable right now.</p></div>'

    crypto_html = f"""
  <div class="section-label" id="crypto-prices">
    <span class="chip" style="background:{CRYPTO_ACCENT}22;color:{CRYPTO_ACCENT};border-color:{CRYPTO_ACCENT}55">CR</span>
    <h2>CRYPTO PRICES</h2><span class="line"></span>
  </div>
  {crypto_body}
"""

    if earnings:
        rows_html = ""
        for e in earnings:
            day_label = datetime.datetime.strptime(e["date"], "%Y-%m-%d").strftime("%a %b %d").upper()
            eps = f'Est. EPS {e["eps_forecast"]}' if e.get("eps_forecast") else "Estimate n/a"
            session_class = {
                "Before Open": "sess-bmo",
                "After Close": "sess-amc",
            }.get(e["session"], "sess-tbd")
            rows_html += f"""
    <div class="earnings-row">
      <div class="erow-top">
        <span class="tk">{e['symbol']}</span>
        <span class="eeps">{eps}</span>
      </div>
      <div class="ename">{e['name']}</div>
      <div class="erow-meta">
        <span class="edate">{day_label}</span>
        <span class="esession {session_class}">{e['session']}</span>
      </div>
    </div>"""
        earnings_body = f'<div class="earnings-card">{rows_html}\n  </div>'
    else:
        earnings_body = (
            '<div class="earnings-card">'
            '<p style="padding:16px 0;margin:0;font-size:12.5px;color:var(--text-faint);">'
            "No major earnings found in the lookout window.</p></div>"
        )

    earnings_html = f"""
  <div class="section-label" id="earnings">
    <span class="chip" style="background:{EARNINGS_ACCENT}22;color:{EARNINGS_ACCENT};border-color:{EARNINGS_ACCENT}55">ER</span>
    <h2>EARNINGS THIS WEEK</h2><span class="line"></span>
  </div>
  {earnings_body}
"""

    def _mover_row(q: dict) -> str:
        arrow = "&#9650;" if q["change_pct"] >= 0 else "&#9660;"
        cls = "mv-up" if q["change_pct"] >= 0 else "mv-down"
        return f"""
    <div class="mover-row">
      <span class="tk">{q['symbol']}</span>
      <span class="mname">{q['name']}</span>
      <span class="mchange {cls}">{arrow} {abs(q['change_pct']):.2f}%</span>
    </div>"""

    gainers = movers.get("gainers", [])
    losers = movers.get("losers", [])
    if gainers or losers:
        gainers_html = "".join(_mover_row(q) for q in gainers) or '<p class="movers-empty">No data</p>'
        losers_html = "".join(_mover_row(q) for q in losers) or '<p class="movers-empty">No data</p>'
        movers_body = f"""<div class="movers-card">
    <div class="movers-subhead mv-up">&#9650; Top Gainers</div>
    {gainers_html}
    <div class="movers-subhead mv-down">&#9660; Top Losers</div>
    {losers_html}
  </div>"""
    else:
        movers_body = (
            '<div class="movers-card">'
            '<p style="padding:16px 0;margin:0;font-size:12.5px;color:var(--text-faint);">'
            "Stock data unavailable right now.</p></div>"
        )

    movers_html = f"""
  <div class="section-label" id="tech-movers">
    <span class="chip" style="background:{MOVERS_ACCENT}22;color:{MOVERS_ACCENT};border-color:{MOVERS_ACCENT}55">TC</span>
    <h2>TECH STOCKS TODAY</h2><span class="line"></span>
  </div>
  {movers_body}
"""

    html = template.replace("{{DATE}}", data.get("date", ""))
    html = html.replace("{{TRENDING}}", trending_html)
    html = html.replace("{{CALLS_CHECKED}}", calls_html)
    html = html.replace("{{CRYPTO_PRICES}}", crypto_html)
    html = html.replace("{{TECH_MOVERS}}", movers_html)
    html = html.replace("{{EARNINGS}}", earnings_html)
    html = html.replace("{{SECTIONS}}", sections_html)
    html = html.replace("{{DAY_TABS}}", day_tabs_html)
    return html


DAY_TABS_RE = re.compile(
    r"(<!-- DAY_TABS_START -->\s*<div class=\"tabs\">\n)(.*?)(\n\s*</div>\s*<!-- DAY_TABS_END -->)",
    re.DOTALL,
)


def rewrite_day_tabs_in_file(path: Path, all_dates: list[str], active_date: str) -> None:
    """Update just the tab strip in an already-published old edition, leaving
    its actual story content untouched."""
    text = path.read_text(encoding="utf-8")
    new_tabs = build_day_tabs(all_dates, active_date)
    new_text, n = DAY_TABS_RE.subn(lambda m: m.group(1) + new_tabs + m.group(3), text)
    if n:
        path.write_text(new_text, encoding="utf-8")
    # If markers aren't found (e.g. a very old edition predating this feature),
    # just leave that file as-is rather than failing the whole run.


def date_from_filename(path: Path) -> str | None:
    m = re.match(r"daily-brief-(\d{4}-\d{2}-\d{2})\.html$", path.name)
    return m.group(1) if m else None


def date_from_json_filename(path: Path) -> str | None:
    m = re.match(r"daily-brief-(\d{4}-\d{2}-\d{2})\.json$", path.name)
    return m.group(1) if m else None


def save_edition_sidecar(data: dict, date_str: str, out_dir: Path) -> Path:
    """Saves a small JSON record of this edition:
      - "predictions": only stories with a real prediction (used by the
        next day's call-checking pass) - unchanged from before.
      - "stories": every selected story with its headline/summary/view
        (used by the weekly rollup to synthesize themes).
    """
    predictions = []
    stories = []
    for key, _label, _color, _url in SECTIONS:
        for story in data["sections"].get(key, []):
            stories.append(
                {
                    "section": key,
                    "headline": story.get("headline", ""),
                    "summary": story.get("summary", ""),
                    "view": story.get("view", story.get("meaning", "")),
                    "link": story.get("link", ""),
                }
            )
            pred = story.get("prediction", "")
            if not pred:
                continue
            predictions.append(
                {
                    "section": key,
                    "headline": story.get("headline", ""),
                    "link": story.get("link", ""),
                    "prediction": pred,
                    "grounded": bool(story.get("grounded", False)),
                }
            )
    path = out_dir / f"daily-brief-{date_str}.json"
    path.write_text(
        json.dumps({"date": date_str, "predictions": predictions, "stories": stories}, indent=2),
        encoding="utf-8",
    )
    return path


def load_previous_predictions(out_dir: Path, today_str: str) -> tuple[str | None, list[dict]]:
    """Finds the most recent OTHER edition's sidecar (not necessarily exactly
    yesterday, in case a day was skipped) and loads its predictions."""
    dates = []
    for f in out_dir.glob("daily-brief-*.json"):
        d = date_from_json_filename(f)
        if d and d != today_str:
            dates.append(d)
    if not dates:
        return None, []
    prev_date = max(dates)
    try:
        payload = json.loads((out_dir / f"daily-brief-{prev_date}.json").read_text(encoding="utf-8"))
        return prev_date, payload.get("predictions", [])
    except Exception as e:
        print(f"[callcheck] Failed to load previous predictions: {e}")
        return None, []


def fetch_followup_snippets(query_text: str, limit: int = 5) -> list[dict]:
    """Free, targeted search for fresh news that might confirm or contradict
    a specific past prediction - same approach as the analyst-commentary search."""
    url = _google_news_url(query_text, "en-US", "US", "US:en")
    try:
        return fetch_candidates(url, limit=limit)
    except Exception as e:
        print(f"[callcheck] Skipping follow-up search: {e}")
        return []


def build_callcheck_prompt(prev_predictions: list[dict], snippets_map: dict) -> tuple[str, int]:
    blocks = []
    for i, p in enumerate(prev_predictions):
        found = snippets_map.get(i, [])
        if found:
            snippet_lines = "\n".join(f"  - {s['title']} — {s['source']}" for s in found)
        else:
            snippet_lines = "  (nothing new found)"
        blocks.append(
            f"### [{i}]\n"
            f"ORIGINAL HEADLINE: {p.get('headline', '')}\n"
            f"PREDICTION MADE: {p.get('prediction', '')}\n"
            f"FRESH HEADLINES TODAY:\n{snippet_lines}\n"
        )
    return "\n".join(blocks), len(prev_predictions)


def call_gemini_callcheck(prompt_body: str, count: int, prev_date: str) -> dict | None:
    if count == 0:
        return None
    try:
        api_key = os.environ["GEMINI_API_KEY"]
        client = genai.Client(api_key=api_key)
        prompt = CALLCHECK_PROMPT_TEMPLATE.format(count=count, prev_date=prev_date, predictions=prompt_body)
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        raw = response.text.strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        return json.loads(raw)
    except Exception as e:
        print(f"[callcheck] Grounded-check pass failed: {e}")
        return None


def build_checked_list(prev_predictions: list[dict], callcheck_result: dict | None) -> list[dict]:
    """Merges the AI's verdicts back onto the original predictions. Anything
    missing from the result (or if the whole pass failed) defaults to
    'too_early' rather than silently disappearing or guessing a verdict."""
    checks = (callcheck_result or {}).get("checks", []) if callcheck_result else []
    by_index = {}
    for c in checks:
        idx = c.get("index")
        if isinstance(idx, str) and idx.strip().lstrip("-").isdigit():
            idx = int(idx)
        if isinstance(idx, int):
            by_index[idx] = c

    results = []
    for i, p in enumerate(prev_predictions):
        c = by_index.get(i)
        status = c.get("status") if c else "too_early"
        if status not in ("held_up", "missed", "too_early"):
            status = "too_early"
        note = (c.get("note") if c and c.get("note") else "Not enough new information yet to say.")
        results.append({**p, "status": status, "note": note})
    return results


def persist_checked_statuses(out_dir: Path, prev_date: str, checked_calls: list[dict]) -> None:
    """Writes today's verdicts back onto that prior day's own sidecar file,
    so a future run can compute a 30-day track record just by reading
    resolved statuses off disk, without re-running any Gemini calls."""
    path = out_dir / f"daily-brief-{prev_date}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[track-record] Couldn't update sidecar {path.name}: {e}")
        return
    preds = payload.get("predictions", [])
    for i, c in enumerate(checked_calls):
        if i < len(preds):
            preds[i]["status"] = c["status"]
            preds[i]["note"] = c["note"]
    payload["predictions"] = preds
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def compute_track_record(out_dir: Path, window_days: int = TRACK_RECORD_WINDOW_DAYS) -> dict:
    """Scans all sidecars within the window and tallies already-resolved
    verdicts. 'too_early' and never-checked predictions are excluded from
    the percentage since they're not resolved yet - this is a stat about
    calls that actually got confirmed or contradicted, not a raw average."""
    cutoff = datetime.date.today() - datetime.timedelta(days=window_days)
    held_up = 0
    missed = 0
    for f in out_dir.glob("daily-brief-*.json"):
        d_str = date_from_json_filename(f)
        if not d_str:
            continue
        try:
            d = datetime.date.fromisoformat(d_str)
        except ValueError:
            continue
        if d < cutoff:
            continue
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for p in payload.get("predictions", []):
            status = p.get("status")
            if status == "held_up":
                held_up += 1
            elif status == "missed":
                missed += 1

    total_resolved = held_up + missed
    if total_resolved == 0:
        return {"pct": None, "held_up": 0, "missed": 0, "total_resolved": 0, "window_days": window_days}
    return {
        "pct": round(held_up / total_resolved * 100),
        "held_up": held_up,
        "missed": missed,
        "total_resolved": total_resolved,
        "window_days": window_days,
    }


def write_failure_page(out_dir: Path, today_str: str, error_message: str) -> None:
    """If today's edition can't be generated even after retries, this writes
    a clear, honest notice to index.html instead of leaving the site either
    broken or silently showing yesterday's content with no explanation.
    Yesterday's own dated page is left completely untouched."""
    existing_dates = []
    for f in out_dir.glob("daily-brief-*.html"):
        d = date_from_filename(f)
        if d:
            existing_dates.append(d)
    most_recent = max(existing_dates) if existing_dates else None
    link_html = (
        f'<p><a href="daily-brief-{most_recent}.html">&#8594; View the most recent edition ({most_recent})</a></p>'
        if most_recent
        else "<p>No previous edition is available yet.</p>"
    )
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>The Daily Brief — Temporarily Unavailable</title>
<style>
  body{{background:#0B0E11;color:#EDF0F2;font-family:system-ui,-apple-system,sans-serif;
        max-width:600px;margin:80px auto;padding:0 20px;line-height:1.6;}}
  h1{{font-size:24px;font-weight:700;}}
  code{{background:#161B21;padding:2px 6px;border-radius:4px;color:#F87171;
        font-size:13px;word-break:break-word;}}
  a{{color:#2DD4BF;}}
</style>
</head>
<body>
  <h1>Today's Daily Brief couldn't be generated</h1>
  <p>The automated run for {today_str} hit an error and wasn't able to build
  today's edition, even after a few retries:</p>
  <p><code>{error_message}</code></p>
  {link_html}
  <p>This usually resolves on its own by the next scheduled run. If it keeps
  happening, check the <b>Actions</b> tab in the repo for the full error log
  — and since this run is marked as failed, GitHub should have already
  emailed you about it.</p>
</body>
</html>"""
    (out_dir / "index.html").write_text(html, encoding="utf-8")


def main():
    today = datetime.date.today().strftime("%B %d, %Y")
    today_str = datetime.date.today().isoformat()

    out_dir = Path(__file__).parent / "output"
    out_dir.mkdir(exist_ok=True)

    # Bring forward any previously published editions (HTML + JSON sidecars)
    # so the archive persists and past predictions stay available to check.
    if EXISTING_SITE_DIR.is_dir():
        for f in EXISTING_SITE_DIR.glob("daily-brief-*.html"):
            dest = out_dir / f.name
            if not dest.exists():
                shutil.copy(f, dest)
        for f in EXISTING_SITE_DIR.glob("daily-brief-*.json"):
            dest = out_dir / f.name
            if not dest.exists():
                shutil.copy(f, dest)

    # --- Check the most recent prior edition's predictions ---
    # This runs regardless of whether today's generation later succeeds -
    # it's entirely about yesterday's data.
    prev_date, prev_predictions = load_previous_predictions(out_dir, today_str)
    checked_calls = []
    if prev_predictions:
        capped = prev_predictions[:MAX_CALLS_TO_CHECK]
        snippets_map = {i: fetch_followup_snippets(p["prediction"]) for i, p in enumerate(capped)}
        prompt_body, count = build_callcheck_prompt(capped, snippets_map)
        callcheck_result = call_gemini_callcheck(prompt_body, count, prev_date)
        checked_calls = build_checked_list(capped, callcheck_result)
        persist_checked_statuses(out_dir, prev_date, checked_calls)

    track_record = compute_track_record(out_dir)

    # --- The risky part: generating today's actual edition ---
    # If this fails even after retries, publish an honest failure notice
    # instead of crashing silently or leaving stale content unexplained.
    try:
        all_items = {key: fetch_candidates(url) for key, _label, _color, url in SECTIONS}
        candidates_block = build_candidates_block(all_items)
        data = call_gemini_with_retries(candidates_block, today)
        data = resolve_links(data, all_items)

        # Second pass: try to ground "The Angle" in real analyst/expert
        # reactions, honestly marking which stories actually got real
        # research vs. the model's own reasoning. Never blocks the run.
        snippets = gather_analyst_snippets(data)
        stories_block, story_count = build_meaning_prompt(data, snippets)
        meanings_result = call_gemini_meanings(stories_block, story_count)
        data = apply_grounded_meanings(data, meanings_result)

        data = attach_images(data)
        earnings = fetch_upcoming_earnings()
        movers = fetch_tech_stock_movers()
        trending = fetch_trending()
        crypto_prices = fetch_crypto_prices()

        # Figure out the full set of dates once today's edition is included.
        all_dates = set()
        for f in out_dir.glob("daily-brief-*.html"):
            d = date_from_filename(f)
            if d:
                all_dates.add(d)
        all_dates.add(today_str)
        all_dates = sorted(all_dates)

        today_tabs = build_day_tabs(all_dates, today_str)
        html = render_html(
            data, today_tabs, earnings, movers, trending,
            checked_calls, prev_date, track_record, crypto_prices,
        )

        dated_path = out_dir / f"daily-brief-{today_str}.html"
        dated_path.write_text(html, encoding="utf-8")

        index_path = out_dir / "index.html"
        index_path.write_text(html, encoding="utf-8")

        # Save today's predictions so tomorrow's run can check them.
        save_edition_sidecar(data, today_str, out_dir)

        # Update the tab strip inside every OTHER previously published
        # edition too, so they all show the latest full archive.
        for f in out_dir.glob("daily-brief-*.html"):
            d = date_from_filename(f)
            if d and d != today_str:
                rewrite_day_tabs_in_file(f, all_dates, active_date=d)

        manifest_src = Path(__file__).parent / "manifest.webmanifest"
        if manifest_src.exists():
            shutil.copy(manifest_src, out_dir / "manifest.webmanifest")

        print(f"Wrote {dated_path} and {index_path}. Archive now has {len(all_dates)} edition(s).")
        return dated_path

    except Exception as e:
        print(f"[main] FATAL: could not generate today's edition after retries: {e}")
        write_failure_page(out_dir, today_str, str(e))
        manifest_src = Path(__file__).parent / "manifest.webmanifest"
        if manifest_src.exists():
            shutil.copy(manifest_src, out_dir / "manifest.webmanifest")
        # Re-raise so this GitHub Actions run is marked failed - which is
        # what makes GitHub send its automatic failure-notification email.
        raise


if __name__ == "__main__":
    main()

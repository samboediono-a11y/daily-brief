#!/usr/bin/env python3
"""
generate_weekly.py

Reads the past 7 days of JSON sidecars written by generate_brief.py and asks
Gemini to synthesize the week's biggest themes into a single rollup page.
Runs on its own weekly schedule (see .github/workflows/weekly-rollup.yml),
separate from the daily brief.

Requires:
    pip install -r requirements.txt

Environment variables:
    GEMINI_API_KEY   - required, same free key used by generate_brief.py

Usage:
    python generate_weekly.py
    -> writes ./output/weekly-YYYY-MM-DD.html and weekly-latest.html
"""

import os
import re
import json
import shutil
import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from google import genai

GEMINI_MODEL = "gemini-3-flash-preview"
LOCAL_TIMEZONE = "America/Los_Angeles"  # see generate_brief.py for why this matters
LOOKBACK_DAYS = 7
MAX_THEMES = 6

# Where the previously published site gets checked out to before this runs.
EXISTING_SITE_DIR = Path(__file__).parent / "existing-site"

WEEKLY_PROMPT_TEMPLATE = """You are a senior analyst writing a weekly rewind. Below are story
summaries and analyst views collected from the past {days} days of a daily
news brief (Tech, Markets, US News, Indonesia, Crypto & FX). Find the
{max_themes} biggest THEMES of the week - patterns or storylines that
connect multiple days or stories, not just a list of individual headlines.
If there isn't enough material for that many distinct themes, return fewer -
don't invent thin ones just to hit a number.

For each theme, write:
  - "title": a short, punchy theme name (under 8 words)
  - "synthesis": 3-4 sentences pulling together what happened across the
    week and why it matters as a pattern - written with the same confident,
    specific analyst voice as the daily brief. Take a real position on
    where this is headed, don't just summarize.
  - "watch_next": one concrete thing to watch for in the coming week

Return ONLY valid JSON (no markdown fences, no commentary) in this shape:
{{"week_of": "{week_of}", "themes": [{{"title": "...", "synthesis": "...", "watch_next": "..."}}, ...]}}

This week's material:
{material}
"""


def get_today() -> datetime.date:
    """Returns "today" in LOCAL_TIMEZONE, not the server's system timezone -
    see generate_brief.py for the full explanation of why this matters."""
    try:
        return datetime.datetime.now(ZoneInfo(LOCAL_TIMEZONE)).date()
    except Exception as e:
        print(f"[weekly] Couldn't load timezone {LOCAL_TIMEZONE}, falling back to UTC date: {e}")
        return datetime.datetime.now(datetime.timezone.utc).date()


def load_week_stories(days: int = LOOKBACK_DAYS) -> list[dict]:
    """Reads every daily JSON sidecar within the lookback window. Missing or
    unreadable files are just skipped - a thin week still produces whatever
    themes the available material supports."""
    if not EXISTING_SITE_DIR.is_dir():
        return []
    cutoff = get_today() - datetime.timedelta(days=days)
    stories = []
    for f in EXISTING_SITE_DIR.glob("daily-brief-*.json"):
        m = re.match(r"daily-brief-(\d{4}-\d{2}-\d{2})\.json$", f.name)
        if not m:
            continue
        try:
            d = datetime.date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if d < cutoff:
            continue
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[weekly] Skipping unreadable sidecar {f.name}: {e}")
            continue
        for s in payload.get("stories", []):
            entry = dict(s)
            entry["date"] = m.group(1)
            stories.append(entry)
    return stories


def build_weekly_material(stories: list[dict]) -> str:
    lines = []
    for s in stories:
        lines.append(
            f"[{s.get('date', '')}] ({s.get('section', '')}) "
            f"{s.get('headline', '')} — {s.get('summary', '')} "
            f"| View: {s.get('view', '')}"
        )
    return "\n".join(lines)


def call_gemini_weekly(material: str, week_of: str, days: int, max_themes: int) -> dict | None:
    if not material.strip():
        print("[weekly] No material found for this window - skipping Gemini call.")
        return None
    try:
        api_key = os.environ["GEMINI_API_KEY"]
        client = genai.Client(api_key=api_key)
        prompt = WEEKLY_PROMPT_TEMPLATE.format(
            days=days, max_themes=max_themes, week_of=week_of, material=material
        )
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        raw = response.text.strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        return json.loads(raw)
    except Exception as e:
        print(f"[weekly] Failed to generate weekly rollup: {e}")
        return None


def build_week_tabs(all_weeks: list[str], active_week: str) -> str:
    tabs = []
    for w in sorted(all_weeks, reverse=True):
        cls = "tab active" if w == active_week else "tab"
        tabs.append(f'    <a class="{cls}" href="weekly-{w}.html">Week of {w}</a>')
    return "\n".join(tabs)


WEEK_TABS_RE = re.compile(
    r"(<!-- WEEK_TABS_START -->\s*<div class=\"tabs\">\n)(.*?)(\n\s*</div>\s*<!-- WEEK_TABS_END -->)",
    re.DOTALL,
)


def rewrite_week_tabs_in_file(path: Path, all_weeks: list[str], active_week: str) -> None:
    text = path.read_text(encoding="utf-8")
    new_tabs = build_week_tabs(all_weeks, active_week)
    new_text, n = WEEK_TABS_RE.subn(lambda m: m.group(1) + new_tabs + m.group(3), text)
    if n:
        path.write_text(new_text, encoding="utf-8")


def date_from_weekly_filename(path: Path) -> str | None:
    m = re.match(r"weekly-(\d{4}-\d{2}-\d{2})\.html$", path.name)
    return m.group(1) if m else None


def render_weekly_html(result: dict | None, week_of: str, week_tabs_html: str) -> str:
    template_path = Path(__file__).parent / "weekly_template.html"
    template = template_path.read_text(encoding="utf-8")

    themes = (result or {}).get("themes", []) if result else []
    if themes:
        themes_html = ""
        for t in themes:
            themes_html += f"""
  <article class="theme-card">
    <h2 class="theme-title">{t.get('title', '')}</h2>
    <p class="theme-synth">{t.get('synthesis', '')}</p>
    <p class="theme-watch"><span class="watch-label">Watch next —</span> {t.get('watch_next', '')}</p>
  </article>
"""
    else:
        themes_html = '<p class="themes-empty">Not enough material yet to synthesize this week\'s themes.</p>'

    html = template.replace("{{WEEK_OF}}", week_of)
    html = html.replace("{{THEMES}}", themes_html)
    html = html.replace("{{WEEK_TABS}}", week_tabs_html)
    return html


def main():
    today_str = get_today().isoformat()

    out_dir = Path(__file__).parent / "output"
    out_dir.mkdir(exist_ok=True)

    # Bring forward previously published weekly editions.
    if EXISTING_SITE_DIR.is_dir():
        for f in EXISTING_SITE_DIR.glob("weekly-*.html"):
            dest = out_dir / f.name
            if not dest.exists():
                shutil.copy(f, dest)

    stories = load_week_stories()
    material = build_weekly_material(stories)
    result = call_gemini_weekly(material, today_str, LOOKBACK_DAYS, MAX_THEMES)

    all_weeks = set()
    for f in out_dir.glob("weekly-*.html"):
        w = date_from_weekly_filename(f)
        if w:
            all_weeks.add(w)
    all_weeks.add(today_str)
    all_weeks = sorted(all_weeks)

    tabs = build_week_tabs(all_weeks, today_str)
    html = render_weekly_html(result, today_str, tabs)

    dated_path = out_dir / f"weekly-{today_str}.html"
    dated_path.write_text(html, encoding="utf-8")

    latest_path = out_dir / "weekly-latest.html"
    latest_path.write_text(html, encoding="utf-8")

    for f in out_dir.glob("weekly-*.html"):
        w = date_from_weekly_filename(f)
        if w and w != today_str:
            rewrite_week_tabs_in_file(f, all_weeks, active_week=w)

    print(f"Wrote {dated_path}. Weekly archive now has {len(all_weeks)} edition(s), based on {len(stories)} stories.")
    return dated_path


if __name__ == "__main__":
    main()

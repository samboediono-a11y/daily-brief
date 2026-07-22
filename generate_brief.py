#!/usr/bin/env python3
"""
generate_brief.py  (100%-free version)

How this stays free, end to end:
  1. Headlines come from Google News RSS feeds — public, free, no API key.
  2. The "what it means" writing is done by Google's Gemini API free tier
     (Flash model) — free, no credit card, no expiration, generous daily
     limit (this script only makes ONE request per day, well under it).
  3. Hosting is GitHub Pages — free.

Requires:
    pip install -r requirements.txt

Environment variables:
    GEMINI_API_KEY   - required, free key from https://aistudio.google.com/apikey

Usage:
    python generate_brief.py
    -> writes ./output/index.html and ./output/daily-brief-YYYY-MM-DD.html
"""

import os
import json
import shutil
import datetime
import urllib.parse
from pathlib import Path

import feedparser
from google import genai

STORIES_PER_SECTION = 4  # change to taste
GEMINI_MODEL = "gemini-2.5-flash-lite"  # free-tier model, current as of July 2026


def _google_news_url(query: str, hl: str, gl: str, ceid: str) -> str:
    q = urllib.parse.quote(query)
    return f"https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={ceid}"


# Each section: (key, label shown on the page, Google News RSS URL)
SECTIONS = [
    (
        "tech",
        "TECH",
        _google_news_url("technology industry", "en-US", "US", "US:en"),
    ),
    (
        "markets",
        "MARKETS",
        _google_news_url("stock market", "en-US", "US", "US:en"),
    ),
    (
        "us_news",
        "US NEWS",
        "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",  # top US headlines
    ),
    (
        "indonesia",
        "INDONESIA",
        _google_news_url("Indonesia economy OR Indonesia politics", "en-US", "ID", "ID:en"),
    ),
]

PROMPT_TEMPLATE = """You are writing a daily news briefing for a smart, busy reader
who does not want jargon. Below are real candidate headlines (with source and
short snippet) fetched today for each section. For each section, pick the
{n} most important, non-duplicate stories and write, for each:
  - "headline": a punchy plain-English headline (under 12 words) based on the
    real story (rewrite it in your own words, don't copy the source headline verbatim)
  - "summary": 1-2 plain sentences explaining what happened
  - "meaning": 1-2 sentences on the real-world implication - what it affects,
    what might happen next - written for a smart non-expert reader

Return ONLY valid JSON (no markdown fences, no commentary) in exactly this shape:
{{
  "date": "{date}",
  "throughline": "one sentence tying today's news together",
  "sections": {{
    "tech": [{{"headline": "...", "summary": "...", "meaning": "..."}}, ...],
    "markets": [...],
    "us_news": [...],
    "indonesia": [...]
  }}
}}

Candidate headlines by section:
{candidates}
"""


def fetch_candidates(url: str, limit: int = 12) -> list[dict]:
    feed = feedparser.parse(url)
    items = []
    for entry in feed.entries[:limit]:
        items.append(
            {
                "title": getattr(entry, "title", ""),
                "source": getattr(getattr(entry, "source", None), "title", ""),
                "published": getattr(entry, "published", ""),
                "summary": getattr(entry, "summary", "")[:300],
            }
        )
    return items


def build_candidates_block() -> str:
    blocks = []
    for key, label, url in SECTIONS:
        items = fetch_candidates(url)
        lines = [f"## {label} ({key})"]
        for it in items:
            lines.append(f"- {it['title']} — {it['source']} — {it['summary']}")
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


def render_html(data: dict) -> str:
    template_path = Path(__file__).parent / "template.html"
    template = template_path.read_text(encoding="utf-8")

    sections_html = ""
    for key, label, _ in SECTIONS:
        stories = data["sections"].get(key, [])
        sections_html += (
            f'\n  <div class="section-label"><span class="tag">{label}</span>'
            f'<span class="line"></span><span class="count">{len(stories)} stories</span></div>\n'
        )
        for s in stories:
            sections_html += f"""
  <article class="story">
    <h2 class="headline">{s['headline']}</h2>
    <p class="summary">{s['summary']}</p>
    <div class="meaning">
      <span class="label">What it means</span>
      <p>{s['meaning']}</p>
    </div>
  </article>
"""

    html = template.replace("{{DATE}}", data.get("date", ""))
    html = html.replace("{{THROUGHLINE}}", data.get("throughline", ""))
    html = html.replace("{{SECTIONS}}", sections_html)
    return html


def main():
    today = datetime.date.today().strftime("%B %d, %Y")
    candidates_block = build_candidates_block()
    data = call_gemini(candidates_block, today)
    html = render_html(data)

    out_dir = Path(__file__).parent / "output"
    out_dir.mkdir(exist_ok=True)
    today_str = datetime.date.today().isoformat()

    dated_path = out_dir / f"daily-brief-{today_str}.html"
    dated_path.write_text(html, encoding="utf-8")

    index_path = out_dir / "index.html"
    index_path.write_text(html, encoding="utf-8")

    manifest_src = Path(__file__).parent / "manifest.webmanifest"
    if manifest_src.exists():
        shutil.copy(manifest_src, out_dir / "manifest.webmanifest")

    print(f"Wrote {dated_path} and {index_path}")
    return dated_path


if __name__ == "__main__":
    main()

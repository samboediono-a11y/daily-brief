# The Daily Brief — 100% free auto-updating website

Every part of this costs $0:

| Piece | What it does | Cost |
|---|---|---|
| Google News RSS | Supplies real headlines | Free, no signup |
| Gemini API (Google) | Writes the plain-English summaries and "what it means" | Free tier, no credit card |
| GitHub Pages | Hosts your website | Free |
| GitHub Actions | Runs everything automatically every morning | Free |

## Setup checklist

1. **Create a GitHub repo** named `daily-brief` (Public).
2. **Add these 5 files** to it, in these exact locations:
   - `template.html`
   - `manifest.webmanifest`
   - `requirements.txt`
   - `generate_brief.py`
   - `.github/workflows/daily-brief.yml`
3. **Get a free Gemini API key**: go to
   [aistudio.google.com/apikey](https://aistudio.google.com/apikey), sign
   in with any Google account, click "Create API key." No credit card
   anywhere in this flow.
4. **Add it as a GitHub secret**: in your repo, go to
   **Settings → Secrets and variables → Actions → New repository
   secret**. Name it `GEMINI_API_KEY`, paste in the key, save.
5. **Run it once to test**: go to the **Actions** tab → click
   "Daily Brief" on the left → click **Run workflow** → wait about a
   minute.
6. **Turn on the website**: go to **Settings → Pages**. Under "Build
   and deployment," set source to **Deploy from a branch**, branch
   **`gh-pages`**, folder **`/ (root)`**, click Save.
7. Refresh that same Pages settings page — it will show your live
   website address (something like
   `https://yourusername.github.io/daily-brief/`). That's your Daily
   Brief.

From here on, it rebuilds itself automatically every morning at 7:00
AM Jakarta time — you never have to do anything again.

## On your phone

Open your website address in Safari (iPhone) or Chrome (Android), tap
the share/menu button, choose **"Add to Home Screen."** It'll sit on
your phone with its own icon and open full-screen, like a normal app.

## If something goes wrong

Go to the **Actions** tab in your repo — every run is logged there,
and clicking into a failed (red ❌) run shows exactly which step
failed and why. Paste me that error message and I'll help you fix it.

## Adjusting it later

- More/fewer stories per section: change `STORIES_PER_SECTION` near
  the top of `generate_brief.py`.
- Add/remove categories: edit the `SECTIONS` list in the same file.
- Change the run time: edit the `cron` line in
  `.github/workflows/daily-brief.yml` — [crontab.guru](https://crontab.guru)
  helps translate times to the cron format.
- Change the look: edit `template.html` directly.

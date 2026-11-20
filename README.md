# FOMO Filter

A web page listing events in Santa Cruz, Scotts Valley, Soquel, Capitola,
Davenport, Aptos and Watsonville from **Good Times** and **Eventbrite**. Pick
dates, tick events, tap **Add to Calendar**.

A GitHub Action refreshes the list every morning (about the next 90 days) and
publishes it with GitHub Pages. Nothing runs on your computer.

## Using the page
- **Dates:** set From / To, or tap *This weekend*, *Next 7 days*, *Next 3 weeks*.
- **Narrow down:** search box, town and source menus.
- Tap an **address** for Google Maps; tap **Good Times / Eventbrite / Event site** for the original page.
- Tick events (ticks are remembered on that device), then **Add to Calendar**:
  - **iPhone (Safari):** a calendar preview opens; tap **Add All**.
  - **Mac:** `events.ics` downloads; open it and Calendar asks which calendar to use.
- **Refresh now** (top right) opens GitHub's *Run workflow* button for an immediate update (~2 minutes).

## Scanning an event from a picture
Tap **📷 Scan an event**, then take a photo of a flyer or pick a screenshot
(Facebook, Instagram, a website, a text). On a computer you can also paste a
screenshot. Claude reads it and fills in a form: check it, fix anything, then
**Add to Calendar**.

- Needs your own **Anthropic API key** (console.anthropic.com → API keys, with
  some credit added). Roughly 1–3 cents per scan.
- The key is stored only in that browser on that device and is sent only to
  Anthropic (`api.anthropic.com`). It is never in this repo or on the site.
  Enter it once on each device you scan from.
- Uses Claude Opus 5.5 with structured output. If a safety check declines a
  picture, the API retries it on another model automatically.
- `vendor/anthropic-sdk-1.6.0.mjs` is the Anthropic JavaScript SDK bundled for
  browsers (how to rebuild it is at the top of the file).

## Settings (top of `build.py`)
- `TOWNS`: towns to keep. Nearby towns like Felton are listed, commented out.
- `DAYS_AHEAD`: how far ahead the daily update fetches (90).
- `EVENTBRITE_PAGES`: more pages finds more Eventbrite events but runs longer.

Edit the file on github.com (pencil icon) and commit; the next update uses it.

## Files
- `build.py`: fetches and merges events, writes `site/index.html` + `site/events.json`.
- `page.html`: the web page.
- `vendor/`: browser libraries copied into the site (the Claude SDK).
- `.github/workflows/update.yml`: daily schedule and the Run workflow button.

## Try it locally
```bash
python3 build.py
cd site && python3 -m http.server 8000
```
Then open http://localhost:8000.

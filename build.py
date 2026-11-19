#!/usr/bin/env python3
"""Event Picker builder: fetches Santa Cruz County events from Good Times and
Eventbrite and writes the website into ./site (index.html + events.json).

Run by GitHub Actions every morning. To try it locally:
    python3 build.py && open site/index.html   (needs a local web server, see README)

Uses only the Python standard library.
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------- settings --
TOWNS = [
    "Santa Cruz", "Scotts Valley", "Soquel", "Capitola",
    "Davenport", "Aptos", "Watsonville",
    # Nearby towns that also show up in the feeds. Uncomment to include:
    # "Felton", "Ben Lomond", "Boulder Creek", "La Selva Beach", "Corralitos",
]
DAYS_AHEAD = 90  # how far ahead the daily job fetches; the page filters by date

# Eventbrite location pages to read, and how many result pages each (20/page).
EVENTBRITE_PLACES = ["ca--santa-cruz", "ca--watsonville", "ca--scotts-valley"]
EVENTBRITE_PAGES = 8

# Good Times (goodtimes.sc) calendar runs on CitySpark.
CITYSPARK_SLUG = "GoodTimesSantaCruz"
CITYSPARK_PPID = 9083
CITYSPARK_LAT, CITYSPARK_LNG, CITYSPARK_MILES = 36.9741171, -122.0307963, 20

TZ = ZoneInfo("America/Los_Angeles")
HERE = os.path.dirname(os.path.abspath(__file__))
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

TOWN_SET = {t.lower() for t in TOWNS}


def log(msg: str) -> None:
    print(msg, flush=True)


def http_get(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def http_post_json(url: str, body: dict, timeout: int = 30):
    req = urllib.request.Request(url, json.dumps(body).encode(),
                                 {"User-Agent": UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def town_ok(city: str | None) -> bool:
    return bool(city) and city.strip().lower() in TOWN_SET


def iso_utc(d: dt.datetime) -> str:
    return d.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------- Good Times --
def parse_utc(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    return dt.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)


# Names of feeds CitySpark imports from; they are not the event's host.
AGGREGATORS = {"eventbrite", "bandsintown", "visit san jose", "ticketmaster", "meetup",
               "allevents", "facebook", "songkick", "do408", "dosantacruz"}


def cityspark_host(e: dict) -> str:
    # Link type 1 is the calendar the event was imported from (usually the
    # organizer, e.g. "Kuumbwa Jazz"); type 0 is the event's own web page.
    for link in e.get("Links") or []:
        name = (link.get("name") or "").strip()
        if (link.get("type") == 1 and name and name.lower() not in AGGREGATORS
                and name.lower() != (e.get("Name") or "").strip().lower()):
            return name
    m = re.match(r"\s*(.{3,60}?)\s+presents\b", (e.get("Name") or "") + " " + (e.get("Description") or ""), re.I)
    return m.group(1).strip() if m else ""


def fetch_goodtimes(start: dt.datetime, end: dt.datetime) -> list[dict]:
    url = f"https://portal.cityspark.com/api/events/GetEvents/{CITYSPARK_SLUG}"
    out, skip = [], 0
    while skip < 5000:
        body = {"ppid": CITYSPARK_PPID, "start": start.strftime("%Y-%m-%dT00:00"),
                "end": end.strftime("%Y-%m-%dT23:59"), "labels": [], "lat": CITYSPARK_LAT,
                "lng": CITYSPARK_LNG, "distance": CITYSPARK_MILES, "defFilter": "all",
                "skip": skip, "tps": "0"}
        batch = http_post_json(url, body).get("Value") or []
        if not batch:
            break
        skip += len(batch)
        for e in batch:
            city = (e.get("CityState") or "").split(",")[0]
            if not town_ok(city):
                continue
            all_day = bool(e.get("AllDay")) or not e.get("HasTime", True)
            s, en = parse_utc(e.get("StartUTC")), parse_utc(e.get("EndUTC"))
            if all_day or not s:
                day = (e.get("Date") or e.get("DateStart") or "")[:10]
                s_local, e_local = day, None
            else:
                s_local, e_local = iso_utc(s), (iso_utc(en) if en and en > s else None)
            addr = ", ".join(x for x in [e.get("Address"), e.get("CityState")] if x)
            if e.get("Zip"):
                addr += " " + e["Zip"]
            time_key = (e.get("DateStart") or "")[:13]
            slug = re.sub(r"\W+", "-", (e.get("Name") or "").lower())
            link = f"https://www.goodtimes.sc/events-calendar/#/details/{slug}/{e['PId']}/{time_key}"
            out.append({
                "id": f"gt-{e['PId']}-{time_key}",
                "name": (e.get("Name") or "").strip(),
                "allDay": all_day or not s, "start": s_local, "end": e_local,
                "venue": (e.get("Venue") or "").strip(), "address": addr, "town": city,
                "host": cityspark_host(e),
                "description": (e.get("Summary") or e.get("Description") or "").strip(),
                "links": [{"label": "Good Times", "url": link}] + [
                    {"label": "Event site", "url": l["url"]} for l in (e.get("Links") or [])[:1]
                    if (l.get("url") or "").startswith("http") and "eventbrite.com" not in l["url"]],
                "sources": ["Good Times"],
            })
    log(f"  Good Times: {len(out)} events in your towns")
    return out


# ------------------------------------------------------------- Eventbrite --
def eb_page(place: str, page: int) -> list[dict]:
    url = f"https://www.eventbrite.com/d/{place}/all-events/" + (f"?page={page}" if page > 1 else "")
    for attempt in range(4):  # Eventbrite often returns an empty page; retry
        try:
            h = http_get(url, timeout=40)
            i = h.find("window.__SERVER_DATA__ = ")
            if i >= 0:
                data, _ = json.JSONDecoder().raw_decode(h[i + 25:])
                ev = (data.get("search_data") or {}).get("events") or {}
                res = ev.get("results") if isinstance(ev, dict) else None
                if res:
                    return res
        except (urllib.error.URLError, ValueError, TimeoutError, OSError):
            pass
        time.sleep(1.5 * (attempt + 1))
    return []


def eb_organizer(url: str) -> str:
    try:
        h = http_get(url, timeout=25)
    except Exception:
        return ""
    for m in re.finditer(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', h, re.S):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        for x in d if isinstance(d, list) else [d]:
            if isinstance(x, dict) and "Event" in str(x.get("@type")):
                org = x.get("organizer")
                if isinstance(org, list):
                    org = org[0] if org else None
                if isinstance(org, dict) and org.get("name"):
                    return org["name"].strip()
    return ""


def fetch_eventbrite(start: dt.datetime, end: dt.datetime, cache: dict) -> list[dict]:
    jobs = [(p, n) for p in EVENTBRITE_PLACES for n in range(1, EVENTBRITE_PAGES + 1)]
    raw: dict[str, dict] = {}
    done = 0
    with cf.ThreadPoolExecutor(6) as pool:
        for res in pool.map(lambda j: eb_page(*j), jobs):
            done += 1
            for e in res:
                raw[str(e.get("id"))] = e
            print(f"\r  Eventbrite: read {done}/{len(jobs)} pages, {len(raw)} events seen", end="", flush=True)
    print()
    out = []
    for e in raw.values():
        if e.get("is_online_event") or e.get("is_cancelled"):
            continue
        v = e.get("primary_venue") or {}
        a = v.get("address") or {}
        if not town_ok(a.get("city")):
            continue
        tz = ZoneInfo(e.get("timezone") or "America/Los_Angeles")
        try:
            s = dt.datetime.strptime(f"{e['start_date']} {e.get('start_time') or '00:00'}", "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        except (KeyError, ValueError):
            continue
        if not (start <= s <= end):
            continue
        en = None
        if e.get("end_date") and e.get("end_time"):
            en = dt.datetime.strptime(f"{e['end_date']} {e['end_time']}", "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        out.append({
            "id": f"eb-{e['id']}", "name": (e.get("name") or "").strip(),
            "allDay": False, "start": iso_utc(s), "end": iso_utc(en) if en and en > s else None,
            "venue": (v.get("name") or "").strip(),
            "address": a.get("localized_address_display") or "", "town": a.get("city"),
            "host": "", "description": (e.get("summary") or "").strip(),
            "links": [{"label": "Eventbrite", "url": e.get("url")}], "sources": ["Eventbrite"],
            "_url": e.get("url"),
        })
    hosts = cache.setdefault("eb_hosts", {})
    need = [x for x in out if x["id"] not in hosts]
    if need:
        log(f"  Eventbrite: looking up hosts for {len(need)} events")
        with cf.ThreadPoolExecutor(6) as pool:
            for x, h in zip(need, pool.map(lambda x: eb_organizer(x["_url"]), need)):
                hosts[x["id"]] = h
    for x in out:
        x["host"] = hosts.get(x["id"], "")
        x.pop("_url", None)
    log(f"  Eventbrite: {len(out)} events in your towns")
    return out


# ------------------------------------------------------------------ merge --
def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())[:32]


def local_day(ev: dict) -> str:
    if ev["allDay"]:
        return ev["start"][:10]
    return parse_utc(ev["start"]).astimezone(TZ).strftime("%Y-%m-%d")


def merge(events: list[dict]) -> list[dict]:
    by_key: dict[str, dict] = {}
    for ev in events:
        key = norm(ev["name"]) + local_day(ev)
        cur = by_key.get(key)
        if not cur:
            by_key[key] = ev
            continue
        for link in ev["links"]:
            if link["url"] not in {l["url"] for l in cur["links"]}:
                cur["links"].append(link)
        cur["sources"] = sorted(set(cur["sources"]) | set(ev["sources"]))
        for f in ("host", "end", "address", "venue", "description"):
            if not cur.get(f) and ev.get(f):
                cur[f] = ev[f]
    return sorted(by_key.values(), key=lambda e: (local_day(e), not e["allDay"], e["start"], e["name"].lower()))


# ------------------------------------------------------------------- main --
def main() -> None:
    now = dt.datetime.now(TZ)
    start = dt.datetime.combine(now.date(), dt.time(0, 0), TZ)
    end = start + dt.timedelta(days=DAYS_AHEAD, hours=23, minutes=59)
    log(f"Fetching events {start:%b %-d} to {end:%b %-d, %Y} in: {', '.join(TOWNS)}")

    events, warnings, counts = [], [], {}
    for name, fn in (("Good Times", lambda: fetch_goodtimes(start, end)),
                     ("Eventbrite", lambda: fetch_eventbrite(start, end, {}))):
        try:
            found = fn()
            counts[name] = len(found)
            events += found
            if not found:
                warnings.append(f"{name} returned no events on the last update. "
                                "The site may be blocking the updater or have changed.")
        except Exception as ex:  # one broken source should not stop the rest
            counts[name] = 0
            warnings.append(f"{name} failed on the last update: {ex}")
            log(f"  {name} FAILED: {ex}")

    events = merge(events)
    for e in events:  # keep the download small
        if len(e["description"]) > 400:
            e["description"] = e["description"][:397].rstrip() + "…"
    log(f"Total: {len(events)} events after removing duplicates")

    out = os.path.join(HERE, "site")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "events.json"), "w", encoding="utf-8") as f:
        json.dump({"events": events, "warnings": warnings, "counts": counts,
                   "generated": iso_utc(now), "towns": TOWNS}, f, ensure_ascii=False,
                  separators=(",", ":"))
    with open(os.path.join(HERE, "page.html"), encoding="utf-8") as src, \
            open(os.path.join(out, "index.html"), "w", encoding="utf-8") as dst:
        dst.write(src.read())
    log(f"Wrote {out}/index.html and events.json")
    if not events:
        sys.exit("No events from any source; not publishing an empty page.")


if __name__ == "__main__":
    main()

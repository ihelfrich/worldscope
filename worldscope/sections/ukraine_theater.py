"""ukraine_theater: total-theater monitoring of the Russia/Ukraine war.

This section exists because Ian has friends and family in Ukraine
(Kyiv region especially) and wants a constant safety-and-context layer
across the daily brief. Every record carries `geo_resolution_m` and
`latency_hours` so the synthesis pass can honestly state what open
sources can and cannot show.

Sources wired (each with documented latency + resolution):

  1. ACLED Ukraine events                 1000m   24-72h    OAuth
  2. NASA FIRMS NRT (VIIRS Europe 24h)     375m    3-6h     public CSV
  3. DeepStateMap frontline GeoJSON       1000m   24h       public
  4. ISW daily assessment (HTML scrape)   1000m   24h       public
  5. UA Air Force air alerts             50000m   30s       public JSON
  6. Liveuamap RSS                        1000m    1-2h     public
  7. Ukrainian official RSS (ZSU/MoD/KCS) 50000m    1h      public
  8. OSINT Telegram via RSSHub             5000m    1-6h    public (rate-limited)
  9. BlueSky tag search                    5000m  <1h       public
 10. UNOSAT damage products                   1m   24-96h   public
 11. Copernicus EMS activations              1m   6-72h    public
 12. Sentinel-2 STAC (deferred)              10m  5d revisit
 13. Kontur HRSL                             30m  static
 14. web-intel BFS-2 crawl of editorial sites 50000m 12h

The ZSU-PROTECTION RULE is implemented in `_is_zsu_active_position`:
any record geocoded inside Ukrainian-claimed territory whose text marks
it as an ACTIVE position of a ZSU / territorial-defense / National
Guard unit is dropped at ingest. Even if a source publishes one, we
do not store it. Movement, strike target, and destroyed-unit records
are NOT dropped. See docs/ukraine_theater.md for the heuristic and
expected false-positive rate.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import requests

from . import Section, SectionState
from .state_news import _parse_rss

UA = "worldscope/0.1 ukraine-theater (contact: ianthelfrich@gmail.com)"

# Bbox covering Ukraine and Russian border oblasts (Belgorod, Kursk,
# Bryansk, Rostov). Used by FIRMS, BlueSky filter, and the maps.
THEATER_BBOX = (22.0, 44.0, 40.0, 53.0)   # (lon_min, lat_min, lon_max, lat_max)

# Ukrainian-claimed territory bbox. The ZSU-protection rule fires
# only inside this envelope; the rest of the theater bbox (Russian
# territory) is not subject to the rule.
UKRAINE_CLAIMED_BBOX = (22.0, 44.0, 40.3, 52.4)

# ZSU/territorial-defense/National Guard mention markers, in English
# + Ukrainian + Russian transliteration. We err on the side of
# false positives: better to drop a benign mention than to surface a
# real position.
ZSU_UNIT_TOKENS = [
    "zsu", "afu", "ZSU", "AFU",
    "ЗСУ", "ВСУ",
    "armed forces of ukraine", "armed forces of ukr",
    "national guard of ukraine", "ngu",
    "Національна гвардія", "Нацгвардія",
    "territorial defense", "territorial defence",
    "ТРО", "tro",
    "ЗС України", "ЗС Украины",
    "ukrainian brigade", "ukrainian battalion", "ukrainian regiment",
    "marine brigade", "airborne brigade", "air assault brigade",
    "mechanised brigade", "mechanized brigade",
]

# Markers that the unit mention is about a DESTROYED, MOVING, or
# TARGETED unit, in which case we do NOT drop it. We want surveillance
# of strikes against Ukrainian forces and unit movements as widely
# reported; we only refuse to publish current resting positions.
NON_POSITION_TOKENS = [
    "destroyed", "knocked out", "neutralized", "neutralised",
    "eliminated", "killed", "casualties", "losses",
    "знищено", "знищена", "ліквідовано", "втрати",
    "strike on", "attack on", "shelling of", "missile hit",
    "moving", "moved", "redeployed", "withdrew", "withdrawal",
    "відступ", "переміщення", "перекинуто",
    "wounded", "captured", "POW", "prisoner",
]

# Position-indicating words used together with a ZSU token to fire the
# filter. The combination matters: "ZSU strike on X" is allowed,
# "ZSU position at X" is not.
POSITION_TOKENS = [
    "position", "positions", "stronghold", "fortified",
    "dug in", "dug-in", "deployment", "deployed at",
    "stationed at", "stationed in", "garrisoned",
    "concentrated at", "concentrated in",
    "позиція", "позиции", "укріплення", "укрытие",
    "опорний пункт", "опорный пункт",
    "розташуван", "располож",
    "field HQ", "command post", "штаб", "командний пункт",
]


def _slug(s: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in (s or "")).strip("-")


def _in_bbox(lat: float, lon: float, bbox: tuple[float, float, float, float]) -> bool:
    lon_min, lat_min, lon_max, lat_max = bbox
    return (lon_min <= lon <= lon_max) and (lat_min <= lat <= lat_max)


def _flt(x: Any) -> Optional[float]:
    try:
        if x is None or x == "":
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def _is_zsu_active_position(record: dict) -> bool:
    """Structural ZSU protection.

    Returns True iff the record:
      (a) is geocoded inside Ukrainian-claimed territory, AND
      (b) mentions a ZSU / territorial-defense / National Guard unit, AND
      (c) co-mentions a position-indicating word (position, deployed, etc), AND
      (d) does NOT include destruction / movement / strike-target markers.

    Records meeting all four conditions are dropped at ingest. The
    function is conservative: any one of (a)-(c) missing means the
    record is kept; any (d) marker also keeps it (destroyed, moving,
    or struck units are reportable, not operational position leaks).
    """
    lat = _flt(record.get("latitude"))
    lon = _flt(record.get("longitude"))
    if lat is None or lon is None:
        return False
    if not _in_bbox(lat, lon, UKRAINE_CLAIMED_BBOX):
        return False

    blob_parts = [
        str(record.get("title", "")),
        str(record.get("summary", "")),
        str(record.get("notes", "")),
        str(record.get("text", "")),
    ]
    blob = " ".join(blob_parts).lower()
    if not blob.strip():
        return False

    has_zsu = any(tok.lower() in blob for tok in ZSU_UNIT_TOKENS)
    if not has_zsu:
        return False

    has_position = any(tok.lower() in blob for tok in POSITION_TOKENS)
    if not has_position:
        return False

    has_non_position = any(tok.lower() in blob for tok in NON_POSITION_TOKENS)
    if has_non_position:
        return False

    return True


# ---------------------------------------------------------------------------
# Source-specific pulls
# ---------------------------------------------------------------------------

def _fetch_acled() -> list[dict]:
    """ACLED Ukraine events, last 72 hours. Uses the AcledSection token logic."""
    from .acled import AcledSection
    sec = AcledSection()
    token = sec._get_token()
    if not token:
        return []
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=3)
    params = {
        "country": "Ukraine",
        "event_date": f"{start.isoformat()}|{end.isoformat()}",
        "event_date_where": "BETWEEN",
        "limit": 1000,
    }
    items: list[dict] = []
    for ev in sec._query(token, params):
        norm = sec._normalize(ev)
        norm["source_label"] = "ACLED Ukraine"
        norm["source_kind"] = "conflict-events"
        norm["geo_resolution_m"] = 1000
        norm["latency_hours"] = 48.0
        items.append(norm)
    return items


def _fetch_firms() -> list[dict]:
    """NASA FIRMS NRT, last-24h Europe CSV, filtered to the theater bbox.

    Public endpoint, no MAP_KEY required. Uses the same VIIRS-S/NPP 375m
    product the FirmsSection uses; we just consume the public Europe-24h
    CSV which is regenerated every few hours.
    """
    public_csv = "https://firms.modaps.eosdis.nasa.gov/data/active_fire/suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_Europe_24h.csv"
    try:
        resp = requests.get(public_csv, headers={"User-Agent": UA}, timeout=30)
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        return [{
            "id": "firms-fetch-error",
            "_error": True,
            "title": f"[FIRMS error] {type(exc).__name__}",
            "url": public_csv,
            "summary": str(exc)[:300],
            "source_label": "NASA FIRMS",
            "source_kind": "thermal",
            "geo_resolution_m": 375,
            "latency_hours": 5.0,
            "date": date.today().isoformat(),
        }]
    items: list[dict] = []
    reader = csv.DictReader(io.StringIO(resp.text))
    for row in reader:
        lat = _flt(row.get("latitude"))
        lon = _flt(row.get("longitude"))
        if lat is None or lon is None:
            continue
        if not _in_bbox(lat, lon, THEATER_BBOX):
            continue
        conf = (row.get("confidence") or "").strip().lower()
        if conf and conf not in ("nominal", "n", "high", "h"):
            continue
        acq_date = row.get("acq_date", "")
        acq_time = row.get("acq_time", "")
        frp = row.get("frp", "")
        sat = row.get("satellite", "")
        fid = f"firms-theater-{acq_date}-{acq_time}-{lat:.3f}-{lon:.3f}"
        items.append({
            "id": fid,
            "date": acq_date,
            "title": f"[FIRMS] thermal anomaly {lat:.3f}, {lon:.3f} (FRP {frp} MW, conf {conf})",
            "url": f"https://firms.modaps.eosdis.nasa.gov/usfs/map/#d:24hrs;@{lon:.3f},{lat:.3f},9z",
            "summary": f"VIIRS S-NPP NRT, sat {sat}, acquired {acq_date} {acq_time}Z, FRP {frp} MW",
            "latitude": lat,
            "longitude": lon,
            "frp": frp,
            "confidence": conf,
            "source_label": "NASA FIRMS",
            "source_kind": "thermal",
            "geo_resolution_m": 375,
            "latency_hours": 5.0,
        })
    return items


def _fetch_deepstatemap() -> list[dict]:
    """DeepStateMap latest frontline GeoJSON. One synthetic record summarising
    the frontline; the actual polygons land in the lake `extra` payload so the
    cartographer can draw them later."""
    url = "https://deepstatemap.live/api/history/last"
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except (requests.exceptions.RequestException, ValueError) as exc:
        return [{
            "id": "deepstatemap-error",
            "_error": True,
            "title": f"[DeepStateMap error] {type(exc).__name__}",
            "url": url,
            "summary": str(exc)[:300],
            "source_label": "DeepStateMap",
            "source_kind": "frontline",
            "geo_resolution_m": 1000,
            "latency_hours": 24.0,
            "date": date.today().isoformat(),
        }]
    # The endpoint returns a GeoJSON-like FeatureCollection. Extract a
    # summary record with metadata; the polygons themselves live in
    # `extra` for the map renderer.
    map_date = data.get("date") or data.get("created_at") or date.today().isoformat()
    features = data.get("map", {}).get("features") if isinstance(data.get("map"), dict) else data.get("features", [])
    feat_count = len(features) if isinstance(features, list) else 0
    return [{
        "id": f"deepstatemap-{map_date}",
        "date": str(map_date)[:10],
        "title": f"[DeepStateMap] frontline snapshot, {feat_count} polygons",
        "url": "https://deepstatemap.live/",
        "summary": "Daily community-maintained frontline cartography. Polygon coverage in extra.",
        "source_label": "DeepStateMap",
        "source_kind": "frontline",
        "geo_resolution_m": 1000,
        "latency_hours": 24.0,
        "features_json": json.dumps(features)[:30000] if isinstance(features, list) else "",
    }]


def _fetch_air_alerts() -> list[dict]:
    """Ukrainian Air Force active alerts.

    alerts.in.ua requires an API token (free, request at api@alerts.in.ua).
    Configure via ALERTS_IN_UA_TOKEN env var. Without the token we return
    an explanatory stub record so the source-health table still tracks
    the attempt. v2 endpoint is preferred when the token is set.
    """
    token = os.environ.get("ALERTS_IN_UA_TOKEN")
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        url = "https://api.alerts.in.ua/v2/alerts/active.json"
    else:
        url = "https://api.alerts.in.ua/v1/alerts/active.json"
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except (requests.exceptions.RequestException, ValueError) as exc:
        return [{
            "id": "alerts-fetch-error",
            "_error": True,
            "title": (f"[Air alerts error] {type(exc).__name__} "
                      f"(set ALERTS_IN_UA_TOKEN for v2)"),
            "url": url,
            "summary": str(exc)[:300],
            "source_label": "UA Air Force alerts",
            "source_kind": "air-alert",
            "geo_resolution_m": 50000,
            "latency_hours": 0.01,
            "date": date.today().isoformat(),
        }]
    items: list[dict] = []
    alerts = data.get("alerts") or []
    ingested = datetime.now(timezone.utc).isoformat()
    for a in alerts:
        oblast = a.get("location_oblast") or a.get("location_title") or ""
        started_at = a.get("started_at") or ""
        atype = a.get("alert_type") or "air_raid"
        aid = a.get("id") or hashlib.sha1(f"{oblast}|{started_at}".encode()).hexdigest()[:12]
        items.append({
            "id": f"alert-{aid}",
            "date": (started_at[:10] if started_at else date.today().isoformat()),
            "title": f"[Air alert] {oblast} ({atype})",
            "url": "https://alerts.in.ua/",
            "summary": f"Active {atype}, started {started_at}, oblast {oblast}",
            "oblast": oblast,
            "alert_type": atype,
            "started_at": started_at,
            "ingested_at": ingested,
            "source_label": "UA Air Force alerts",
            "source_kind": "air-alert",
            "geo_resolution_m": 50000,
            "latency_hours": 0.01,
        })
    return items


def _fetch_liveuamap() -> list[dict]:
    """Liveuamap event RSS.

    Liveuamap retired its public RSS in early 2024. We try the legacy URL
    first (some mirrors keep it alive) and fall back to Google News
    site-search, the same pattern local_news.py uses for retired feeds.
    """
    urls = [
        "https://liveuamap.com/en/feed",
        "https://liveuamap.com/feed.xml",
        ("https://news.google.com/rss/search?q=site%3Aliveuamap.com&hl=en-US"
         "&gl=US&ceid=US:en"),
    ]
    body = None
    last_status = None
    last_url = urls[0]
    for url in urls:
        last_url = url
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": UA, "Accept": "application/rss+xml,*/*"},
                timeout=15,
            )
            last_status = resp.status_code
            if resp.status_code == 200 and resp.content:
                body = resp.content
                break
        except requests.exceptions.RequestException as exc:
            last_status = type(exc).__name__
            continue
    if body is None:
        return [{
            "id": f"liveuamap-error-{last_status}",
            "_error": True,
            "title": f"[Liveuamap error] HTTP {last_status}",
            "url": last_url,
            "summary": "primary feed retired; Google News proxy also unreachable",
            "source_label": "Liveuamap",
            "source_kind": "osint-feed",
            "geo_resolution_m": 1000,
            "latency_hours": 1.5,
            "date": date.today().isoformat(),
        }]
    items = []
    # Reuse the parsed body
    class _R:
        content = body
    resp = _R()
    for it in _parse_rss(resp.content):
        # Liveuamap items occasionally embed lat/lon in the description;
        # we don't parse it out here, but the title and URL are usable.
        iid = hashlib.sha1((it.get("url", "") + "|" + it.get("title", "")).encode()).hexdigest()
        items.append({
            "id": f"liveuamap-{iid[:16]}",
            "date": it.get("date", "") or date.today().isoformat(),
            "title": f"[Liveuamap] {it.get('title','')[:240]}",
            "url": it.get("url", ""),
            "summary": (it.get("summary") or "")[:400],
            "source_label": "Liveuamap",
            "source_kind": "osint-feed",
            "geo_resolution_m": 1000,
            "latency_hours": 1.5,
        })
    return items


def _fetch_official_ua() -> list[dict]:
    """Try the three Ukrainian official RSS feeds; fall back to Google News
    site-search for any that 404. Each item is labeled by ministry/agency."""
    feeds = [
        ("ZSU general staff",
         "https://www.zsu.gov.ua/rss",
         "https://news.google.com/rss/search?q=site%3Azsu.gov.ua&hl=en-US&gl=US&ceid=US:en"),
        ("UA Ministry of Defence",
         "https://mod.gov.ua/feed",
         "https://news.google.com/rss/search?q=site%3Amod.gov.ua+OR+site%3Amil.gov.ua&hl=en-US&gl=US&ceid=US:en"),
        ("Kyiv City State Administration",
         "https://kyivcity.gov.ua/rss/",
         "https://news.google.com/rss/search?q=site%3Akyivcity.gov.ua&hl=en-US&gl=US&ceid=US:en"),
    ]
    items: list[dict] = []
    for label, primary, fallback in feeds:
        body = None
        for url in (primary, fallback):
            try:
                resp = requests.get(url, headers={"User-Agent": UA}, timeout=15)
                if resp.status_code == 200 and resp.content:
                    body = resp.content
                    break
            except requests.exceptions.RequestException:
                continue
        if body is None:
            continue
        for it in _parse_rss(body):
            iid = hashlib.sha1((label + "|" + it.get("url", "") + "|" + it.get("title", "")).encode()).hexdigest()
            items.append({
                "id": f"official-ua-{iid[:16]}",
                "date": it.get("date", "") or date.today().isoformat(),
                "title": f"[{label}] {it.get('title','')[:240]}",
                "url": it.get("url", ""),
                "summary": (it.get("summary") or "")[:400],
                "source_label": label,
                "source_kind": "official-ua",
                "geo_resolution_m": 50000,
                "latency_hours": 1.0,
            })
    return items


# RSSHub OSINT channels. Heavily rate-limited; we cache aggressively
# via the lake (so re-running the section the same day skips re-fetch).
RSSHUB_CHANNELS = [
    "noelreports",
    "asbmilnews",
    "ukraineweaponstracker",
    "geoconfirmed",
    "markus_reisner",
    "schemes_ukraine",
]


def _fetch_rsshub_telegram() -> list[dict]:
    """OSINT Telegram channels via the public RSSHub instance. Heavily
    rate-limited; per-channel failures are tolerated."""
    items: list[dict] = []
    for ch in RSSHUB_CHANNELS:
        url = f"https://rsshub.app/telegram/channel/{ch}"
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=15)
        except requests.exceptions.RequestException:
            continue
        if resp.status_code != 200 or not resp.content:
            continue
        for it in _parse_rss(resp.content):
            iid = hashlib.sha1((ch + "|" + it.get("url", "") + "|" + it.get("title", "")).encode()).hexdigest()
            items.append({
                "id": f"tg-{ch}-{iid[:16]}",
                "date": it.get("date", "") or date.today().isoformat(),
                "title": f"[tg:{ch}] {it.get('title','')[:240]}",
                "url": it.get("url", ""),
                "summary": (it.get("summary") or "")[:400],
                "channel": ch,
                "source_label": f"Telegram/{ch}",
                "source_kind": "osint-telegram",
                "geo_resolution_m": 5000,
                "latency_hours": 3.0,
            })
        # Per-channel throttle so RSSHub doesn't rate-limit us
        time.sleep(0.5)
    return items


def _fetch_bluesky() -> list[dict]:
    """BlueSky public search for war-tag posts. The public xrpc endpoint
    is unauthenticated and returns JSON; we map each hit to a record."""
    queries = ["#UkraineWar", "#GeoConfirmed", "#NAFO"]
    items: list[dict] = []
    for q in queries:
        url = f"https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts?q={q}&limit=30"
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except (requests.exceptions.RequestException, ValueError):
            continue
        for post in data.get("posts", []):
            uri = post.get("uri", "")
            cid = post.get("cid", "")
            author = (post.get("author") or {}).get("handle", "")
            record = post.get("record") or {}
            text = record.get("text", "")
            created_at = record.get("createdAt", "")
            iid = hashlib.sha1((uri + cid).encode()).hexdigest()
            items.append({
                "id": f"bsky-{iid[:16]}",
                "date": (created_at[:10] if created_at else date.today().isoformat()),
                "title": f"[bsky/{author}] {text[:240]}",
                "url": f"https://bsky.app/profile/{author}/post/{uri.split('/')[-1]}" if author and uri else "https://bsky.app/",
                "summary": text[:400],
                "author": author,
                "query": q,
                "source_label": "BlueSky tag search",
                "source_kind": "osint-social",
                "geo_resolution_m": 50000,
                "latency_hours": 0.5,
            })
        time.sleep(0.5)
    return items


def _fetch_isw() -> list[dict]:
    """ISW campaign assessment.

    The dated URL pattern (backgrounder/...-may-27-2026) is the canonical
    one but the slug capitalization and exact wording drift between
    weekdays and special editions. We try the dated URL for today and
    yesterday, then fall back to a Google News site-search RSS that
    surfaces recent posts. The fallback gives us at least the link so
    a reader can navigate to the day's assessment.
    """
    items: list[dict] = []
    today = datetime.now(timezone.utc).date()
    direct_hit = False
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
    }
    for d in (today, today - timedelta(days=1)):
        slug = d.strftime("%B-%d-%Y").lower()
        url = f"https://www.understandingwar.org/backgrounder/russian-offensive-campaign-assessment-{slug}"
        try:
            resp = requests.get(url, headers=headers, timeout=20)
        except requests.exceptions.RequestException:
            continue
        if resp.status_code != 200:
            continue
        text = re.sub(r"<[^>]+>", " ", resp.text)
        text = re.sub(r"\s+", " ", text).strip()
        body_start = text.find("Key Takeaways")
        if body_start < 0:
            body_start = 0
        excerpt = text[body_start:body_start + 1200]
        items.append({
            "id": f"isw-{d.isoformat()}",
            "date": d.isoformat(),
            "title": f"[ISW] Russian Offensive Campaign Assessment {d.isoformat()}",
            "url": url,
            "summary": excerpt[:500],
            "source_label": "Institute for the Study of War",
            "source_kind": "analysis",
            "geo_resolution_m": 1000,
            "latency_hours": 24.0,
        })
        direct_hit = True
        break
    if direct_hit:
        return items
    # Fallback: Google News site-search
    proxy = ("https://news.google.com/rss/search?q=site%3Aunderstandingwar.org+"
             "russian+offensive+campaign+assessment&hl=en-US&gl=US&ceid=US:en")
    try:
        resp = requests.get(proxy, headers=headers, timeout=20)
        if resp.status_code == 200:
            for it in _parse_rss(resp.content)[:5]:
                iid = hashlib.sha1(it.get("url", "").encode()).hexdigest()
                items.append({
                    "id": f"isw-proxy-{iid[:16]}",
                    "date": it.get("date", "") or today.isoformat(),
                    "title": f"[ISW] {it.get('title','')[:240]}",
                    "url": it.get("url", ""),
                    "summary": (it.get("summary") or "")[:400],
                    "source_label": "Institute for the Study of War",
                    "source_kind": "analysis",
                    "geo_resolution_m": 1000,
                    "latency_hours": 24.0,
                })
    except requests.exceptions.RequestException:
        pass
    return items


def _fetch_unosat() -> list[dict]:
    """UNOSAT damage products. The site does not expose a stable RSS;
    we hit a search URL and look for product links in the past 14 days.
    On failure we return a stub error record so the section health table
    still records the attempt."""
    url = "https://unosat.org/products/?country=ukraine"
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        return [{
            "id": "unosat-fetch-error",
            "_error": True,
            "title": f"[UNOSAT error] {type(exc).__name__}",
            "url": url,
            "summary": str(exc)[:300],
            "source_label": "UNOSAT",
            "source_kind": "damage-assessment",
            "geo_resolution_m": 1,
            "latency_hours": 72.0,
            "date": date.today().isoformat(),
        }]
    # Light HTML pattern match for product cards
    items: list[dict] = []
    links = re.findall(
        r'href="(/products/[^"]+)"[^>]*>([^<]{8,180})',
        resp.text,
    )
    seen: set[str] = set()
    for href, title in links[:50]:
        if href in seen:
            continue
        seen.add(href)
        full = f"https://unosat.org{href}"
        items.append({
            "id": f"unosat-{hashlib.sha1(full.encode()).hexdigest()[:16]}",
            "date": date.today().isoformat(),
            "title": f"[UNOSAT] {title.strip()[:240]}",
            "url": full,
            "summary": "UNOSAT damage product; see page for AOI and acquisition date.",
            "source_label": "UNOSAT",
            "source_kind": "damage-assessment",
            "geo_resolution_m": 1,
            "latency_hours": 72.0,
        })
    return items


def _fetch_copernicus_ems() -> list[dict]:
    """Copernicus EMS Rapid Mapping activations for Ukraine. The site uses
    a list page with paginated activations; we parse the first page."""
    # Their list-of-activations URL has shifted multiple times. We try
    # two known patterns and fall back to a Google News site-search.
    urls = [
        "https://emergency.copernicus.eu/mapping/list-of-activations-rapid?title=&country=UKR",
        "https://emergency.copernicus.eu/mapping/list-of-activations-rapid",
        "https://news.google.com/rss/search?q=site%3Aemergency.copernicus.eu+Ukraine&hl=en-US&gl=US&ceid=US:en",
    ]
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
    }
    resp = None
    url = urls[0]
    for u in urls:
        try:
            r = requests.get(u, headers=headers, timeout=20)
            if r.status_code == 200 and r.content:
                resp = r
                url = u
                break
        except requests.exceptions.RequestException:
            continue
    if resp is None:
        return [{
            "id": "copernicus-fetch-error",
            "_error": True,
            "title": "[Copernicus EMS error] all endpoints 4xx/5xx",
            "url": urls[0],
            "summary": "all known activation-list URLs returned non-200",
            "source_label": "Copernicus EMS",
            "source_kind": "damage-assessment",
            "geo_resolution_m": 5,
            "latency_hours": 48.0,
            "date": date.today().isoformat(),
        }]
    try:
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        return [{
            "id": "copernicus-fetch-error",
            "_error": True,
            "title": f"[Copernicus EMS error] {type(exc).__name__}",
            "url": url,
            "summary": str(exc)[:300],
            "source_label": "Copernicus EMS",
            "source_kind": "damage-assessment",
            "geo_resolution_m": 5,
            "latency_hours": 48.0,
            "date": date.today().isoformat(),
        }]
    items: list[dict] = []
    matches = re.findall(
        r'href="(/mapping/list-of-components/[^"]+)"[^>]*>([^<]{5,180})',
        resp.text,
    )
    seen: set[str] = set()
    for href, title in matches[:30]:
        if href in seen:
            continue
        seen.add(href)
        full = f"https://emergency.copernicus.eu{href}"
        items.append({
            "id": f"ems-{hashlib.sha1(full.encode()).hexdigest()[:16]}",
            "date": date.today().isoformat(),
            "title": f"[Copernicus EMS] {title.strip()[:240]}",
            "url": full,
            "summary": "Copernicus EMS Rapid Mapping activation product for Ukraine.",
            "source_label": "Copernicus EMS",
            "source_kind": "damage-assessment",
            "geo_resolution_m": 5,
            "latency_hours": 48.0,
        })
    return items


def _fetch_webintel_crawl() -> list[dict]:
    """Shell out to web-intel for a BFS-2 of Ukrainian + dissident editorial
    sites. Cached to lake/sections/ukraine_theater/<date>/crawl/."""
    from ..crawlers.ukraine_osint import crawl_seeds
    out_root = Path("lake/sections/ukraine_theater") / date.today().isoformat() / "crawl"
    pages = crawl_seeds(out_root)
    items: list[dict] = []
    for p in pages:
        title = p.get("title") or ""
        url = p.get("url") or ""
        if not url:
            continue
        iid = hashlib.sha1(url.encode()).hexdigest()
        items.append({
            "id": f"crawl-{iid[:16]}",
            "date": (p.get("fetched_at") or "")[:10] or date.today().isoformat(),
            "title": f"[{p.get('host','')}] {title[:240]}",
            "url": url,
            "summary": f"Editorial page crawled via web-intel BFS-2, host {p.get('host','')}",
            "source_label": f"web-intel/{p.get('host','')}",
            "source_kind": "editorial-crawl",
            "geo_resolution_m": 50000,
            "latency_hours": 12.0,
        })
    return items


# ---------------------------------------------------------------------------
# Section class
# ---------------------------------------------------------------------------

class UkraineTheaterSection(Section):
    id = "ukraine_theater"
    title = "Ukraine Theater (total-war monitoring)"
    emoji = "🛡️"

    source_id = "ukraine-theater-aggregate"
    source_name = "Ukraine theater multi-source aggregate"
    source_url = "https://github.com/ihelfrich/worldscope"
    source_tier = "mixed"
    source_license = "varies-per-feed"
    attribution_required = True
    attribution_text = (
        "ACLED conflict events (CC-BY 4.0). NASA FIRMS near-real-time "
        "VIIRS active fires (public domain). DeepStateMap community "
        "cartography. UNOSAT and Copernicus EMS damage products. "
        "Ukrainian government RSS. OSINT Telegram via RSSHub. "
        "Per-record latency and geo resolution preserved in raw.jsonl "
        "so downstream consumers can honestly state what is and is not "
        "shown."
    )
    source_country = "Ukraine"
    source_language = "en"

    PULL_TIMEOUT_S = 180

    # The set of pulls that run on the hourly cadence. Heavier sources
    # (UNOSAT, Copernicus EMS) move to a daily-only list.
    HOURLY_PULLS = [
        ("acled",        _fetch_acled),
        ("firms",        _fetch_firms),
        ("deepstatemap", _fetch_deepstatemap),
        ("isw",          _fetch_isw),
        ("alerts",       _fetch_air_alerts),
        ("liveuamap",    _fetch_liveuamap),
        ("official_ua",  _fetch_official_ua),
        ("rsshub",       _fetch_rsshub_telegram),
        ("bluesky",      _fetch_bluesky),
        ("webintel",     _fetch_webintel_crawl),
    ]
    DAILY_PULLS = [
        ("unosat",       _fetch_unosat),
        ("copernicus",   _fetch_copernicus_ems),
    ]

    def pull(self) -> list[dict]:
        all_items: list[dict] = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {
                pool.submit(fn): name
                for name, fn in (self.HOURLY_PULLS + self.DAILY_PULLS)
            }
            for fut in as_completed(futures):
                name = futures[fut]
                try:
                    res = fut.result()
                    all_items.extend(res)
                except Exception as exc:
                    all_items.append({
                        "id": f"{name}-exception",
                        "_error": True,
                        "title": f"[{name} exception] {type(exc).__name__}",
                        "url": "",
                        "summary": str(exc)[:300],
                        "source_label": name,
                        "source_kind": "internal",
                        "geo_resolution_m": 0,
                        "latency_hours": 0.0,
                        "date": date.today().isoformat(),
                    })

        # Apply the ZSU protection rule. Filtered records are dropped
        # from the returned list entirely; we keep a count for the
        # source-health log.
        kept: list[dict] = []
        zsu_drop_count = 0
        for it in all_items:
            if not it.get("_error") and _is_zsu_active_position(it):
                zsu_drop_count += 1
                continue
            kept.append(it)
        if zsu_drop_count:
            kept.append({
                "id": "zsu-filter-summary",
                "date": date.today().isoformat(),
                "title": f"[ZSU-protection] dropped {zsu_drop_count} record(s) as active-position mentions",
                "url": "",
                "summary": (
                    "Records geocoded inside Ukrainian-claimed territory that mentioned "
                    "a ZSU/territorial-defense/National Guard unit at an explicit active "
                    "position were dropped at ingest. Strike, destruction, movement, and "
                    "casualty records are NOT subject to this filter. See "
                    "docs/ukraine_theater.md for the heuristic."
                ),
                "source_label": "internal/ZSU-filter",
                "source_kind": "internal",
                "geo_resolution_m": 0,
                "latency_hours": 0.0,
                "_filter_summary": True,
                "_zsu_drop_count": zsu_drop_count,
            })
        return kept

    # ---- contract overrides ------------------------------------------------

    def to_raw_record(self, item: dict, *, today_iso: str) -> dict:
        record = super().to_raw_record(item, today_iso=today_iso)
        record["source_id"] = f"ukraine-theater:{_slug(item.get('source_label',''))}"
        record["source_tier"] = item.get("source_tier", self.source_tier)
        # The two non-negotiable contract additions
        record["geo_resolution_m"] = int(item.get("geo_resolution_m") or 0)
        record["latency_hours"] = float(item.get("latency_hours") or 0.0)
        if not item.get("_error"):
            record["entities"] = [e["id"] for e in self.extract_entities(item)]
        return record

    def extract_entities(self, item: dict) -> list[dict]:
        if item.get("_error"):
            return []
        entities = [{
            "id": "place:country-ukraine",
            "type": "place",
            "canonical_name": "Ukraine",
            "metadata": {"kind": "country"},
        }]
        if item.get("oblast"):
            entities.append({
                "id": f"place:oblast-{_slug(item['oblast'])}",
                "type": "place",
                "canonical_name": item["oblast"],
                "metadata": {"kind": "oblast", "country": "Ukraine"},
            })
        label = item.get("source_label", "")
        if label:
            entities.append({
                "id": f"org:source-{_slug(label)}",
                "type": "org",
                "canonical_name": label,
                "metadata": {
                    "kind": item.get("source_kind", "feed"),
                    "geo_resolution_m": item.get("geo_resolution_m"),
                    "latency_hours": item.get("latency_hours"),
                },
            })
        return entities

    def emit_structured(self, state_obj: SectionState) -> dict:
        base = super().emit_structured(state_obj)
        seen: dict[str, dict] = {}
        relationships = []
        feed_errors = []
        zsu_summary_count = 0
        for item in state_obj.items:
            if item.get("_error"):
                feed_errors.append(item)
                continue
            if item.get("_filter_summary"):
                zsu_summary_count = item.get("_zsu_drop_count", 0)
                continue
            for e in self.extract_entities(item):
                seen[e["id"]] = e
            label = item.get("source_label", "")
            if label:
                relationships.append({
                    "from": f"org:source-{_slug(label)}",
                    "to": "place:country-ukraine",
                    "type": "reports-on",
                    "weight": 1.0,
                    "evidence": [item.get("_id") or self._item_id(item)],
                })
        base["entities_added"] = list(seen.values())
        base["relationships"] = relationships
        for err in feed_errors:
            base["anomalies"].append({
                "category": "feed-failure",
                "z_score": None,
                "description": err.get("title", ""),
                "evidence": [err.get("_id") or self._item_id(err)],
            })
        if zsu_summary_count:
            base["anomalies"].append({
                "category": "zsu-protection-filter",
                "z_score": None,
                "description": f"Dropped {zsu_summary_count} record(s) by ZSU active-position rule.",
                "evidence": ["zsu-filter-summary"],
            })
        return base

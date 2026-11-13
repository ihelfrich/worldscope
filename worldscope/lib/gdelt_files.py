"""GDELT GKG 2.1 bulk files: the rate-limit-free path to GDELT.

The DOC search API throttles GitHub's shared runner addresses far below its
documented one request per 5 s; on 2026-11-13 two builds got 1-3 of 19
countries and the theme section failed outright. GDELT's own 429 text tells
heavy users to switch to the bulk datasets. Every 15 minutes GDELT publishes a
static GKG file (~940 English articles, ~4 MB zipped) at a predictable URL:
URL, outlet domain, page title, themes, locations and tone for every article.

``recent_articles()`` downloads the last ``hours`` of files once per process
(a few dozen MB), parses them, and caches the result, so gdelt_regions,
gdelt_gkg and conflict share one download and match locally.
"""
from __future__ import annotations

import io
import re
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import unescape

import requests

BASE = "https://data.gdeltproject.org/gdeltv2"
UA = "worldscope/0.1 (+https://github.com/ihelfrich/worldscope)"
DEFAULT_HOURS = 8
STEP_MIN = 30  # every other 15-minute file: half the download, same spread

_TITLE_RE = re.compile(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>", re.S)
_lock = threading.Lock()
_cache: dict[tuple[int, str], "Batch"] = {}


class GdeltFilesUnavailable(Exception):
    """No GKG file could be downloaded."""


@dataclass
class Article:
    url: str
    domain: str
    title: str
    seen: datetime
    themes: frozenset[str]
    countries: tuple[str, ...]          # FIPS codes, most-mentioned first
    subnational: frozenset[str]         # FIPS codes named at city/province level
    us_local: bool                      # mentions a US state or city
    country_name: str                   # name of countries[0], "" if none
    points: tuple[tuple[float, float], ...]  # (lat, lon) of mentioned places
    tone: float | None

    @property
    def date(self) -> str:
        return self.seen.date().isoformat()

    @property
    def seendate(self) -> str:
        return self.seen.strftime("%Y%m%dT%H%M%SZ")


@dataclass
class Batch:
    articles: list[Article]
    files_ok: int
    files_tried: int
    errors: list[str] = field(default_factory=list)


def _slots(now: datetime, hours: float) -> list[str]:
    t = now.replace(second=0, microsecond=0)
    t -= timedelta(minutes=t.minute % 15 + 15)  # newest file surely published
    out = []
    while t > now - timedelta(hours=hours):
        out.append(t.strftime("%Y%m%d%H%M%S"))
        t -= timedelta(minutes=STEP_MIN)
    return out


def _locations(enhanced: str, plain: str):
    """(FIPS codes most-mentioned first, (lat, lon) points).

    V2 enhanced blocks: type#name#FIPS#ADM1#ADM2#lat#lon#feature#offset (one
    per mention, so counts rank prominence); V1 blocks: type#name#FIPS#ADM1#
    lat#lon#feature (one per place).
    """
    counts: dict[str, int] = {}
    points = []
    sub: set[str] = set()
    names: dict[str, str] = {}
    us_local = False
    use_v2 = bool(enhanced)
    lat_i = 5 if use_v2 else 4
    for block in (enhanced if use_v2 else plain).split(";"):
        parts = block.split("#")
        if len(parts) < lat_i + 2:
            continue
        code = parts[2].strip()
        if code:
            counts[code] = counts.get(code, 0) + 1
        if parts[0] in ("4", "5") and code:
            sub.add(code)
        elif parts[0] in ("2", "3"):
            us_local = True
        try:
            points.append((float(parts[lat_i]), float(parts[lat_i + 1])))
        except ValueError:
            pass
    ranked = tuple(c for c, _ in sorted(counts.items(), key=lambda kv: -kv[1]))
    # Canonical country names come from the V1 blocks ("1#Australia#AS#...";
    # V2 country-level names are the word as written, e.g. "American").
    for block in plain.split(";"):
        parts = block.split("#")
        if len(parts) >= 3 and parts[2]:
            if parts[0] in ("2", "3"):
                names.setdefault(parts[2], "United States")
            else:
                names.setdefault(parts[2], parts[1].split(",")[-1].strip())
    return ranked, tuple(points), frozenset(sub), us_local, \
        (names.get(ranked[0], "") if ranked else "")


def parse_row(line: str) -> Article | None:
    f = line.split("\t")
    if len(f) < 27 or f[3] == "" or not f[4].startswith("http"):
        return None
    m = _TITLE_RE.search(f[26])
    title = unescape(m.group(1)).strip() if m else ""
    if not title:
        return None
    try:
        seen = datetime.strptime(f[1], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    themes = frozenset(t.split(",")[0] for t in (f[8] or f[7]).split(";") if t)
    countries, points, sub, us_local, cname = _locations(f[10], f[9])
    try:
        tone = float(f[15].split(",")[0])
    except (ValueError, IndexError):
        tone = None
    return Article(f[4], f[3].lower(), title, seen, themes, countries, sub, us_local,
                   cname, points, tone)


def _download(stamp: str, timeout: float) -> list[Article]:
    resp = requests.get(f"{BASE}/{stamp}.gkg.csv.zip", headers={"User-Agent": UA},
                        timeout=timeout)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        text = z.read(z.namelist()[0]).decode("utf-8", "replace")
    return [a for a in map(parse_row, text.splitlines()) if a is not None]


def recent_articles(hours: float = DEFAULT_HOURS, *, now: datetime | None = None,
                    timeout: float = 40.0, workers: int = 4) -> Batch:
    """Articles from the last ``hours`` of GKG files, de-duplicated by URL."""
    now = now or datetime.now(timezone.utc)
    key = (int(hours * 60), now.strftime("%Y%m%d%H"))
    with _lock:
        if key in _cache:
            return _cache[key]
    stamps = _slots(now, hours)
    errors: list[str] = []
    seen_urls: set[str] = set()
    articles: list[Article] = []
    ok = 0

    def fetch(stamp):
        try:
            return _download(stamp, timeout)
        except Exception as exc:  # one missing file is normal (late publish)
            errors.append(f"{stamp}: {str(exc)[:80]}")
            return None

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="gkgfile") as pool:
        for batch in pool.map(fetch, stamps):
            if batch is None:
                continue
            ok += 1
            for a in batch:
                if a.url not in seen_urls:
                    seen_urls.add(a.url)
                    articles.append(a)
    if ok == 0:
        raise GdeltFilesUnavailable(
            f"no GKG file downloaded ({len(stamps)} tried; {errors[0] if errors else 'unknown'})")
    articles.sort(key=lambda a: a.seen, reverse=True)
    result = Batch(articles, ok, len(stamps), errors)
    with _lock:
        _cache[key] = result
    return result


def clear_cache() -> None:
    with _lock:
        _cache.clear()


_STOP = frozenset("""about after again against also amid among another around because before
being between could during first from have into just like more most much news over said says
since some still than that their them then there these they this those through under until
update very what when where which while will with without would years your report reports
week today first latest live video watch""".split())
_WORD_RE = re.compile(r"[a-z][a-z'-]{3,}")


def _tokens(title: str) -> frozenset[str]:
    return frozenset(w for w in _WORD_RE.findall(title.lower()) if w not in _STOP)


def top_stories(candidates: list[Article], n: int, *,
                ignore: frozenset[str] = frozenset()) -> list[tuple[Article, int]]:
    """The ``n`` most widely covered distinct stories, with their echo counts.

    A story's echo is how many *other* outlets ran a headline sharing at least
    two content words with it, so wire stories and genuine news outrank one-off
    pieces (including geotagging misfires). One article per outlet, and a
    candidate sharing two or more content words (after removing ``ignore``,
    e.g. the country's own name) with an already picked headline is treated
    as the same story.
    """
    toks = [_tokens(a.title) - ignore for a in candidates]
    index: dict[str, list[int]] = {}
    for i, t in enumerate(toks):
        for w in t:
            index.setdefault(w, []).append(i)
    echo = []
    for i, t in enumerate(toks):
        overlap: dict[int, int] = {}
        for w in t:
            for j in index[w]:
                if j != i:
                    overlap[j] = overlap.get(j, 0) + 1
        domains = {candidates[j].domain for j, k in overlap.items()
                   if k >= 2 and candidates[j].domain != candidates[i].domain}
        echo.append(len(domains))
    order = sorted(range(len(candidates)),
                   key=lambda i: (echo[i], candidates[i].seen), reverse=True)
    picked: list[int] = []
    used_domains: set[str] = set()
    for i in order:
        if len(picked) >= n:
            break
        if candidates[i].domain in used_domains:
            continue
        if any(len(toks[i] & toks[j]) >= 2 for j in picked):
            continue
        picked.append(i)
        used_domains.add(candidates[i].domain)
    return [(candidates[i], echo[i]) for i in picked]

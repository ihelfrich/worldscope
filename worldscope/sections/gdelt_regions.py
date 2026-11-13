"""
gdelt_regions.py — top news stories from GDELT, stratified by country.

For each country in the watchlist, pulls the most-recent N articles whose
source country matches (per GDELT's FIPS-ish code). Normalizes the result
to the standard Section schema.

GDELT DOC 2.0 is multilingual and updates every 15 minutes. No API key.
The free tier is rate-limited: a 429 comes back as plain text "Please limit
requests to one every 5 seconds ..." (observed live 2026-10-09, where a
shared egress IP was throttled on every call and each 429 took ~11s to be
served). That latency × 19 sequential countries is what used to push this
section past its 90s deadline.

Design now (2026-11-13): primary path is GDELT's bulk GKG files
(worldscope.lib.gdelt_files), which are static downloads with no rate limit.
Two CI builds on 2026-11-13 got 1-3 of 19 countries from the DOC API, whose
throttling of shared runner addresses is far below its documented limit.
From the last ~8 hours of English GKG articles (~20k), a story belongs to a
country when its headline names the country, its people, capital or leader
(ALIASES); ambiguous names ("turkey") also need a city or province of that
country in GDELT's location tags. Within a country, stories are ranked by how
many other outlets ran the same story (lib.gdelt_files.top_stories), one per
outlet, duplicates collapsed.

Fallback when no bulk file can be downloaded: the DOC API, one OR'd query for
every country, then per-country top-ups through the shared request spacer.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import re

from ..lib import gdelt, gdelt_files
from . import Section, UpstreamHTTPError


# (FIPS-ish country code GDELT recognizes, display name).
# China appears twice — once as the party-state outlet view (CH), once paired
# with HK/MO when those happen to surface — for now we just use CH and let
# the editorial framing show through whatever GDELT returns.
WATCHLIST: list[tuple[str, str]] = [
    ("CH", "China"),
    ("JA", "Japan"),
    ("KS", "South Korea"),
    ("UP", "Ukraine"),
    ("GM", "Germany"),
    ("IT", "Italy"),
    ("IS", "Israel"),
    ("IR", "Iran"),
    ("SA", "Saudi Arabia"),
    ("TU", "Turkey"),
    ("NI", "Nigeria"),
    ("EC", "Ecuador"),
    ("CO", "Colombia"),
    ("PO", "Portugal"),
    ("CA", "Canada"),
    ("MX", "Mexico"),
    ("UK", "United Kingdom"),
    ("BR", "Brazil"),
    ("IN", "India"),
]


# (strong headline names, ambiguous names that also need a sub-national
# location tag in that country). Lowercase; matched as whole words/phrases.
ALIASES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "CH": (("china", "chinese", "beijing", "shanghai", "xi jinping", "hong kong"), ()),
    "JA": (("japan", "japanese", "tokyo", "osaka"), ()),
    "KS": (("south korea", "south korean", "seoul"), ("korean", "korea")),
    "UP": (("ukraine", "ukrainian", "kyiv", "kiev", "zelensky", "zelenskyy",
            "kharkiv", "odesa"), ()),
    "GM": (("germany", "german", "berlin", "bundestag", "merz"), ("munich",)),
    "IT": (("italy", "italian", "meloni"), ("rome", "milan")),
    "IS": (("israel", "israeli", "jerusalem", "tel aviv", "netanyahu", "idf"), ()),
    "IR": (("iran", "iranian", "tehran", "khamenei"), ()),
    "SA": (("saudi", "riyadh", "jeddah"), ()),
    "TU": (("turkiye", "türkiye", "turkish", "ankara", "istanbul", "erdogan", "erdoğan"),
           ("turkey",)),
    "NI": (("nigeria", "nigerian", "lagos", "abuja", "tinubu"), ()),
    "EC": (("ecuador", "ecuadorian", "quito", "guayaquil", "noboa"), ()),
    "CO": (("colombia", "colombian", "bogota", "bogotá", "medellin", "medellín"), ()),
    "PO": (("portugal", "portuguese", "lisbon"), ("porto",)),
    "CA": (("canada", "canadian", "ottawa", "carney"), ("toronto", "montreal", "vancouver")),
    "MX": (("mexico", "mexican", "sheinbaum"), ()),
    "UK": (("britain", "british", "uk", "england", "scotland", "wales", "london",
            "starmer"), ()),
    "BR": (("brazil", "brazilian", "brasilia", "brasília", "lula", "são paulo",
            "sao paulo", "rio de janeiro"), ()),
    "IN": (("india", "indian", "new delhi", "delhi", "mumbai", "modi"), ()),
}


def _phrase_re(phrases: tuple[str, ...]) -> re.Pattern | None:
    if not phrases:
        return None
    alts = "|".join(re.escape(p) for p in sorted(phrases, key=len, reverse=True))
    return re.compile(rf"(?<![\w.])(?:{alts})(?![\w.])", re.I)


def _about(code: str, art: "gdelt_files.Article",
           strong: re.Pattern | None, weak: re.Pattern | None) -> bool:
    if strong is not None and strong.search(art.title):
        return True
    return weak is not None and code in art.subnational and bool(weak.search(art.title))


def _norm(name: str) -> str:
    return "".join(ch for ch in (name or "").lower() if ch.isalpha())


class GdeltRegionsSection(Section):
    id = "gdelt_regions"
    title = "World News (by country, top stories)"
    emoji = "🌍"

    PER_COUNTRY = 6
    COMBINED_RECORDS = 250   # DOC API maximum
    FILE_HOURS = 8           # bulk GKG window (~20k English articles)
    REQUEST_TIMEOUT_S = 15
    PULL_TIMEOUT_S = 150
    BUDGET_S = 125           # stop issuing queries; in-flight one may add REQUEST_TIMEOUT_S

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self._deadline: float = float("inf")
        self.last_error: str = ""

    def _fetch_one(self, code: str, params: dict) -> dict | None:
        """One country's artlist; None on any failure (never raises)."""
        try:
            return gdelt.doc_get(params, timeout=self.REQUEST_TIMEOUT_S,
                                 deadline=self._deadline)
        except gdelt.GdeltUnavailable as exc:
            self.last_error = str(exc)
            return None

    @staticmethod
    def _normalize(name: str, art: dict) -> dict:
        # GDELT returns seendate like "20260525T180000Z"
        seen = art.get("seendate", "")
        try:
            dt = datetime.strptime(seen, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            dt = None
        return {
            "id": art.get("url", "") + "|" + seen,
            "date": dt.date().isoformat() if dt else "",
            "title": f"[{name}] {art.get('title','(no title)')}",
            "url": art.get("url", ""),
            "summary": art.get("domain", "") + " · " + art.get("language", ""),
            "country": name,
            "domain": art.get("domain", ""),
            "tone": art.get("tone", ""),
            "language": art.get("language", ""),
        }

    def pull(self) -> list[dict]:
        try:
            batch = gdelt_files.recent_articles(self.FILE_HOURS)
        except gdelt_files.GdeltFilesUnavailable as exc:
            print(f"[gdelt_regions] bulk files unavailable ({exc}); using the DOC API",
                  flush=True)
            return self._pull_doc()
        return self._pull_files(batch)

    def _pull_files(self, batch: "gdelt_files.Batch") -> list[dict]:
        items: list[dict] = []
        covered = 0
        for code, name in WATCHLIST:
            strong, weak = ALIASES.get(code, ((name.lower(),), ()))
            s_re, w_re = _phrase_re(strong), _phrase_re(weak)
            cands = [a for a in batch.articles if _about(code, a, s_re, w_re)]
            ignore = frozenset(w for ph in strong + weak for w in ph.split())
            top = gdelt_files.top_stories(cands, self.PER_COUNTRY, ignore=ignore)
            covered += bool(top)
            for art, echo in top:
                items.append({
                    "id": f"{art.url}|{art.seendate}",
                    "date": art.date,
                    "title": f"[{name}] {art.title}",
                    "url": art.url,
                    "summary": (f"{art.domain} · English"
                                + (f" · {echo} other outlets ran this story" if echo else "")),
                    "country": name,
                    "domain": art.domain,
                    "tone": art.tone if art.tone is not None else "",
                    "language": "English",
                    "echo": echo,
                })
        self.coverage = {"source": "gkg-files", "countries": covered, "of": len(WATCHLIST),
                         "files": f"{batch.files_ok}/{batch.files_tried}",
                         "articles": len(batch.articles)}
        print(f"[gdelt_regions] coverage {covered}/{len(WATCHLIST)} countries from "
              f"{len(batch.articles)} GKG articles ({batch.files_ok}/{batch.files_tried} files)",
              flush=True)
        items.sort(key=lambda it: (it["country"], -it["echo"]))
        return items

    def _pull_doc(self) -> list[dict]:
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=36)
        base = {
            "mode": "artlist",
            "format": "json",
            "startdatetime": start.strftime("%Y%m%d%H%M%S"),
            "enddatetime": end.strftime("%Y%m%d%H%M%S"),
            "sort": "datedesc",
        }
        self._deadline = time.monotonic() + self.BUDGET_S
        items: list[dict] = []
        got_any = False
        covered: set[str] = set()
        by_norm = {_norm(name): name for _, name in WATCHLIST}

        # 1. One request for every country. From shared CI runners GDELT
        #    rate-limits far below its documented 1 req / 5 s (2026-11-13:
        #    126 s bought 4 of 19 per-country answers), so a request is the
        #    scarce resource. If GDELT rejects the OR'd query it answers in
        #    plain text, which doc_get reports as a failure, and step 2 covers
        #    every country instead.
        clause = " OR ".join(f"sourcecountry:{code}" for code, _ in WATCHLIST)
        combined = self._fetch_one("ALL", {"query": f"({clause}) sourcelang:english",
                                           "maxrecords": self.COMBINED_RECORDS, **base})
        if combined is not None:
            got_any = True
            per: dict[str, int] = {}
            for art in combined.get("articles") or []:
                name = by_norm.get(_norm(art.get("sourcecountry", "")))
                if name is None or per.get(name, 0) >= self.PER_COUNTRY:
                    continue
                per[name] = per.get(name, 0) + 1
                items.append(self._normalize(name, art))
            covered = set(per)

        # 2. Top up the countries the combined answer missed, one at a time,
        #    starting at a daily-rotating offset so a short budget does not
        #    always drop the same ones.
        shift = datetime.now(timezone.utc).toordinal() % len(WATCHLIST)
        topped = 0
        for code, name in WATCHLIST[shift:] + WATCHLIST[:shift]:
            if name in covered:
                continue
            if time.monotonic() >= self._deadline:
                break  # budget spent — return partial instead of overrunning
            params = {"query": f"sourcecountry:{code} sourcelang:english",
                      "maxrecords": self.PER_COUNTRY, **base}
            data = self._fetch_one(code, params)
            if data is None:
                continue
            got_any = True
            arts = (data.get("articles") or [])[: self.PER_COUNTRY]
            if arts:
                topped += 1
                covered.add(name)
            for art in arts:
                items.append(self._normalize(name, art))
        self.coverage = {"combined": combined is not None, "countries": len(covered),
                         "of": len(WATCHLIST), "topped_up": topped}
        print(f"[gdelt_regions] coverage {len(covered)}/{len(WATCHLIST)} countries "
              f"(combined query {'ok' if combined is not None else 'failed'}, "
              f"{topped} topped up){'; last error: ' + self.last_error if self.last_error else ''}",
              flush=True)
        # Sort newest first for the briefing
        items.sort(key=lambda it: it.get("date", ""), reverse=True)
        if not items and not got_any:
            raise UpstreamHTTPError(
                f"GDELT returned nothing for any country ({self.last_error or 'rate-limited / blocked'})")
        return items

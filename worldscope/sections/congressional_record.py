"""congressional_record — GovInfo Congressional Record (CREC) floor activity.

Why this section exists
=======================
`political_figures` scores each figure on six weighted components:

    stock_activity 0.25 · speech_volume 0.15 · speech_topic_drift 0.15
    gdelt_tone 0.15 · new_filings 0.10 · enforcement_hits 0.20

It shipped with a hardcoded ``"speeches": []`` and the comment "GovInfo
speeches require key; left empty". So speech_volume and speech_topic_drift
returned 0.0 for every figure on every day since the section was written:
**30% of the composite was structurally dead**, and because the score is a
weighted sum, the shortfall was invisible — it just compressed every figure's
score toward zero and distorted the top-10 ranking.

Those two components are also the ones with the most forward-looking content.
A member who suddenly starts talking about a subject they have never raised is
a leading indicator; a member who filed a trade is a lagging one.

Design
======
Acquisition lives here rather than in `political_figures`, matching how that
section already reuses `congressional_trades`, `gdelt_gkg` and `form4` lake
artifacts instead of refetching. Its wall-clock budget stays low; this
section pays the network cost once.

The join key is `bioGuideId`, which CREC MODS carries natively and
`figures_registry.yaml` already stores as `bioguide_id`. No name matching.

Cost: one MODS document per published day (~375 KB, ~78 granules), not one
call per granule. A 7-day incremental window is enough because the scorer's
90-day baseline accumulates in the lake across runs.

Two deliberate approximations, both documented in the output:

  * **word_count is estimated from the page extent**, not counted from the
    text. Counting words would require fetching each granule's HTML (78
    requests/day). `speech_volume_score` compares a member against *their own*
    90-day baseline, so a consistent proxy preserves the z-score; only the
    absolute magnitude is nominal.
  * **topic vectors are lexical, not semantic** — a hashed TF-IDF over granule
    titles using numpy only. This avoids taking a sentence-transformers
    dependency (≈2 GB of torch) for one component, and avoids the md5
    bag-of-words fallback in worldscope.embeddings, which silently pretends to
    be an embedding. Lexical drift over speech subjects is a weaker signal
    than semantic drift, and it is labelled as such rather than overclaimed.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from typing import Any, Iterable, Optional

import numpy as np
import requests

from . import Section, UpstreamHTTPError, UpstreamParseError

__version__ = "0.1.0"

UA = "Ian Helfrich worldscope/0.1 ianthelfrich@gmail.com"
API = "https://api.govinfo.gov"
DETAILS = "https://www.govinfo.gov/app/details"

MODS_NS = {"m": "http://www.loc.gov/mods/v3"}

# CREC sets three columns per page; 900 is a conservative words-per-page figure
# for the running text. Only the ratio matters to speech_volume_score.
WORDS_PER_PAGE = 900

# Hashed TF-IDF width. Small enough to stay cheap in the lake, wide enough that
# collisions between distinct legislative topics stay rare.
TOPIC_DIM = 256

# MODS role attribute values that constitute floor speech. SUBMITTING and
# friends attach a member to a document they did not speak.
SPEAKING_ROLES = ("SPEAKING",)

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]+")

# Procedural boilerplate carries no topic signal and would dominate a lexical
# vector built from titles.
_STOPWORDS = frozenset("""
a an and are as at be by for from has have in into is it its of on or that the
to was were will with mr mrs ms of_the united states congress congressional
record vol no house senate representatives
""".split())


def _api_key() -> str:
    """GovInfo accepts DEMO_KEY at roughly 30 requests/hour, which is enough
    for a 7-day incremental window but not for a backfill."""
    return os.environ.get("GOVINFO_API_KEY") or "DEMO_KEY"


def _window(today: date, lookback: int) -> list[date]:
    """Inclusive [today-lookback+1, today], chronological."""
    return [today - timedelta(days=i) for i in range(lookback - 1, -1, -1)]


# --------------------------------------------------------------------------- #
# Page arithmetic
# --------------------------------------------------------------------------- #

def _page_number(label: str) -> Optional[int]:
    """CREC page labels are chamber-prefixed: E793, H5247, S1200."""
    m = re.search(r"(\d+)", label or "")
    return int(m.group(1)) if m else None


def page_count(start: str, end: str) -> int:
    """Inclusive page span, clamped to at least 1.

    A granule always occupies at least one page; a missing or inverted extent
    is a metadata quirk, not evidence of zero speech.
    """
    s, e = _page_number(start), _page_number(end)
    if s is None or e is None:
        return 1
    return max(1, e - s + 1)


# --------------------------------------------------------------------------- #
# MODS parsing
# --------------------------------------------------------------------------- #

def _clean_title(raw: str) -> str:
    """Drop the '; Congressional Record Vol. 172, No. 134' citation tail."""
    title = (raw or "").strip()
    return re.split(r";\s*Congressional Record\b", title)[0].strip()


def _text(node: Optional[ET.Element]) -> str:
    return (node.text or "").strip() if node is not None else ""


def parse_mods(xml_text: str, roles: Optional[Iterable[str]] = SPEAKING_ROLES) -> list[dict]:
    """Extract one row per (granule, attributed member) from a CREC MODS doc.

    `roles=None` keeps every attribution regardless of role.

    Raises UpstreamParseError on an unparseable body: per the section trust
    rule, a malformed document is a broken source, not a quiet day in Congress.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise UpstreamParseError(
            f"congressional_record: MODS body did not parse: {exc}"
        ) from exc

    wanted = {r.upper() for r in roles} if roles is not None else None
    out: list[dict] = []

    for ext in root.iter():
        if not ext.tag.endswith("}extension") and ext.tag != "extension":
            continue
        members = [c for c in ext if c.tag.endswith("congMember") or c.tag == "congMember"]
        if not members:
            continue

        def child(tag: str) -> str:
            for c in ext:
                if c.tag.endswith("}" + tag) or c.tag == tag:
                    return (c.text or "").strip()
            return ""

        access_id = child("accessId")
        granule_date = child("granuleDate")
        if not access_id or not granule_date:
            continue

        # The page extent lives on the enclosing relatedItem, not the extension.
        start = end = ""
        parent = _find_parent(root, ext)
        if parent is not None:
            for extent in parent.iter():
                if extent.tag.endswith("}extent") or extent.tag == "extent":
                    if extent.get("unit") != "pages":
                        continue
                    for c in extent:
                        if c.tag.endswith("}start") or c.tag == "start":
                            start = (c.text or "").strip()
                        elif c.tag.endswith("}end") or c.tag == "end":
                            end = (c.text or "").strip()
                    break

        pages = page_count(start, end)
        title = _clean_title(child("searchTitle"))
        package_id = access_id.split("-pt")[0] if "-pt" in access_id else access_id

        for m in members:
            role = (m.get("role") or "").upper()
            if wanted is not None and role not in wanted:
                continue
            bioguide = m.get("bioGuideId") or ""
            if not bioguide:
                continue
            name = ""
            for n in m:
                if (n.tag.endswith("}name") or n.tag == "name") and \
                        n.get("type") == "authority-fnf":
                    name = (n.text or "").strip()
                    break
            out.append({
                "id": f"crec:{access_id}:{bioguide}",
                "bioguide_id": bioguide,
                "name": name,
                "date": granule_date,
                "chamber": child("chamber"),
                "granule_class": child("granuleClass"),
                "access_id": access_id,
                "title": title,
                "pages": pages,
                # Estimated, not counted — see the module docstring.
                "word_count": pages * WORDS_PER_PAGE,
                "word_count_is_estimated": True,
                "role": role,
                "party": m.get("party") or "",
                "state": m.get("state") or "",
                "url": f"{DETAILS}/{package_id}/{access_id}",
                "summary": title,
            })

    return out


def _find_parent(root: ET.Element, target: ET.Element) -> Optional[ET.Element]:
    for parent in root.iter():
        for child in parent:
            if child is target:
                return parent
    return None


# --------------------------------------------------------------------------- #
# Lexical topic vectors
# --------------------------------------------------------------------------- #

def _tokens(title: str) -> list[str]:
    return [
        w.lower() for w in _WORD_RE.findall(title or "")
        if w.lower() not in _STOPWORDS and len(w) > 2
    ]


def _bucket(token: str) -> int:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % TOPIC_DIM


def topic_vectors(titles: list[str]) -> np.ndarray:
    """Deterministic hashed TF-IDF over granule titles, L2-normalised.

    Lexical, not semantic: two titles about the same subject in different
    words will not be close. Documented rather than dressed up as an
    embedding — `speech_topic_drift` reads it as a topic proxy, and the
    section output flags it.
    """
    titles = list(titles or [])
    if not titles:
        return np.zeros((0, TOPIC_DIM), dtype=np.float64)

    docs = [_tokens(t) for t in titles]
    n = len(docs)

    df: dict[int, int] = {}
    for doc in docs:
        for b in {_bucket(t) for t in doc}:
            df[b] = df.get(b, 0) + 1

    mat = np.zeros((n, TOPIC_DIM), dtype=np.float64)
    for i, doc in enumerate(docs):
        if not doc:
            continue
        tf: dict[int, int] = {}
        for t in doc:
            b = _bucket(t)
            tf[b] = tf.get(b, 0) + 1
        for b, count in tf.items():
            idf = math.log((1.0 + n) / (1.0 + df.get(b, 0))) + 1.0
            mat[i, b] = (count / len(doc)) * idf

    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


# --------------------------------------------------------------------------- #
# Section
# --------------------------------------------------------------------------- #

class CongressionalRecordSection(Section):
    id = "congressional_record"
    title = "Congressional Record (floor speech by member)"
    emoji = "🏛️"

    source_id = "govinfo-crec"
    source_name = "GovInfo Congressional Record"
    source_url = "https://api.govinfo.gov/collections/CREC"
    source_tier = "primary_document"
    source_license = "public-domain"

    PULL_TIMEOUT_S = 120
    LOOKBACK_DAYS = 7

    # Capability contract: GovInfo accepts DEMO_KEY, but api.data.gov caps it
    # near 30 req/hr, which is not enough for a backfill.
    optional_env = ('GOVINFO_API_KEY',)

    def _get(self, url: str, **params) -> requests.Response:
        params["api_key"] = _api_key()
        try:
            resp = requests.get(
                url, params=params, headers={"User-Agent": UA}, timeout=30
            )
        except requests.exceptions.RequestException as exc:
            raise UpstreamHTTPError(
                f"congressional_record: {type(exc).__name__}: {exc}"
            ) from exc
        if resp.status_code != 200:
            raise UpstreamHTTPError(
                f"congressional_record: {url} returned HTTP {resp.status_code}"
            )
        return resp

    def _published_packages(self, days: list[date]) -> list[str]:
        """CREC package ids actually published in the window.

        Congress does not sit every day; asking for a package that does not
        exist is a 404, so the collection endpoint is the authority on which
        days exist.
        """
        start = f"{days[0].isoformat()}T00:00:00Z"
        resp = self._get(f"{API}/collections/CREC/{start}",
                         offset=0, pageSize=100)
        try:
            payload = resp.json()
        except ValueError as exc:
            raise UpstreamParseError(
                f"congressional_record: collection listing was not JSON: {exc}"
            ) from exc
        wanted = {d.isoformat() for d in days}
        return [
            p["packageId"] for p in payload.get("packages", [])
            if p.get("dateIssued") in wanted and p.get("packageId")
        ]

    def pull(self) -> list[dict]:
        days = _window(date.today(), self.LOOKBACK_DAYS)
        packages = self._published_packages(days)

        rows: list[dict] = []
        for package_id in packages:
            resp = self._get(f"{API}/packages/{package_id}/mods")
            rows.extend(parse_mods(resp.text))

        # Attach the lexical topic vector so downstream consumers do not have
        # to re-derive it. Stored as a list for JSON round-tripping.
        if rows:
            vecs = topic_vectors([r["title"] for r in rows])
            for row, vec in zip(rows, vecs):
                row["topic_vector"] = [round(float(x), 6) for x in vec]
                row["topic_vector_kind"] = "lexical-hashed-tfidf"

        return rows

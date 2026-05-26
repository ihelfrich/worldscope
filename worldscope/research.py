"""
research.py — compose a one-shot research brief on any topic / entity / country.

Usage:
    python -m worldscope.research "Sberbank Europe sanctions"
    python -m worldscope.research --type entity "Soho House"
    python -m worldscope.research --type topic "AI safety regulation"
    python -m worldscope.research --type country "Nigeria"

What it does: takes the query, decides which subset of the LEXSCOPE +
ECONSCOPE + WORLDSCOPE tools to fire, runs them in parallel where possible,
and stitches the output into a single Markdown brief + supporting data
bundle in research_reports/<slug>/.

This is the layer that converts "31 adapters and 4 engines" into
"one command produces a brief."
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# Allow running from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Inputs available without external deps
import requests

UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"


# --- query type detection -------------------------------------------------

# Crude heuristic; can swap to an LLM classifier later.
ISO_COUNTRIES = {
    "afghanistan", "argentina", "australia", "austria", "belgium", "brazil",
    "canada", "chile", "china", "colombia", "denmark", "ecuador", "egypt",
    "finland", "france", "germany", "greece", "hungary", "iceland", "india",
    "indonesia", "iran", "iraq", "ireland", "israel", "italy", "japan",
    "korea", "lebanon", "malaysia", "mexico", "morocco", "netherlands",
    "new zealand", "nigeria", "norway", "pakistan", "philippines", "poland",
    "portugal", "qatar", "romania", "russia", "saudi arabia", "singapore",
    "south africa", "spain", "sweden", "switzerland", "syria", "taiwan",
    "thailand", "tunisia", "turkey", "ukraine", "united kingdom", "uk",
    "united states", "us", "usa", "venezuela", "vietnam",
}


def detect_type(query: str) -> str:
    q = query.lower().strip()
    if q in ISO_COUNTRIES:
        return "country"
    # Entity heuristic: title-case multi-word OR has Inc/Corp/Bank/Ltd
    if re.search(r"\b(inc|corp|llc|ltd|bank|s\.a\.|gmbh|holding|group|partners?)\b", q):
        return "entity"
    # Otherwise treat as topic
    return "topic"


# --- data pulls ---------------------------------------------------------

@dataclass
class ResearchResult:
    query: str
    query_type: str
    pulled_at: str
    sections: dict = field(default_factory=dict)


def _safe(label: str, fn, *args, **kwargs):
    """Run a pull function; on any exception return None and log."""
    try:
        out = fn(*args, **kwargs)
        print(f"  [{label}] OK ({len(out) if hasattr(out, '__len__') else 'ok'})")
        return out
    except Exception as exc:
        print(f"  [{label}] skipped: {type(exc).__name__}: {str(exc)[:80]}")
        return None


def pull_federal_register(query: str, days: int = 30) -> list[dict]:
    start = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    r = requests.get(
        "https://www.federalregister.gov/api/v1/documents.json",
        params={
            "conditions[term]": query,
            "conditions[publication_date][gte]": start,
            "per_page": 25, "order": "newest",
        },
        headers={"User-Agent": UA}, timeout=25,
    )
    r.raise_for_status()
    out = []
    for d in (r.json().get("results") or []):
        out.append({
            "date": d.get("publication_date", ""),
            "title": d.get("title", ""),
            "url": d.get("html_url", ""),
            "snippet": (d.get("abstract") or "")[:400],
            "type": d.get("type", ""),
            "agencies": ", ".join(a.get("name", "") for a in d.get("agencies") or []),
        })
    return out


def pull_gdelt(query: str, days: int = 7) -> list[dict]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    q = f'"{query}"' if " " in query else query
    r = requests.get(
        "https://api.gdeltproject.org/api/v2/doc/doc",
        params={
            "query": f"{q} sourcelang:english",
            "mode": "artlist", "format": "json", "maxrecords": 30,
            "startdatetime": start.strftime("%Y%m%d%H%M%S"),
            "enddatetime": end.strftime("%Y%m%d%H%M%S"),
            "sort": "datedesc",
        },
        headers={"User-Agent": UA}, timeout=25,
    )
    r.raise_for_status()
    return [{
        "date": (a.get("seendate") or "")[:8],
        "title": a.get("title", ""),
        "url": a.get("url", ""),
        "snippet": a.get("domain", "") + " · " + (a.get("language") or ""),
        "tone": a.get("tone", ""),
        "country": a.get("sourcecountry", ""),
    } for a in (r.json().get("articles") or [])]


def pull_edgar(query: str, days: int = 365) -> list[dict]:
    """EDGAR full-text search across all SEC filings."""
    start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    q = f'"{query}"' if " " in query else query
    r = requests.get(
        "https://efts.sec.gov/LATEST/search-index",
        params={"q": q, "dateRange": "custom", "startdt": start,
                "enddt": datetime.now(timezone.utc).strftime("%Y-%m-%d")},
        headers={"User-Agent": UA}, timeout=25,
    )
    r.raise_for_status()
    hits = (r.json().get("hits") or {}).get("hits") or []
    out = []
    for h in hits[:25]:
        s = h.get("_source", {})
        names = s.get("display_names") or []
        ciks = s.get("ciks") or []
        out.append({
            "date": s.get("file_date", ""),
            "title": f"{names[0] if names else ''} — {s.get('root_form') or s.get('file_type') or ''}",
            "url": f"https://www.sec.gov/Archives/edgar/data/{int(ciks[0])}/{(h.get('_id') or '').split(':')[0].replace('-','')}/{(h.get('_id') or '').split(':')[0]}-index.htm" if ciks else "",
            "snippet": (s.get("text") or "")[:300],
            "form": s.get("root_form", ""),
            "cik": ciks[0] if ciks else "",
        })
    return out


def pull_courtlistener(query: str, days: int = 365) -> list[dict]:
    import os
    start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    headers = {"User-Agent": UA, "Accept": "application/json"}
    key = os.environ.get("COURTLISTENER_API_TOKEN")
    if key:
        headers["Authorization"] = f"Token {key}"
    q = f'"{query}"' if " " in query else query
    r = requests.get(
        "https://www.courtlistener.com/api/rest/v4/search/",
        params={"q": q, "type": "o", "order_by": "dateFiled desc",
                "filed_after": start},
        headers=headers, timeout=25,
    )
    r.raise_for_status()
    out = []
    for res in (r.json().get("results") or [])[:25]:
        out.append({
            "date": res.get("dateFiled") or res.get("date_filed") or "",
            "title": f"{res.get('caseName','')} ({res.get('court','')})",
            "url": f"https://www.courtlistener.com{res.get('absolute_url','')}",
            "snippet": (res.get("snippet") or "")[:400],
            "court": res.get("court", ""),
        })
    return out


def pull_opensanctions(query: str) -> list[dict]:
    """Local FtM corpus search by name. Cheap streaming match — case-insensitive
    substring against caption/name. Falls back to empty if corpus missing."""
    path = Path.home() / "Projects" / "econscope" / "data" / "opensanctions" / "entities.ftm.json"
    if not path.exists():
        return []
    qlower = query.lower()
    out: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if qlower not in line.lower():
                continue
            try:
                ent = json.loads(line)
            except json.JSONDecodeError:
                continue
            schema = ent.get("schema")
            if schema not in ("Person", "Company", "Organization", "LegalEntity",
                              "Vessel", "Airplane", "PublicBody"):
                continue
            caption = (ent.get("caption") or "")
            if qlower not in caption.lower():
                continue
            props = ent.get("properties") or {}
            out.append({
                "date": (props.get("modifiedAt") or [""])[0],
                "title": f"{caption} ({schema})",
                "url": f"https://www.opensanctions.org/entities/{ent.get('id','')}/",
                "snippet": "datasets: " + ", ".join(ent.get("datasets") or []),
                "datasets": ent.get("datasets") or [],
                "countries": props.get("country") or [],
            })
            if len(out) >= 25:
                break
    return out


def pull_metaculus(query: str) -> list[dict]:
    r = requests.get(
        "https://www.metaculus.com/api2/questions/",
        params={"search": query, "status": "open", "limit": 10},
        headers={"User-Agent": UA}, timeout=25,
    )
    r.raise_for_status()
    out = []
    for q in (r.json().get("results") or [])[:10]:
        qid = q.get("id")
        cp = (q.get("community_prediction") or {}).get("full", {}).get("q2")
        out.append({
            "date": (q.get("publish_time") or "")[:10],
            "title": q.get("title", ""),
            "url": f"https://www.metaculus.com/questions/{qid}/" if qid else "",
            "snippet": f"community: {cp*100:.0f}% · {q.get('number_of_forecasters', 0)} forecasters" if cp else "",
        })
    return out


# --- composition --------------------------------------------------------

def research(query: str, query_type: Optional[str] = None) -> ResearchResult:
    query_type = query_type or detect_type(query)
    print(f"[research] query={query!r}  type={query_type}")
    res = ResearchResult(
        query=query, query_type=query_type,
        pulled_at=datetime.now(timezone.utc).isoformat(),
    )
    # All types get GDELT + Federal Register + EDGAR + CourtListener.
    # Entity types additionally get OpenSanctions (local corpus search).
    # Topic types additionally get Metaculus.
    res.sections["federal_register"] = _safe("federal_register", pull_federal_register, query) or []
    res.sections["gdelt"]             = _safe("gdelt",             pull_gdelt,             query) or []
    res.sections["edgar"]             = _safe("edgar",             pull_edgar,             query) or []
    res.sections["courtlistener"]     = _safe("courtlistener",     pull_courtlistener,     query) or []
    if query_type == "entity":
        res.sections["sanctions"]     = _safe("sanctions",         pull_opensanctions,     query) or []
    if query_type == "topic":
        res.sections["metaculus"]     = _safe("metaculus",         pull_metaculus,         query) or []
    return res


# --- render -------------------------------------------------------------

def render_markdown(r: ResearchResult) -> str:
    lines = [
        f"# Research brief: {r.query}",
        "",
        f"*type: {r.query_type} · pulled: {r.pulled_at[:19]}Z*",
        "",
    ]
    section_titles = {
        "federal_register": "🏛️  U.S. Federal Register (last 30 days)",
        "gdelt": "🌍 News (GDELT, last 7 days)",
        "edgar": "📄 SEC EDGAR filings (last 365 days)",
        "courtlistener": "⚖️  Court opinions (last 365 days)",
        "sanctions": "🚫 OpenSanctions matches",
        "metaculus": "🔮 Metaculus open questions",
    }
    for sid, items in r.sections.items():
        title = section_titles.get(sid, sid)
        lines.append(f"## {title}")
        lines.append(f"_{len(items)} item(s)_")
        lines.append("")
        if not items:
            lines.append("(no hits)")
            lines.append("")
            continue
        for it in items[:12]:
            lines.append(f"- **[{it.get('date','?')}]** [{it.get('title','(untitled)')}]({it.get('url','#')})")
            if it.get("snippet"):
                lines.append(f"  - {it['snippet']}")
        if len(items) > 12:
            lines.append(f"- _… and {len(items)-12} more in the data bundle_")
        lines.append("")
    return "\n".join(lines)


def write_bundle(r: ResearchResult, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", r.query.lower()).strip("-")[:50]
    bdir = out_dir / slug
    bdir.mkdir(exist_ok=True)
    # Brief markdown
    (bdir / "brief.md").write_text(render_markdown(r), encoding="utf-8")
    # Raw data per section
    raw = bdir / "raw"
    raw.mkdir(exist_ok=True)
    for sid, items in r.sections.items():
        (raw / f"{sid}.json").write_text(json.dumps(items, indent=2, default=str), encoding="utf-8")
    # Manifest
    (bdir / "manifest.json").write_text(json.dumps({
        "query": r.query,
        "query_type": r.query_type,
        "pulled_at": r.pulled_at,
        "section_counts": {sid: len(items) for sid, items in r.sections.items()},
    }, indent=2), encoding="utf-8")
    return bdir


def main() -> None:
    p = argparse.ArgumentParser(
        description="Generate a one-shot research brief on any topic, entity, or country.",
    )
    p.add_argument("query", help="topic / entity name / country to research")
    p.add_argument("--type", dest="qtype", choices=("topic", "entity", "country"),
                   default=None, help="force query type (auto-detected if omitted)")
    p.add_argument("--out", default="research_reports", help="output dir")
    args = p.parse_args()
    r = research(args.query, args.qtype)
    bdir = write_bundle(r, Path(args.out))
    print(f"\n→ brief : {bdir/'brief.md'}")
    print(f"→ bundle: {bdir}")


if __name__ == "__main__":
    main()

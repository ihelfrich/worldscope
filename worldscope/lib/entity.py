"""
entity.py — entity resolution across CIK / LEI / OpenCorporates ID /
Wikidata QID / OpenSanctions ID.

The goal: given a name like "Sberbank Europe AG", return a single Entity
struct with every identifier the system can find. Once two adapters both
agree on an entity, their data becomes joinable.

Resolution sources, in order of preference (fastest first):
  1. OpenSanctions FtM corpus — already has cross-walks for many entities
     via the `iso9362_bic`, `ext_gleif`, `permid`, `ru_cbr_banks`, `ru_egrul`
     overlays. Searching by name in the corpus gives us the cluster.
  2. GLEIF — global LEI registry (api.gleif.org). Free, unauth.
  3. Wikidata — for the canonical "Q" identifier + cross-links.
  4. SEC EDGAR company search — for CIK.
  5. OpenCorporates — paid quota, but covers 130M+ entities globally.

This module ships with sources 1, 2, 3, and 4 wired. OpenCorporates can
plug in via OPENCORPORATES_API_TOKEN when needed.
"""
from __future__ import annotations

import json
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

from .wikidata import sparql

UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"
SANCTIONS_PATH = Path.home() / "Projects" / "econscope" / "data" / "opensanctions" / "entities.ftm.json"


@dataclass
class Entity:
    name: str                              # query name as resolved
    aliases: list[str] = field(default_factory=list)
    schema: Optional[str] = None           # Person / Company / Organization / Vessel etc.
    countries: list[str] = field(default_factory=list)

    # Identifiers (any of these can be empty)
    cik: Optional[str] = None              # SEC central index key
    lei: Optional[str] = None              # GLEIF legal entity identifier (20-char)
    wikidata_qid: Optional[str] = None     # Q12345
    opensanctions_id: Optional[str] = None # NK-xxxxx
    opencorporates_id: Optional[str] = None # jurisdiction/company_number
    permid: Optional[str] = None           # Refinitiv PermID
    isin: list[str] = field(default_factory=list)  # ISIN bond/equity identifiers

    # Provenance
    sources: list[str] = field(default_factory=list)  # which resolvers fired

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["aliases"] = self.aliases
        d["countries"] = self.countries
        d["isin"] = self.isin
        d["sources"] = self.sources
        return d


# --- resolver: local OpenSanctions FtM corpus (richest cross-walk) -------

def _from_opensanctions(name: str) -> Optional[Entity]:
    if not SANCTIONS_PATH.exists():
        return None
    qlower = name.lower()
    best: Optional[Entity] = None
    with SANCTIONS_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            if qlower not in line.lower():
                continue
            try:
                ent = json.loads(line)
            except json.JSONDecodeError:
                continue
            caption = (ent.get("caption") or "").lower()
            # Prefer exact-name matches; fall back to substring containment
            if qlower not in caption:
                # Check aliases
                aliases = (ent.get("properties") or {}).get("name", []) + (ent.get("properties") or {}).get("alias", [])
                if not any(qlower in (a or "").lower() for a in aliases):
                    continue
            props = ent.get("properties") or {}
            datasets = ent.get("datasets") or []
            entity = Entity(
                name=ent.get("caption") or name,
                aliases=props.get("name", [])[:5] + props.get("alias", [])[:5],
                schema=ent.get("schema"),
                countries=props.get("country") or props.get("nationality") or [],
                opensanctions_id=ent.get("id"),
                lei=(props.get("leiCode") or [None])[0],
                permid=(props.get("permId") or [None])[0],
                isin=(props.get("isinCode") or [])[:5],
                wikidata_qid=(props.get("wikidataId") or [None])[0],
                sources=["opensanctions"],
            )
            # SEC CIK is sometimes tucked in properties.registrationNumber for US entities
            for rn in (props.get("registrationNumber") or []):
                if isinstance(rn, str) and rn.isdigit() and 4 <= len(rn) <= 10:
                    entity.cik = rn
                    break
            # Prefer the most-overlap match (more datasets ≈ more canonical)
            if best is None or len(ent.get("datasets") or []) > len(best.sources):
                best = entity
            if "wd_companies" in datasets or "us_ofac_sdn" in datasets:
                # Highly-curated overlays — trust them and stop early
                break
    return best


# --- resolver: GLEIF (LEI registry) --------------------------------------

def _from_gleif(name: str) -> Optional[Entity]:
    try:
        r = requests.get(
            "https://api.gleif.org/api/v1/lei-records",
            params={"filter[entity.legalName]": name, "page[size]": 5},
            headers={"User-Agent": UA, "Accept": "application/vnd.api+json"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
    except Exception:
        return None
    records = data.get("data") or []
    if not records:
        return None
    # First record is usually the highest-scored match
    rec = records[0]
    attrs = rec.get("attributes") or {}
    legal = (attrs.get("entity") or {}).get("legalName") or {}
    addr = (attrs.get("entity") or {}).get("legalAddress") or {}
    return Entity(
        name=legal.get("name") or name,
        schema="Company",
        countries=[addr.get("country")] if addr.get("country") else [],
        lei=rec.get("id"),
        sources=["gleif"],
    )


# --- resolver: Wikidata --------------------------------------------------

def _from_wikidata(name: str) -> Optional[Entity]:
    """Wikidata wbsearchentities + lookup."""
    try:
        r = requests.get(
            "https://www.wikidata.org/w/api.php",
            params={
                "action": "wbsearchentities", "search": name, "language": "en",
                "limit": 3, "format": "json", "type": "item",
            },
            headers={"User-Agent": UA}, timeout=15,
        )
        r.raise_for_status()
        results = r.json().get("search") or []
    except Exception:
        return None
    if not results:
        return None
    top = results[0]
    return Entity(
        name=top.get("label") or name,
        aliases=[r.get("label") for r in results[1:] if r.get("label")],
        wikidata_qid=top.get("id"),
        sources=["wikidata"],
    )


# --- resolver: SEC EDGAR company search ---------------------------------

def _from_edgar(name: str) -> Optional[Entity]:
    try:
        r = requests.get(
            "https://efts.sec.gov/LATEST/search-index",
            params={"q": f'"{name}"', "forms": "10-K"},
            headers={"User-Agent": UA}, timeout=15,
        )
        r.raise_for_status()
        hits = (r.json().get("hits") or {}).get("hits") or []
    except Exception:
        return None
    if not hits:
        return None
    s = (hits[0].get("_source") or {})
    names = s.get("display_names") or []
    ciks = s.get("ciks") or []
    if not (names and ciks):
        return None
    return Entity(
        name=names[0],
        aliases=names[1:],
        schema="Company",
        cik=ciks[0],
        sources=["edgar"],
    )


# --- merger --------------------------------------------------------------

def _merge(a: Entity, b: Entity) -> Entity:
    """Merge two Entity records — take non-empty fields from b that a lacks."""
    out = Entity(**a.__dict__)
    for fld in ("schema", "cik", "lei", "wikidata_qid", "opensanctions_id",
                "opencorporates_id", "permid"):
        if not getattr(out, fld) and getattr(b, fld):
            setattr(out, fld, getattr(b, fld))
    seen_aliases = set(out.aliases)
    for al in b.aliases or []:
        if al and al not in seen_aliases:
            out.aliases.append(al); seen_aliases.add(al)
    for c in b.countries or []:
        if c and c not in out.countries:
            out.countries.append(c)
    for i in b.isin or []:
        if i and i not in out.isin:
            out.isin.append(i)
    for s in b.sources or []:
        if s not in out.sources:
            out.sources.append(s)
    return out


def resolve(name: str, *, fast: bool = False) -> Entity:
    """Resolve a name to a unified Entity. Tries OpenSanctions corpus first
    (richest cross-walks), then EDGAR for US entities, then GLEIF, then
    Wikidata. Each resolver result is merged into the running Entity.

    If `fast=True`, stops after the first successful resolver."""
    entity = Entity(name=name)
    for resolver in (_from_opensanctions, _from_edgar, _from_gleif, _from_wikidata):
        try:
            other = resolver(name)
        except Exception:
            other = None
        if not other:
            continue
        entity = _merge(entity, other)
        if fast and (entity.cik or entity.lei or entity.wikidata_qid or entity.opensanctions_id):
            break
    return entity


if __name__ == "__main__":
    import argparse, sys
    p = argparse.ArgumentParser()
    p.add_argument("name", nargs="+")
    p.add_argument("--fast", action="store_true")
    args = p.parse_args()
    e = resolve(" ".join(args.name), fast=args.fast)
    print(json.dumps(e.to_dict(), indent=2, default=str))

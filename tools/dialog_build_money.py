#!/usr/bin/env python3
"""
dialog_build_money.py — curated money-flow dataset for the Dialog explorer.

Encodes documented, sourced dollar flows among Dialog-network actors as a
directed graph for a Sankey view:
  political   donor -> PAC -> candidate            (FEC / reported)
  investment  LP -> fund -> portfolio company       (13F / funding rounds)
  philanthropy donor -> trust/foundation -> grantee (990 / reported)
  enforcement payer -> regulator/victims (settlements); insider -> market (Form 4)

Every flow carries amount, basis (the filing/report it comes from), confidence,
and a source link. Flows with an undisclosed amount are tagged amount_known=false
and rendered at a uniform minimal width (clearly labelled), never invented.

Emits: explorer/money-data.js  (window.MONEY = {...}, inlined for file://)
       explorer/data/money.json

Public-record figures only. A money tie is a transaction record, not an
allegation of wrongdoing.
"""
from __future__ import annotations
import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "research_reports" / "dialog-retreat-members" / "explorer"

M = 1_000_000
B = 1_000_000_000
UNDISCLOSED = 3 * M   # nominal render width for amount_known=false

# node id -> (label, kind)
NODES = {
    # political
    "a16z": ("Andreessen Horowitz", "donor"),
    "brockman": ("Greg Brockman", "donor"),
    "lonsdale": ("Joe Lonsdale", "donor"),
    "ind_donors": ("Other AI-industry donors (agg.)", "donor"),
    "ltf": ("Leading the Future (super PAC)", "pac"),
    "think_big": ("Think Big (LTF affiliate)", "pac"),
    "bores": ("Alex Bores (NY-12) — OPPOSED, lost", "candidate"),
    "torres": ("Ritchie Torres (NY-15)", "candidate"),
    "menendez": ("Rob Menendez (NJ-8)", "candidate"),
    "gottheimer": ("Josh Gottheimer (NJ-5)", "candidate"),
    "gomez": ("Jimmy Gomez (CA-34)", "candidate"),
    "liccardo": ("Sam Liccardo (CA-16)", "candidate"),
    # investment — funds / LPs / portfolio
    "soros": ("Soros Fund Mgmt", "lp"),
    "pif": ("Saudi PIF", "lp"),
    "hydrazine": ("Hydrazine Capital (Thiel sole LP)", "fund"),
    "thiel": ("Peter Thiel", "person"),
    "key_square": ("Key Square (Bessent)", "fund"),
    "affinity": ("Affinity Partners (Kushner)", "fund"),
    "kkr": ("KKR", "fund"),
    "ribbit": ("Ribbit Capital (Malka)", "fund"),
    "eightvc": ("8VC (Lonsdale)", "fund"),
    "rentech": ("Renaissance Technologies", "fund"),
    "founders_fund": ("Founders Fund (Thiel)", "fund"),
    "thrive": ("Thrive Capital (J. Kushner)", "fund"),
    "openai": ("OpenAI", "company"),
    "oscar": ("Oscar Health", "company"),
    "oklo": ("Oklo", "company"),
    "brightspring": ("BrightSpring Health", "company"),
    "henryschein": ("Henry Schein", "company"),
    "bridgebio": ("BridgeBio Pharma", "company"),
    "robinhood": ("Robinhood", "company"),
    "coinbase": ("Coinbase", "company"),
    "nu": ("Nu Holdings", "company"),
    "figure": ("Figure Technologies", "company"),
    "block": ("Block", "company"),
    "joby": ("Joby Aviation", "company"),
    "palantir": ("Palantir", "company"),
    # philanthropy
    "seid": ("Barre Seid", "donor"),
    "marble": ("Marble Freedom Trust (Leo)", "trust"),
    "thiel_fdn": ("Thiel Foundation", "trust"),
    "emergent": ("Emergent Ventures / Mercatus (Cowen)", "grantee"),
    # enforcement / exits
    "galaxy": ("Galaxy Digital (Novogratz)", "person"),
    "genesis": ("Genesis (DCG)", "company"),
    "nyag": ("NY Attorney General", "regulator"),
    "victims": ("Genesis creditors / victims fund", "regulator"),
    "market_pltr": ("Public market (PLTR sales)", "market"),
    "market_brsp": ("Public market (BrightSpring sale)", "market"),
    "kravis": ("Henry Kravis", "person"),
}

# (src, dst, amount, amount_known, category, polarity, basis, conf, url)
FLOWS = [
    # ---- political (FEC / reported) ----
    ("a16z", "ltf", 50*M, True, "political", "neutral", "reported (Fortune/Axios)", "high",
     "https://www.axios.com/2026/01/30/openai-a16z-cash-ai-super-pac"),
    ("ind_donors", "ltf", 75*M, True, "political", "neutral", "H2-2025 raise ≈$125M total (CNBC)", "med",
     "https://www.cnbc.com/2026/01/30/ai-industry-super-pac-raises-campaign-money.html"),
    ("brockman", "ltf", UNDISCLOSED, False, "political", "neutral", "named launch backer (Fortune)", "med",
     "https://fortune.com/2025/08/26/openai-president-greg-brockman-andreessen-horowitz-super-pac-ai/"),
    ("lonsdale", "ltf", UNDISCLOSED, False, "political", "neutral", "named mega-donor", "med", ""),
    ("ltf", "think_big", UNDISCLOSED, False, "political", "neutral", "Dem-facing affiliate", "high", ""),
    ("ltf", "bores", 9*M, True, "political", "oppose", "~$8–10M spent to defeat (The Nation); Bores lost 6/24/26", "high",
     "https://www.thenation.com/article/politics/alex-bores-super-pac-money-ai/"),
    ("think_big", "torres", 982_000, True, "political", "support", "~$982K via Think Big", "med",
     "https://www.axios.com/2026/05/08/ai-super-pac-endorsement-democrats"),
    ("ltf", "menendez", UNDISCLOSED, False, "political", "support", "endorsed (May-2026 tranche)", "med", ""),
    ("ltf", "gottheimer", UNDISCLOSED, False, "political", "support", "endorsed (reported)", "low", ""),
    ("ltf", "gomez", UNDISCLOSED, False, "political", "support", "endorsed (reported)", "low", ""),
    ("ltf", "liccardo", UNDISCLOSED, False, "political", "support", "endorsed (reported)", "low", ""),
    # ---- investment: LP -> fund ----
    ("soros", "key_square", 2*B, True, "investment", "neutral", "~$2B anchor at 2015 launch", "high", ""),
    ("pif", "affinity", 2*B, True, "investment", "neutral", "largest LP (~$2B), reported", "high", ""),
    ("thiel", "hydrazine", UNDISCLOSED, False, "investment", "neutral", "Thiel = sole LP", "high", ""),
    # ---- investment: fund -> portfolio (13F Q1-2026 / rounds) ----
    ("kkr", "brightspring", 1780*M, True, "investment", "neutral", "13F Q1-2026", "high",
     "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001399770&type=13F"),
    ("kkr", "henryschein", 1150*M, True, "investment", "neutral", "13F Q1-2026", "high", ""),
    ("kkr", "bridgebio", 985*M, True, "investment", "neutral", "13F Q1-2026", "high", ""),
    ("ribbit", "nu", 424*M, True, "investment", "neutral", "13F Q1-2026", "high", ""),
    ("ribbit", "figure", 382*M, True, "investment", "neutral", "13F Q1-2026", "high", ""),
    ("ribbit", "robinhood", 225*M, True, "investment", "neutral", "13F Q1-2026 (+1.9% via Bullfrog 13G)", "high", ""),
    ("ribbit", "coinbase", 129*M, True, "investment", "neutral", "13F Q1-2026", "high", ""),
    ("ribbit", "block", 86*M, True, "investment", "neutral", "13F Q1-2026", "high", ""),
    ("rentech", "palantir", 1560*M, True, "investment", "neutral", "13F Q4-2025 (~2.4% of book; top holding)", "high", ""),
    ("eightvc", "joby", 39.6*M, True, "investment", "neutral", "13F Q1-2026 (sole disclosed holding)", "high", ""),
    ("founders_fund", "oscar", 165*M, True, "investment", "neutral", "2018 round led by FF", "high", ""),
    ("a16z", "openai", 200*M, True, "investment", "neutral", "reported a16z stake", "med", ""),
    ("thrive", "openai", UNDISCLOSED, False, "investment", "neutral", "co-led OpenAI rounds (reported, $B)", "med", ""),
    ("hydrazine", "oklo", UNDISCLOSED, False, "investment", "neutral", "early backer (Altman fund, Thiel LP)", "med", ""),
    # ---- philanthropy ----
    ("seid", "marble", 1600*M, True, "philanthropy", "neutral", "$1.6B gift, 2022 (NYT)", "high",
     "https://www.nytimes.com/2022/08/22/us/politics/leonard-leo-barre-seid-courts.html"),
    ("thiel_fdn", "emergent", 1*M, True, "philanthropy", "neutral", "$1M to launch Emergent Ventures, 2018", "med", ""),
    # ---- enforcement / exits ----
    ("galaxy", "nyag", 200*M, True, "enforcement", "oppose", "NY AG settlement re: Terra-LUNA, 2025", "high", ""),
    ("genesis", "victims", 2*B, True, "enforcement", "oppose", "up to $2B settlement (NY AG)", "high", ""),
    ("thiel", "market_pltr", 1790*M, True, "enforcement", "neutral", "Σ Palantir Form 4 sales 2024–2026", "high",
     "https://www.secform4.com/insider-trading/1211060.htm"),
    ("kravis", "market_brsp", 858*M, True, "enforcement", "neutral", "BrightSpring Form 4 sale 2026 (14.67M @ $58.45)", "high", ""),
]


def main():
    used = set()
    flows = []
    for (s, d, amt, known, cat, pol, basis, conf, url) in FLOWS:
        used.add(s); used.add(d)
        flows.append({"source": s, "target": d, "amount": amt, "amount_known": known,
                      "category": cat, "polarity": pol, "basis": basis,
                      "confidence": conf, "url": url})
    nodes = [{"id": k, "label": NODES[k][0], "kind": NODES[k][1]} for k in NODES if k in used]
    cats = {}
    for f in flows:
        cats.setdefault(f["category"], {"flows": 0, "known_usd": 0})
        cats[f["category"]]["flows"] += 1
        if f["amount_known"]:
            cats[f["category"]]["known_usd"] += f["amount"]
    meta = {"title": "Dialog money flows",
            "n_nodes": len(nodes), "n_flows": len(flows),
            "categories": cats,
            "note": "public-record/reported figures only; a money tie is a transaction record, not an allegation. "
                    "Flows marked amount-undisclosed render at a uniform minimal width."}
    payload = {"nodes": nodes, "flows": flows, "meta": meta}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "money-data.js").write_text("window.MONEY = " + json.dumps(payload) + ";\n", encoding="utf-8")
    (OUT / "data").mkdir(exist_ok=True)
    (OUT / "data" / "money.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(json.dumps(meta, indent=2, default=str))
    print(f"nodes={len(nodes)} flows={len(flows)}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
dialog_build_network.py — assemble the socioeconomic network for the
"Dialog retreat members" research report.

Encodes the documented relationships surfaced by the research dossiers as a
typed node / edge graph, then emits:
  - network.json   machine-readable nodes + edges
  - network.md     human-readable adjacency by person + hub ranking
  - network.dot    Graphviz source (render: dot -Tsvg network.dot -o network.svg)

Nodes are people, companies, funds, PACs, institutions, media, and families.
Edges are documented or publicly-reported ties only. The "Dialog" retreat node
is the satirical hub; membership edges to it are marked claim/unverified.
Everything here is built from public-record / public-reporting facts.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "research_reports" / "dialog-retreat-members"

# --- node types ----------------------------------------------------------
P, CO, FUND, PAC, ORG, MEDIA, FAM, GOV, DOC = (
    "person", "company", "fund", "pac", "org", "media", "family", "gov", "document")

# id: (label, type)
NODES: dict[str, tuple[str, str]] = {
    # --- hubs / intermediaries ---
    "dialog": ("Dialog retreat (invite-only, off-the-record)", ORG),
    "paypal": ("PayPal (the 'Mafia' origin)", CO),
    "stanford_review": ("Stanford Review", MEDIA),
    "stanford_gsb": ("Stanford GSB", ORG),
    "stanford": ("Stanford University", ORG),
    "yc": ("Y Combinator", ORG),
    "cfr": ("Council on Foreign Relations", ORG),
    "aspen": ("Aspen Institute / Economic Strategy Group", ORG),
    "milken": ("Milken Institute", ORG),
    "allin": ("All-In podcast", MEDIA),
    "nyt_opinion": ("NYT Opinion", MEDIA),
    "free_press": ("The Free Press (Bari Weiss)", MEDIA),
    "wired": ("WIRED", MEDIA),
    "ltf": ("Leading the Future (pro-AI super PAC, ~$125M)", PAC),
    "think_big": ("Think Big (LTF Dem-facing affiliate)", PAC),
    "fairshake": ("Fairshake (crypto super PAC, the model)", PAC),
    "a16z": ("Andreessen Horowitz (a16z)", FUND),
    "aimoneywatch": ("aimoneywatch.org (Demand Progress)", ORG),
    "demand_progress": ("Demand Progress", ORG),
    # --- Thiel orbit cores ---
    "thiel": ("Peter Thiel", P),
    "founders_fund": ("Founders Fund", FUND),
    "clarium": ("Clarium Capital", FUND),
    "thiel_capital": ("Thiel Capital", FUND),
    "thiel_foundation": ("Thiel Foundation", ORG),
    "palantir": ("Palantir Technologies", CO),
    "facebook": ("Facebook / Meta", CO),
    "auren_hoffman": ("Auren Hoffman (Dialog co-founder)", P),
    "karp": ("Alex Karp (Palantir CEO)", P),
    # --- Musk ---
    "musk": ("Elon Musk", P),
    "spacex": ("SpaceX", CO),
    "tesla": ("Tesla", CO),
    "xai": ("xAI / X", CO),
    "neuralink": ("Neuralink", CO),
    # --- Lonsdale ---
    "lonsdale": ("Joe Lonsdale", P),
    "eightvc": ("8VC", FUND),
    "addepar": ("Addepar", CO),
    "cicero": ("Cicero Institute", ORG),
    "epirus": ("Epirus", CO),
    "saronic": ("Saronic", CO),
    # --- Hoffman ---
    "hoffman": ("Reid Hoffman", P),
    "linkedin": ("LinkedIn", CO),
    "greylock": ("Greylock Partners", FUND),
    "inflection": ("Inflection AI", CO),
    "manas": ("Manas AI", CO),
    "microsoft": ("Microsoft", CO),
    # --- OpenAI cluster ---
    "openai": ("OpenAI", CO),
    "brockman": ("Greg Brockman", P),
    "altman": ("Sam Altman", P),
    "kwon": ("Jason Kwon", P),
    "stripe": ("Stripe", CO),
    "collison": ("Patrick Collison", P),
    "bret_taylor": ("Bret Taylor (OpenAI board chair)", P),
    "sierra": ("Sierra", CO),
    "bronstein": ("Manuel Bronstein", P),
    "hydrazine": ("Hydrazine Capital (Altman; Thiel sole LP)", FUND),
    "oklo": ("Oklo", CO),
    "cochran": ("Caroline Cochran", P),
    "anthropic": ("Anthropic", CO),
    "teller": ("Astro Teller", P),
    "googlex": ("X, the Moonshot Factory (Alphabet)", CO),
    "songhurst": ("Charlie Songhurst", P),
    "akhund": ("Immad Akhund", P),
    "mercury": ("Mercury", CO),
    # --- politicians / officials ---
    "cruz": ("Ted Cruz", P),
    "booker": ("Cory Booker", P),
    "himes": ("Jim Himes", P),
    "bessent": ("Scott Bessent", P),
    "key_square": ("Key Square Capital", FUND),
    "soros": ("Soros Fund Management", FUND),
    "goldman": ("Goldman Sachs", CO),
    "monaco": ("Lisa Monaco", P),
    "wes_moore": ("Wes Moore", P),
    "robin_hood": ("Robin Hood Foundation", ORG),
    "under_armour": ("Under Armour", CO),
    "norquist": ("Grover Norquist", P),
    "atr": ("Americans for Tax Reform", ORG),
    "leo": ("Leonard Leo", P),
    "fedsoc": ("Federalist Society", ORG),
    "marble": ("Marble Freedom Trust", ORG),
    "brand": ("Rachel Brand", P),
    "walmart": ("Walmart", CO),
    "pclob": ("Privacy & Civil Liberties Oversight Board", GOV),
    "slaughter": ("Anne-Marie Slaughter", P),
    "new_america": ("New America", ORG),
    "reema": ("Reema bint Bandar Al Saud", P),
    "pif": ("Saudi PIF / Vision Fund orbit", FUND),
    "bandar": ("Prince Bandar bin Sultan", FAM),
    # --- finance / crypto ---
    "novogratz": ("Mike Novogratz", P),
    "galaxy": ("Galaxy Digital", CO),
    "fortress": ("Fortress Investment Group", FUND),
    "sternlicht": ("Barry Sternlicht", P),
    "starwood": ("Starwood Capital", FUND),
    "rubin": ("Robert Rubin", P),
    "citi": ("Citigroup", CO),
    "centerview": ("Centerview Partners", CO),
    "berggruen": ("Nicolas Berggruen", P),
    "berggruen_holdings": ("Berggruen Holdings", FUND),
    "berggruen_inst": ("Berggruen Institute", ORG),
    "rigetti": ("Rigetti Computing", CO),
    "silbert": ("Barry Silbert", P),
    "dcg": ("Digital Currency Group", CO),
    "grayscale": ("Grayscale", CO),
    "bittensor": ("Bittensor / Yuma", CO),
    "casares": ("Wences Casares", P),
    "xapo": ("Xapo Bank", CO),
    "ribbit": ("Ribbit Capital", FUND),
    "meanwhile": ("Meanwhile (BTC life insurance)", CO),
    "galperin": ("Marcos Galperin", P),
    "meli": ("MercadoLibre", CO),
    "kaszek": ("Kaszek Ventures", FUND),
    "chamath": ("Chamath Palihapitiya", P),
    "social_capital": ("Social Capital", FUND),
    "sacks": ("David Sacks", P),
    "bryan_johnson": ("Bryan Johnson", P),
    "braintree": ("Braintree / Venmo", CO),
    "kernel": ("Kernel", CO),
    "os_fund": ("OS Fund", FUND),
    # --- corporate ---
    "mohan": ("Neal Mohan", P),
    "youtube": ("YouTube / Alphabet", CO),
    "narasimhan": ("Vas Narasimhan", P),
    "novartis": ("Novartis", CO),
    "schlosser": ("Mario Schlosser", P),
    "oscar": ("Oscar Health", CO),
    "josh_kushner": ("Joshua Kushner", P),
    "thrive": ("Thrive Capital", FUND),
    "cook": ("Scott Cook", P),
    "intuit": ("Intuit", CO),
    "cannon_brookes": ("Mike Cannon-Brookes", P),
    "atlassian": ("Atlassian", CO),
    "grok_ventures": ("Grok Ventures", FUND),
    "mcchrystal": ("Stan McChrystal", P),
    "mcchrystal_group": ("McChrystal Group", CO),
    "hamburg": ("Peggy Hamburg", P),
    "mutlu": ("Demet Mutlu", P),
    "trendyol": ("Trendyol", CO),
    "alibaba": ("Alibaba", CO),
    # --- academics / authors ---
    "cowen": ("Tyler Cowen", P),
    "mercatus": ("Mercatus Center / Emergent Ventures", ORG),
    "grant": ("Adam Grant", P),
    "wharton": ("Wharton (UPenn)", ORG),
    "athey": ("Susan Athey", P),
    "levin": ("Jon Levin", P),
    "haidt": ("Jonathan Haidt", P),
    "nyu_stern": ("NYU Stern", ORG),
    "klein": ("Ezra Klein", P),
    "vox": ("Vox Media", MEDIA),
    "harris": ("Sam Harris", P),
    "weinstein": ("Eric Weinstein (Thiel Capital MD)", P),
    "cialdini": ("Robert Cialdini", P),
    "kim_scott": ("Kim Scott", P),
    "stephens": ("Bret Stephens", P),
    "weiss": ("Bari Weiss", P),
    "thompson": ("Nick Thompson", P),
    "atlantic": ("The Atlantic", MEDIA),
    "warren": ("Rick Warren", P),
    "kapadia": ("Gaurav Kapadia", P),
    "xn": ("XN LP", FUND),
    "soroban": ("Soroban Capital", FUND),
    # --- finance hub people from candidates / PAC ---
    "bores": ("Alex Bores (NY; RAISE Act)", P),
    "torres": ("Ritchie Torres", P),
    "menendez": ("Rob Menendez", P),
    "gottheimer": ("Josh Gottheimer", P),
    "gomez": ("Jimmy Gomez", P),
    "liccardo": ("Sam Liccardo", P),
    # --- documentary evidence ---
    "epstein": ("Jeffrey Epstein (email artifact)", DOC),
    "randall": ("Lisa Randall (email sender)", DOC),
}

# (source, relation, target, note)
EDGES: list[tuple[str, str, str, str]] = [
    # Dialog hub (satirical membership claims — unverified)
    ("thiel", "co-founded", "dialog", "with Auren Hoffman, ~2006 (real retreat)"),
    ("auren_hoffman", "co-founded", "dialog", "Dialog co-founder"),
    *[(p, "named-in (claim/unverified)", "dialog", "video's leaked roster")
      for p in ("hoffman", "brockman", "songhurst", "berggruen", "casares",
                "bronstein", "cowen", "grant", "athey", "levin", "haidt",
                "klein", "harris", "stephens", "thompson", "cruz", "lonsdale",
                "musk", "reema")],
    # PayPal mafia origin
    *[(p, "co-founded / PayPal alum", "paypal", "PayPal Mafia") for p in
      ("thiel", "musk", "hoffman")],
    ("sacks", "PayPal alum", "paypal", "PayPal Mafia"),
    ("bryan_johnson", "sold Braintree/Venmo to", "paypal", "$800M, 2013"),
    ("casares", "board member", "paypal", "2016-2020"),
    # Thiel structures
    ("thiel", "co-founded / GP", "founders_fund", ""),
    ("thiel", "founded", "clarium", ""),
    ("thiel", "controls", "thiel_capital", ""),
    ("thiel", "funds", "thiel_foundation", ""),
    ("thiel", "co-founded / chairman", "palantir", "~4% stake"),
    ("thiel", "first outside investor", "facebook", "2004"),
    ("thiel", "early investor", "openai", "no longer active"),
    ("thiel", "early investor", "linkedin", "via Hoffman"),
    ("thiel", "sole LP", "hydrazine", "Altman's fund"),
    ("karp", "CEO", "palantir", ""),
    ("founders_fund", "led round", "oscar", "2018, $165M"),
    ("founders_fund", "portfolio", "stripe", "PayPal-mafia orbit"),
    # Lonsdale (tightest Thiel protege)
    ("lonsdale", "Stanford Review / PayPal / Clarium", "thiel", "protege path"),
    ("lonsdale", "co-founded", "palantir", "2004"),
    ("lonsdale", "founder/managing partner", "eightvc", ">$6B AUM"),
    ("lonsdale", "co-founded", "addepar", ""),
    ("lonsdale", "founded", "cicero", ""),
    ("lonsdale", "co-founded", "epirus", "defense"),
    ("eightvc", "built", "saronic", "autonomous maritime"),
    ("lonsdale", "mega-donor", "ltf", "pro-AI PAC"),
    # Musk
    ("musk", "founder/CEO", "spacex", ""),
    ("musk", "CEO/largest holder", "tesla", ""),
    ("musk", "founder", "xai", "X merged in"),
    ("musk", "co-founder", "neuralink", ""),
    ("musk", "co-founded (2015)", "openai", "now litigation adversary"),
    ("cannon_brookes", "Grok Ventures stake", "spacex", "portfolio"),
    # Hoffman
    ("hoffman", "co-founded", "linkedin", "sold to MSFT $26.2B"),
    ("hoffman", "partner", "greylock", ""),
    ("hoffman", "co-founded", "inflection", "absorbed by MSFT"),
    ("hoffman", "co-founded/chairman", "manas", "AI drug discovery"),
    ("hoffman", "board (departing 2026)", "microsoft", ""),
    ("hoffman", "founding investor / ex-board", "openai", "resigned 2023"),
    ("linkedin", "acquired by", "microsoft", "$26.2B 2016"),
    ("inflection", "talent absorbed by", "microsoft", "2024"),
    # OpenAI cluster
    ("brockman", "co-founder/president/chair", "openai", ""),
    ("brockman", "first CTO", "stripe", "2010-15"),
    ("altman", "co-founder/CEO", "openai", ""),
    ("kwon", "Chief Strategy Officer", "openai", ""),
    ("collison", "CEO", "stripe", "connector Brockman->Altman"),
    ("brockman", "mega-donor", "ltf", "pro-AI PAC"),
    ("bret_taylor", "board chair", "openai", ""),
    ("bret_taylor", "co-founded", "sierra", ""),
    ("bronstein", "product exec", "sierra", ""),
    ("bronstein", "brief VP Product (2024)", "openai", "unverified"),
    ("altman", "founded", "hydrazine", "Thiel sole LP"),
    ("altman", "chaired board (to 2025)", "oklo", ""),
    ("hydrazine", "backed", "oklo", ""),
    ("cochran", "co-founder/COO", "oklo", ""),
    ("thiel", "post-IPO stake", "oklo", "via Hydrazine LP"),
    ("narasimhan", "board (2026)", "anthropic", "first healthcare board member"),
    ("teller", "CEO", "googlex", "Alphabet"),
    ("songhurst", "board", "facebook", "Meta, 2025"),
    ("songhurst", "angel overlaps", "founders_fund", "deal-flow overlap"),
    ("akhund", "co-founder/CEO", "mercury", ""),
    # politicians
    ("cruz", "longtime donor relationship", "thiel", "$251k 2009 +"),
    ("cruz", "chairs", "openai", "Commerce AI oversight (jurisdiction)"),
    ("bessent", "founder/CEO", "key_square", "~$2B Soros anchor"),
    ("bessent", "former CIO", "soros", ""),
    ("key_square", "anchored by", "soros", ""),
    ("himes", "ex-banker", "goldman", ""),
    ("himes", "ranking member, oversees", "palantir", "House Intel / contracting"),
    ("himes", "ranking member, oversees", "openai", "House Intel / contracting"),
    ("monaco", "President Global Affairs", "microsoft", "2025"),
    ("wes_moore", "former CEO", "robin_hood", "Wall St donor network"),
    ("wes_moore", "former board", "under_armour", ""),
    ("norquist", "founder/president", "atr", ""),
    ("leo", "board co-chair", "fedsoc", ""),
    ("leo", "chairman/trustee", "marble", "$1.6B Barre Seid"),
    ("brand", "chief legal officer (departing)", "walmart", ""),
    ("brand", "former member", "pclob", "surveillance oversight"),
    ("slaughter", "CEO (to 2026)", "new_america", "tech-policy hub"),
    ("reema", "ambassador; daughter of", "bandar", ""),
    ("reema", "envoy for", "pif", "Saudi tech/VC capital"),
    ("rubin", "co-chair emeritus", "cfr", ""),
    # finance / crypto
    ("novogratz", "founder/CEO", "galaxy", ""),
    ("novogratz", "ex-principal", "fortress", ""),
    ("novogratz", "ex-partner", "goldman", ""),
    ("sternlicht", "founder/CEO", "starwood", ""),
    ("rubin", "ex-co-chair", "goldman", ""),
    ("rubin", "ex-director", "citi", ""),
    ("rubin", "senior counselor", "centerview", ""),
    ("berggruen", "founder", "berggruen_holdings", ""),
    ("berggruen", "co-founder/chair", "berggruen_inst", ""),
    ("berggruen", "early investor", "rigetti", "quantum"),
    ("berggruen", "co-invested (Ezetap)", "thiel", "with Chamath, Sacks"),
    ("berggruen", "co-invested (Ezetap)", "chamath", ""),
    ("silbert", "founder/CEO", "dcg", ""),
    ("silbert", "chairman", "grayscale", ""),
    ("silbert", "founder/CEO", "bittensor", "Yuma / decentralized AI"),
    ("dcg", "owns", "grayscale", ""),
    ("casares", "founder", "xapo", ""),
    ("casares", "partner", "ribbit", ""),
    ("casares", "co-invested", "meanwhile", "with Altman"),
    ("casares", "co-invested", "altman", "Meanwhile"),
    ("galperin", "founder/exec chair", "meli", ""),
    ("galperin", "Stanford GSB cohort", "stanford_gsb", "with Kaszek founders"),
    ("kaszek", "founded by MELI alumni", "meli", ""),
    ("chamath", "founder/CEO", "social_capital", ""),
    ("chamath", "early exec", "facebook", ""),
    ("chamath", "All-In co-host with", "sacks", ""),
    ("sacks", "co-host", "allin", ""),
    ("chamath", "co-host", "allin", ""),
    ("bryan_johnson", "founder/ex-CEO", "braintree", ""),
    ("bryan_johnson", "founder", "kernel", ""),
    ("bryan_johnson", "founder/GP", "os_fund", ""),
    ("bryan_johnson", "longevity-funding theme", "thiel", "shared interest"),
    ("kapadia", "founder/CEO", "xn", ""),
    ("kapadia", "ex-co-founder", "soroban", ""),
    # corporate
    ("mohan", "CEO", "youtube", ""),
    ("mohan", "board", "cfr", ""),
    ("narasimhan", "CEO", "novartis", ""),
    ("narasimhan", "member", "cfr", ""),
    ("schlosser", "co-founder", "oscar", "with Josh Kushner"),
    ("josh_kushner", "co-founder", "oscar", ""),
    ("josh_kushner", "founder", "thrive", ""),
    ("thrive", "early backer", "oscar", ""),
    ("cook", "co-founder/chair", "intuit", ""),
    ("cannon_brookes", "co-founder/CEO", "atlassian", ""),
    ("cannon_brookes", "founder", "grok_ventures", ""),
    ("mcchrystal", "founder/CEO", "mcchrystal_group", ""),
    ("mutlu", "founder/CEO", "trendyol", ""),
    ("alibaba", "majority owner", "trendyol", ""),
    # academics
    ("cowen", "chair/director", "mercatus", ""),
    ("thiel_foundation", "funded launch", "mercatus", "Emergent Ventures $1M"),
    ("cowen", "platformed", "klein", "Conversations w/ Tyler"),
    ("cowen", "platformed", "haidt", ""),
    ("grant", "professor", "wharton", ""),
    ("athey", "professor", "stanford_gsb", ""),
    ("levin", "president", "stanford", ""),
    ("levin", "GSB colleague", "athey", ""),
    ("haidt", "professor", "nyu_stern", ""),
    ("klein", "co-founded", "vox", ""),
    ("klein", "columnist", "nyt_opinion", ""),
    ("stephens", "columnist", "nyt_opinion", ""),
    ("harris", "podcast interlocutor", "weinstein", "Thiel Capital MD"),
    ("weinstein", "managing director", "thiel_capital", ""),
    ("harris", "podcast guest", "stephens", ""),
    ("stephens", "orbit", "weiss", "Free Press / ex-WSJ"),
    ("weiss", "founder", "free_press", ""),
    ("thiel", "interview subject", "free_press", "Weiss post-2024"),
    ("thompson", "CEO", "atlantic", ""),
    ("thompson", "ex-EIC", "wired", ""),
    ("cialdini", "intellectual tie", "grant", ""),
    # PAC / candidates
    ("ltf", "modeled on", "fairshake", ""),
    ("a16z", "funds ($50M+)", "ltf", ""),
    ("ltf", "Dem-facing arm", "think_big", ""),
    ("ltf", "targeted (~$8-10M)", "bores", "Bores lost 6/24/26 primary"),
    ("bores", "ex-employee", "palantir", ""),
    ("think_big", "backed (~$982k)", "torres", ""),
    ("ltf", "endorsed", "menendez", ""),
    ("ltf", "endorsed", "gottheimer", ""),
    ("ltf", "endorsed", "gomez", ""),
    ("ltf", "endorsed", "liccardo", ""),
    ("aimoneywatch", "project of", "demand_progress", ""),
    ("aimoneywatch", "tracks", "ltf", ""),
    # documentary
    ("randall", "forwarded invite to", "epstein", "email artifact"),
    ("epstein", "invite re:", "dialog", "video's documentary claim"),
]


# ========================================================================
# EXPANSION — second-degree web from the real (WIRED-verified) leak roster.
# People on the 113 Directory List + 222-registrant file not already above,
# plus the intermediary nodes (boards, funds, institutions) that connect them.
# Public-record professional/financial ties only. Roster presence != membership.
# ========================================================================

NODES.update({
    # --- new intermediary / org / fund nodes ---
    "vance": ("JD Vance (VP; Thiel protégé)", P),
    "narya": ("Narya Capital", FUND),
    "brex": ("Brex (board interlock)", CO),
    "meta_board": ("Meta board of directors", ORG),
    "fed_advisory": ("Federal Reserve advisory councils", GOV),
    "uatx": ("University of Austin (UATX)", ORG),
    "next_big_idea": ("Next Big Idea Club", ORG),
    "ferriss_show": ("The Tim Ferriss Show", MEDIA),
    "edge_org": ("Edge.org / Brockman literary agency", MEDIA),
    "harvard": ("Harvard University", ORG),
    "uchicago": ("University of Chicago", ORG),
    "cmu": ("Carnegie Mellon University", ORG),
    "mithril": ("Mithril Capital", FUND),
    "thiel_fellowship": ("Thiel Fellowship", ORG),
    "hoover": ("Hoover Institution (Stanford)", ORG),
    "hudson": ("Hudson Institute", ORG),
    "atlantic_council": ("Atlantic Council", ORG),
    "wef": ("World Economic Forum", ORG),
    "kkr": ("KKR", FUND),
    "arnold_ventures": ("Arnold Ventures", ORG),
    "bridgewater": ("Bridgewater Associates", FUND),
    "rentech": ("Renaissance Technologies", FUND),
    "millennium": ("Millennium Management", FUND),
    "jain_global": ("Jain Global", FUND),
    "tiaa": ("TIAA", CO),
    "breyer_capital": ("Breyer Capital", FUND),
    "accel": ("Accel (Facebook 2005 Series A)", FUND),
    "blackstone": ("Blackstone", FUND),
    "circle": ("Circle", CO),
    "strategy_mstr": ("Strategy / MicroStrategy", CO),
    "verisk": ("Verisk Analytics", CO),
    "patomak": ("Patomak Global Partners", CO),
    "relativity": ("Relativity Space", CO),
    "steel_perlot": ("Steel Perlot", CO),
    "scsp": ("Special Competitive Studies Project", ORG),
    "quora": ("Quora / Poe", CO),
    "airtable": ("Airtable", CO),
    "duolingo": ("Duolingo", CO),
    "groq": ("Groq", CO),
    "nvidia": ("Nvidia", CO),
    "psiquantum": ("PsiQuantum", CO),
    "wellhub": ("Wellhub (Gympass)", CO),
    "anduril": ("Anduril Industries", CO),
    "erebor": ("Erebor (bank)", CO),
    "oculus": ("Oculus (→ Meta)", CO),
    "trae_stephens": ("Trae Stephens (Anduril; Founders Fund)", P),
    "relativity_n": ("Wendy Schmidt", P),
    "affinity_partners": ("Affinity Partners", FUND),
    "qxo": ("QXO, Inc.", CO),
    "verizon": ("Verizon", CO),
    "take_two": ("Take-Two Interactive", CO),
    "zmc": ("ZMC (Zelnick Media Capital)", FUND),
    "kcl": ("Latino Community Foundation", ORG),
    "purdue": ("Purdue University", ORG),
    "king_faisal": ("King Faisal Foundation", ORG),
    "kpc": ("Kuwait Petroleum Corp", CO),
    "airblue": ("Airblue", CO),
    "js_group": ("JS Group / JS Bank", CO),
    "onzero": ("OnZero (data centers)", CO),
    "entrepreneur_first": ("Entrepreneur First", CO),
    "aria_uk": ("ARIA (UK R&D agency)", GOV),
    "helsing": ("Helsing (defense AI)", CO),
    "hybe": ("HYBE America", CO),
    "a24": ("A24", CO),
    "chipotle": ("Chipotle", CO),
    "jigsaw": ("Google Jigsaw", CO),
    "deepmind": ("Google DeepMind", CO),
    "adl": ("Anti-Defamation League", ORG),
    "cato": ("Cato Institute", ORG),
    "koch_fdn": ("Charles Koch Foundation", ORG),
    "renew_dem": ("Renew Democracy Initiative / HRF", ORG),
    "unitedhealth": ("UnitedHealth (historical)", CO),
    "robinhood": ("Robinhood", CO),
    "coinbase": ("Coinbase", CO),
    "sandbox_aq": ("SandboxAQ", CO),
    "block_sq": ("Block (Square)", CO),
    "haven": ("Haven (Amazon-BRK-JPM)", CO),
    # --- new person nodes (alphabetical-ish by surname) ---
    "abbasi": ("Shahid Khaqan Abbasi", P),
    "abramzon": ("Shmuel Abramzon", P),
    "andreessen": ("Marc Andreessen", P),
    "arnold": ("John Arnold", P),
    "attia": ("Peter Attia", P),
    "auken": ("Ida Auken", P),
    "belsky": ("Scott Belsky", P),
    "bharara": ("Preet Bharara", P),
    "blackburn": ("Elizabeth Blackburn", P),
    "bond": ("Sarah Bond", P),
    "braun": ("Scooter Braun", P),
    "breyer": ("Jim Breyer", P),
    "briger": ("Pete Briger", P),
    "brolin": ("Josh Brolin", P),
    "p_brown": ("Peter Brown", P),
    "duckett": ("Thasunda Brown Duckett", P),
    "bush": ("Sophia Bush", P),
    "carvalho": ("Cesar Carvalho", P),
    "castro": ("Julian Castro", P),
    "clifford": ("Matt Clifford", P),
    "cohler": ("Matt Cohler", P),
    "dangelo": ("Adam D'Angelo", P),
    "daniels": ("Mitch Daniels", P),
    "driscoll": ("Dan Driscoll", P),
    "duhigg": ("Charles Duhigg", P),
    "ells": ("Steve Ells", P),
    "ferguson": ("Niall Ferguson", P),
    "ferriss": ("Tim Ferriss", P),
    "gawande": ("Atul Gawande", P),
    "goettler": ("Peter Goettler", P),
    "goldstein": ("Tom Goldstein", P),
    "jgl": ("Joseph Gordon-Levitt", P),
    "greenblatt": ("Jonathan Greenblatt", P),
    "green_y": ("Yasmin Green", P),
    "grynkewich": ("Gen. Alexus Grynkewich", P),
    "gulati": ("Himanshu Gulati", P),
    "hacker": ("Severin Hacker", P),
    "hallie_hoffman": ("Hallie Hoffman", P),
    "hur": ("Rob Hur", P),
    "jain": ("Bob Jain", P),
    "kallas": ("Kaja Kallas", P),
    "kniol": ("Karen Karniol-Tambour", P),
    "kasparov": ("Garry Kasparov", P),
    "katyal": ("Neal Katyal", P),
    "kono": ("Tarō Kōno", P),
    "kravis": ("Henry Kravis", P),
    "mj_kravis": ("Marie-Josée Kravis", P),
    "kroszner": ("Randy Kroszner", P),
    "jared_kushner": ("Jared Kushner", P),
    "i_lee": ("Isaac Lee", P),
    "liu": ("Howie Liu", P),
    "tom_lue": ("Tom Lue", P),
    "luckey": ("Palmer Luckey", P),
    "malka": ("Micky Malka", P),
    "mekhennet": ("Souad Mekhennet", P),
    "myerson": ("Roger Myerson", P),
    "oneill": ("Jim O'Neill", P),
    "pasek": ("Benj Pasek", P),
    "pink": ("Daniel Pink", P),
    "pinker": ("Steven Pinker", P),
    "polis": ("Jared Polis", P),
    "ross_groq": ("Jonathan Ross", P),
    "g_rubin": ("Gretchen Rubin", P),
    "salsano": ("Sandro Salsano", P),
    "scharf": ("Will Scharf", P),
    "schmidt": ("Eric Schmidt", P),
    "schulman": ("Dan Schulman", P),
    "d_scott": ("Drew Scott", P),
    "sellitto": ("Michael Sellitto", P),
    "shadbolt": ("Pete Shadbolt", P),
    "ali_siddiqui": ("Ali Jehangir Siddiqui", P),
    "stephens_l": ("Simon Stevens (Lord Stevens)", P),
    "stephenson": ("Scott Stephenson", P),
    "stowers": ("Ryan Stowers", P),
    "summers": ("Lawrence Summers", P),
    "townsend": ("John Townsend", P),
    "tugendhat": ("Tom Tugendhat", P),
    "turki": ("Turki Al Faisal Al Saud", P),
    "urban": ("Tim Urban", P),
    "nawaf_sabah": ("Nawaf Saud Nasir Al-Sabah", P),
    "zelnick": ("Strauss Zelnick", P),
    "zilis": ("Shivon Zilis", P),
})

# Roster-membership edges (claim/unverified hub link) for the new people.
_ROSTER_NEW = [
    "abbasi", "arnold", "attia", "auken", "belsky", "bharara", "blackburn",
    "bond", "braun", "breyer", "briger", "brolin", "p_brown", "duckett",
    "bush", "carvalho", "castro", "clifford", "cohler", "dangelo", "daniels",
    "driscoll", "duhigg", "ells", "ferguson", "ferriss", "gawande", "goettler",
    "goldstein", "jgl", "greenblatt", "green_y", "grynkewich", "gulati",
    "hacker", "hallie_hoffman", "hur", "jain", "kallas", "kniol", "kasparov",
    "katyal", "kono", "kravis", "kroszner", "jared_kushner", "i_lee", "liu",
    "tom_lue", "luckey", "malka", "mekhennet", "myerson", "oneill", "pasek",
    "pink", "pinker", "polis", "ross_groq", "g_rubin", "salsano", "scharf",
    "schmidt", "schulman", "d_scott", "sellitto", "shadbolt", "ali_siddiqui",
    "stephens_l", "stephenson", "stowers", "summers", "townsend", "tugendhat",
    "turki", "urban", "nawaf_sabah", "zelnick", "zilis",
]

EDGES.extend([(p, "named-in (real leak)", "dialog", "WIRED-verified roster")
              for p in _ROSTER_NEW])

EDGES.extend([
    # --- companies founded/led ---
    ("kravis", "co-founder", "kkr", ""), ("kravis", "married to", "mj_kravis", "MoMA"),
    ("arnold", "founder", "arnold_ventures", ""), ("arnold", "director", "meta_board", ""),
    ("briger", "exec chairman", "fortress", ""), ("briger", "director", "strategy_mstr", "Saylor"),
    ("novogratz", "co-founded", "fortress", ""),
    ("p_brown", "CEO", "rentech", ""), ("p_brown", "married to", "hamburg", ""),
    ("jain", "founder", "jain_global", ""), ("jain", "ex-Co-CIO", "millennium", ""),
    ("jain", "advisory", "fed_advisory", "NY Fed"),
    ("malka", "founder", "ribbit", ""), ("malka", "board", "robinhood", ""),
    ("malka", "board", "brex", ""), ("malka", "early investor", "coinbase", ""),
    ("kniol", "Co-CIO", "bridgewater", ""), ("kniol", "advisory", "fed_advisory", "Dallas Fed"),
    ("kniol", "board", "atlantic_council", ""),
    ("stephenson", "ex-CEO", "verisk", ""),
    ("duckett", "CEO", "tiaa", ""), ("duckett", "board", "brex", ""),
    ("andreessen", "co-founder", "a16z", ""), ("andreessen", "director", "meta_board", ""),
    ("andreessen", "board", "coinbase", ""), ("andreessen", "co-backed", "narya", "with Thiel"),
    ("vance", "founded", "narya", ""), ("thiel", "backed", "narya", "Vance's fund"),
    ("a16z", "co-invests", "openai", "with Founders Fund"),
    ("breyer", "founder", "breyer_capital", ""), ("breyer", "ex-partner", "accel", ""),
    ("accel", "led 2005 Series A", "facebook", ""), ("thiel", "first board seat", "facebook", "2005-13"),
    ("breyer", "board", "blackstone", ""), ("breyer", "lead director", "circle", ""),
    ("kroszner", "professor", "uchicago", ""), ("kroszner", "advisor", "patomak", ""),
    ("kroszner", "ex-governor", "fed_advisory", ""),
    # --- intra-roster bridges (the web) ---
    ("arnold", "Meta-board peer", "andreessen", "direct roster pairing"),
    ("malka", "Brex-board peer", "duckett", "direct roster pairing"),
    ("malka", "a16z co-investor", "andreessen", "8+ shared deals"),
    # --- tech ---
    ("schmidt", "Chair/CEO", "relativity", ""), ("schmidt", "exec chair", "steel_perlot", ""),
    ("schmidt", "founder", "scsp", ""), ("schmidt", "co-founder", "jigsaw", "ex-Google Ideas"),
    ("dangelo", "co-founder/CEO", "quora", ""), ("dangelo", "director", "openai", "board"),
    ("liu", "co-founder/CEO", "airtable", ""), ("airtable", "backed by", "thrive", ""),
    ("airtable", "backed by", "founders_fund", ""),
    ("hacker", "co-founder/CTO", "duolingo", ""), ("hacker", "studied at", "cmu", ""),
    ("ross_groq", "founder", "groq", ""), ("ross_groq", "joined", "nvidia", "2025"),
    ("chamath", "earliest backer", "groq", ""),
    ("shadbolt", "co-founder", "psiquantum", ""), ("founders_fund", "led Series B", "psiquantum", ""),
    ("carvalho", "co-founder/CEO", "wellhub", ""),
    ("cohler", "ex-GP", "accel", ""), ("cohler", "early employee", "facebook", ""),
    ("cohler", "founding team", "linkedin", "Hoffman's deputy"), ("cohler", "board", "kkr", ""),
    ("luckey", "founder/CEO", "anduril", ""), ("luckey", "co-founder", "erebor", "with Thiel"),
    ("thiel", "co-launched", "erebor", ""), ("luckey", "founded", "oculus", ""),
    ("trae_stephens", "co-founder", "anduril", ""), ("trae_stephens", "partner", "founders_fund", ""),
    ("founders_fund", "seeded", "anduril", ""),
    ("zilis", "exec", "neuralink", ""), ("zilis", "ex-board", "openai", "2018-23"),
    ("zilis", "works for", "musk", "Neuralink/xAI/Tesla"),
    ("schulman", "CEO", "verizon", ""), ("schulman", "ex-CEO", "paypal", "2014-23"),
    ("zelnick", "Chairman/CEO", "take_two", ""), ("zelnick", "founder", "zmc", ""),
    ("schmidt", "co-founder (philanthropy)", "relativity_n", ""),
    # --- politics / law ---
    ("jared_kushner", "founder/CEO", "affinity_partners", ""),
    ("jared_kushner", "director", "qxo", ""), ("jared_kushner", "brother of", "josh_kushner", ""),
    ("josh_kushner", "founder", "thrive", ""), ("thrive", "major backer", "openai", ""),
    ("scharf", "staff secretary", "harvard", "HLS node"),
    ("castro", "CEO", "kcl", ""),
    ("daniels", "interim president", "purdue", ""),
    ("driscoll", "Yale Law friend of", "vance", ""), ("driscoll", "Army Secretary", "anduril", "procurement"),
    ("oneill", "MD", "mithril", ""), ("oneill", "MD", "thiel_capital", ""),
    ("oneill", "co-founder", "thiel_fellowship", ""), ("thiel", "co-founded", "mithril", ""),
    ("hur", "DOJ alum", "harvard", "elite-law node"),
    ("katyal", "ex-Acting SG", "goldstein", "SCOTUS bar peer"),
    ("bharara", "co-founder", "vox", "CAFE→Vox"),
    ("polis", "co-founder", "founders_fund", "(Techstars/VC milieu — not FF; peripheral)"),
    # --- foreign / intl ---
    ("turki", "founder", "king_faisal", ""), ("turki", "member", "wef", ""),
    ("turki", "co-listed", "reema", "Saudi"),
    ("nawaf_sabah", "CEO", "kpc", ""),
    ("abbasi", "founder", "airblue", ""),
    ("ali_siddiqui", "chairman", "js_group", ""), ("ali_siddiqui", "board chair", "onzero", ""),
    ("ali_siddiqui", "board", "atlantic_council", ""),
    ("ali_siddiqui", "led 2025 delegation w/", "stephens_l", "Dialog→Pakistan"),
    ("ali_siddiqui", "led 2025 delegation w/", "green_y", "Dialog→Pakistan"),
    ("tugendhat", "fellow", "hudson", ""),
    ("clifford", "chair", "entrepreneur_first", ""), ("clifford", "chair", "aria_uk", ""),
    ("clifford", "investor", "helsing", ""),
    ("stephens_l", "ex-president global health", "unitedhealth", ""),
    # --- academics / authors ---
    ("pinker", "professor", "harvard", ""), ("pinker", "salon member", "edge_org", ""),
    ("summers", "professor", "harvard", ""), ("summers", "ex-board", "openai", "resigned 2025"),
    ("summers", "advisor", "a16z", ""), ("summers", "advisor", "block_sq", ""),
    ("summers", "advisor", "sandbox_aq", ""),
    ("kasparov", "founder/chair", "renew_dem", ""), ("kasparov", "friend of", "thiel", "Palantir Night Live"),
    ("attia", "host", "ferriss_show", "reciprocal"), ("attia", "domain peer", "gawande", "longevity"),
    ("gawande", "professor", "harvard", ""), ("gawande", "ex-CEO", "haven", ""),
    ("blackburn", "Nobel biologist", "harvard", "(UCSF/Salk)"),
    ("ferriss", "host", "ferriss_show", ""), ("thiel", "early guest", "ferriss_show", ""),
    ("ferriss", "podcast tie", "ferguson", ""),
    ("pink", "co-curator", "next_big_idea", ""), ("grant", "co-curator", "next_big_idea", ""),
    ("duhigg", "featured author", "next_big_idea", ""),
    ("g_rubin", "podcast guest", "ferriss_show", ""), ("g_rubin", "family", "rubin", "father-in-law"),
    ("urban", "commissioned by", "musk", "Wait But Why"),
    ("ferguson", "co-founder", "uatx", "with Lonsdale"), ("lonsdale", "co-founder", "uatx", ""),
    ("ferguson", "fellow", "hoover", ""), ("ferguson", "columnist", "free_press", ""),
    ("myerson", "professor", "uchicago", ""), ("myerson", "member", "cfr", ""),
    # --- media / culture / orgs ---
    ("jgl", "founder", "edge_org", "(HitRecord — creator platform)"),
    ("braun", "ex-CEO", "hybe", ""), ("i_lee", "ex-exec", "hybe", ""),
    ("belsky", "partner", "a24", ""), ("ells", "founder", "chipotle", ""),
    ("bond", "president of Xbox", "microsoft", ""), ("zelnick", "gaming-exec peer", "bond", "leak cluster"),
    ("zelnick", "gaming-exec peer", "bronstein", "leak cluster"),
    ("green_y", "CEO", "jigsaw", ""), ("tom_lue", "GC", "deepmind", ""),
    ("sellitto", "global affairs", "anthropic", ""),
    ("greenblatt", "CEO", "adl", ""), ("goettler", "CEO", "cato", ""),
    ("stowers", "exec director", "koch_fdn", ""),
    ("mekhennet", "correspondent", "nyt_opinion", "(WaPo)"),
])


def build():
    nodes = [{"id": k, "label": v[0], "type": v[1]} for k, v in NODES.items()]
    edges = [{"source": s, "relation": r, "target": t, "note": n}
             for (s, r, t, n) in EDGES]
    # validate
    ids = set(NODES)
    for e in edges:
        for end in ("source", "target"):
            if e[end] not in ids:
                raise SystemExit(f"edge references unknown node: {e[end]} in {e}")
    return nodes, edges


def degree_rank(edges):
    deg = defaultdict(int)
    for e in edges:
        deg[e["source"]] += 1
        deg[e["target"]] += 1
    return sorted(deg.items(), key=lambda x: -x[1])


def write_outputs(nodes, edges):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "network.json").write_text(
        json.dumps({"nodes": nodes, "edges": edges}, indent=2), encoding="utf-8")

    label = {n["id"]: n["label"] for n in nodes}
    ntype = {n["id"]: n["type"] for n in nodes}
    adj = defaultdict(list)
    for e in edges:
        adj[e["source"]].append((e["relation"], e["target"], e["note"]))
        adj[e["target"]].append(("← " + e["relation"], e["source"], e["note"]))

    ranked = degree_rank(edges)
    lines = ["# Dialog network — adjacency & hub ranking", "",
             f"_{len(nodes)} nodes · {len(edges)} edges · "
             "public-record / public-reporting ties only_", "",
             "## Most-connected nodes (degree centrality)", ""]
    for nid, d in ranked[:25]:
        lines.append(f"- **{label[nid]}** ({ntype[nid]}) — degree {d}")
    lines += ["", "## Adjacency by person", ""]
    for n in nodes:
        if n["type"] != P:
            continue
        nid = n["id"]
        if nid not in adj:
            continue
        lines.append(f"### {n['label']}")
        for rel, tgt, note in adj[nid]:
            suffix = f" — _{note}_" if note else ""
            lines.append(f"- {rel} → **{label[tgt]}**{suffix}")
        lines.append("")
    (OUT / "network.md").write_text("\n".join(lines), encoding="utf-8")

    # Graphviz
    color = {P: "#cfe8ff", CO: "#d8f5d0", FUND: "#ffe9b3", PAC: "#ffd6d6",
             ORG: "#eee0ff", MEDIA: "#e0f7f7", FAM: "#f0d0e0", GOV: "#e8e8e8",
             DOC: "#f5f5d0"}
    dot = ["digraph dialog {", "  rankdir=LR; node [style=filled, shape=box, "
           "fontsize=9, fontname=Helvetica];"]
    for n in nodes:
        safe = n["label"].replace('"', "'")
        dot.append(f'  "{n["id"]}" [label="{safe}", fillcolor="{color[n["type"]]}"];')
    for e in edges:
        dot.append(f'  "{e["source"]}" -> "{e["target"]}" '
                   f'[label="{e["relation"]}", fontsize=7, color="#888888"];')
    dot.append("}")
    (OUT / "network.dot").write_text("\n".join(dot), encoding="utf-8")
    return ranked


if __name__ == "__main__":
    nodes, edges = build()
    ranked = write_outputs(nodes, edges)
    print(f"nodes={len(nodes)} edges={len(edges)}")
    print("top hubs:")
    lbl = {k: v[0] for k, v in NODES.items()}
    for nid, d in ranked[:12]:
        print(f"  {d:3d}  {lbl[nid]}")

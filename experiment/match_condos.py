"""Match DBPR(Florida Department of Business and Professional Regulation) condo projects (Condo_MD.csv) to City of Miami parent folios."""

import csv
import re
from collections import Counter, defaultdict
from pathlib import Path

from rapidfuzz import fuzz, process
from step0 import LAYERS, base_address, query

DATA = Path(__file__).parent / "data"
OUT = Path(__file__).parent / "out"

WORDS = {
    "NORTH": "N",
    "SOUTH": "S",
    "EAST": "E",
    "WEST": "W",
    "NORTHEAST": "NE",
    "NORTHWEST": "NW",
    "SOUTHEAST": "SE",
    "SOUTHWEST": "SW",
    "STREET": "ST",
    "AVENUE": "AVE",
    "AV": "AVE",
    "DRIVE": "DR",
    "ROAD": "RD",
    "COURT": "CT",
    "PLACE": "PL",
    "TERRACE": "TER",
    "BOULEVARD": "BLVD",
    "LANE": "LN",
    "CIRCLE": "CIR",
}
DIRECTIONS = {"N", "S", "E", "W", "NE", "NW", "SE", "SW"}
NAME_NOISE = {
    "A",
    "THE",
    "CONDO",
    "CONDOMINIUM",
    "CONDOMINIUMS",
    "OF",
    "NO",
    "AT",
    "ASSN",
    "ASSOCIATION",
    "INC",
}


def match_address(text):
    """Street part of an address in one spelling: '1901 NW SOUTH RIVER DRIVE' -> '1901 NW S RIVER DR'."""
    street = (text or "").upper().split(",")[0]  # drop city, state, zip
    street = re.sub(r"#.*", "", street).replace(".", " ")  # drop unit, N.W. -> N W
    street = re.sub(r"\b(\d+)(ST|ND|RD|TH)\b", r"\1", street)  # 15TH -> 15
    street = " ".join(WORDS.get(w, w) for w in street.split())
    return re.sub(r"\b([NS]) ([EW])\b", r"\1\2", street)  # N W -> NW


def loose_address(address):
    """Same address without directions: '6820 W FLAGLER ST' -> '6820 FLAGLER ST'."""
    return " ".join(w for w in address.split() if w not in DIRECTIONS)


def match_name(text):
    """Condo name without punctuation and filler words: 'GRAND, THE' -> 'GRAND'."""
    words = re.sub(r"[^A-Z0-9 ]", " ", (text or "").upper()).split()
    return " ".join(w for w in words if w not in NAME_NOISE)


FUZZY_MIN = 85  # checked by hand: all 23 matches at 85+ were right
NUMBERS = {
    "ONE": "1",
    "TWO": "2",
    "THREE": "3",
    "FOUR": "4",
    "FIVE": "5",
    "I": "1",
    "II": "2",
    "III": "3",
    "IV": "4",
    "V": "5",
}


def fuzzy_name(text):
    """match_name with numbers in one spelling: 'PHASE TWO' / 'PHASE II' -> 'PHASE 2'."""
    return " ".join(NUMBERS.get(w, w) for w in match_name(text).split())


# 1. City condos: tower addresses and the name from the legal description
units = [
    f["attributes"]
    for f in query(
        LAYERS["property"],
        "FOLIO LIKE '01%' AND PARENT_FOLIO IS NOT NULL",
        [
            "FOLIO",
            "PARENT_FOLIO",
            "TRUE_SITE_ADDR",
            "TRUE_SITE_UNIT",
            "LEGAL",
            "TRUE_SITE_ZIP_CODE",
        ],
    )
]
condos = defaultdict(lambda: {"towers": set(), "units": 0, "name": None, "zip": None})
for u in units:
    c = condos[u["PARENT_FOLIO"]]
    c["towers"].add(match_address(base_address(u)))
    c["units"] += 1
    c["zip"] = c["zip"] or (u["TRUE_SITE_ZIP_CODE"] or "")[:5]
    if not c["name"]:
        m = re.match(r"^(.*?)\s+UNIT\b", u["LEGAL"] or "")
        c["name"] = m.group(1) if m else None

# Lookups: key -> set of parent folios
by_address = defaultdict(set)
by_loose = defaultdict(set)
by_name = defaultdict(set)
for parent, c in condos.items():
    for tower in c["towers"]:
        by_address[tower].add(parent)
        by_loose[loose_address(tower)].add(parent)
    if c["name"]:
        by_name[match_name(c["name"])].add(parent)

# 2. DBPR projects in Miami-Dade, matched by address, loose address, then name
with (DATA / "Condo_MD.csv").open(encoding="latin-1") as f:
    projects = [r for r in csv.DictReader(f) if r["County"] == "Dade"]


def only(parents):
    """The parent folio if exactly one matches, else None (ambiguous or missing)."""
    return next(iter(parents)) if parents and len(parents) == 1 else None


matches = defaultdict(list)  # parent folio -> list of DBPR projects
methods = Counter()
for p in projects:
    address = match_address(p["Street City State Zip"])
    parent, method = only(by_address.get(address)), "address"
    if not parent:
        parent, method = only(by_loose.get(loose_address(address))), "loose address"
    if not parent:
        parent, method = only(by_name.get(match_name(p["Condo Name"]))), "name"
    if parent:
        matches[parent].append({**p, "method": method})
        methods[method] += 1


# 3. Fuzzy name pass: leftover projects vs leftover condos in the same zip
used = {m["Project Number"] for found in matches.values() for m in found}
leftover_by_zip = defaultdict(dict)  # zip -> {parent folio: name}
for parent, c in condos.items():
    if parent not in matches and c["name"]:
        leftover_by_zip[c["zip"]][parent] = fuzzy_name(c["name"])

for p in projects:
    if p["Project Number"] in used:
        continue
    zip_code = re.search(r"(\d{5})(-\d{4})?\s*$", p["Street City State Zip"])
    choices = leftover_by_zip.get(zip_code.group(1)) if zip_code else None
    if not choices:
        continue
    best = process.extract(
        fuzzy_name(p["Condo Name"]), choices, scorer=fuzz.token_sort_ratio, limit=2
    )
    clear_winner = len(best) == 1 or best[1][1] < best[0][1]
    if best[0][1] >= FUZZY_MIN and clear_winner:
        _, score, parent = best[0]
        matches[parent].append({**p, "method": f"fuzzy name {score:.0f}"})
        methods["fuzzy name"] += 1


# 4. Summary and a CSV to review by hand
print(f"{len(condos)} City condos, {len(matches)} matched to DBPR: {dict(methods)}")
print(
    f"condos with more than one DBPR project: {sum(len(v) > 1 for v in matches.values())}"
)

missing = [p for p in condos if p not in matches]
for label, minimum in (("11+", 11), ("51+", 51)):
    print(
        f"unmatched with {label} units: {sum(condos[p]['units'] >= minimum for p in missing)}"
    )

rows = []
for parent, c in sorted(condos.items(), key=lambda kv: -kv[1]["units"]):
    for m in matches.get(parent) or [None]:
        rows.append(
            {
                "parent_folio": parent,
                "pa_name": c["name"],
                "units": c["units"],
                "towers": " | ".join(sorted(c["towers"])),
                "project_number": m["Project Number"] if m else None,
                "dbpr_name": m["Condo Name"] if m else None,
                "dbpr_address": m["Street City State Zip"] if m else None,
                "dbpr_units": m["Units"] if m else None,
                "method": m["method"] if m else None,
            }
        )
OUT.mkdir(exist_ok=True)
with (OUT / "condo_match.csv").open("w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
print(f"-> {OUT / 'condo_match.csv'}")

# 4. Fuzzy name pass: leftover projects vs leftover condos in the same zip
used = {m["Project Number"] for found in matches.values() for m in found}
leftover_by_zip = defaultdict(dict)  # zip -> {parent folio: name}
for parent, c in condos.items():
    if parent not in matches and c["name"]:
        leftover_by_zip[c["zip"]][parent] = fuzzy_name(c["name"])

for p in projects:
    if p["Project Number"] in used:
        continue
    zip_code = re.search(r"(\d{5})(-\d{4})?\s*$", p["Street City State Zip"])
    choices = leftover_by_zip.get(zip_code.group(1)) if zip_code else None
    if not choices:
        continue
    best = process.extract(
        fuzzy_name(p["Condo Name"]), choices, scorer=fuzz.token_sort_ratio, limit=2
    )
    clear_winner = len(best) == 1 or best[1][1] < best[0][1]
    if best[0][1] >= FUZZY_MIN and clear_winner:
        _, score, parent = best[0]
        matches[parent].append({**p, "method": f"fuzzy name {score:.0f}"})
        methods["fuzzy name"] += 1

"""How many City of Miami condos have Hold/Expired/Revoked iBuild permits."""

from collections import Counter, defaultdict
from datetime import date

from step0 import LAYERS, THIS_YEAR, ms_to_date, query

BAD = ("Hold", "Expired", "Revoked")

# 1. All condo units in the City of Miami, grouped by parent folio
units = [
    f["attributes"]
    for f in query(
        LAYERS["property"],
        "FOLIO LIKE '01%' AND PARENT_FOLIO IS NOT NULL",
        ["FOLIO", "PARENT_FOLIO", "YEAR_BUILT"],
    )
]
condos = defaultdict(lambda: {"folios": set(), "years": []})
parent_of = {}
for u in units:
    c = condos[u["PARENT_FOLIO"]]
    c["folios"].add(u["FOLIO"])
    if u["YEAR_BUILT"]:
        c["years"].append(u["YEAR_BUILT"])
    parent_of[u["FOLIO"]] = u["PARENT_FOLIO"]
    parent_of[u["PARENT_FOLIO"]] = u["PARENT_FOLIO"]

# 2. Every Hold/Expired/Revoked permit, once per PermitNumber

rows = query(
    LAYERS["ibuild_permits"],
    "PermitStatus IN ('Hold','Expired','Revoked') AND PermitNumber IS NOT NULL",
    [
        "PermitNumber",
        "FOLIO",
        "PermitStatus",
        "PermitType",
        "PermitIssuedDate",
        "ScopeOfWork",
    ],
)
permits = {}
for f in rows:
    p = f["attributes"]
    permits[p["PermitNumber"]] = p
print(f"{len(rows)} rows, {len(permits)} unique bad permits")

# 3. Attach each permit to its condo: building level (parent folio) or unit level
by_condo = defaultdict(lambda: {"building": [], "unit": []})
for p in permits.values():
    parent = parent_of.get(p["FOLIO"])
    if not parent:
        continue  # not a City condo
    level = "building" if p["FOLIO"] == parent else "unit"
    by_condo[parent][level].append(p)


def years_old(p):
    issued = ms_to_date(p["PermitIssuedDate"])
    if not issued:
        return None
    return (date.today() - date.fromisoformat(issued)).days / 365


def age_bucket(years):
    if years is None:
        return "no date"
    if years < 2:
        return "<2y"
    if years < 5:
        return "2-5y"
    return "5y+"


def age_group(age):
    if age is None:
        return "unknown"
    if age >= 40:
        return "40+"
    if age >= 30:
        return "30-39"
    return "<30"


def size_group(units):
    if units <= 10:
        return "1-10"
    if units <= 50:
        return "11-50"
    return "51+"


# 4. Share of condos with at least one bad permit, per status and level
totals = Counter()
hits = defaultdict(Counter)  # group -> "building Expired", "unit Hold", ...
ages = Counter()  # age of building-level bad permits
for parent, c in condos.items():
    age = THIS_YEAR - min(c["years"]) if c["years"] else None
    group = age_group(age)
    if group == "40+":
        group = f"40+ {size_group(len(c['folios']))}"
    totals[group] += 1
    found = by_condo.get(parent, {"building": [], "unit": []})
    for level in ("building", "unit"):
        statuses = {p["PermitStatus"] for p in found[level]}
        for s in statuses:
            hits[group][f"{level} {s}"] += 1
        if statuses:
            hits[group][f"{level} any"] += 1
    for p in found["building"]:
        ages[age_bucket(years_old(p))] += 1

GROUPS = ("40+ 1-10", "40+ 11-50", "40+ 51+", "30-39", "<30", "unknown")
for level in ("building", "unit"):
    print(f"\n{level.upper()} level (% of condos with at least one):")
    for g in GROUPS:
        n = totals[g] or 1
        cols = "  ".join(
            f"{s}: {hits[g][f'{level} {s}']:>4} ({100 * hits[g][f'{level} {s}'] / n:4.1f}%)"
            for s in (*BAD, "any")
        )
        print(f"  {g:10} n={totals[g]:>5}  {cols}")

print("\nAge of building-level bad permits:", dict(ages))

# 5. Same table, building level only, counting only recent permits
for max_years in (5, 2):
    print(f"\nBUILDING level, permits younger than {max_years}y:")
    recent_hits = defaultdict(Counter)
    for parent, c in condos.items():
        age = THIS_YEAR - min(c["years"]) if c["years"] else None
        group = age_group(age)
        if group == "40+":
            group = f"40+ {size_group(len(c['folios']))}"
        found = by_condo.get(parent, {"building": []})["building"]
        recent = [p for p in found if (years_old(p) or 99) < max_years]
        statuses = {p["PermitStatus"] for p in recent}
        for s in statuses:
            recent_hits[group][s] += 1
        if statuses:
            recent_hits[group]["any"] += 1
    for g in GROUPS:
        n = totals[g] or 1
        cols = "  ".join(
            f"{s}: {recent_hits[g][s]:>4} ({100 * recent_hits[g][s] / n:4.1f}%)"
            for s in (*BAD, "any")
        )
        print(f"  {g:10} n={totals[g]:>5}  {cols}")

# 6. What the building-level Hold permits are about
print("\nBuilding-level Hold permits:")
for parent, found in by_condo.items():
    for p in found["building"]:
        if p["PermitStatus"] == "Hold":
            print(
                f"  {parent}  {ms_to_date(p['PermitIssuedDate']) or '----------'}"
                f"  {p['PermitType'] or '-':12}  {(p['ScopeOfWork'] or '-')[:60]}"
            )

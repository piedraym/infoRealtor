"""How many City of Miami condos have a recertification record, by age."""

from collections import Counter, defaultdict
from datetime import date

from step0 import LAYERS, base_address, norm_address, query

THIS_YEAR = date.today().year

# 1. All condo units in the City of Miami, grouped by parent folio
units = [
    f["attributes"]
    for f in query(
        LAYERS["property"],
        "FOLIO LIKE '01%' AND PARENT_FOLIO IS NOT NULL",
        ["FOLIO", "PARENT_FOLIO", "TRUE_SITE_ADDR", "TRUE_SITE_UNIT", "YEAR_BUILT"],
    )
]

condos = defaultdict(lambda: {"folios": set(), "towers": set(), "years": []})
for u in units:
    c = condos[u["PARENT_FOLIO"]]
    c["folios"].add(u["FOLIO"])
    c["towers"].add(norm_address(base_address(u)))
    if u["YEAR_BUILT"]:  # 0 = unknown
        c["years"].append(u["YEAR_BUILT"])

# 2. The whole recertification layer: only folios and addresses
recert = [
    f["attributes"] for f in query(LAYERS["recert"], "1=1", ["FolioNumber", "Address"])
]
recert_folios = {r["FolioNumber"] for r in recert}
recert_addresses = {norm_address(r["Address"]) for r in recert}


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


# 3. Does each condo have a record, by folio or by tower address?
by_age = defaultdict(Counter)
by_size = defaultdict(Counter)  # only 40+ condos
for parent, c in condos.items():
    age = THIS_YEAR - min(c["years"]) if c["years"] else None
    has_record = (
        parent in recert_folios
        or bool(c["folios"] & recert_folios)
        or bool(c["towers"] & recert_addresses)
    )
    result = "with record" if has_record else "no record"
    by_age[age_group(age)][result] += 1
    if age_group(age) == "40+":
        by_size[size_group(len(c["folios"]))][result] += 1

print(f"{len(units)} units, {len(condos)} condos\n")
print("By age:")
for group in ("40+", "30-39", "<30", "unknown"):
    print(f"  {group:8} {dict(by_age[group])}")
print("\n40+ condos by number of units:")
for group in ("1-10", "11-50", "51+"):
    print(f"  {group:8} {dict(by_size[group])}")

"""How many City of Miami condos have a recertification record, by age."""

import csv
import re
from collections import Counter, defaultdict
from pathlib import Path

from step0 import (
    LAYERS,
    THIS_YEAR,
    base_address,
    latest_record,
    norm_address,
    query,
    recert_label,
)

# 1. All condo units in the City of Miami, grouped by parent folio
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
            "YEAR_BUILT",
            "LEGAL",
        ],
    )
]

condos = defaultdict(
    lambda: {"folios": set(), "towers": set(), "years": [], "legal": None}
)
for u in units:
    c = condos[u["PARENT_FOLIO"]]
    c["folios"].add(u["FOLIO"])
    c["towers"].add(norm_address(base_address(u)))
    if u["YEAR_BUILT"]:  # 0 = unknown
        c["years"].append(u["YEAR_BUILT"])
    if not c["legal"]:
        c["legal"] = u["LEGAL"]

# 2. The whole recertification layer
recert = [
    f["attributes"]
    for f in query(
        LAYERS["recert"],
        "1=1",
        [
            "FolioNumber",
            "Address",
            "CertificationStatus",
            "RecertificateYear",
            "PlanStatus",
            "PlanStatusDate",
        ],
    )
]
recert_by_folio = defaultdict(list)
recert_by_address = defaultdict(list)
for r in recert:
    recert_by_folio[r["FolioNumber"]].append(r)
    recert_by_address[norm_address(r["Address"])].append(r)


def condo_status(parent, c):
    """Status of the latest recertification cycle, None if there is no record."""
    records = list(recert_by_folio[parent])
    for folio in c["folios"]:
        records += recert_by_folio[folio]
    for tower in c["towers"]:
        records += recert_by_address[tower]
    if not records:
        return None
    return recert_label(latest_record(records))


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
missing = []
for parent, c in condos.items():
    age = THIS_YEAR - min(c["years"]) if c["years"] else None
    status = condo_status(parent, c)
    result = status or "no record"
    by_age[age_group(age)][result] += 1
    if age_group(age) == "40+":
        by_size[size_group(len(c["folios"]))][result] += 1
    if age_group(age) == "40+" and len(c["folios"]) > 10 and status is None:
        m = re.match(r"^(.*?)\s+UNIT\b", c["legal"] or "")
        missing.append(
            {
                "parent_folio": parent,
                "name": m.group(1) if m else (c["legal"] or "")[:40],
                "year": min(c["years"]),
                "units": len(c["folios"]),
                "towers": " | ".join(sorted(c["towers"])),
            }
        )


print(f"{len(units)} units, {len(condos)} condos\n")
COLUMNS = (
    "Completed",
    "Completed: overdue",
    "Exempted",
    "Pending: city review",
    "Pending: corrections",
    "Pending: corrections >1y",
    "Pending: stalled",
    "Pending: other",
    "Canceled",
    "no record",
)
print("By age:")
for group in ("40+", "30-39", "<30", "unknown"):
    print(f"  {group:8} " + "  ".join(f"{k}: {by_age[group][k]}" for k in COLUMNS))
print("\n40+ condos by number of units:")
for group in ("1-10", "11-50", "51+"):
    print(f"  {group:8} " + "  ".join(f"{k}: {by_size[group][k]}" for k in COLUMNS))

missing.sort(key=lambda r: -r["units"])
out = Path(__file__).parent / "out" / "no_record_11plus.csv"
out.parent.mkdir(exist_ok=True)
with out.open("w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=missing[0].keys())
    writer.writeheader()
    writer.writerows(missing)


print(f"\n40+ condos with 11+ units and no record: {len(missing)} -> {out}")
for r in missing:
    print(
        f"  {r['parent_folio']}  {r['units']:>5}  {r['year']}  {r['name'][:30]:30}  {r['towers'][:60]}"
    )

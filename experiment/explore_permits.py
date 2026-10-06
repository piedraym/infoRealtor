"""Explore the City's iBuild permits layer before using it in step0."""

import json

import requests
from step0 import LAYERS, ms_to_date, query

URL = LAYERS["ibuild_permits"]
THE_GRAND = "0132310480001"


def count_by(field, where="1=1"):
    """How many rows have value of a field, most common first"""
    stats = [
        {
            "statisticType": "count",
            "onStatisticField": "OBJECTID",
            "outStatisticFieldName": "n",
        }
    ]
    params = {
        "where": where,
        "groupByFieldsForStatistics": field,
        "outStatistics": json.dumps(stats),
        "f": "json",
    }

    r = requests.get(f"{URL}/query", params=params, timeout=60)
    r.raise_for_status()

    rows = [
        (f["attributes"][field], f["attributes"]["n"]) for f in r.json()["features"]
    ]
    return sorted(rows, key=lambda row: -row[1])


# 1. Which values each status and type field has in the whole layer
for field in ("MasterPermitStatus", "PermitStatus", "MasterPermitType", "PermitType"):
    print(f"\n{field}:")
    for value, n in count_by(field)[:20]:
        print(f"  {n:>7} {value}")

# 2 One known building, row by row

fields = [
    "PlanNumber",
    "PermitNumber",
    "MasterPermitNumber",
    "PermitType",
    "MasterPermitType",
    "ScopeOfWork",
    "PermitStatus",
    "MasterPermitStatus",
    "MasterPlanStatus",
    "PermitIssuedDate",
    "FULLADDR",
]

rows = [f["attributes"] for f in query(URL, f"FOLIO='{THE_GRAND}'", fields)]
rows.sort(key=lambda r: r["PermitIssuedDate"] or 0, reverse=True)
print(f"\n The Grand: {len(rows)} rows")
for r in rows[:40]:
    print(
        f"  {ms_to_date(r['PermitIssuedDate']) or '----------'}"
        f"  {r['PermitNumber'] or '-':24}"
        f"  {r['PermitType'] or '-':12}"
        f"  {r['MasterPermitStatus'] or '-':10}"
        f"  {r['PermitStatus'] or '-':10}"
        f"  {(r['ScopeOfWork'] or '-')[:30]}"
    )

"""Which City condos reported a SIRS to DBPR, through Condo_MD projects.

Needs out/condo_match.csv from match_condos.py (DBPR project -> parent folio).
"""

import csv
import re
from collections import Counter, defaultdict
from pathlib import Path

import openpyxl
from rapidfuzz import fuzz, process
from step0 import FUZZY_MIN, fuzzy_name

DATA = Path(__file__).parent / "data"
OUT = Path(__file__).parent / "out"

SIRS_NOISE = {"ASSOC", "SIRS", "REPORT", "PROJECT"}  # e.g. "CONDOMINIUM PROJECT"


def sirs_name(text):
    """Name for matching; '' when the report has no usable name."""
    text = str(text or "")
    if text.upper().startswith("MISSING"):  # "Missing/Unknown"
        return ""
    return " ".join(w for w in fuzzy_name(text).split() if w not in SIRS_NOISE)


def zip_of(text):
    """Five-digit zip at the end of a DBPR address, or None."""
    found = re.search(r"(\d{5})(-\d{4})?\s*$", text or "")
    return found.group(1) if found else None


def read_excel(name):
    """Rows of the first sheet as dicts, keyed by the header row."""
    sheet = openpyxl.load_workbook(DATA / name, read_only=True).active
    rows = list(sheet.iter_rows(values_only=True))
    return [dict(zip(rows[0], row)) for row in rows[1:]]


# 1. Bridge from match_condos.py (project -> parent folio) and DBPR projects
with (OUT / "condo_match.csv").open() as f:
    parent_of = {
        r["project_number"]: r["parent_folio"]
        for r in csv.DictReader(f)
        if r["project_number"]
    }

with (DATA / "Condo_MD.csv").open(encoding="latin-1") as f:
    projects = [r for r in csv.DictReader(f) if r["County"] == "Dade"]

by_name = defaultdict(set)  # name -> project numbers
by_zip = defaultdict(dict)  # zip -> {project number: name}
for p in projects:
    name = sirs_name(p["Condo Name"])
    by_name[name].add(p["Project Number"])
    zip_code = zip_of(p["Street City State Zip"])
    if zip_code:
        by_zip[zip_code][p["Project Number"]] = name

# 2. SIRS reports in Miami-Dade (the two files use different column names)
reports = [
    {
        "period": "July 2025+",
        "names": (r["Project Name"], r["Association Name"]),
        "zip": str(r["Zip Code"])[:5],
    }
    for r in read_excel("mayorJulio25.xlsx")
    if r["County"] == "Dade"
]
reports += [
    {
        "period": "before July 2025",
        "names": (r["Project Name"], r["Association Name"]),
        "zip": str(r["Zip"])[:5],
    }
    for r in read_excel("menorJulio25.xlsx")
    if r["County"] == "MIAMI-DADE"
]

sirs = defaultdict(set)  # parent folio -> periods with a report
methods = Counter()
for report in reports:
    names = [n for n in (sirs_name(x) for x in report["names"]) if n]
    project, method = None, None
    for name in names:  # exact name, unique in the county
        found = by_name.get(name)
        if found and len(found) == 1:
            project, method = next(iter(found)), "exact"
            break
    if not project:  # fuzzy name inside the same zip
        choices = by_zip.get(report["zip"]) or {}
        for name in names:
            if not choices:
                break
            best = process.extract(name, choices, scorer=fuzz.token_sort_ratio, limit=2)
            clear_winner = len(best) == 1 or best[1][1] < best[0][1]
            if best and best[0][1] >= FUZZY_MIN and clear_winner:
                project, method = best[0][2], "fuzzy"
                break
    methods[method or "no project"] += 1
    if project in parent_of:  # only City condos
        sirs[parent_of[project]].add(report["period"])

# 3. Summary and the result per condo
print(f"{len(reports)} Miami-Dade SIRS reports: {dict(methods)}")
print(f"{len(sirs)} City condos with a SIRS report:")
for periods, n in Counter(" + ".join(sorted(v)) for v in sirs.values()).most_common():
    print(f"  {n:>4}  {periods}")

OUT.mkdir(exist_ok=True)
with (OUT / "sirs_by_folio.csv").open("w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["parent_folio", "sirs_periods"])
    for parent, periods in sorted(sirs.items()):
        writer.writerow([parent, " + ".join(sorted(periods))])
print(f"-> {OUT / 'sirs_by_folio.csv'}")

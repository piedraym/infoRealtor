import csv
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# 1. Fuentes de datos (servicios ArcGIS públicos)
# ---------------------------------------------------------------------------

COUNTY = "https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/ArcGIS/rest/services"
CITY = "https://gis.miami.gov/gis/rest/services"

OUT = Path(__file__).parent / "out"  # CSVs written by match_condos.py and match_sirs.py

LAYERS = {
    "property": f"{COUNTY}/PaGISView_gdb/FeatureServer/0",
    "recert": f"{CITY}/SmartCities/Recertification_40Years/MapServer/0",
    "ibuild_permits": f"{CITY}/Maps/iBuildPermits/MapServer/0",
    # "permits": f"{CITY}/Building_Permits_Since_2014/FeatureServer/0",
    # "open_violations": f"{COUNTY}/Open_Building_Violations/FeatureServer/0",
    # "closed_violations_5y": f"{COUNTY}/Closed_Building_Violations_(Past_5_years)/FeatureServer/0",
    "flood": f"{COUNTY}/FEMAFloodZone_gdb/FeatureServer/0",
    "shoreline": f"{COUNTY}/Shoreline_gdb/FeatureServer/0",
}

# Licencias: las de la Ciudad están publicadas como CC BY 4.0 (uso comercial
# permitido citando la fuente). Las del condado hay que revisarlas una a una en
# su página del portal (gis-mdc.opendata.arcgis.com) antes de cobrar.
LICENSES = {
    "recert": "Pendiente de verificar (servidor GIS de la Ciudad)",
    "permits": "CC BY 4.0 (Ciudad de Miami)",
    "ibuild_permits": "Pendiente de verificar (servidor GIS de la Ciudad)",
    "flood": "Pendiente de verificar (condado)",
    "shoreline": "Pendiente de verificar (condado)",
}

# Campos que pedimos.
# ni OwnerName ni VIOL_NAME: así ni siquiera llegan a tu computadora.
PROPERTY_FIELDS = [
    "FOLIO",
    "PARENT_FOLIO",
    "CONDO_FLAG",
    "TRUE_SITE_ADDR",
    "TRUE_SITE_UNIT",
    "TRUE_SITE_CITY",
    "TRUE_SITE_ZIP_CODE",
    "DOR_CODE_CUR",
    "DOR_DESC",
    "FLOOR_COUNT",
    "UNIT_COUNT",
    "YEAR_BUILT",
    "BUILDING_ACTUAL_AREA",
    "LEGAL",
    "SUBDIVISION",
]
RECERT_FIELDS = [
    "RecertificateYear",
    "CertificationStatus",
    "RecertificationProcessStatus",
    "Address",
    "BuildingNumber",
    "FolioNumber",
    "YearBuilt",
    "PlanNumber",
    "SubmittedDate",
    "PlanStatus",
    "PlanStatusReason",
    "PlanStatusDate",
    "RequestType",
    "RequestStatus",
    "RequestResult",
    "UpdatedDate",
    "OBJECTID",
]

PERMIT_FIELDS = [
    "PermitNumber",
    "PermitStatus",
    "PermitType",
    "ScopeOfWork",
    "PermitIssuedDate",
]

BAD_PERMIT_STATUSES = ("Hold", "Expired", "Revoked")

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

FUZZY_MIN = 85  # checked by hand: all matches at 85+ were right

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


def match_name(text):
    """Condo name without punctuation and filler words: 'GRAND, THE' -> 'GRAND'."""
    words = re.sub(r"[^A-Z0-9 ]", " ", (text or "").upper()).split()
    return " ".join(w for w in words if w not in NAME_NOISE)


def layer_info(url):
    r = requests.get(url, params={"f": "json"}, timeout=30)
    r.raise_for_status()
    return r.json()


def safe_fields(url, wanted):
    """Devuelve solo los campos de la lista permitida que existe en la capa"""
    existing = {f["name"] for f in layer_info(url)["fields"]}
    missing = [f for f in wanted if f not in existing]
    if missing:
        print(f" ! no existen en la capa: {missing}")
    return [f for f in wanted if f in existing]


def base_address(r):
    """Building address without the unit number at the end."""
    addr = r["TRUE_SITE_ADDR"] or ""
    unit = r["TRUE_SITE_UNIT"]
    if unit and addr.endswith(" " + unit):
        addr = addr[: -len(unit) - 1]
    return addr


SUFFIXES = {"AVE": "AV"}

STREET_NAMES = {
    "N RIVER DR": "NORTH RIVER DR",
    "S RIVER DR": "SOUTH RIVER DR",
    "S TAMIAMI CANAL DR": "SOUTH TAMIAMI CANAL DR",
}


def norm_address(addr):
    """Upper case, single spaces and the recert layer's street spelling."""
    words = (addr or "").upper().split()
    addr = " ".join(SUFFIXES.get(w, w) for w in words)
    for short, full in STREET_NAMES.items():
        if addr.endswith(" " + short):
            addr = addr[: -len(short)] + full
    return addr


def ms_to_date(ms):
    """ArcGIS date are in milliseconds"""
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()


def point_query(url, x, y, fields, distance=None):
    """Features of a layer that contain the point (longitude, latitude)."""
    params = {
        "geometry": f"{x},{y}",
        "geometryType": "esriGeometryPoint",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": ",".join(fields),
        "returnGeometry": "false",
        "f": "json",
    }
    if distance:
        params["distance"] = distance
        params["units"] = "esriSRUnit_StatuteMile"
    r = requests.get(f"{url}/query", params=params, timeout=60)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(data["error"])
    return [f["attributes"] for f in data["features"]]


# ---------------------------------------------------------------------------
# Recertification traffic light
# ---------------------------------------------------------------------------

THIS_YEAR = date.today().year

# When records share the latest year, the best status wins
PRIORITY = {"Completed": 3, "Exempted": 2, "Pending": 1, "Canceled": 0}

# Pending records split by who has to act next
PENDING_GROUPS = {
    "In Review": "city review",
    "Prescreen": "city review",
    "Submitted": "city review",
    "Approved": "city review",
    "Permit Issued": "city review",
    "Final": "city review",
    "Applicant Corrections": "corrections",
    "Prescreen Corrections": "corrections",
    "Applicant Upload": "corrections",
    "Incomplete": "corrections",
    "Cancelled": "stalled",
    "Expired": "stalled",
    "Inactive": "stalled",
    "Hold": "stalled",
}

STUCK_DAYS = 365  # corrections older than this count as stuck

# Recertification schedule. ESTIMATE with the strictest rule (County Sec. 8-11(f)):
# change these when the City confirms which one it applies (its web page says 40).
FIRST_RECERT_AGE = 30
FIRST_RECERT_AGE_COASTAL = 25  # 3+ floors within 3 miles of the coast
RECERT_EVERY = 10

LIGHTS = {
    "Completed": "green",
    "Exempted": "green",
    "Completed: overdue": "yellow",
    "Pending: city review": "yellow",
    "Pending: corrections": "yellow",
    "Pending: other": "yellow",
    None: "yellow",  # no record
    "Pending: corrections >1y": "red",
    "Pending: stalled": "red",
    "Canceled": "red",
}
LIGHT_ORDER = ["green", "yellow", "red"]


def days_since(ms):
    """Days from an ArcGIS date (milliseconds) to today, None if missing."""
    if ms is None:
        return None
    return (date.today() - datetime.fromtimestamp(ms / 1000).date()).days


def latest_record(records):
    """Record of the latest cycle; on a tie, the best status."""
    return max(
        records,
        key=lambda r: (
            r["RecertificateYear"] or 0,
            PRIORITY.get(r["CertificationStatus"], -1),
        ),
    )


def recert_label(r):
    """Label of one recert record (a key of LIGHTS), dates must still be in ms."""
    status = r["CertificationStatus"]
    if status == "Completed" and (r["RecertificateYear"] or 0) + 10 < THIS_YEAR:
        return "Completed: overdue"
    if status == "Pending":
        group = PENDING_GROUPS.get(r["PlanStatus"], "other")
        days = days_since(r["PlanStatusDate"])
        if group == "corrections" and days is not None and days > STUCK_DAYS:
            group = "corrections >1y"
        return f"Pending: {group}"
    return status


def query(url, where, fields, geometry=False):
    """Consulta con paginacion, sigue pidiendo mientras queden registros"""
    out, offset = [], 0
    while True:
        params = {
            "where": where,
            "outFields": ",".join(fields),
            "returnGeometry": str(geometry).lower(),
            "outSR": 4326,
            "resultOffset": offset,
            "f": "json",
        }
        r = requests.post(f"{url}/query", data=params, timeout=60)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise RuntimeError(data["error"])
        out.extend(data["features"])
        if not data.get("exceededTransferLimit"):
            return out
        offset += len(data["features"])


def building_summary(parent_folio, fields):
    """Junta todas las unidades de un condominio en un resumen de un edificio"""
    all_rows = [
        f["attributes"]
        for f in query(
            LAYERS["property"],
            f"PARENT_FOLIO='{parent_folio}' OR FOLIO='{parent_folio}'",
            fields,
        )
    ]
    master = [r for r in all_rows if r["FOLIO"] == parent_folio]
    rows = [r for r in all_rows if r["FOLIO"] != parent_folio]
    if not rows:
        return None

    master_legal = master[0]["LEGAL"] if master else None

    folios = [r["FOLIO"] for r in rows]
    years = [r["YEAR_BUILT"] for r in rows if r["YEAR_BUILT"]]  # 0 = desconocido
    floors = [
        int(r["TRUE_SITE_UNIT"][:-2])
        for r in rows
        if r["TRUE_SITE_UNIT"]
        and r["TRUE_SITE_UNIT"].isdigit()
        and len(r["TRUE_SITE_UNIT"]) >= 3
    ]
    # Condominium Name
    condo_legal_name = rows[0]["LEGAL"] or ""
    m = re.match(r"^(.*?)\s+UNIT\b", condo_legal_name)

    # Towers: one parent folio can cover several buildings (one address each)
    towers = Counter(base_address(r) for r in rows)

    return {
        "parent_folio": parent_folio,
        "master_in_pa": bool(master),
        "master_legal": master_legal,
        "towers": dict(towers.most_common()),
        "zip": (rows[0]["TRUE_SITE_ZIP_CODE"] or "")[:5],
        "condo_name": m.group(1) if m else None,
        "units": len(rows),
        "estimate_floors": max(floors) if floors else None,
        "year_of_construction": min(years) if years else None,
        "folios": folios,
    }


def recert_records(building, fields):
    """Recertification records of the building, by folio or by tower address."""
    all_folios = [building["parent_folio"]] + building["folios"]
    in_list = ",".join(f"'{f}'" for f in all_folios)
    by_folio = query(LAYERS["recert"], f"FolioNumber IN({in_list})", fields)

    addresses = [norm_address(t) for t in building["towers"]]
    in_list = ",".join(f"'{current_address}'" for current_address in addresses)
    by_address = query(LAYERS["recert"], f"Address IN({in_list})", fields)

    records = {}
    for f in by_folio + by_address:
        r = f["attributes"]
        if r["OBJECTID"] in records:  # found in both ways: keep only one
            continue
        if r["FolioNumber"] == building["parent_folio"]:
            r["matched_on"] = "master"
        elif r["FolioNumber"] in all_folios:
            r["matched_on"] = "unit"
        else:
            r["matched_on"] = "address"
        r["label"] = recert_label(r)
        for key in ("SubmittedDate", "PlanStatusDate"):
            r[key] = ms_to_date(r.get(key))
        records[r["OBJECTID"]] = r
    return list(records.values())


def recert_by_tower(building, records):
    """Traffic light of each tower, from its latest recertification cycle."""
    by_tower = defaultdict(list)
    for r in records:
        by_tower[norm_address(r["Address"])].append(r)
    result = {}
    for tower in building["towers"]:
        tower_records = by_tower.get(norm_address(tower))
        latest = latest_record(tower_records) if tower_records else None
        label = latest["label"] if latest else None
        result[tower] = {
            "label": label or "no record",
            "light": LIGHTS.get(label, "yellow"),
            "year_due": latest["RecertificateYear"] if latest else None,
            "status_date": latest["PlanStatusDate"] if latest else None,
            "plan": latest["PlanNumber"] if latest else None,
        }
    return result


def building_light(towers):
    """The building takes the worst light of its towers."""
    return max((t["light"] for t in towers.values()), key=LIGHT_ORDER.index)


def open_permits(building, fields):
    """Building-level permits left in Hold, Expired or Revoked, newest first."""
    statuses = ",".join(f"'{s}'" for s in BAD_PERMIT_STATUSES)
    rows = query(
        LAYERS["ibuild_permits"],
        f"FOLIO='{building['parent_folio']}' AND PermitStatus IN ({statuses})"
        " AND PermitNumber IS NOT NULL",
        fields,
    )
    permits = {}
    for f in rows:
        p = f["attributes"]
        p["PermitIssuedDate"] = ms_to_date(p["PermitIssuedDate"])
        permits[p["PermitNumber"]] = p  # the layer has exact duplicate rows
    return sorted(
        permits.values(), key=lambda p: p["PermitIssuedDate"] or "", reverse=True
    )


def building_point(building):
    """Location of the building: master folio, else the first unit."""
    folio = (
        building["parent_folio"] if building["master_in_pa"] else building["folios"][0]
    )
    found = query(LAYERS["property"], f"FOLIO='{folio}'", ["FOLIO"], geometry=True)
    if not found or not found[0].get("geometry"):
        return None
    return found[0]["geometry"]


def flood_zone(point):
    """FEMA flood zone at the building's location."""
    if not point:
        return None
    zones = point_query(
        LAYERS["flood"], point["x"], point["y"], ["FZONE", "ZONESUBTY", "ELEV"]
    )
    if not zones:
        return None
    z = zones[0]
    return {
        "zone": z["FZONE"],
        "subtype": (z["ZONESUBTY"] or "").strip() or None,
        "base_flood_elevation_ft": z["ELEV"]
        if z["ELEV"] not in (None, -9999)
        else None,
    }


COAST_BANDS = (0.25, 0.5, 1, 2, 3)  # miles


def coast_distance(point):
    """Smallest band (miles) with shoreline nearby, and whether it is within 3 miles."""
    if not point:
        return None
    for miles in COAST_BANDS:
        if point_query(
            LAYERS["shoreline"], point["x"], point["y"], ["OBJECTID"], distance=miles
        ):
            return {"within_miles": miles, "within_3_miles": True}
    return {"within_miles": None, "within_3_miles": False}


def first_recert_age(building):
    """Age of the first recertification: 25 for coastal buildings of 3+ floors."""
    coastal = (building.get("coast") or {}).get("within_3_miles")
    floors = building["estimate_floors"]
    if coastal and (floors is None or floors >= 3):  # unknown floors: be strict
        return FIRST_RECERT_AGE_COASTAL
    return FIRST_RECERT_AGE


def next_recert(building, tower):
    """Year the tower's next recertification is due, estimated."""
    label = tower["label"]
    if label == "Exempted":
        return None
    if label.startswith("Completed"):
        year, basis = tower["year_due"] + RECERT_EVERY, "last recert + 10"
    elif tower["year_due"]:  # Pending or Canceled: this cycle is still open
        year, basis = tower["year_due"], "open cycle"
    else:  # no record
        built = building["year_of_construction"]
        if not built:
            return None
        age = first_recert_age(building)
        year, basis = built + age, f"year built + {age}"
    return {"year": year, "overdue": year < THIS_YEAR, "basis": basis}


def dbpr_info(building):
    """DBPR projects, Delinquent status and SIRS reports, from match_condos.py and match_sirs.py."""
    parent = building["parent_folio"]
    with (OUT / "condo_match.csv").open() as f:
        projects = [
            {
                "number": r["project_number"],
                "name": r["dbpr_name"],
                "status": r["dbpr_status"],
            }
            for r in csv.DictReader(f)
            if r["parent_folio"] == parent and r["project_number"]
        ]
    with (OUT / "sirs_by_folio.csv").open() as f:
        sirs = next(
            (
                r["sirs_periods"]
                for r in csv.DictReader(f)
                if r["parent_folio"] == parent
            ),
            None,
        )
    return {
        "projects": projects,
        "delinquent": any(p["status"] == "Delinquent" for p in projects),
        "sirs_reported": sirs,
    }


if __name__ == "__main__":
    fields = safe_fields(LAYERS["property"], PROPERTY_FIELDS)
    building = building_summary("0141370780001", fields)
    if not building:
        raise SystemExit("Sin resultados para ese folio")
    recert_fields = safe_fields(LAYERS["recert"], RECERT_FIELDS)
    building["recert"] = recert_records(building, recert_fields)
    building["recert_by_tower"] = recert_by_tower(building, building["recert"])
    building["recert_light"] = building_light(building["recert_by_tower"])
    permit_fields = safe_fields(LAYERS["ibuild_permits"], PERMIT_FIELDS)
    building["open_permits"] = open_permits(building, permit_fields)
    point = building_point(building)
    building["flood"] = flood_zone(point)
    building["coast"] = coast_distance(point)
    for tower in building["recert_by_tower"].values():
        tower["next_due"] = next_recert(building, tower)
    building["dbpr"] = dbpr_info(building)
    building["folios"] = building["folios"][:5]
    print(json.dumps(building, indent=2, ensure_ascii=False))

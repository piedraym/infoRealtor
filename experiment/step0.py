import json
import re
from collections import Counter

import requests

# ---------------------------------------------------------------------------
# 1. Fuentes de datos (servicios ArcGIS públicos)
# ---------------------------------------------------------------------------

COUNTY = "https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/ArcGIS/rest/services"
CITY = "https://services1.arcgis.com/CvuPhqcTQpZPT9qY/arcgis/rest/services"

LAYERS = {
    "property": f"{COUNTY}/PaGISView_gdb/FeatureServer/0",
    "recert": f"{CITY}/40_Year_Recertification/FeatureServer/0",
    # "permits": f"{CITY}/Building_Permits_Since_2014/FeatureServer/0",
    # "open_violations": f"{COUNTY}/Open_Building_Violations/FeatureServer/0",
    # "closed_violations_5y": f"{COUNTY}/Closed_Building_Violations_(Past_5_years)/FeatureServer/0",
    # "flood": f"{COUNTY}/FEMAFloodZone_gdb/FeatureServer/0",
    # "shoreline": f"{COUNTY}/Shoreline_gdb/FeatureServer/0",
}

# Licencias: las de la Ciudad están publicadas como CC BY 4.0 (uso comercial
# permitido citando la fuente). Las del condado hay que revisarlas una a una en
# su página del portal (gis-mdc.opendata.arcgis.com) antes de cobrar.
LICENSES = {
    "recert": "CC BY 4.0 (Ciudad de Miami)",
    "permits": "CC BY 4.0 (Ciudad de Miami)",
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
]


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


if __name__ == "__main__":
    fields = safe_fields(LAYERS["property"], PROPERTY_FIELDS)
    building = building_summary("0142070010001", fields)
    if not building:
        raise SystemExit("Sin resultados para ese folio")
    building["folios"] = building["folios"][:5]
    print(json.dumps(building, indent=2, ensure_ascii=False))

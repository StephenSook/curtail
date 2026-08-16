"""Join the Board's own point-of-diversion coordinates onto the curtailed rights.

Split the way the deployment probe and the chaos recorder are split, and for the same
reason: the half that needs the network writes a committed artifact, and the half that
CI runs needs no network at all. A suite that reaches a state agency is a suite that goes
red when somebody else has a bad afternoon.

**The owner column is dropped, and that is a decision this repository already made.**
`rights_scott_addendum12.json` records it verbatim: "The attachment's Primary Owner
column is deliberately not read. No third-party personal data enters this repository, and
a public source does not change that." The CalWATRS response carries `primary_owner` on
every feature. It is stripped here rather than filtered later, so it never lands on disk.
Where a diversion is and whether it is curtailed is the whole demonstration; who owns it
adds nothing and is somebody's name.

**139 of the 469 curtailed rights can never get a coordinate and every one is
groundwater.** The Board's own dataset description says so: "Ground water extraction
points (such as water supply wells) are generally not included in this dataset." So the
join is structurally incomplete, the shortfall is counted here, and the console prints
that count. A map that silently showed 330 of 469 would under-report who is curtailed,
which is the calm-wrong-answer failure this console exists to avoid.

    uv run python scripts/build_diversions.py          # fetch and write
    uv run python scripts/build_diversions.py --check  # verify, offline
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "core" / "src" / "curtail_core" / "data"
OUT = DATA / "diversions.json"

#: The Board's CalWATRS point-of-diversion layer, current and updated daily.
#:
#: NOT the eWRIMS flat files. Those are roughly a year stale, are 61 MB, and one of them
#: carries a Scott right with a blank coordinate that this layer has. `outSR=4326` is
#: load-bearing: the Hub's bulk GeoJSON download is EPSG:3310 and its geometry is
#: California Albers METRES, which fed to a map puts every point off the planet.
SERVICE = (
    "https://services.arcgis.com/Wvy1i2QPQwOxNumH/arcgis/rest/services"
    "/point_of_diversion/FeatureServer/0/query"
)

#: The watershed each basin's rights are EXPECTED to sit in. Used to report a
#: disagreement, never to filter, see `_fetch`.
EXPECTED_HUC8 = {"scott": "Scott", "shasta": "Shasta"}

#: Fields requested. `primary_owner` is deliberately absent, see the module docstring.
FIELDS = "wr_id,app_type,subtype,county_id,huc8_name,huc12_name,diversion_type,diversion_status"

#: California, generously. A coordinate outside this is a projection error rather than a
#: diversion, and the EPSG:3310 trap produces exactly that.
BOUNDS = {"lon": (-125.0, -113.5), "lat": (32.0, 42.5)}


class BuildError(RuntimeError):
    """The artifact could not be built or does not hold together."""


def _rights(basin: str) -> dict[str, dict[str, Any]]:
    """The curtailed rights for a basin, keyed by application number."""
    name = "rights_scott_addendum12" if basin == "scott" else "rights_shasta_addendum6"
    payload = json.loads((DATA / f"{name}.json").read_text())
    return {r["application_number"]: r for r in payload["rights"]}


def _fetch(application_numbers: list[str]) -> list[dict[str, Any]]:
    """Every POD record for the rights we hold, queried BY RIGHT rather than by watershed.

    **Filtering by watershed loses rights the Board itself codes elsewhere.** The first
    version asked for HUC8 18010208 and 18010207 and came back one Scott right short.
    `S027847` is listed in Order WR 2024-0024-DWR Addendum 12 as a curtailed Scott right
    in group 8, and the Board's own point-of-diversion layer codes its POD in HUC8
    18010206, Upper Klamath. Both statements are the Board's.

    Widening the watershed filter would have hidden that. Asking for the rights we
    actually hold makes the watershed a REPORTED FIELD rather than a search key, so a
    disagreement between two of the Board's datasets surfaces as a labelled finding
    instead of a silently missing dot. That disagreement is the thing this system exists
    to make visible.
    """
    features: list[dict[str, Any]] = []
    # POST, not GET. A WHERE clause naming 469 ids is about 8 KB of URL, and this
    # service answers a long GET with **404**, not 414: the status describes the wrong
    # cause entirely, so a chunk size tuned by trial would look like a bad endpoint.
    # Measured: 200 at 1,216 characters, 404 at 2,176. POST has no such limit.
    #
    # Still chunked, because the layer caps a RESPONSE at 2000 records and a future
    # basin could exceed it. 200 ids keeps that impossible while staying few requests.
    for start in range(0, len(application_numbers), 200):
        chunk = application_numbers[start : start + 200]
        ids = ",".join(f"'{number}'" for number in chunk)
        body = urllib.parse.urlencode(
            {
                "where": f"wr_id IN ({ids})",
                "outFields": FIELDS,
                "outSR": "4326",
                "f": "geojson",
            }
        ).encode()
        request = urllib.request.Request(
            SERVICE,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            if response.status != 200:
                raise BuildError(f"CalWATRS returned HTTP {response.status}")
            payload = json.loads(response.read().decode())
        if "error" in payload:
            # ArcGIS reports failures INSIDE a 200 body. A caller checking only the
            # status code writes an error object to disk as though it were data.
            raise BuildError(f"CalWATRS returned an error object: {payload['error']}")
        chunk_features = payload.get("features")
        if not isinstance(chunk_features, list):
            raise BuildError("CalWATRS returned no features array")
        features.extend(chunk_features)

    if not features:
        raise BuildError("CalWATRS returned no features. An empty layer is not zero diversions.")
    return features


def _point(feature: dict[str, Any]) -> tuple[float, float] | None:
    """`(lon, lat)` if the geometry is a usable WGS84 point, else None.

    `float()` and `isfinite()` rather than a truthiness check, because the Board's own
    files carry the literal string "NaN" for every groundwater row and `"NaN"` is a
    perfectly non-empty string. That exact trap reported 8,000 rows as having
    coordinates during research.
    """
    geometry = feature.get("geometry") or {}
    coordinates = geometry.get("coordinates")
    if geometry.get("type") != "Point" or not isinstance(coordinates, list | tuple):
        return None
    if len(coordinates) < 2:
        return None
    try:
        lon, lat = float(coordinates[0]), float(coordinates[1])
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lon) and math.isfinite(lat)):
        return None
    if not (BOUNDS["lon"][0] <= lon <= BOUNDS["lon"][1]):
        return None
    if not (BOUNDS["lat"][0] <= lat <= BOUNDS["lat"][1]):
        return None
    return lon, lat


def build() -> dict[str, Any]:
    wanted = sorted({a for basin in ("scott", "shasta") for a in _rights(basin)})
    features = _fetch(wanted)
    out: dict[str, Any] = {
        "source": {
            "service": SERVICE,
            "layer": "California State Water Resources Control Board, points of diversion",
            "licence": "Public domain (data.ca.gov license_id other-pd)",
            "attribution": "California State Water Resources Control Board",
            "projection": "EPSG:4326, requested explicitly",
        },
        "privacy": (
            "The primary_owner field is not requested and never written. No third-party "
            "personal data enters this repository, and a public source does not change "
            "that. Matches the same decision recorded in the rights tables."
        ),
        "basins": {},
    }

    # A right maps to MANY points, not one.
    #
    # **The first version kept a dict keyed by right and silently discarded every point
    # but the last.** `S010583` has two: one in Scott, Siskiyou County, and one in
    # Lower Sacramento, 200 miles south. The dict kept the Sacramento one, and the
    # artifact reported a curtailed Scott right whose diversion sat near Sacramento,
    # which read as an error in the Board's data and was an error in this code. Same
    # shape as any one-key-overwrites-another collision: the loss is invisible because
    # what survives looks perfectly well formed.
    #
    # Multiple points of diversion are ordinary in California water law. An order
    # curtails a RIGHT, and the right may divert in several places.
    by_id: dict[str, list[dict[str, Any]]] = {}
    for feature in features:
        properties = feature.get("properties") or {}
        wr_id = properties.get("wr_id")
        point = _point(feature)
        if isinstance(wr_id, str) and point is not None:
            by_id.setdefault(wr_id, []).append(
                {
                    "lon": round(point[0], 6),
                    "lat": round(point[1], 6),
                    # Reported, never used to filter.
                    "huc8_name": properties.get("huc8_name"),
                }
            )

    for basin in ("scott", "shasta"):
        rights = _rights(basin)
        located: list[dict[str, Any]] = []
        missing: list[str] = []
        discrepancies: list[str] = []
        for application_number, record in sorted(rights.items()):
            points = by_id.get(application_number)
            if not points:
                missing.append(application_number)
                continue
            entry: dict[str, Any] = {
                "application_number": application_number,
                "points": sorted(points, key=lambda p: (p["lon"], p["lat"])),
            }
            if "curtailment_group" in record:
                entry["curtailment_group"] = record["curtailment_group"]
            if "band" in record:
                entry["band"] = record["band"]

            watersheds = {p.get("huc8_name") for p in points}
            if EXPECTED_HUC8[basin] not in watersheds:
                # A discrepancy is "NO point of this right sits in the basin the order
                # names", not "some point sits elsewhere". A right diverting in two
                # watersheds is ordinary; a right the order places in Scott whose every
                # diversion is somewhere else is a disagreement between two of the
                # Board's own datasets. Surfaced, not corrected, and not dropped.
                entry["watershed_discrepancy"] = {
                    "order_says": EXPECTED_HUC8[basin],
                    "diversion_layer_says": sorted(w for w in watersheds if w),
                }
                discrepancies.append(application_number)
            located.append(entry)

        # Every miss should be groundwater. If one is not, the join is wrong rather than
        # the dataset being incomplete, and those are different problems.
        groundwater = [a for a in missing if a.startswith("SG")]
        out["basins"][basin] = {
            "rights_total": len(rights),
            "located": located,
            "without_coordinate": len(missing),
            "without_coordinate_groundwater": len(groundwater),
            "watershed_discrepancies": sorted(discrepancies),
            "watershed_discrepancy_note": (
                "The curtailment order lists these rights in this basin. The Board's own "
                "point-of-diversion layer places the diversion in a different watershed. "
                "Both statements are the Board's, and this system reports the "
                "disagreement rather than resolving it."
            ),
            "without_coordinate_note": (
                "The Board's own dataset excludes groundwater extraction points: "
                "'Ground water extraction points (such as water supply wells) are "
                "generally not included in this dataset.' Every right below is a "
                "groundwater right, so this is a limit of the source and not a gap in "
                "the join."
            ),
        }
    return out


def check() -> list[str]:
    """Everything verifiable about the committed artifact, without a network call."""
    problems: list[str] = []
    if not OUT.exists():
        return [f"{OUT.name} is missing. Run scripts/build_diversions.py."]

    payload = json.loads(OUT.read_text())

    # Scanned on the DATA's keys, never on the file's text. Scanning the text matched
    # this file's own privacy note explaining that the owner field is never written,
    # which is the fifth time in this repository that a scanner has found its own
    # needle. A guard whose subject is a field must look at fields.
    owner_like = {"primary_owner", "owner", "name", "contact", "address", "phone", "email"}
    for basin, section in payload.get("basins", {}).items():
        for entry in section.get("located", []):
            leaked = owner_like & set(entry)
            if leaked:
                problems.append(f"{basin}: an entry carries personal fields {sorted(leaked)}")
                break

    for basin in ("scott", "shasta"):
        section = payload["basins"].get(basin)
        if section is None:
            problems.append(f"{basin} is absent")
            continue

        rights = _rights(basin)
        if section["rights_total"] != len(rights):
            problems.append(
                f"{basin} claims {section['rights_total']} rights, the table holds {len(rights)}"
            )

        located = section["located"]
        if len(located) + section["without_coordinate"] != len(rights):
            problems.append(
                f"{basin}: {len(located)} located plus {section['without_coordinate']} "
                f"missing does not reconcile to {len(rights)}"
            )
        if section["without_coordinate"] != section["without_coordinate_groundwater"]:
            problems.append(
                f"{basin}: {section['without_coordinate']} rights lack a coordinate but "
                f"only {section['without_coordinate_groundwater']} are groundwater. The "
                "rest are a join failure, not a source limitation."
            )

        for entry in located:
            number = entry["application_number"]
            if number not in rights:
                problems.append(f"{basin}: {number} is mapped but is not a curtailed right")
            if not entry.get("points"):
                problems.append(f"{basin}: {number} is listed as located with no points")
            for point in entry.get("points", []):
                lon, lat = point["lon"], point["lat"]
                if not (BOUNDS["lon"][0] <= lon <= BOUNDS["lon"][1]):
                    problems.append(f"{basin}: {number} longitude {lon} is outside California")
                if not (BOUNDS["lat"][0] <= lat <= BOUNDS["lat"][1]):
                    problems.append(f"{basin}: {number} latitude {lat} is outside California")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify the artifact, offline")
    args = parser.parse_args(argv)

    if args.check:
        problems = check()
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        if problems:
            return 1
        payload = json.loads(OUT.read_text())
        for basin, section in payload["basins"].items():
            print(
                f"{basin}: {len(section['located'])} of {section['rights_total']} located, "
                f"{section['without_coordinate']} groundwater without a coordinate"
            )
        return 0

    try:
        payload = build()
    except (BuildError, OSError, ValueError) as exc:
        print(f"could not build: {exc}", file=sys.stderr)
        return 1

    OUT.write_text(json.dumps(payload, indent=2) + "\n")
    for basin, section in payload["basins"].items():
        print(
            f"{basin}: {len(section['located'])} of {section['rights_total']} located, "
            f"{section['without_coordinate']} without a coordinate "
            f"({section['without_coordinate_groundwater']} groundwater)"
        )
    print(f"wrote {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

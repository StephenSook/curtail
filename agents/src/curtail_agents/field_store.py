"""Field measurements: append-only evidence submitted from a device.

**This is the correction path, and it is the reason the mobile layer exists at all.**
The public record of July 2025: the Fort Jones gage read below the July minimum and
Addendum 7 reinstated curtailment, community members disputed the measurement, the
Watermaster District took additional field flows and sent them to USGS, USGS shifted the
rating curve, flows read above 75 cfs, and Addendum 8 lifted curtailment on July 22.
An agent architecture that cannot be corrected by the person responsible for it is not
governed, so the correction path is the design rather than a feature beside it.

**Three properties, each load-bearing.**

1. **Append-only.** A measurement is evidence, never an edit to state. There is no
   update and no delete, which removes the entire class of write conflicts that an
   offline queue would otherwise create, and it means a device that has been out of
   signal for a day can sync in any order without losing or overwriting anything.

2. **Idempotent on a client-generated key.** The queue survives app termination and
   iOS has no Background Sync, so a flush can be interrupted and retried. The same
   measurement submitted twice is stored once. Dedup is on the key the DEVICE chose,
   because the server cannot tell a retry from a genuinely repeated reading.

3. **It confers no authority.** Hard rule 14: the device authenticates a human and
   submits evidence. It never mutates an order, never signs, never writes the rights
   ledger. Appending here changes what an official can SEE, and nothing else. The
   Allocation Core may be re-run against a field reading, and the result is labelled
   as attested by an observer rather than fetched from USGS, because a number a person
   typed on a riverbank is not the same kind of fact as a gage record.

**The photograph is hashed, not uploaded.** The device records that a photo was taken
and sends its SHA-256 and byte length; the image stays on the phone. That keeps a
demonstration datastore free of imagery of private land while still binding the
measurement to a specific artifact the observer holds, which is the part evidence
integrity actually needs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import isfinite
from typing import Any, Protocol

from curtail_core.basins import Basin

#: One document per basin, mirroring the season ledger.
COLLECTION = "field_measurements"

#: A reading outside this is a typo or a unit error, not a river.
MAX_CFS = 100_000.0

#: Enough entropy to be unique per device without being a device identifier.
KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,128}$")

#: Lowercase hex sha-256, or nothing at all.
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class FieldEvidenceError(ValueError):
    """The submission is not acceptable evidence, and the reason says which part."""


class FieldStoreUnavailableError(RuntimeError):
    """The durable store is configured and unreachable.

    Raised rather than degrading to memory. A measurement silently accepted into a
    store that forgets it is worse than a refusal: the observer walks away from the
    river believing the record exists.
    """


@dataclass(frozen=True, slots=True)
class FieldMeasurement:
    """One attested reading, taken by a named human, at a place and a time."""

    idempotency_key: str
    basin: Basin
    observer: str
    discharge_cfs: float
    captured_at: datetime
    instrument_note: str = ""
    latitude: float | None = None
    longitude: float | None = None
    photo_sha256: str | None = None
    photo_bytes: int | None = None

    def to_state(self) -> dict[str, Any]:
        return {
            "idempotency_key": self.idempotency_key,
            "basin": self.basin.value,
            "observer": self.observer,
            "discharge_cfs": self.discharge_cfs,
            "captured_at": self.captured_at.isoformat(),
            "instrument_note": self.instrument_note,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "photo_sha256": self.photo_sha256,
            "photo_bytes": self.photo_bytes,
        }


def measurement_from(payload: dict[str, Any], basin: Basin) -> FieldMeasurement:
    """Validate a submission into a measurement, or refuse it with a reason.

    Every refusal names the field, because the person reading it is standing in a river
    holding a phone and a flow meter, and "invalid request" is useless there.
    """
    key = str(payload.get("idempotency_key", "")).strip()
    if not KEY_PATTERN.match(key):
        raise FieldEvidenceError(
            "idempotency_key must be 8 to 128 characters of letters, digits, hyphen or "
            "underscore. The device generates it so a retried sync stores once."
        )

    observer = str(payload.get("observer", "")).strip()
    if not 2 <= len(observer) <= 120:
        raise FieldEvidenceError("observer must name the person who took the reading")
    # Length was the ONLY check, and this endpoint takes unauthenticated writes that are
    # stored durably and rendered on a page. 120 characters is ample for a script tag, so
    # the second layer is a character class: a person's name has no angle brackets, no
    # ampersands, no quotes and no control characters. The rendering side was fixed too;
    # neither layer is trusted alone.
    if any(character in observer for character in "<>&\"'`"):
        raise FieldEvidenceError(
            "observer carries markup characters. A name does not contain < > & \" ' or `"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in observer):
        raise FieldEvidenceError("observer carries control characters")

    raw_cfs = payload.get("discharge_cfs")
    try:
        cfs = float(raw_cfs)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise FieldEvidenceError("discharge_cfs must be a number") from exc
    if not isfinite(cfs):
        raise FieldEvidenceError(
            "discharge_cfs must be finite. NaN compares false against every threshold, "
            "so it would read as a river above its minimum."
        )
    if cfs < 0:
        raise FieldEvidenceError("discharge_cfs cannot be negative")
    if cfs > MAX_CFS:
        raise FieldEvidenceError(f"discharge_cfs above {MAX_CFS:,.0f} is a unit error")

    captured_raw = str(payload.get("captured_at", "")).strip()
    try:
        captured = datetime.fromisoformat(captured_raw)
    except ValueError as exc:
        raise FieldEvidenceError("captured_at must be an ISO 8601 timestamp") from exc
    if captured.tzinfo is None:
        raise FieldEvidenceError(
            "captured_at must carry an offset. A naive timestamp on a compliance record "
            "is a timestamp somebody will later read in the wrong zone."
        )

    digest = payload.get("photo_sha256")
    if digest is not None:
        digest = str(digest).strip().lower()
        if not DIGEST_PATTERN.match(digest):
            raise FieldEvidenceError("photo_sha256 must be lowercase hex sha-256, or absent")

    return FieldMeasurement(
        idempotency_key=key,
        basin=basin,
        observer=observer,
        discharge_cfs=cfs,
        captured_at=captured,
        instrument_note=str(payload.get("instrument_note", "")).strip()[:500],
        latitude=_coordinate(payload.get("latitude"), "latitude", 90.0),
        longitude=_coordinate(payload.get("longitude"), "longitude", 180.0),
        photo_sha256=digest,
        photo_bytes=_positive_int(payload.get("photo_bytes"), "photo_bytes"),
    )


def _coordinate(value: Any, name: str, limit: float) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise FieldEvidenceError(f"{name} must be a number or absent") from exc
    if not isfinite(number) or abs(number) > limit:
        raise FieldEvidenceError(f"{name} must be within +/-{limit:g}")
    return number


def _positive_int(value: Any, name: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise FieldEvidenceError(f"{name} must be a whole number or absent") from exc
    if number < 0:
        raise FieldEvidenceError(f"{name} cannot be negative")
    return number


class FieldStore(Protocol):
    @property
    def durable(self) -> bool: ...
    def describe(self) -> str: ...
    def append(self, measurement: FieldMeasurement) -> tuple[list[dict[str, Any]], bool]: ...
    def load(self, basin: Basin) -> list[dict[str, Any]]: ...


@dataclass
class InMemoryFieldStore:
    """Process memory, and it says so.

    Exists for tests and for a developer with no cloud credentials. It reports
    `durable = False` with a reason, so a caller can never read an empty list from it
    as "no measurements have been taken".
    """

    _rows: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    @property
    def durable(self) -> bool:
        return False

    def describe(self) -> str:
        return (
            "process memory: no project is configured, so submitted evidence lives only "
            "in this instance and does not survive a restart"
        )

    def append(self, measurement: FieldMeasurement) -> tuple[list[dict[str, Any]], bool]:
        rows = self._rows.setdefault(measurement.basin.value, [])
        if any(r["idempotency_key"] == measurement.idempotency_key for r in rows):
            return list(rows), False
        rows.append(measurement.to_state())
        return list(rows), True

    def load(self, basin: Basin) -> list[dict[str, Any]]:
        return list(self._rows.get(basin.value, []))


class FirestoreFieldStore:
    """The durable one, appending under a transaction.

    Two instances can accept two measurements in the same second, and a read-then-write
    without a transaction loses whichever lands second. Losing a field measurement is
    losing the correction path, which is the one thing this surface exists for.
    """

    def __init__(self, project: str, client: Any | None = None) -> None:
        self._project = project
        if client is not None:
            self._client = client
            return
        try:
            import google.cloud.firestore as firestore
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise FieldStoreUnavailableError(
                f"google-cloud-firestore is not importable ({exc})"
            ) from exc
        try:
            self._client = firestore.Client(project=project)
        except Exception as exc:  # pragma: no cover - credential shape varies
            raise FieldStoreUnavailableError(
                f"the Firestore client could not be built for {project}: {exc}"
            ) from exc

    @property
    def durable(self) -> bool:
        return True

    def describe(self) -> str:
        return (
            f"Cloud Firestore, native mode, project {self._project}, collection "
            f"`{COLLECTION}`. Append-only, one document per basin, deduplicated on the "
            "idempotency key the device generated."
        )

    def _document(self, basin: Basin) -> Any:
        return self._client.collection(COLLECTION).document(basin.value)

    def load(self, basin: Basin) -> list[dict[str, Any]]:
        try:
            snapshot = self._document(basin).get()
        except Exception as exc:
            raise FieldStoreUnavailableError(
                f"field measurements for {basin.value} could not be read: {exc}. "
                "Reported rather than returned empty, which would read as a basin "
                "nobody has been out to."
            ) from exc
        if not getattr(snapshot, "exists", False):
            return []
        raw = snapshot.to_dict() or {}
        rows = raw.get("measurements", [])
        return list(rows)

    def append(self, measurement: FieldMeasurement) -> tuple[list[dict[str, Any]], bool]:
        document = self._document(measurement.basin)
        appended = False

        def _txn(transaction: Any) -> list[dict[str, Any]]:
            nonlocal appended
            snapshot = document.get(transaction=transaction)
            rows = (
                list((snapshot.to_dict() or {}).get("measurements", [])) if snapshot.exists else []
            )
            if any(r.get("idempotency_key") == measurement.idempotency_key for r in rows):
                appended = False
                return rows
            rows.append(measurement.to_state())
            appended = True
            transaction.set(document, {"measurements": rows, "basin": measurement.basin.value})
            return rows

        try:
            import google.cloud.firestore as firestore

            transactional = firestore.transactional(_txn)
            rows = transactional(self._client.transaction())
        except FieldStoreUnavailableError:
            raise
        except Exception as exc:
            raise FieldStoreUnavailableError(
                f"the measurement could not be appended: {exc}. Refused rather than "
                "acknowledged, so the observer does not walk away from the river "
                "believing a record exists."
            ) from exc
        return list(rows), appended


def store_for(project: str | None = None, *, client: Any | None = None) -> FieldStore:
    """The durable store when a project is configured, memory when none is.

    **Fails closed.** If a project IS set and Firestore cannot be built, this raises
    rather than handing back memory, for the same reason the season store does: a
    silent downgrade turns a durability guarantee into a coin flip nobody is told about.
    """
    import os

    chosen = project if project is not None else os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    if not chosen:
        return InMemoryFieldStore()
    return FirestoreFieldStore(chosen, client=client)


def evidence_payload(store: FieldStore, basin: Basin) -> dict[str, Any]:
    """What the field route and the console both read."""
    rows = store.load(basin)
    latest = max(rows, key=lambda r: str(r.get("captured_at", "")), default=None)
    return {
        "basin": basin.value,
        "durable": store.durable,
        "store": store.describe(),
        "count": len(rows),
        "latest": latest,
        "measurements": rows,
        "disclaimer": (
            "Attested field evidence, not a gage record. It is appended and never "
            "edits an order. 23 CCR 875(b) vests the determination in a named human "
            "official, and a measurement changes what that official can see."
        ),
        "as_of": datetime.now(UTC).isoformat(timespec="seconds"),
    }

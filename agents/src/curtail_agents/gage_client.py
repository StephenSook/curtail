"""USGS Water Data client for the Scott and Shasta compliance gages.

Reads the OGC API at api.waterdata.usgs.gov. Three collections matter:

    latest-continuous      current discharge, the trigger for everything
    time-series-revisions  retroactive corrections to already-published values
    field-measurements     the physical measurements behind a rating change

The revisions collection is why REVISION_IMPACT is a real subscription rather
than a guess. USGS publishes corrections to approved data, and a correction can
retroactively change whether a past curtailment decision was justified. That is
not hypothetical: on July 22, 2025 the Watermaster District's field flows moved
the Fort Jones rating curve, readings went above 75 cfs, and Addendum 8 lifted
curtailment across the Scott River watershed. The sensor said curtail, a human
went and measured, and the authoritative number changed.

Rate limits: 100 requests per hour per IP without a key, 1,000 with one. The
key is optional and the client works without it, which matters because a judge
running this from a clean checkout has no key.

The legacy waterservices endpoint remains as a fallback. USGS has stated it
will be decommissioned in the first quarter of 2027, with no intentional
degradation before August 2026, so it is a fallback and not a primary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from typing import Any, Final, Literal

import httpx

OGC_BASE: Final = "https://api.waterdata.usgs.gov/ogcapi/v0"
LEGACY_BASE: Final = "https://waterservices.usgs.gov/nwis/iv/"

#: Discharge, cubic feet per second. The only parameter the regulation keys on.
PARAM_DISCHARGE: Final = "00060"

#: The API caps a single page at 50,000 items.
MAX_LIMIT: Final = 50_000

Collection = Literal[
    "latest-continuous",
    "continuous",
    "time-series-revisions",
    "field-measurements",
]


class GageError(RuntimeError):
    """Raised when the gage cannot be read.

    Deliberately loud. A curtailment recommendation built on a silently
    defaulted or stale discharge value is worse than no recommendation, so
    nothing in this module returns a placeholder reading on failure.
    """


@dataclass(frozen=True, slots=True)
class Reading:
    """One discharge observation at a compliance gage."""

    monitoring_location_id: str
    observed_at: datetime
    cfs: float
    unit: str
    qualifier: str | None
    #: True when the value came from a cached snapshot rather than a live call.
    from_cache: bool = False

    def __post_init__(self) -> None:
        """Reject readings that are not physically meaningful discharge values.

        Two confirmed catastrophic paths, in opposite directions.

        A NaN fails every comparison, so `is_below_minimum` and
        `is_near_threshold` both return False and the engine reports the river
        is above its minimum and not even worth field-verifying, on a reading
        that does not exist. Python's json module parses bare NaN literals, so
        this arrives straight off the wire.

        And -999999 is the standard NWIS no-data sentinel. Passed through, it
        curtails the entire watershed at maximum confidence off a dead sensor,
        and sits far outside the near-threshold band so it is not even flagged
        for field verification.
        """
        if not isfinite(self.cfs):
            raise GageError(
                f"{self.monitoring_location_id}: discharge is not finite ({self.cfs!r}). "
                "A non-finite reading fails every comparison silently and would be "
                "reported as a river above its minimum."
            )
        if self.cfs < 0:
            raise GageError(
                f"{self.monitoring_location_id}: discharge cannot be negative "
                f"({self.cfs}). -999999 is the NWIS no-data sentinel; passing it "
                "through would curtail the watershed off a dead sensor."
            )
        if self.unit != "ft^3/s":
            raise GageError(
                f"{self.monitoring_location_id}: expected discharge in ft^3/s, got "
                f"{self.unit!r}. The regulation's minimums are in cubic feet per second, "
                "and a value in other units would be compared as though it were cfs."
            )

    @property
    def is_provisional(self) -> bool:
        """Provisional data has not completed USGS review and can be revised.

        Surfaced rather than hidden: an order resting on a provisional value
        carries a different risk profile than one resting on approved record.
        """
        return bool(self.qualifier and "P" in self.qualifier.upper())


@dataclass(frozen=True, slots=True)
class Revision:
    """A retroactive correction to previously published data."""

    monitoring_location_id: str
    revised_at: datetime | None
    explanation: str | None
    raw: dict[str, Any]


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    # The API returns offset-aware ISO 8601. Normalize to UTC so downstream
    # comparisons never mix naive and aware datetimes, which ruff's DTZ rules
    # exist to prevent and which would silently misorder a season timeline.
    parsed = datetime.fromisoformat(value)
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class GageClient:
    """Async client for the USGS OGC API.

    Usage:
        async with GageClient() as client:
            reading = await client.latest_discharge("USGS-11519500")
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = OGC_BASE,
        timeout: float = 20.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout,
            headers={"Accept": "application/json"},
            # Retry transport handles transient connection resets. A dropped
            # connection must never be mistaken for "the river is fine".
            transport=httpx.AsyncHTTPTransport(retries=3),
        )

    async def __aenter__(self) -> GageClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _params(self, **kwargs: Any) -> dict[str, Any]:
        params: dict[str, Any] = {"f": "json", **kwargs}
        if self._api_key:
            params["api_key"] = self._api_key
        return params

    async def _get(self, collection: Collection, **params: Any) -> dict[str, Any]:
        url = f"{self._base_url}/collections/{collection}/items"
        try:
            response = await self._client.get(url, params=self._params(**params))
        except httpx.HTTPError as exc:
            raise GageError(f"{collection} request failed: {exc}") from exc

        if response.status_code != 200:
            raise GageError(f"{collection} returned HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            # A 200 carrying a captive-portal or proxy interstitial is not JSON.
            raise GageError(f"{collection} returned a non-JSON body: {exc}") from exc

        if not isinstance(payload, dict):
            raise GageError(f"{collection} returned {type(payload).__name__}, expected an object")
        if "features" not in payload:
            raise GageError(f"{collection} response has no features key")
        result: dict[str, Any] = payload
        return result

    async def latest_discharge(self, monitoring_location_id: str) -> Reading:
        """Return the most recent discharge reading for a gage.

        Raises:
            GageError: on transport failure, non-200, or an empty feature set.
                An empty result is an error, not a zero. A river that reports
                no data is not a river reading zero cfs, and conflating the two
                would curtail an entire watershed off a missing sensor.
        """
        payload = await self._get(
            "latest-continuous",
            monitoring_location_id=monitoring_location_id,
            parameter_code=PARAM_DISCHARGE,
            limit=1,
        )
        features = payload["features"]
        if not features:
            raise GageError(
                f"no discharge reading for {monitoring_location_id}. "
                "An absent reading is not a reading of zero."
            )

        props = features[0].get("properties") or {}
        observed_at = _parse_time(props.get("time"))
        raw_value = props.get("value")
        if observed_at is None or raw_value is None:
            raise GageError(f"incomplete reading for {monitoring_location_id}: {props}")

        return Reading(
            monitoring_location_id=monitoring_location_id,
            observed_at=observed_at,
            cfs=float(raw_value),
            unit=str(props.get("unit_of_measure") or "ft^3/s"),
            qualifier=props.get("qualifier"),
        )

    async def discharge_series(
        self,
        monitoring_location_id: str,
        *,
        since: datetime,
        until: datetime | None = None,
        limit: int = 20_000,
    ) -> list[Reading]:
        """Every discharge reading in a window, oldest first.

        The `continuous` collection was declared in `Collection` from the beginning and
        nothing called it, so the only shape of the river this system could show was a
        single point. A hydrograph is the difference between "the river is at 4.91 cfs"
        and "the river fell from 17.9 to 4.91 over four weeks", and the second is what a
        watermaster actually reasons about.

        Readings that cannot be parsed are DROPPED rather than defaulted, and the count
        of survivors is what the caller gets. A series is a shape, and one interpolated
        point inside it is invisible in a way a single bad reading is not: it would be
        drawn as though it were measured.

        Raises:
            GageError: on transport failure or non-200. An EMPTY window is not an error
                here, unlike a missing latest reading: a gage genuinely may not have
                reported over the range asked for, and the caller can say so.
        """
        window = f"{since.strftime('%Y-%m-%dT%H:%M:%SZ')}/"
        window += until.strftime("%Y-%m-%dT%H:%M:%SZ") if until else ".."
        payload = await self._get(
            "continuous",
            monitoring_location_id=monitoring_location_id,
            parameter_code=PARAM_DISCHARGE,
            datetime=window,
            limit=min(limit, MAX_LIMIT),
        )

        out: list[Reading] = []
        for feature in payload["features"]:
            props = feature.get("properties") or {}
            observed_at = _parse_time(props.get("time"))
            raw_value = props.get("value")
            if observed_at is None or raw_value is None:
                continue
            try:
                cfs = float(raw_value)
            except (TypeError, ValueError):
                continue
            try:
                out.append(
                    Reading(
                        monitoring_location_id=monitoring_location_id,
                        observed_at=observed_at,
                        cfs=cfs,
                        unit=str(props.get("unit_of_measure") or "ft^3/s"),
                        qualifier=props.get("qualifier"),
                    )
                )
            except ValueError:
                # `Reading` refuses NaN, infinities and negatives. A sensor fault must
                # not enter a series that a human will read a trend off.
                continue
        out.sort(key=lambda r: r.observed_at)
        return out

    async def revisions(self, monitoring_location_id: str, *, limit: int = 100) -> list[Revision]:
        """Return published revisions for a gage's time series.

        This is the REVISION_IMPACT feed. A hit here means a value that may
        already have justified a signed order has been superseded, so the
        Season Ledger must surface which orders rested on the old number.
        """
        payload = await self._get(
            "time-series-revisions",
            monitoring_location_id=monitoring_location_id,
            limit=min(limit, MAX_LIMIT),
        )
        out: list[Revision] = []
        for feature in payload["features"]:
            props = feature.get("properties") or {}
            out.append(
                Revision(
                    monitoring_location_id=monitoring_location_id,
                    revised_at=_parse_time(props.get("time") or props.get("revision_time")),
                    explanation=props.get("explanation") or props.get("description"),
                    raw=props,
                )
            )
        return out

    async def field_measurements(
        self, monitoring_location_id: str, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Return physical field measurements behind rating-curve changes.

        These are the measurements a watermaster standing in the river produces.
        They are the evidence trail for why an authoritative number moved.
        """
        payload = await self._get(
            "field-measurements",
            monitoring_location_id=monitoring_location_id,
            limit=min(limit, MAX_LIMIT),
        )
        return [f.get("properties") or {} for f in payload["features"]]

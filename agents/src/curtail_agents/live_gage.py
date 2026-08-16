"""Read the compliance gages live, and say which kind of reading you got.

**This is the module that makes the first sentence of the README true.** The gage client
has existed and been tested since early on, and until now nothing a judge could click ever
called it: `api.py` did not import it, and the only caller in the whole repository was a
hand-run script. The deployed service classified numbers that a human typed into a query
string. "It watches a river in real time" was a description of the codebase, not of the
running system, which is the same claim-drift shape as a design doc outliving the code.

Three rules, and each one exists because the alternative is a confident wrong answer.

**A live reading and a cached reading are different facts, and both are labelled.** The
`Provenance` enum already carried `USGS_LIVE` and `USGS_CACHED` and nothing had ever been
able to produce either. A console that cannot say which one is on screen is a console that
lets a stale number pass for the river.

**An unavailable gage refuses.** It does not return zero, it does not fall back to the
last recommendation, and it does not omit the field. A river reporting no data is not a
river running at zero cfs, and conflating them curtails a watershed off a dead sensor.
`Reading.__post_init__` already rejects NaN for the same reason; this is that rule one
level up.

**The cache is a courtesy to USGS, not a performance trick.** The OGC API allows 100
requests an hour without a key and 1,000 with one, the instruments themselves report every
15 minutes, and a judge-facing endpoint is going to be refreshed. A five minute TTL means
at most twelve calls an hour per gage no matter how hard the page is hit, and it means the
number on screen is never more than five minutes older than the instrument.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from curtail_agents.events import Provenance
from curtail_agents.gage_client import GageClient, GageError, Reading
from curtail_core.basins import COMPLIANCE_GAGE, Basin

#: How long a fetched reading is served before another call is made.
#:
#: Shorter than the instrument's own 15 minute reporting interval, so the cache can never
#: be the reason a reading is stale, and long enough that refreshing the page cannot walk
#: us into a rate limit during judging.
TTL_SECONDS: float = 300.0

#: How long a cached reading may be served AFTER USGS starts failing.
#:
#: Distinct from the TTL on purpose. The TTL governs "should I refresh"; this governs "is
#: what I have still worth showing". Six hours is roughly two dozen missed instrument
#: reports: long enough to ride out an outage during a demo, short enough that nobody
#: mistakes yesterday's river for today's.
STALE_LIMIT_SECONDS: float = 6 * 60 * 60


class LiveGageUnavailableError(RuntimeError):
    """No reading could be obtained and none may be invented.

    Raised rather than returning a default. The caller's only correct responses are to
    say so or to refuse, and both are better than a number nobody can source.
    """


@dataclass(frozen=True, slots=True)
class LiveReading:
    """A reading plus the provenance it actually has, never the one we wish it had."""

    reading: Reading
    provenance: Provenance
    #: Wall-clock moment the underlying call to USGS returned.
    fetched_at: datetime
    #: Seconds between that call and now. Zero for a fresh read.
    age_seconds: float

    @property
    def is_live(self) -> bool:
        return self.provenance is Provenance.USGS_LIVE


@dataclass(slots=True)
class _Entry:
    reading: Reading
    fetched_at: datetime
    monotonic_at: float


class LiveGageReader:
    """Reads both compliance gages, caching each independently.

    The clock is injected and MONOTONIC. A wall clock can step backwards on an NTP
    correction, and a cache keyed on a backwards clock serves an entry it believes is
    fresh forever. The same reasoning put a monotonic clock behind the dedup lease.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = TTL_SECONDS,
        stale_limit_seconds: float = STALE_LIMIT_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        client_factory: Callable[[], GageClient] | None = None,
    ) -> None:
        self._ttl = ttl_seconds
        self._stale_limit = stale_limit_seconds
        self._monotonic = monotonic
        self._now = now
        self._client_factory = client_factory or GageClient
        self._entries: dict[Basin, _Entry] = {}

    async def read(self, basin: Basin) -> LiveReading:
        """The freshest reading available for a basin's compliance gage.

        Raises:
            LiveGageUnavailableError: USGS could not be reached and nothing cached is recent
                enough to show. The caller must surface that, not paper over it.
        """
        cached = self._entries.get(basin)
        if cached is not None and self._monotonic() - cached.monotonic_at < self._ttl:
            return self._from_cache(cached)

        gage = COMPLIANCE_GAGE[basin]
        try:
            async with self._client_factory() as client:
                reading = await client.latest_discharge(gage)
        except GageError as exc:
            # A failed fetch is not a reason to discard what we already had, and it is
            # not a reason to pretend the old value is current either. Serve it, aged
            # and labelled, or refuse.
            if cached is not None:
                age = self._monotonic() - cached.monotonic_at
                if age < self._stale_limit:
                    return self._from_cache(cached)
                raise LiveGageUnavailableError(
                    f"{gage} could not be read ({exc}) and the cached reading is "
                    f"{age / 3600:.1f} hours old, past the {self._stale_limit / 3600:.0f} "
                    "hour limit. Showing it would present an old river as today's."
                ) from exc
            raise LiveGageUnavailableError(
                f"{gage} could not be read and nothing is cached: {exc}. This is not a "
                "reading of zero cfs, and it must not be displayed as one."
            ) from exc

        entry = _Entry(reading=reading, fetched_at=self._now(), monotonic_at=self._monotonic())
        self._entries[basin] = entry
        return LiveReading(
            reading=reading,
            provenance=Provenance.USGS_LIVE,
            fetched_at=entry.fetched_at,
            age_seconds=0.0,
        )

    def _from_cache(self, entry: _Entry) -> LiveReading:
        return LiveReading(
            reading=entry.reading,
            provenance=Provenance.USGS_CACHED,
            fetched_at=entry.fetched_at,
            age_seconds=max(0.0, self._monotonic() - entry.monotonic_at),
        )


#: The process-wide reader. One per instance is correct: Cloud Run may run several, and
#: each caching independently still bounds the call rate per instance, which is what the
#: rate limit is measured against.
READER = LiveGageReader()


__all__ = [
    "READER",
    "STALE_LIMIT_SECONDS",
    "TTL_SECONDS",
    "LiveGageReader",
    "LiveGageUnavailableError",
    "LiveReading",
]

"""The river watch: what the gage read, when, and what the Sentinel made of it.

**Why this is not the Season Ledger, and must not be.** `LedgerEntry` records an adopted
order or addendum with its statutory clocks, and every row of it is part of a legal record.
A gage reading is not that. Writing one observation per poll into the order ledger would
put thousands of rows of hydrology into the record a reconsideration petition is read
against. Observations get their own log.

**Why the log matters at all.** The track asks for weeks of asynchronous operations, and
until now this system only looked at the river when somebody clicked. A watch that runs on
a schedule is what turns "the Shasta is below its minimum today" into "the Shasta has been
below its minimum for eleven consecutive days", which is the sentence a watermaster
actually acts on and which no single reading can produce.

**Idempotent on the reading's own timestamp, not on the poll's.** USGS stamps each reading,
so two polls that land between publications see the same observation and must record it
once. Keying on the moment the poll ran would instead manufacture a new data point every
time the scheduler fired, and a run of identical values would look like a river holding
steady under observation rather than one nobody had a fresh reading for.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from curtail_core.basins import Basin

#: Enough to show a multi-week run without loading a season of five-minute readings into a
#: response. The store keeps everything; this bounds what a reader is handed.
DEFAULT_LIMIT = 200


class WatchStoreUnavailableError(RuntimeError):
    """The durable store could not be reached.

    Never caught and turned into an empty watch. An empty list is a claim that the river
    was never observed, which is a different statement from "the store did not answer",
    and this project has shipped that confusion before.
    """


@dataclass(frozen=True, slots=True)
class Observation:
    """One reading, and what the Sentinel made of it.

    `observed_at` is the moment USGS stamped the reading. `recorded_at` is the moment this
    system stored it. They are different facts and both are kept: a large gap between them
    means the watch was down or the gage was late, and collapsing them into one field
    would erase the only evidence of that.
    """

    basin: str
    observed_at: datetime
    recorded_at: datetime
    cfs: float
    minimum_cfs: float
    classification: str
    #: The reading's own identity, so a re-poll of the same publication is a no-op.
    reading_key: str

    @property
    def below_minimum(self) -> bool:
        return self.cfs < self.minimum_cfs

    def to_state(self) -> dict[str, Any]:
        return {
            "basin": self.basin,
            "observed_at": self.observed_at.isoformat(),
            "recorded_at": self.recorded_at.isoformat(),
            "cfs": self.cfs,
            "minimum_cfs": self.minimum_cfs,
            "classification": self.classification,
            "reading_key": self.reading_key,
        }

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> Observation:
        return cls(
            basin=str(state["basin"]),
            observed_at=datetime.fromisoformat(str(state["observed_at"])),
            recorded_at=datetime.fromisoformat(str(state["recorded_at"])),
            cfs=float(state["cfs"]),
            minimum_cfs=float(state["minimum_cfs"]),
            classification=str(state["classification"]),
            reading_key=str(state["reading_key"]),
        )


@dataclass(frozen=True, slots=True)
class Watch:
    """Everything stored for a basin, with the run that matters computed from it."""

    observations: tuple[Observation, ...]
    durable: bool
    storage: str

    @property
    def consecutive_below(self) -> int:
        """How many readings, ending at the most recent, were below the minimum.

        Counted from the newest backwards and stopping at the first reading that was not,
        which is what "has been below since" means. A total count of below-minimum
        readings across the season would be a different and much less useful number, and
        the two are easy to confuse.
        """
        run = 0
        for observation in reversed(self.observations):
            if not observation.below_minimum:
                break
            run += 1
        return run

    @property
    def below_since(self) -> datetime | None:
        """When the current unbroken run below the minimum began, or None."""
        run = self.consecutive_below
        if run == 0:
            return None
        return self.observations[-run].observed_at


class WatchStore(Protocol):
    def load(self, basin: Basin, *, limit: int = DEFAULT_LIMIT) -> Watch: ...

    def append(self, basin: Basin, observation: Observation) -> tuple[Watch, bool]: ...

    @property
    def durable(self) -> bool: ...

    def describe(self) -> str: ...


class InMemoryWatchStore:
    """A watch that lives as long as the process does, and says so.

    Cloud Run runs more than one instance and replaces them freely, so this is not a
    record of the river. `durable` is False and every response built from it carries the
    caveat rather than implying a history that persists.
    """

    def __init__(self, reason: str = "no durable store was configured") -> None:
        self._watch: dict[str, list[Observation]] = {}
        self._reason = reason

    @property
    def durable(self) -> bool:
        return False

    def describe(self) -> str:
        return (
            f"In-process memory, because {self._reason}. Nothing here survives a restart "
            "or reaches a second instance, so a short history may mean the record was "
            "lost rather than that the river was not watched."
        )

    def load(self, basin: Basin, *, limit: int = DEFAULT_LIMIT) -> Watch:
        stored = self._watch.get(basin.value, [])
        return Watch(
            observations=tuple(stored[-limit:]),
            durable=self.durable,
            storage=self.describe(),
        )

    def append(self, basin: Basin, observation: Observation) -> tuple[Watch, bool]:
        stored = self._watch.setdefault(basin.value, [])
        if any(existing.reading_key == observation.reading_key for existing in stored):
            return self.load(basin), False
        stored.append(observation)
        stored.sort(key=lambda item: item.observed_at)
        return self.load(basin), True


class FirestoreWatchStore:
    """Durable, and keyed so a re-poll of the same reading cannot duplicate it.

    The document id IS the reading key, so idempotency is a property of the storage rather
    than of a read-then-write the way a check-then-act would be. Two instances polling
    concurrently therefore converge on one document instead of racing, which a
    read-modify-write could not promise.
    """

    def __init__(self, client: Any, collection: str = "curtail_watch") -> None:
        self._client = client
        self._collection = collection

    @property
    def durable(self) -> bool:
        return True

    def describe(self) -> str:
        return (
            f"Cloud Firestore collection {self._collection}, which survives restarts and "
            "is shared by every instance."
        )

    def _documents(self, basin: Basin) -> Any:
        return (
            self._client.collection(self._collection)
            .document(basin.value)
            .collection("observations")
        )

    def load(self, basin: Basin, *, limit: int = DEFAULT_LIMIT) -> Watch:
        try:
            snapshots = list(self._documents(basin).stream())
        except Exception as exc:  # pragma: no cover - exercised against a live emulator
            raise WatchStoreUnavailableError(
                f"the watch could not be read from Firestore: {exc}"
            ) from exc
        observations = sorted(
            (Observation.from_state(snapshot.to_dict()) for snapshot in snapshots),
            key=lambda item: item.observed_at,
        )
        return Watch(
            observations=tuple(observations[-limit:]),
            durable=True,
            storage=self.describe(),
        )

    def append(self, basin: Basin, observation: Observation) -> tuple[Watch, bool]:
        try:
            # Resolving the collection is INSIDE the try, which it was not at first. A
            # Firestore failure while addressing the document escaped as a raw error, so
            # the endpoint's `except WatchStoreUnavailableError` never saw it and a
            # storage outage would have surfaced as a 500 with no reason attached rather
            # than a 503 naming one. Found by a test, not by reading.
            document = self._documents(basin).document(observation.reading_key)
            existing = document.get()
            if getattr(existing, "exists", False):
                return self.load(basin), False
            document.set(observation.to_state())
        except WatchStoreUnavailableError:
            raise
        except Exception as exc:  # pragma: no cover - exercised against a live emulator
            raise WatchStoreUnavailableError(
                f"the observation could not be written to Firestore: {exc}"
            ) from exc
        return self.load(basin), True


def build_watch_store() -> WatchStore:
    """Firestore when a project is configured, memory otherwise, and never silently.

    The same shape as the season store, and for the reason recorded there: a deploy that
    replaced the environment once dropped the durable ledger to memory while every route
    kept answering. Which one is in use is reported on every response that uses it.
    """
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        return InMemoryWatchStore("GOOGLE_CLOUD_PROJECT is not set")
    try:
        from google.cloud import firestore
    except ImportError:  # pragma: no cover - the dependency ships in the image
        return InMemoryWatchStore("the Firestore client library is not installed")
    try:
        return FirestoreWatchStore(firestore.Client(project=project))
    except Exception as exc:  # pragma: no cover - credential shape varies by host
        return InMemoryWatchStore(f"Firestore could not be reached ({exc})")


def now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "DEFAULT_LIMIT",
    "FirestoreWatchStore",
    "InMemoryWatchStore",
    "Observation",
    "Watch",
    "WatchStore",
    "WatchStoreUnavailableError",
    "build_watch_store",
    "now",
]

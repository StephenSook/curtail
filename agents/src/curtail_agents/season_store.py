"""Where the Season Ledger lives between requests.

**The gap this closes, stated plainly.** The Fleet track asks for a system that
"safely maintains context across weeks of asynchronous operations". This repository had
a Season Ledger with every statutory clock in it, 551 lines, fully tested, and nothing
that outlived a request. The clocks were computed and then thrown away. That is the
adjective without the mechanism, and the ledger's own docstring says so: it is defined
by what survives a restart.

**Why Firestore and not Cloud SQL.** A Cloud Run service reaching Cloud SQL needs an
instance running around the clock and a connector, which is real money for a service
that is idle almost always and a second failure domain in front of the demo. Firestore
in native mode is serverless, in the same region as the service, needs no connector, and
its transactional read-modify-write is exactly the primitive an append-only ledger wants
when two instances sign two orders at the same moment.

**The rule this module exists to obey.** An unavailable store is NOT an empty season.
A season that quietly forgets is indistinguishable from a season in which nothing
happened, and a watermaster reading "no open clocks" would take the second as good news.

So there are exactly two ways to get the in-memory store, and both are the operator
asking for it: Firestore explicitly disabled, or no project configured. A configured
project whose store is unreachable RAISES. It used to fall back with the reason
attached, which sounds careful and is not: the container would start, serve for hours,
and lose every clock it recorded, while the response said `durable: false` in a field
nobody reads during an incident.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from curtail_agents.ledger import (
    LedgerEntry,
    LedgerIntegrityError,
    LoadedLedger,
    append,
    from_state,
    to_state,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from curtail_core.basins import Basin

#: One document per basin, holding that basin's whole season. A season is small, is
#: read whole, and is written under a transaction, so a document is the right grain.
COLLECTION = "seasons"

PROJECT_ENV = "GOOGLE_CLOUD_PROJECT"
DISABLE_ENV = "CURTAIL_DISABLE_FIRESTORE"


class SeasonStoreUnavailableError(RuntimeError):
    """The durable store could not be reached.

    Never caught and turned into an empty ledger. That conversion is the whole class of
    bug this project keeps finding: an errored query reading as a confident absence.
    """


@runtime_checkable
class SeasonStore(Protocol):
    """Load and append a basin's season."""

    def load(self, basin: Basin) -> LoadedLedger: ...

    def append_entry(self, basin: Basin, entry: LedgerEntry) -> LoadedLedger: ...

    @property
    def durable(self) -> bool:
        """False when nothing survives this process. Reported, never hidden."""
        ...

    def describe(self) -> str:
        """One sentence a caller can show a human about where this record lives."""
        ...


class InMemorySeasonStore:
    """A season that lives as long as the process does.

    Honest about it. `durable` is False and `describe` says so, so any response built
    from this store carries the caveat rather than implying a record that persists.
    """

    def __init__(self, reason: str = "no durable store was configured") -> None:
        self._seasons: dict[str, tuple[LedgerEntry, ...]] = {}
        self._reason = reason

    @property
    def durable(self) -> bool:
        return False

    def describe(self) -> str:
        return (
            f"In-process memory, because {self._reason}. Nothing here survives a "
            "restart or reaches a second instance, so an empty season may mean the "
            "record was lost rather than that nothing happened."
        )

    def load(self, basin: Basin) -> LoadedLedger:
        return from_state(to_state(self._seasons.get(basin.value, ())))

    def append_entry(self, basin: Basin, entry: LedgerEntry) -> LoadedLedger:
        current = self._seasons.get(basin.value, ())
        self._seasons[basin.value] = append(current, entry)
        return self.load(basin)


class FirestoreSeasonStore:
    """The durable one. Reads and writes a basin's whole season in a transaction.

    **Why a transaction and not a plain write.** Two Cloud Run instances can sign two
    orders in the same second. A read-then-write without one loses whichever entry
    lands second, and losing a signed order's adoption timestamp moves or extinguishes
    a party's window to petition for reconsideration under Water Code 1122. Firestore
    retries the transaction body on contention, and `ledger.append` refuses to replace
    an entry that already exists, so the retry is safe to run twice.
    """

    def __init__(self, project: str, client: Any | None = None) -> None:
        self._project = project
        if client is not None:
            self._client = client
            return
        try:
            import google.cloud.firestore as firestore
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise SeasonStoreUnavailableError(
                f"google-cloud-firestore is not importable ({exc})"
            ) from exc
        try:
            self._client = firestore.Client(project=project)
        except Exception as exc:  # pragma: no cover - credential shape varies
            raise SeasonStoreUnavailableError(
                f"the Firestore client could not be built for {project}: {exc}"
            ) from exc

    @property
    def durable(self) -> bool:
        return True

    def describe(self) -> str:
        return (
            f"Cloud Firestore, native mode, project {self._project}, collection "
            f"`{COLLECTION}`. One document per basin, appended under a transaction, so "
            "the season survives a restart and is shared across instances."
        )

    def _document(self, basin: Basin) -> Any:
        return self._client.collection(COLLECTION).document(basin.value)

    def load(self, basin: Basin) -> LoadedLedger:
        try:
            snapshot = self._document(basin).get()
        except Exception as exc:
            raise SeasonStoreUnavailableError(
                f"the season for {basin.value} could not be read: {exc}. Reported "
                "rather than returned as an empty season, which would read as a "
                "basin in which nothing has happened."
            ) from exc
        if not getattr(snapshot, "exists", False):
            return from_state([])
        raw = snapshot.to_dict() or {}
        return from_state(raw.get("entries", []))

    def append_entry(self, basin: Basin, entry: LedgerEntry) -> LoadedLedger:
        document = self._document(basin)

        def _txn(transaction: Any) -> list[dict[str, Any]]:
            snapshot = document.get(transaction=transaction)
            existing = (snapshot.to_dict() or {}).get("entries", []) if snapshot.exists else []
            loaded = from_state(existing)
            merged = append(loaded.entries, entry)
            state = to_state(merged)
            transaction.set(document, {"entries": state, "basin": basin.value})
            return state

        try:
            import google.cloud.firestore as firestore

            transactional = firestore.transactional(_txn)
            state = transactional(self._client.transaction())
        except (SeasonStoreUnavailableError, LedgerIntegrityError, ValueError):
            # **A domain refusal is not an outage, and conflating them tells an operator
            # the database is down when the order is simply already on record.** The
            # ledger raises LedgerIntegrityError from inside the transaction body when an
            # order id already exists, which is correct behaviour and the reason a
            # Firestore retry on contention is safe to run twice. The broad handler below
            # was catching it and reporting a write failure.
            #
            # It also made the two implementations of this protocol DIVERGE: the
            # in-memory store let the refusal through and the durable one masked it, so
            # a behaviour every test exercised in memory was different in production.
            # Found by testing the Firestore path with a fake client.
            raise
        except Exception as exc:
            raise SeasonStoreUnavailableError(
                f"the season for {basin.value} could not be written: {exc}. The order "
                "was signed; its clocks were NOT recorded, and that gap is reported "
                "rather than swallowed."
            ) from exc
        return from_state(state)


def store_for(project: str | None = None, *, client: Any | None = None) -> SeasonStore:
    """The store this deployment actually has. **Fails closed rather than degrading.**

    Two cases return the in-memory store, and both are the operator saying they do not
    want a durable one: Firestore explicitly disabled, or no project configured at all.
    Each says which in `describe()`.

    **A configured project whose Firestore cannot be reached RAISES.** An earlier
    version caught that and returned the in-memory store with the reason attached, which
    reads reasonable and is the exact failure this module was written against. An
    operator who set `GOOGLE_CLOUD_PROJECT` asked for a durable legal record. Handing
    them a volatile one because a client failed to build is deciding, on their behalf,
    that losing the season is preferable to an error, and it is invisible at the moment
    it matters: the container starts, serves for hours, and every statutory clock it
    records dies with it.

    A review caught it by noticing this function's own docstring promised "never a
    silent downgrade" three lines above the downgrade. **A comment claiming a property
    is not that property**, which is a lesson this project keeps relearning at a
    different layer each time.

    Raises:
        SeasonStoreUnavailableError: a project is configured and its store is not
            reachable. Callers decide what to do; `api.py` answers 503 on the season
            endpoint and reports the gap on the signing path, rather than serving a
            season that looks empty because it is lost.
    """
    if os.environ.get(DISABLE_ENV):
        return InMemorySeasonStore(f"{DISABLE_ENV} is set")
    resolved = project or os.environ.get(PROJECT_ENV)
    if not resolved:
        return InMemorySeasonStore(f"{PROJECT_ENV} is not set")
    if client is not None:
        return FirestoreSeasonStore(resolved, client=client)
    return FirestoreSeasonStore(resolved)


def season_payload(store: SeasonStore, basin: Basin, *, now: Any = None) -> dict[str, Any]:
    """A basin's season, with every clock's state and an honest note about the store."""
    from curtail_agents.ledger import open_clocks

    loaded = store.load(basin)
    entries = loaded.entries
    open_now = open_clocks(entries, now) if now is not None else open_clocks(entries)
    return {
        "basin": basin.value,
        "orders": to_state(entries),
        "open_clocks": [
            {
                "order_id": order_id,
                "clock": clock.clock_type.value,
                "opens_at": clock.opens_at.isoformat(),
                "closes_at": clock.closes_at.isoformat(),
                "exhaustion_required": clock.exhaustion_required,
            }
            for order_id, clock in open_now
        ],
        "quarantined": [
            {"reason": q.reason, "raw": q.raw} for q in getattr(loaded, "quarantined", ())
        ],
        "durable": store.durable,
        "store": store.describe(),
    }

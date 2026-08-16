"""Rank the corpus against a query vector. Deterministic, offline, no dependencies.

The split here matches every other networked thing in this repository: turning a question
into a vector needs the network and lives in the agents package, and everything that can
be decided without it lives here where it can be tested exactly.

**Two properties this module exists to enforce.**

*A dot product is only a cosine over unit vectors,* and `gemini-embedding-001` does not
return one below its full 3072 dimensions: the 768-dimension vector measures about 0.59.
The builder normalises, and this module refuses an index that does not claim normalisation,
an index whose vectors are not ACTUALLY unit length, and a query vector that is not unit
length, rather than quietly ranking by magnitude.

The middle one was missing until a second-model review pointed at it, and the gap is worth
stating because it is a shape this repository keeps meeting. The `"normalised": true` field
is a sentence the artifact writes about itself. The builder measures norms and a test
measures the committed file, and NEITHER of those runs in the container serving a request,
so an artifact that claimed normalisation while carrying vectors of any length would have
loaded and ranked happily. A claim and a check are different things.

*The corpus repeats itself, heavily.* 2,072 chunks carry only 864 distinct texts, because
every addendum restates the same standard paragraphs. Ranked naively, a reader asking one
question gets the same boilerplate five times from five different addenda and never sees
the second-best answer. So identical text collapses to ONE result that names every
document carrying it. That is more informative than either showing the duplicates or
hiding them: "this paragraph appears in twelve addenda" is itself a finding about how
these orders are written.
"""

from __future__ import annotations

import base64
import json
import math
import struct
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA = Path(__file__).resolve().parent / "data" / "corpus_index.json"

#: Anything further from 1.0 than this is not float16 rounding.
UNIT_TOLERANCE = 0.01


class CorpusIndexUnavailableError(RuntimeError):
    """The index is missing or does not hold together.

    Raised rather than degraded. An empty result list is a confident claim that the
    corpus says nothing about the question, which is a different answer from "the index
    could not be read" and must never stand in for it.
    """


@dataclass(frozen=True, slots=True)
class Hit:
    """One passage, and every document that carries it."""

    score: float
    snippet: str
    files: tuple[str, ...]
    characters: int

    @property
    def appears_in(self) -> int:
        return len(self.files)


@dataclass(frozen=True, slots=True)
class Index:
    vectors: tuple[tuple[float, ...], ...]
    entries: tuple[dict[str, Any], ...]
    dimensions: int
    model: str
    documents_read: int
    documents_represented: int
    absent: tuple[dict[str, str], ...]
    distinct_texts: int


def require_unit_length(values: Any, *, describe: str) -> None:
    """Refuse a vector that is not finite, then one that is not unit length.

    **The finiteness test is first because it is the one that hides.** Every comparison
    with NaN is False, so `abs(norm - 1.0) > UNIT_TOLERANCE` PASSES a NaN vector, and a
    tolerance check written the obvious way is not a guard against the worst input it can
    receive. float16 stores NaN and infinity perfectly well, so a corrupted or crafted
    index can carry them, and one non-finite component makes the whole norm non-finite.

    A NaN score does not merely rank itself wrongly. It breaks the comparisons around it:
    sorting `[0.9, nan, 0.5, 0.7]` descending yields `[0.7, 0.9, nan, 0.5]`, so 0.5
    outranks 0.7 and two passages that have nothing to do with the corrupt one come back
    in the wrong order. Nothing raises and the page looks entirely normal.

    This project has met NaN once before, where it recommended lifting curtailment.
    """
    norm = 0.0
    for value in values:
        norm += value * value
    norm = norm**0.5
    if not math.isfinite(norm):
        raise CorpusIndexUnavailableError(
            f"{describe} has a length of {norm}, which is not a finite number. A single "
            "NaN or infinite component passes an ordinary tolerance check and then "
            "scrambles the order of unrelated results."
        )
    if abs(norm - 1.0) > UNIT_TOLERANCE:
        raise CorpusIndexUnavailableError(
            f"{describe} has length {norm:.3f} rather than 1, so ranking would order "
            "results partly by magnitude rather than by similarity."
        )


@lru_cache(maxsize=1)
def load() -> Index:
    """Read and validate the committed index once.

    Cached because unpacking two thousand vectors on every request would make search
    feel broken for a reason that has nothing to do with search.
    """
    if not DATA.exists():
        raise CorpusIndexUnavailableError(
            f"{DATA.name} is missing. It is built by scripts/build_corpus_index.py from "
            "the corpus, which is not tracked in git."
        )
    try:
        payload = json.loads(DATA.read_text())
    except (OSError, ValueError) as exc:
        raise CorpusIndexUnavailableError(f"the corpus index could not be read: {exc}") from exc

    source = payload.get("source", {})
    if not source.get("normalised"):
        # Refusing beats ranking. Non-unit vectors do not error, they just order the
        # results by a mixture of similarity and magnitude, and nothing on screen says so.
        raise CorpusIndexUnavailableError(
            "the corpus index does not claim normalised vectors, so a dot product would "
            "not be a cosine and the ranking would be silently wrong"
        )
    # **The flag above is a SENTENCE the artifact writes about itself. The measurement is
    # below.** Checking only the claim left this module's stated guarantee resting on a
    # field the artifact sets, so an index that said `"normalised": true` while carrying
    # vectors of any length loaded happily and ranked by magnitude. The builder verifies
    # norms and a test verifies the committed file, and neither of those is running in the
    # container that answers a request. This is the same defect this repository has already
    # met as a quote nobody grepped against its source and as an eval artifact claiming a
    # metric nothing computed: a claim and a check are different things.
    #
    # The cost is one pass over values already unpacked below, so it is folded into that
    # loop rather than paid twice.
    dimensions = int(source.get("dimensions", 0))
    entries = payload.get("entries", [])
    if not entries:
        raise CorpusIndexUnavailableError("the corpus index holds no entries")

    counts = payload.get("counts", {})
    # Read explicitly rather than with a default. A missing count silently becoming zero
    # is how a renamed field turns into "0 documents searched" on a page that otherwise
    # looks entirely healthy.
    for required in ("documents_read", "documents_represented"):
        if not isinstance(counts.get(required), int):
            raise CorpusIndexUnavailableError(
                f"the corpus index does not state {required}, so what it covers is unknown"
            )

    vectors: list[tuple[float, ...]] = []
    for entry in entries:
        raw = base64.b64decode(entry["vector"])
        values = struct.unpack(f"<{len(raw) // 2}e", raw)
        if len(values) != dimensions:
            raise CorpusIndexUnavailableError(
                f"{entry['file']} carries {len(values)} dimensions and the index says {dimensions}"
            )
        require_unit_length(values, describe=f"{entry['file']} chunk {entry.get('chunk')}")
        vectors.append(values)

    return Index(
        vectors=tuple(vectors),
        entries=tuple(entries),
        dimensions=dimensions,
        model=str(source.get("model", "unknown")),
        documents_read=counts["documents_read"],
        documents_represented=counts["documents_represented"],
        # Carried, not summarised. Five of these documents are scanned images with no
        # text layer, so a question they would answer is not searchable at all, and a
        # reader who is not told that has no way to find out.
        absent=tuple(payload.get("absent", ())),
        distinct_texts=len({entry["text_sha256"] for entry in entries}),
    )


def search(query: list[float] | tuple[float, ...], *, limit: int = 5) -> list[Hit]:
    """The best passages for an already-embedded question.

    Raises:
        CorpusIndexUnavailableError: if the index is unusable, or if the query vector is
            the wrong width or not unit length. A query of the wrong width would raise
            deep inside a zip; one that is not normalised would rank perfectly happily
            and produce a wrong order nobody could see.
    """
    index = load()
    if len(query) != index.dimensions:
        raise CorpusIndexUnavailableError(
            f"the question was embedded to {len(query)} dimensions and the index holds "
            f"{index.dimensions}"
        )
    require_unit_length(query, describe="the question vector")

    scored: list[tuple[float, int]] = []
    for position, vector in enumerate(index.vectors):
        total = 0.0
        for a, b in zip(query, vector, strict=True):
            total += a * b
        scored.append((total, position))
    scored.sort(key=lambda pair: pair[0], reverse=True)

    hits: list[Hit] = []
    seen: dict[str, int] = {}
    for score, position in scored:
        entry = index.entries[position]
        digest = entry["text_sha256"]
        if digest in seen:
            # Same passage in another document. Fold it into the hit already made rather
            # than spending a second slot on it or dropping the fact that it recurs.
            existing = hits[seen[digest]]
            hits[seen[digest]] = Hit(
                score=existing.score,
                snippet=existing.snippet,
                files=(*existing.files, entry["file"]),
                characters=existing.characters,
            )
            continue
        if len(hits) >= limit:
            # Full is full, but the scan continues: a duplicate of a hit already chosen
            # still has to be folded in above, and stopping here would under-report how
            # many documents carry the passage. The list is two thousand items and the
            # remaining work is a dictionary lookup each, so there is nothing to save by
            # being clever about an early exit.
            continue
        seen[digest] = len(hits)
        hits.append(
            Hit(
                score=score,
                snippet=entry["snippet"],
                files=(entry["file"],),
                characters=int(entry["characters"]),
            )
        )
    return hits


__all__ = [
    "CorpusIndexUnavailableError",
    "Hit",
    "Index",
    "load",
    "require_unit_length",
    "search",
]

"""Embed the corpus prose with `gemini-embedding-001` so it can be searched by meaning.

**Why this is not a search box for its own sake.** The corpus is 101 curtailment documents
issued over five years across four order series. A question a watermaster actually has,
"when was curtailment last suspended after a gage revision", is not a keyword: the
documents say "rating curve", "shift", "revised", "field measurements" and "suspension of
curtailment" in different combinations, and no two addenda phrase it the same way. The
rights tables inside these documents are already structured data this repository parses
exactly. The PROSE is the part nothing can index, and it is where the findings live.

**What is indexed and what is deliberately not.** Attachment A rows are dropped: a
paragraph carrying two or more application numbers, or that is more than 30 percent
digits, is a table fragment. Those are already in the rights records, parsed rather than
approximated, and embedding them would fill the index with near-identical rows that crowd
out the findings. Owner names are never read from these documents anywhere in this
repository and that does not change here.

**A trap this file exists to record.** `gemini-embedding-001` returns a UNIT vector at its
full 3072 dimensions and a NON-unit vector at any smaller `outputDimensionality`: measured,
the 768-dimension vector has an L2 norm of about 0.59. Matryoshka truncation drops
components and does not renormalise. Cosine similarity is still correct if computed as a
true cosine, but the usual shortcut of a bare dot product over "unit" vectors is silently
wrong, and wrong in a way that ranks longer-normed chunks higher rather than erroring. So
every vector is normalised HERE, once, at build time, and the search path may then use a
dot product honestly.

Split like every other networked builder in this repo: this half needs the corpus and the
API, and the committed artifact is what CI reads.

    uv run python scripts/build_corpus_index.py           # extract, embed, write
    uv run python scripts/build_corpus_index.py --check    # verify, offline
    uv run python scripts/build_corpus_index.py --dry-run  # count chunks, embed nothing
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any

import google.auth
import google.auth.transport.requests
import httpx

REPO = Path(__file__).resolve().parents[1]
CORPUS = REPO / "data" / "corpus"
OUT = REPO / "core" / "src" / "curtail_core" / "data" / "corpus_index.json"

MODEL = "gemini-embedding-001"
LOCATION = "us-central1"
ENDPOINT = (
    "https://{location}-aiplatform.googleapis.com/v1/projects/{project}"
    "/locations/{location}/publishers/google/models/{model}:predict"
)

#: 768 of the model's 3072. Enough to separate these documents and a quarter of the
#: storage. See the module docstring for why the result must be renormalised.
DIMENSIONS = 768

#: Long enough to hold a finding with its context, short enough that a hit points at
#: something a reader can actually locate in the document.
CHUNK_CHARS = 1800
SNIPPET_CHARS = 420

#: The API's per-request instance ceiling for this model.
BATCH = 20

#: A paragraph carrying two or more application numbers, or that is mostly digits, is a
#: rights-table fragment. Those live in the parsed rights records, exactly, and embedding
#: them would crowd the index with near-identical rows.
#:
#: **The prefixes are measured from the corpus, not guessed.** The first version of this
#: pattern read `[ASHD]`, which invented `H`, omitted `C`, and above all missed `SG`, the
#: single most common prefix in these documents by an order of magnitude: 6,738
#: occurrences in a sample of 25 against 1,743 for the next. Because `SG` rows carry long
#: owner names, they also sat under the digit-share threshold, so BOTH filters passed them
#: and 49 Attachment A fragments carrying private individuals' full names reached the
#: index. Counting the prefixes that actually occur is what a filter over somebody else's
#: format requires.
APPLICATION_NUMBER = re.compile(r"\b(?:SG|[ACDS])\d{5,6}\b")
DIGIT_SHARE = 0.30

#: The Attachment A column header. Any paragraph carrying it is a rights table however it
#: scores on every other test, and this is the direct check rather than an inference.
OWNER_HEADER = re.compile(r"PRIMARY\s+OWNER", re.IGNORECASE)

#: `Surname, Firstname` as these tables print it, which always carries an initial or an
#: ampersand: "Lunsford, Carolyn S.", "Fine, Glenn & Susan", "Farmer, M. & J. Trust",
#: "Stapleton, Michael R. & Betsy".
#:
#: **That tail is doing real work and its absence cost 20 documents.** The looser version,
#: any capitalised word on each side of a comma, matched "Friday, February", "Wednesday,
#: January" and "Watershed, Issued" throughout perfectly ordinary prose. Two of those in a
#: merged chunk tripped the name rule and the chunk was discarded, so twenty 2021 addenda
#: full of usable findings ended up with no indexed prose at all while the artifact still
#: reported all 101 documents. A filter over personal data that is too eager does not
#: announce itself: it removes content and leaves the counts looking fine.
#:
#: A name with no initial, "St Germaine, Katherine Anne", is not matched here and does not
#: need to be: it only ever appears as a table ROW, beside a right identifier, and the
#: identifier rules below catch the row.
PERSON = re.compile(r"\b[A-Z][A-Za-z'\-]{2,},\s+(?:[A-Z][A-Za-z'\-]*\s+)?(?:[A-Z]\.|&)")

#: A right holder that is a business rather than a person. Found by a test rather than by
#: reading: a single row naming "Anonymous Holdings LLC" carries one identifier, no
#: `Surname, F.` shape and only a quarter digits, so it passed every rule above. Two such
#: names, "Valtinjos LLC" and "Outpost MR LLC", had leaked in an earlier build and were
#: removed by a different rule entirely, which is luck rather than a guard. A business is
#: still a party this repository does not publish.
ENTITY = re.compile(
    r"\b[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z.]+){0,3}\s+"
    r"(?:LLC|L\.L\.C\.|Inc\.?|Ranch|Ranches|Farms?|Trust|Company|Corp\.?|Holdings)\b"
)

#: The heading of the other table layout, which pairs a right id with an owner and a
#: stream rather than sitting under a Primary Owner column.
PARTY_LIST = re.compile(r"List of Parties", re.IGNORECASE)

#: **Some of these PDFs are printouts of the notification email itself**, complete with a
#: To: line. One carried a Board staff member's name and work address, which is precisely
#: the third-party contact data this repository refuses to publish, from a source being
#: public making no difference. A passage holding an address is a mail header or a contact
#: footer; neither is a finding, so nothing is lost by dropping it and the guarantee is
#: clean rather than a judgement call per address.
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

#: Footnote blocks are a run of numbered URLs, and they retrieved well in the first build
#: because a query about a legal topic is genuinely near a citation to that topic. The hit
#: pointed at the right document and showed the reader a list of links. Dropped on the
#: share of the paragraph that is URL text.
URL = re.compile(r"https?://\S+")
URL_SHARE = 0.25


class BuildError(RuntimeError):
    """The index could not be built or does not hold together."""


def text_of(path: Path) -> str:
    result = subprocess.run(
        ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise BuildError(f"pdftotext failed on {path.name}: {result.stderr.strip()[:160]}")
    return result.stdout


def is_table_fragment(text: str) -> bool:
    """Whether a passage is rights-table material rather than prose.

    Applied to each paragraph AND again to the assembled chunk, which is the fix for the
    defect that let names through twice. `pdftotext` emits one table ROW per paragraph,
    and a row carries exactly one application number, so a rule of "two or more" is
    satisfied by no row and the rows are then merged into a chunk that is nothing but
    table. Filtering the merged result as well is what closes it.
    """
    identifiers = APPLICATION_NUMBER.findall(text)
    if len(identifiers) >= 2:
        return True
    if OWNER_HEADER.search(text) or PARTY_LIST.search(text):
        return True
    if EMAIL.search(text):
        return True
    people = PERSON.findall(text)
    if len(people) >= 2:
        return True
    # One right id beside one name-shaped token is a table ROW, which is exactly the
    # shape that defeated the counting rules above. The holder may be a person or a
    # business, and both are parties whose names do not belong here.
    if identifiers and (people or ENTITY.search(text)):
        return True
    digits = sum(character.isdigit() for character in text)
    return digits / max(len(text), 1) > DIGIT_SHARE


def is_footnote_block(paragraph: str) -> bool:
    """A paragraph that is mostly links. It answers a query about the right topic and
    shows the reader nothing but URLs."""
    linked = sum(len(match) for match in URL.findall(paragraph))
    return linked / max(len(paragraph), 1) > URL_SHARE


def chunks_of(text: str) -> list[str]:
    """Prose only, merged into chunks that end on a paragraph boundary.

    Merging on paragraphs rather than cutting at a fixed offset matters here: these
    documents are numbered findings, and a chunk that starts mid-finding reads as though
    the Board said something it did not.
    """
    out: list[str] = []
    current: list[str] = []
    size = 0
    for raw in re.split(r"\n\s*\n", text):
        paragraph = " ".join(raw.split())
        if len(paragraph) < 40 or is_table_fragment(paragraph):
            continue
        if is_footnote_block(paragraph):
            continue
        if size + len(paragraph) > CHUNK_CHARS and current:
            out.append(" ".join(current))
            current, size = [], 0
        current.append(paragraph)
        size += len(paragraph)
    if current:
        out.append(" ".join(current))
    # The second pass, and it is not belt and braces. A merged chunk of single-id table
    # rows passes every per-paragraph test and is pure table.
    return [chunk for chunk in out if not is_table_fragment(chunk)]


def authorise() -> tuple[Any, str]:
    credentials, project = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(google.auth.transport.requests.Request())
    quota = getattr(credentials, "quota_project_id", None) or project
    if not quota:
        raise BuildError("no project is set. Set GOOGLE_CLOUD_PROJECT.")
    return credentials, str(quota)


def embed(
    texts: list[str], *, task: str, credentials: Any, project: str, client: httpx.Client
) -> list[list[float]]:
    """Embed a batch, and refuse rather than returning a short list.

    A response holding fewer vectors than the batch held texts would silently pair every
    later chunk with the wrong document, which is the kind of corruption that produces
    plausible search results nobody can trace.
    """
    response = client.post(
        ENDPOINT.format(location=LOCATION, project=project, model=MODEL),
        json={
            "instances": [{"content": text, "task_type": task} for text in texts],
            "parameters": {"outputDimensionality": DIMENSIONS},
        },
        headers={"Authorization": f"Bearer {credentials.token}"},
    )
    if response.status_code != 200:
        raise BuildError(f"embedding returned HTTP {response.status_code}: {response.text[:200]}")
    predictions = response.json().get("predictions", [])
    if len(predictions) != len(texts):
        raise BuildError(
            f"asked for {len(texts)} embeddings and got {len(predictions)}. Refusing rather "
            "than pairing vectors with the wrong text."
        )
    vectors: list[list[float]] = []
    for prediction in predictions:
        values = prediction["embeddings"]["values"]
        if len(values) != DIMENSIONS:
            raise BuildError(f"expected {DIMENSIONS} dimensions and got {len(values)}")
        vectors.append(normalise(values))
    return vectors


def normalise(values: list[float]) -> list[float]:
    """Unit-length, because the API does not return one below full dimensionality.

    Measured: the 3072-dimension vector has norm 1.0 and the 768-dimension vector has
    norm about 0.59. Normalising once here is what makes a dot product a true cosine
    everywhere downstream.
    """
    norm = sum(value * value for value in values) ** 0.5
    if not math.isfinite(norm):
        # Checked before the zero test, because `nan == 0.0` is False and a NaN would
        # otherwise divide cleanly into an all-NaN vector and be written to the artifact.
        raise BuildError(f"a vector came back with length {norm}, which is not a finite number")
    if norm == 0.0:
        raise BuildError("a zero vector came back, which cannot be normalised or compared")
    return [value / norm for value in values]


def pack(vector: list[float]) -> str:
    """float16, base64. Half the bytes, and far below the precision that separates these
    documents: the gap between a matching chunk and an unrelated one is on the order of
    0.1 cosine, while float16 costs about 0.0005."""
    return base64.b64encode(struct.pack(f"<{len(vector)}e", *vector)).decode()


def build(*, dry_run: bool = False) -> dict[str, Any]:
    if shutil.which("pdftotext") is None:
        raise BuildError("pdftotext is not installed. brew install poppler.")
    if not CORPUS.is_dir():
        raise BuildError(f"{CORPUS} is absent. The corpus is not tracked in git.")

    documents = sorted(CORPUS.glob("*.pdf"))
    if not documents:
        raise BuildError(f"{CORPUS} holds no PDFs")

    staged: list[dict[str, Any]] = []
    for path in documents:
        for index, chunk in enumerate(chunks_of(text_of(path))):
            staged.append({"file": path.name, "chunk": index, "text": chunk})

    print(f"  {len(documents)} documents, {len(staged):,} prose chunks")
    if dry_run:
        packed = len(staged) * DIMENSIONS * 2
        print(
            f"  vectors would be {packed / 1e6:.1f} MB raw, "
            f"about {packed * 4 / 3 / 1e6:.1f} MB base64"
        )
        return {}

    credentials, project = authorise()
    entries: list[dict[str, Any]] = []
    with httpx.Client(timeout=120.0) as client:
        for start in range(0, len(staged), BATCH):
            batch = staged[start : start + BATCH]
            vectors = embed(
                [item["text"] for item in batch],
                task="RETRIEVAL_DOCUMENT",
                credentials=credentials,
                project=project,
                client=client,
            )
            for item, vector in zip(batch, vectors, strict=True):
                entries.append(
                    {
                        "file": item["file"],
                        "chunk": item["chunk"],
                        # A snippet, not the chunk. The document is the source of record
                        # and this artifact is an index into it, not a copy of it.
                        "snippet": item["text"][:SNIPPET_CHARS],
                        "characters": len(item["text"]),
                        # The corpus carries at least one order under two different
                        # filenames, so identical prose scores identically and would
                        # occupy two of the reader's three results. Search collapses on
                        # this and reports that it did, rather than quietly hiding one.
                        "text_sha256": hashlib.sha256(item["text"].encode()).hexdigest()[:16],
                        "vector": pack(vector),
                    }
                )
            done = min(start + BATCH, len(staged))
            print(f"    embedded {done:,} of {len(staged):,}", end="\r", flush=True)
    print()

    represented = {entry["file"] for entry in entries}
    absent = [
        {
            "file": path.name,
            "why": (
                "no text layer, so this is a scanned image and nothing can be extracted without OCR"
                if len(text_of(path).strip()) < 200
                else "every passage is rights-table material, which this index excludes"
            ),
        }
        for path in documents
        if path.name not in represented
    ]

    return {
        "source": {
            "corpus": f"data/corpus, {len(documents)} documents, not tracked in git",
            "model": MODEL,
            "location": LOCATION,
            "dimensions": DIMENSIONS,
            "task_type": "RETRIEVAL_DOCUMENT",
            "normalised": True,
            "normalisation_note": (
                "The model returns a unit vector at 3072 dimensions and a NON-unit vector "
                "at any smaller outputDimensionality: the 768-dimension vector measures "
                "about 0.59. Vectors here are normalised at build time so a dot product is "
                "a true cosine."
            ),
            "personal_data": (
                "No owner names are indexed. The rights records in this repository "
                "deliberately do not read the Primary Owner column, and an embedding "
                "index is not a way around that decision. Three filters enforce it and a "
                "test asserts the committed artifact against all three."
            ),
            "excluded": (
                "Attachment A rows. A paragraph holding two or more application numbers, "
                "or more than 30 percent digits, is a rights-table fragment. Those are "
                "parsed exactly into the rights records and are not approximated here."
            ),
        },
        "counts": {
            "documents_read": len(documents),
            "documents_represented": len({entry["file"] for entry in entries}),
            "chunks": len(entries),
            "characters_indexed": sum(entry["characters"] for entry in entries),
        },
        # **Named, not counted.** A search that silently covers 76 of 101 documents reports
        # nothing about the 25, and a reader has no way to learn that the answer they did
        # not get was never searchable. Each absent document says WHY, and the two reasons
        # are different problems: a scan needs OCR, an all-table document has nothing this
        # index is for.
        "absent": absent,
        "entries": entries,
    }


def check() -> list[str]:
    problems: list[str] = []
    if not OUT.exists():
        return [f"{OUT.name} is missing. Run scripts/build_corpus_index.py."]

    payload = json.loads(OUT.read_text())
    entries = payload["entries"]
    source = payload["source"]

    if source["model"] != MODEL:
        problems.append(f"the index was built with {source['model']} and this code expects {MODEL}")
    if source["dimensions"] != DIMENSIONS:
        problems.append("the index dimensionality does not match this code")
    if not source.get("normalised"):
        problems.append(
            "the index does not claim normalised vectors, so a dot product is not a cosine"
        )
    counts = payload["counts"]
    if counts["chunks"] != len(entries):
        problems.append("the chunk count does not match the entries")
    represented = {entry["file"] for entry in entries}
    if counts["documents_represented"] != len(represented):
        problems.append("the represented-document count does not match the entries")
    if counts["documents_read"] - len(represented) != len(payload.get("absent", [])):
        problems.append(
            "the absent list does not account for every document that produced no prose"
        )

    # Every vector must actually BE unit length. The claim in `source` is a sentence; this
    # is the measurement, and a sentence that nothing checks is how a wrong artifact ships.
    off = 0
    for entry in entries:
        raw = base64.b64decode(entry["vector"])
        values = struct.unpack(f"<{len(raw) // 2}e", raw)
        if len(values) != DIMENSIONS:
            problems.append(f"{entry['file']} chunk {entry['chunk']} has {len(values)} dimensions")
            break
        norm = sum(value * value for value in values) ** 0.5
        # float16 rounding moves the norm slightly. Anything beyond this is not rounding.
        # The finiteness test is separate and comes first: every comparison with NaN is
        # False, so a tolerance check alone reports a NaN vector as perfectly fine.
        if not math.isfinite(norm) or abs(norm - 1.0) > 0.01:
            off += 1
    if off:
        problems.append(f"{off} vectors are not unit length, so ranking would be skewed")

    files = {entry["file"] for entry in entries}
    if len(files) < 90:
        problems.append(f"only {len(files)} documents are represented, which is too few")
    for entry in entries:
        if not entry["snippet"].strip():
            problems.append(f"{entry['file']} chunk {entry['chunk']} has an empty snippet")
            break
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify the artifact, offline")
    parser.add_argument("--dry-run", action="store_true", help="count chunks, embed nothing")
    args = parser.parse_args(argv)

    if args.check:
        problems = check()
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        if problems:
            return 1
        counts = json.loads(OUT.read_text())["counts"]
        print(
            f"{counts['chunks']:,} chunks over {counts['documents_represented']} of "
            f"{counts['documents_read']} documents, "
            f"{counts['characters_indexed']:,} characters indexed, every vector unit length"
        )
        return 0

    try:
        payload = build(dry_run=args.dry_run)
    except (BuildError, OSError, ValueError) as exc:
        print(f"could not build: {exc}", file=sys.stderr)
        return 1
    if args.dry_run:
        return 0

    OUT.write_text(json.dumps(payload) + "\n")
    counts = payload["counts"]
    print(
        f"{counts['chunks']:,} chunks over {counts['documents_represented']} of "
        f"{counts['documents_read']} documents"
    )
    for gap in payload["absent"]:
        print(f"  absent: {gap['file']}: {gap['why']}")
    print(f"wrote {OUT.relative_to(REPO)} ({OUT.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

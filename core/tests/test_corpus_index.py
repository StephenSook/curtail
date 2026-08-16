"""The corpus index, and the personal data that must never be in it.

Offline. `scripts/build_corpus_index.py` needs the corpus and the embedding API; this
asserts the committed artifact. Same split as the diversions builder and the deployment
probe, for the same reason.

**Why the personal-data class here is the important part of this file.**

This repository already decided, in the rights records, that the Primary Owner column is
not read: "No third-party personal data enters this repository, and a public source does
not change that." An embedding index over the same documents is a back door to exactly
that decision, and it walked through three times before it was closed:

1. The application-number pattern read `[ASHD]`, which invented a prefix that does not
   exist and missed `SG`, the most common one in these documents by roughly four to one.
   49 Attachment A fragments carrying private individuals' full names were indexed.
2. The person pattern required capitals, and the "List of Parties Curtailed" tables print
   title case, so "Lunsford, Carolyn S." and "Fine, Glenn & Susan" passed. Worse, that
   table prints one row per paragraph and a row holds ONE identifier, so a rule of "two or
   more" was satisfied by no row at all, and the rows were then merged into a chunk that
   was nothing but table. The filter now runs on the assembled chunk as well.
3. Some of these PDFs are printouts of the notification email, To: line included. One
   carried a Board staff member's name and work address.

Each fix was a wider version of the same guard. This file is the version that catches the
fourth one, because it asserts on the CLASS rather than on the three known instances.
"""

from __future__ import annotations

import base64
import json
import re
import struct
from pathlib import Path
from typing import Any

import pytest

from curtail_core.corpus_search import (
    CorpusIndexUnavailableError,
    load,
    search,
)

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "core" / "src" / "curtail_core" / "data" / "corpus_index.json"


@pytest.fixture(scope="module")
def index() -> dict[str, Any]:
    loaded = json.loads(DATA.read_text())
    assert isinstance(loaded, dict)
    return loaded


@pytest.fixture(scope="module")
def prose(index: dict[str, Any]) -> str:
    return "\n".join(entry["snippet"] for entry in index["entries"])


class TestNoOwnerDataIsIndexed:
    """One test per shape that has actually leaked, plus the header checks that make a
    new leak visible. A pattern here failing means a rights table reached the index."""

    def test_no_application_numbers(self, prose: str) -> None:
        """A right identifier in an indexed passage means a table row got through, and a
        row carries an owner name beside it. Prefixes are the ones the corpus actually
        uses, counted rather than guessed: A, C, D, S and SG."""
        found = re.findall(r"\b(?:SG|[ACDS])\d{5,6}\b", prose)
        assert not found, f"{len(found)} right identifiers are indexed, e.g. {found[:5]}"

    def test_no_primary_owner_column(self, prose: str) -> None:
        assert not re.search(r"PRIMARY\s+OWNER", prose, re.IGNORECASE)

    def test_no_party_list_heading(self, prose: str) -> None:
        assert not re.search(r"List of Parties", prose, re.IGNORECASE)

    def test_no_email_addresses(self, prose: str) -> None:
        """Several of these PDFs are printouts of the notification email itself. One
        carried a named Board employee and their work address."""
        found = re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", prose)
        assert not found, f"email addresses are indexed: {found[:3]}"

    def test_no_entity_owner_names(self, prose: str) -> None:
        """The right holders in these tables are ranches, trusts and companies as well as
        individuals, and a business name is still a party this repository does not
        publish."""
        found = re.findall(
            r"\b[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3}\s+(?:Ranch|Ranches|LLC|Inc\.)\b", prose
        )
        assert not found, f"named right holders are indexed: {sorted(set(found))[:5]}"

    def test_no_personal_names(self, prose: str) -> None:
        """`Surname, Firstname` as these tables print it, which always carries an initial
        or an ampersand: "Lunsford, Carolyn S.", "Fine, Glenn & Susan", "Farmer, M. & J.
        Trust".

        The tail is what makes this assertable at zero. A looser pattern, any capitalised
        word on each side of a comma, matches ordinary prose constantly: "Friday,
        February", "Watershed, Issued", "Sincerely, Juliet". The first version of the
        FILTER used that looser shape and discarded twenty documents' worth of findings
        while the counts still reported all 101, which is the opposite failure and just as
        invisible.

        A name with no initial, "St Germaine, Katherine Anne", is not matched here and
        does not need to be: it appears only as a table row beside a right identifier, and
        `test_no_application_numbers` covers the row.
        """
        found = re.findall(
            r"\b[A-Z][A-Za-z'\-]{2,},\s+(?:[A-Z][A-Za-z'\-]*\s+)?(?:[A-Z]\.|&)", prose
        )
        assert not found, f"personal names are indexed: {sorted(set(found))[:5]}"

    def test_the_artifact_states_the_rule_it_follows(self, index: dict[str, Any]) -> None:
        assert "personal_data" in index["source"]
        assert "Primary Owner" in index["source"]["personal_data"]


class TestTheVectorsAreUsable:
    def test_every_vector_is_unit_length(self, index: dict[str, Any]) -> None:
        """A dot product is a cosine only over unit vectors, and this model returns a
        NON-unit vector below its full 3072 dimensions: 768 comes back at about 0.59.
        Ranking non-unit vectors raises nothing and orders results partly by magnitude.
        """
        dimensions = index["source"]["dimensions"]
        for entry in index["entries"]:
            raw = base64.b64decode(entry["vector"])
            values = struct.unpack(f"<{len(raw) // 2}e", raw)
            assert len(values) == dimensions
            norm = sum(value * value for value in values) ** 0.5
            assert abs(norm - 1.0) < 0.01, f"{entry['file']} vector has length {norm:.3f}"

    def test_the_counts_reconcile(self, index: dict[str, Any]) -> None:
        """Read and REPRESENTED are separate numbers, and conflating them is what hid a
        real gap: the artifact reported 101 documents while 25 of them had no indexed
        prose at all, so a reader could not learn that the answer they did not get was
        never searchable."""
        counts = index["counts"]
        represented = {entry["file"] for entry in index["entries"]}
        assert counts["chunks"] == len(index["entries"])
        assert counts["documents_represented"] == len(represented)
        assert counts["documents_read"] >= counts["documents_represented"]

    def test_every_absent_document_is_named_with_a_reason(self, index: dict[str, Any]) -> None:
        """A count of what is missing is not a report of what is missing. The two reasons
        are different problems wanting different fixes: a scan needs OCR, an all-table
        document has nothing this index is for."""
        counts = index["counts"]
        absent = index["absent"]
        assert len(absent) == counts["documents_read"] - counts["documents_represented"]
        represented = {entry["file"] for entry in index["entries"]}
        for gap in absent:
            assert gap["file"] not in represented
            assert gap["why"].strip(), f"{gap['file']} is absent for no stated reason"

    def test_most_of_the_corpus_is_represented(self, index: dict[str, Any]) -> None:
        """The filters are aggressive on purpose and an over-eager one removes content
        without announcing itself. This is the floor that made that visible: it failed at
        76 of 101 and sent me looking for why."""
        assert index["counts"]["documents_represented"] >= 90
        assert len(index["entries"]) >= 400


class TestSearchRefusesRatherThanRanksWrongly:
    def test_a_query_of_the_wrong_width_is_refused(self) -> None:
        with pytest.raises(CorpusIndexUnavailableError, match="dimensions"):
            search([0.0] * 10)

    def test_a_query_that_is_not_unit_length_is_refused(self) -> None:
        """It would rank perfectly happily and produce a wrong order nobody could see."""
        loaded = load()
        doubled = [value * 2 for value in loaded.vectors[0]]
        with pytest.raises(CorpusIndexUnavailableError, match="rather than 1"):
            search(doubled)

    def test_a_repeated_passage_collapses_and_says_how_often(self) -> None:
        """609 chunks over 96 of 101 documents, and these addenda restate the same standard
        paragraphs constantly. Ranked naively a reader gets one boilerplate paragraph
        several times and never sees the second-best answer."""
        loaded = load()
        hits = search(list(loaded.vectors[0]), limit=5)
        assert len({hit.snippet for hit in hits}) == len(hits), "a passage was returned twice"
        assert any(hit.appears_in > 1 for hit in hits), (
            "no hit reports appearing in more than one document, so either the corpus "
            "stopped repeating itself or the collapse stopped counting"
        )
        for hit in hits:
            assert len(hit.files) == hit.appears_in

    def test_results_are_ordered_by_score(self) -> None:
        loaded = load()
        hits = search(list(loaded.vectors[0]), limit=5)
        assert [hit.score for hit in hits] == sorted((hit.score for hit in hits), reverse=True)

    def test_a_passage_matches_itself_best(self) -> None:
        """The sanity check that the packing, the unpacking and the arithmetic all agree.
        A vector compared with itself is a cosine of 1."""
        loaded = load()
        hits = search(list(loaded.vectors[0]), limit=1)
        assert hits[0].score == pytest.approx(1.0, abs=0.01)


class TestTheLoaderRefusesABadArtifact:
    """The guards that only a malformed index can exercise.

    Mutation is what forced this class into existence: deleting either refusal changed no
    result, because every other test here loads the good committed file and the failure
    was unreachable. These are exactly the guards that matter when a rebuild goes wrong,
    which is the moment nobody is looking.
    """

    @staticmethod
    def with_index(monkeypatch: Any, tmp_path: Path, payload: dict[str, Any]) -> None:
        import curtail_core.corpus_search as module

        path = tmp_path / "corpus_index.json"
        path.write_text(json.dumps(payload))
        monkeypatch.setattr(module, "DATA", path)
        # The loader is cached for a whole process, so a test that does not clear it reads
        # whatever the previous test loaded and passes for the wrong reason.
        module.load.cache_clear()

    @staticmethod
    def sound(index: dict[str, Any]) -> dict[str, Any]:
        return {
            "source": dict(index["source"]),
            "counts": dict(index["counts"]),
            "absent": list(index["absent"]),
            "entries": [dict(entry) for entry in index["entries"][:3]],
        }

    def test_an_index_not_claiming_normalisation_is_refused(
        self, monkeypatch: Any, tmp_path: Path, index: dict[str, Any]
    ) -> None:
        """A dot product over non-unit vectors raises nothing. It orders results by a
        mixture of similarity and magnitude and reports them as a ranking."""
        payload = self.sound(index)
        payload["source"]["normalised"] = False
        self.with_index(monkeypatch, tmp_path, payload)
        with pytest.raises(CorpusIndexUnavailableError, match="not be a cosine"):
            load()

    def test_an_index_that_does_not_state_its_coverage_is_refused(
        self, monkeypatch: Any, tmp_path: Path, index: dict[str, Any]
    ) -> None:
        """Read with a default, a renamed field becomes "0 documents searched" on a page
        that otherwise looks entirely healthy."""
        payload = self.sound(index)
        del payload["counts"]["documents_represented"]
        self.with_index(monkeypatch, tmp_path, payload)
        with pytest.raises(CorpusIndexUnavailableError, match="documents_represented"):
            load()

    def test_a_missing_index_is_refused_with_the_reason(
        self, monkeypatch: Any, tmp_path: Path
    ) -> None:
        import curtail_core.corpus_search as module

        monkeypatch.setattr(module, "DATA", tmp_path / "absent.json")
        module.load.cache_clear()
        with pytest.raises(CorpusIndexUnavailableError, match="build_corpus_index"):
            load()

    def test_an_empty_index_is_refused_rather_than_returning_nothing(
        self, monkeypatch: Any, tmp_path: Path, index: dict[str, Any]
    ) -> None:
        """No results is a claim that the corpus is silent on the question. An unusable
        index must not be able to make that claim."""
        payload = self.sound(index)
        payload["entries"] = []
        self.with_index(monkeypatch, tmp_path, payload)
        with pytest.raises(CorpusIndexUnavailableError, match="no entries"):
            load()

    def test_an_index_that_lies_about_normalisation_is_refused(
        self, monkeypatch: Any, tmp_path: Path, index: dict[str, Any]
    ) -> None:
        """The gap a second-model review found, and the reason it is worth a named test.

        `"normalised": true` is a sentence the artifact writes about itself. The builder
        measures norms and `test_every_vector_is_unit_length` measures the committed file,
        and NEITHER of those runs in the container that answers a request. So an artifact
        claiming normalisation while carrying vectors of any length loaded happily and
        ranked by magnitude, with the module's own docstring promising otherwise.

        A claim and a check are different things. This repository has met that already as
        a quote nobody grepped against its source and as an eval artifact reporting a
        metric nothing computed.
        """
        payload = self.sound(index)
        assert payload["source"]["normalised"] is True, "the fixture must still claim it"
        # Same width, same shape, twice the length. Nothing about it looks wrong.
        dimensions = payload["source"]["dimensions"]
        stretched = [2.0 / dimensions**0.5] * dimensions
        payload["entries"][1]["vector"] = base64.b64encode(
            struct.pack(f"<{dimensions}e", *stretched)
        ).decode()
        self.with_index(monkeypatch, tmp_path, payload)

        with pytest.raises(CorpusIndexUnavailableError, match="rather than 1"):
            load()

    def test_the_refusal_names_the_offending_passage(
        self, monkeypatch: Any, tmp_path: Path, index: dict[str, Any]
    ) -> None:
        """A rebuild that goes wrong on one document is a different problem from one that
        goes wrong on all of them, and a bare "the index is bad" sends whoever diagnoses
        it back to the file to find out which."""
        payload = self.sound(index)
        dimensions = payload["source"]["dimensions"]
        payload["entries"][2]["vector"] = base64.b64encode(
            struct.pack(f"<{dimensions}e", *([0.5 / dimensions**0.5] * dimensions))
        ).decode()
        self.with_index(monkeypatch, tmp_path, payload)

        with pytest.raises(CorpusIndexUnavailableError) as raised:
            load()
        assert payload["entries"][2]["file"] in str(raised.value)

    def test_float16_rounding_is_still_accepted(
        self, monkeypatch: Any, tmp_path: Path, index: dict[str, Any]
    ) -> None:
        """Guards the guard from the other side. Vectors are stored as float16, so every
        norm is slightly off 1, and a check with no tolerance would reject the real
        artifact on every load: a guard that cannot pass is as useless as one that cannot
        fail."""
        payload = self.sound(index)
        self.with_index(monkeypatch, tmp_path, payload)
        loaded = load()
        norms = [sum(v * v for v in vector) ** 0.5 for vector in loaded.vectors]
        assert all(norm != 1.0 for norm in norms), (
            "no norm is exactly 1, so the tolerance is doing real work rather than "
            "passing by accident"
        )

    def test_a_vector_of_the_wrong_width_is_refused(
        self, monkeypatch: Any, tmp_path: Path, index: dict[str, Any]
    ) -> None:
        payload = self.sound(index)
        payload["entries"][0]["vector"] = base64.b64encode(struct.pack("<4e", 1, 0, 0, 0)).decode()
        self.with_index(monkeypatch, tmp_path, payload)
        with pytest.raises(CorpusIndexUnavailableError, match="dimensions"):
            load()

    @pytest.fixture(autouse=True)
    def _restore_the_real_index(self) -> Any:
        """Every test here repoints a module-level path and a process-wide cache. Without
        this the next test in the session searches a three-entry fixture."""
        yield
        import curtail_core.corpus_search as module

        module.load.cache_clear()


def builder() -> Any:
    """The build script, loaded by path like the TestFlight watcher's tests do."""
    import importlib.util

    script = REPO / "scripts" / "build_corpus_index.py"
    spec = importlib.util.spec_from_file_location("build_corpus_index", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestTheFiltersThemselves:
    """Direct tests for the filter, because the artifact tests cannot reach it.

    Mutation made the gap explicit: breaking the right-id filter or the email filter
    changed nothing, since every assertion above reads the COMMITTED artifact and the
    artifact only changes when somebody rebuilds it. A filter regression would therefore
    ship silently and only appear the next time the index was regenerated, which is
    exactly the wrong moment.

    Each string below is real text from the corpus, kept verbatim except that the names
    are replaced. Using genuine private names as a test fixture would put them in the
    repository, which is the thing being prevented.
    """

    @pytest.mark.parametrize(
        ("passage", "why"),
        [
            (
                "TEMPORARY SUSPENSION OF CURTAILMENTS WATER PRIMARY OWNER GROUP RIGHT ID "
                "8 SG005126 EXAMPLE, ALEX J. TRUST 8 SG002943 SAMPLE, RILEY R. & JAMIE",
                "the Attachment A layout, which the Primary Owner header alone identifies",
            ),
            (
                "SG005313 Placeholder, M. & J. Trust Shasta River 3/1/1895 SG005314 "
                "Example, Kim & Alex Shasta River 3/1/1895",
                "the List of Parties layout, whose rows defeated the counting rules",
            ),
            (
                "SG005318 Anonymous Holdings LLC Kellogg Spring 3/1/1895",
                "one identifier beside one name, which is a single table ROW and the "
                "shape that passed every count-based rule",
            ),
            (
                "To:Example, Alex@Waterboards <Alex.Example@Waterboards.ca.gov> Having "
                "trouble viewing this?",
                "a printed email header, which carried a real employee address",
            ),
        ],
    )
    def test_table_and_contact_material_is_rejected(self, passage: str, why: str) -> None:
        assert builder().is_table_fragment(passage), f"not filtered: {why}"

    @pytest.mark.parametrize(
        "passage",
        [
            "In light of the lack of precipitation in the forecast, current and predicted "
            "snow melt, and current and projected flows at the Fort Jones gage, the State "
            "Water Board is extending the conditional suspension of curtailments.",
            "This Order does not address water diversions under adjudicated rights for the "
            "Willow, Jullian, and Yreka tributaries to the Shasta River.",
            "Through reporting errors, administrative errors, or changes in rights, the "
            "rights identified in Attachment A were not included in State Water Board "
            "Order WR 2024-0024-DWR, even though they are within Priority Groups 1 "
            "through 8.",
        ],
    )
    def test_findings_are_kept(self, passage: str) -> None:
        """The other half, and the half that cost twenty documents when it was missing.

        An over-eager personal-data filter does not announce itself: it removes content
        and leaves every count looking fine. The third passage is Finding 8 of WR
        2026-0005-DWR, the single best piece of evidence this project has, and a filter
        that drops it has broken the product to protect nobody.
        """
        assert not builder().is_table_fragment(passage)

"""Embedding a question, and the search endpoint over it.

Offline. The transport is stubbed and nothing reaches Vertex, for the same reason the gage
and speech tests are stubbed: a test that depends on somebody else's uptime reports their
outage as your regression.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from curtail_agents.embeddings import (
    DIMENSIONS,
    MAX_QUERY_CHARACTERS,
    EmbeddingUnavailableError,
    embed_query,
)


class FakeCredentials:
    def __init__(self, *, quota_project_id: str | None = "curtail-505118") -> None:
        self.token = "fake-token"
        self.quota_project_id = quota_project_id

    def refresh(self, _request: Any) -> None:
        return None


def transport(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def vector(values: list[float]) -> dict[str, Any]:
    return {"predictions": [{"embeddings": {"values": values}}]}


def unnormalised() -> list[float]:
    """A vector whose length is nowhere near 1, which is what this model actually
    returns below its full dimensionality: 768 dimensions comes back at about 0.59."""
    return [0.59 / (DIMENSIONS**0.5)] * DIMENSIONS


def responder(body: dict[str, Any], status: int = 200) -> Any:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return handler


class TestTheQueryIsNormalisedHere:
    def test_the_returned_vector_is_unit_length(self) -> None:
        """The model does not return one, and a dot product over a non-unit vector is not
        a cosine. It ranks by a mixture of similarity and magnitude, raises nothing, and
        says nothing on screen."""
        result = embed_query(
            "when was curtailment lifted",
            client=transport(responder(vector(unnormalised()))),
            credentials=FakeCredentials(),
        )
        norm = sum(value * value for value in result) ** 0.5
        assert norm == pytest.approx(1.0, abs=1e-9)
        assert len(result) == DIMENSIONS

    def test_a_zero_vector_is_refused(self) -> None:
        """It would score every passage identically and present an arbitrary ordering as
        a search result."""
        with pytest.raises(EmbeddingUnavailableError, match="arbitrary ordering"):
            embed_query(
                "a question",
                client=transport(responder(vector([0.0] * DIMENSIONS))),
                credentials=FakeCredentials(),
            )


class TestItAsksForTheRightThing:
    def test_a_question_is_embedded_as_a_query_not_as_a_document(self) -> None:
        """The asymmetry that makes retrieval work. The model places a question near
        passages that ANSWER it rather than near passages that resemble it, and using one
        task type for both degrades the ranking without producing an error anywhere."""
        seen: dict[str, Any] = {}

        def capture(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json=vector(unnormalised()))

        embed_query("a question", client=transport(capture), credentials=FakeCredentials())
        assert seen["body"]["instances"][0]["task_type"] == "RETRIEVAL_QUERY"
        assert seen["body"]["parameters"]["outputDimensionality"] == DIMENSIONS
        assert "gemini-embedding-001" in seen["url"]
        assert "curtail-505118" in seen["url"]


class TestItRefusesRatherThanReturningSomething:
    def test_an_empty_question_is_refused(self) -> None:
        with pytest.raises(EmbeddingUnavailableError, match="no question"):
            embed_query("   ", client=transport(responder({})), credentials=FakeCredentials())

    def test_an_overlong_question_is_refused_before_sending(self) -> None:
        sent = False

        def watch(_request: httpx.Request) -> httpx.Response:
            nonlocal sent
            sent = True
            return httpx.Response(200, json=vector(unnormalised()))

        with pytest.raises(EmbeddingUnavailableError, match="limit is"):
            embed_query(
                "x" * (MAX_QUERY_CHARACTERS + 1),
                client=transport(watch),
                credentials=FakeCredentials(),
            )
        assert not sent

    def test_a_non_200_carries_the_status(self) -> None:
        with pytest.raises(EmbeddingUnavailableError, match="403"):
            embed_query(
                "a question",
                client=transport(responder({"error": "denied"}, status=403)),
                credentials=FakeCredentials(),
            )

    def test_a_response_with_no_prediction_is_refused(self) -> None:
        with pytest.raises(EmbeddingUnavailableError, match="no vector"):
            embed_query(
                "a question",
                client=transport(responder({"predictions": []})),
                credentials=FakeCredentials(),
            )

    def test_the_wrong_width_is_refused(self) -> None:
        """Silently comparing a 512-wide query against a 768-wide index would raise deep
        inside a zip, far from the cause."""
        with pytest.raises(EmbeddingUnavailableError, match="dimensions"):
            embed_query(
                "a question",
                client=transport(responder(vector([0.1] * 512))),
                credentials=FakeCredentials(),
            )

    def test_an_unreachable_service_is_refused(self) -> None:
        def boom(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host")

        with pytest.raises(EmbeddingUnavailableError, match="unreachable"):
            embed_query("a question", client=transport(boom), credentials=FakeCredentials())

    def test_a_non_json_body_is_refused(self) -> None:
        def html(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>error</html>")

        with pytest.raises(EmbeddingUnavailableError, match="non-JSON"):
            embed_query("a question", client=transport(html), credentials=FakeCredentials())

    def test_no_project_is_refused(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        with pytest.raises(EmbeddingUnavailableError, match="no project"):
            embed_query(
                "a question",
                client=transport(responder(vector(unnormalised()))),
                credentials=FakeCredentials(quota_project_id=None),
            )


class TestTheSearchEndpoint:
    @staticmethod
    def client() -> Any:
        from fastapi.testclient import TestClient

        from curtail_agents.api import app

        return TestClient(app)

    def test_it_reports_what_it_searched_and_what_it_could_not(self, monkeypatch: Any) -> None:
        """The coverage line is not decoration. Five documents are scanned images with no
        text layer, and a search that quietly covers 96 of 101 invites a reader to
        conclude the corpus is silent on a question it never read."""
        from curtail_agents import api
        from curtail_core.corpus_search import load

        index = load()
        monkeypatch.setattr(api, "embed_query", lambda _q: list(index.vectors[0]))
        response = self.client().get("/api/search", params={"q": "a real question"})

        assert response.status_code == 200
        body = response.json()
        assert body["model"] == "gemini-embedding-001"
        assert body["searched"]["documents"] == index.documents_represented
        assert body["searched"]["documents_in_corpus"] == index.documents_read
        assert len(body["not_searched"]) == index.documents_read - index.documents_represented
        for gap in body["not_searched"]:
            assert gap["why"].strip()
        assert body["results"], "a passage compared with itself should match"
        assert body["results"][0]["score"] == pytest.approx(1.0, abs=0.01)

    def test_an_empty_question_is_refused(self) -> None:
        response = self.client().get("/api/search", params={"q": "   "})
        assert response.status_code == 422

    def test_an_embedding_failure_is_a_503_not_an_empty_result_list(self, monkeypatch: Any) -> None:
        """An empty list is a claim that the corpus says nothing about the question. That
        is a completely different answer from "the question could not be embedded", and a
        reader cannot tell them apart from an empty table."""
        from curtail_agents import api

        def refuse(_question: str) -> None:
            raise EmbeddingUnavailableError("the embedding model was unreachable")

        monkeypatch.setattr(api, "embed_query", refuse)
        response = self.client().get("/api/search", params={"q": "a question"})
        assert response.status_code == 503
        assert "unreachable" in response.json()["detail"]

    def test_an_unusable_index_is_a_503(self, monkeypatch: Any) -> None:
        from curtail_agents import api
        from curtail_core.corpus_search import CorpusIndexUnavailableError

        monkeypatch.setattr(api, "embed_query", lambda _q: [0.0] * DIMENSIONS)

        def broken(*_args: Any, **_kwargs: Any) -> None:
            raise CorpusIndexUnavailableError("the corpus index is missing")

        monkeypatch.setattr(api, "corpus_search", broken)
        response = self.client().get("/api/search", params={"q": "a question"})
        assert response.status_code == 503
        assert "corpus index is missing" in response.json()["detail"]

    def test_the_limit_is_bounded(self, monkeypatch: Any) -> None:
        """A caller asking for a thousand results should get a bounded answer rather than
        the whole index rendered into a page."""
        from curtail_agents import api
        from curtail_core.corpus_search import load

        index = load()
        monkeypatch.setattr(api, "embed_query", lambda _q: list(index.vectors[0]))
        body = self.client().get("/api/search", params={"q": "a question", "limit": 999}).json()
        assert len(body["results"]) <= 20


class TestANonFiniteVectorNeverLeavesTheEmbeddingPath:
    """NaN survives every arithmetic step after it arrives.

    `norm == 0.0` is False for NaN, the division then yields an all-NaN vector, and the
    caller hands that straight to the ranking, where one NaN score reorders passages that
    have nothing to do with it. Refused where it enters.

    **The body is hand-written rather than encoded, and that is the realistic path.**
    Strict JSON has no NaN, so `json=` refuses to produce this shape at all, which reads
    at first like the case being impossible. It is not: Python's `json.loads` accepts bare
    `NaN`, `Infinity` and `-Infinity` literals by default, so a service emitting them is
    parsed into floats silently and no decoder ever complains.
    """

    @staticmethod
    def raw(literal: str) -> Any:
        values = ", ".join([literal] + ["0.0"] * (DIMENSIONS - 1))
        body = '{"predictions": [{"embeddings": {"values": [' + values + "]}}]}"

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content=body.encode(), headers={"content-type": "application/json"}
            )

        return handler

    @pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
    def test_it_is_refused(self, literal: str) -> None:
        with pytest.raises(EmbeddingUnavailableError, match="not a finite number"):
            embed_query(
                "a question",
                client=transport(self.raw(literal)),
                credentials=FakeCredentials(),
            )

    def test_the_literals_really_do_decode_into_floats(self) -> None:
        """The premise, asserted rather than assumed. If `json.loads` rejected these, the
        tests above would pass for a reason that has nothing to do with the guard."""
        import json as stdlib_json
        import math

        decoded = stdlib_json.loads("[NaN, Infinity, -Infinity]")
        assert math.isnan(decoded[0])
        assert math.isinf(decoded[1]) and math.isinf(decoded[2])

    def test_a_sound_vector_still_passes(self) -> None:
        """The other direction, so the new check cannot be rejecting everything."""
        result = embed_query(
            "a question",
            client=transport(responder(vector(unnormalised()))),
            credentials=FakeCredentials(),
        )
        assert len(result) == DIMENSIONS

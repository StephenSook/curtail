"""Turn a question into a vector with `gemini-embedding-001`, or refuse.

Only the network half lives here. Ranking is pure and lives in `curtail_core.corpus_search`
where it can be tested exactly, which is the same split every other networked thing in this
repository uses.

**The asymmetry that makes retrieval work, and that is easy to miss.** A question and a
passage are embedded with DIFFERENT task types: `RETRIEVAL_QUERY` for the question and
`RETRIEVAL_DOCUMENT` for the corpus. The model places a question near passages that ANSWER
it rather than near passages that resemble it, and using one task type for both quietly
degrades the ranking without producing an error anywhere.

**And the normalisation trap, stated once more because it does not announce itself.** The
model returns a unit vector at its full 3072 dimensions and a NON-unit vector at any
smaller `outputDimensionality`: measured, 768 dimensions comes back at about 0.59. A dot
product over those is not a cosine. It ranks by a mixture of similarity and magnitude, it
raises nothing, and nothing on screen says so.
"""

from __future__ import annotations

from typing import Any

import httpx

from curtail_agents.speech import (
    SpeechUnavailableError,
    resolve_credentials,
    resolve_project,
)

MODEL = "gemini-embedding-001"
LOCATION = "us-central1"
ENDPOINT = (
    "https://{location}-aiplatform.googleapis.com/v1/projects/{project}"
    "/locations/{location}/publishers/google/models/{model}:predict"
)

#: Must match the committed index. `corpus_search` refuses a mismatch rather than
#: comparing vectors of different widths.
DIMENSIONS = 768

#: A question, not a document. Long input here is a sign something other than a question
#: is being sent.
MAX_QUERY_CHARACTERS = 1000


class EmbeddingUnavailableError(RuntimeError):
    """The question could not be embedded, so no search may be reported."""


def embed_query(
    question: str,
    *,
    client: httpx.Client | None = None,
    credentials: Any = None,
) -> list[float]:
    """Embed a question as a unit vector.

    Raises:
        EmbeddingUnavailableError: on any failure. It never returns a zero vector or a
            partial one: a zero vector scores every passage identically and would present
            an arbitrary ordering as a search result.
    """
    text = question.strip()
    if not text:
        raise EmbeddingUnavailableError("there is no question to search for")
    if len(text) > MAX_QUERY_CHARACTERS:
        raise EmbeddingUnavailableError(
            f"the question is {len(text):,} characters and the limit is {MAX_QUERY_CHARACTERS:,}"
        )

    try:
        creds, discovered = resolve_credentials(credentials)
    except SpeechUnavailableError as exc:
        raise EmbeddingUnavailableError(str(exc)) from exc
    project = resolve_project(creds, discovered)
    if not project:
        raise EmbeddingUnavailableError(
            "no project is set, and the model path is addressed by project. "
            "Set GOOGLE_CLOUD_PROJECT."
        )

    owns = client is None
    http = client or httpx.Client(timeout=30.0)
    try:
        response = http.post(
            ENDPOINT.format(location=LOCATION, project=project, model=MODEL),
            json={
                "instances": [{"content": text, "task_type": "RETRIEVAL_QUERY"}],
                "parameters": {"outputDimensionality": DIMENSIONS},
            },
            headers={"Authorization": f"Bearer {creds.token}"},
        )
    except httpx.HTTPError as exc:
        raise EmbeddingUnavailableError(f"the embedding model was unreachable: {exc}") from exc
    finally:
        if owns:
            http.close()

    if response.status_code != 200:
        raise EmbeddingUnavailableError(
            f"the embedding model returned HTTP {response.status_code}: {response.text[:200]}"
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise EmbeddingUnavailableError("the embedding model returned a non-JSON body") from exc

    predictions = body.get("predictions") or []
    if not predictions:
        raise EmbeddingUnavailableError("the embedding model returned no vector")
    values = predictions[0].get("embeddings", {}).get("values")
    if not values or len(values) != DIMENSIONS:
        raise EmbeddingUnavailableError(
            f"expected {DIMENSIONS} dimensions and got {len(values) if values else 0}"
        )

    norm = sum(value * value for value in values) ** 0.5
    if norm == 0.0:
        raise EmbeddingUnavailableError(
            "a zero vector came back. It would score every passage identically and "
            "present an arbitrary ordering as a search result."
        )
    return [value / norm for value in values]


__all__ = [
    "DIMENSIONS",
    "MAX_QUERY_CHARACTERS",
    "MODEL",
    "EmbeddingUnavailableError",
    "embed_query",
]

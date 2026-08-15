"""The field surface: evidence in, authority never out.

The tests that matter here are not the happy path. They are the refusals, the
idempotency, and the structural guarantee that a phone cannot sign anything.
"""

from __future__ import annotations

import ast
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from curtail_agents.api import app
from curtail_agents.field_store import (
    FieldEvidenceError,
    InMemoryFieldStore,
    measurement_from,
)
from curtail_core.basins import Basin

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "agents" / "src" / "curtail_agents" / "data"


def _payload(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "idempotency_key": "abcdef1234",
        "observer": "S Sookra",
        "discharge_cfs": 78.4,
        "captured_at": "2025-07-22T16:00:00+00:00",
    }
    base.update(over)
    return base


class TestItRefusesWhatIsNotEvidence:
    """Each refusal names the field. The reader is standing in a river."""

    def test_a_non_finite_reading_is_refused(self) -> None:
        """NaN compares false against every threshold, so it reads as a river above it.

        This project has already shipped one NaN that recommended lifting curtailment.
        A number arriving from a phone over a flaky link is exactly where the next one
        would come from.
        """
        with pytest.raises(FieldEvidenceError, match="finite"):
            measurement_from(_payload(discharge_cfs=float("nan")), Basin.SCOTT)

    def test_a_negative_reading_is_refused(self) -> None:
        with pytest.raises(FieldEvidenceError, match="negative"):
            measurement_from(_payload(discharge_cfs=-1), Basin.SCOTT)

    def test_an_absurd_reading_is_refused_as_a_unit_error(self) -> None:
        with pytest.raises(FieldEvidenceError, match="unit error"):
            measurement_from(_payload(discharge_cfs=1e9), Basin.SCOTT)

    def test_a_naive_timestamp_is_refused(self) -> None:
        """A compliance record with no offset is one somebody reads in the wrong zone."""
        with pytest.raises(FieldEvidenceError, match="offset"):
            measurement_from(_payload(captured_at="2025-07-22T16:00:00"), Basin.SCOTT)

    def test_an_unnamed_observer_is_refused(self) -> None:
        """Attested means attested BY somebody. Anonymous evidence is not evidence."""
        with pytest.raises(FieldEvidenceError, match="observer"):
            measurement_from(_payload(observer=""), Basin.SCOTT)

    def test_a_missing_or_malformed_key_is_refused(self) -> None:
        with pytest.raises(FieldEvidenceError, match="idempotency_key"):
            measurement_from(_payload(idempotency_key="short"), Basin.SCOTT)

    def test_a_malformed_photo_digest_is_refused(self) -> None:
        with pytest.raises(FieldEvidenceError, match="sha-256"):
            measurement_from(_payload(photo_sha256="not-a-digest"), Basin.SCOTT)

    def test_an_impossible_coordinate_is_refused(self) -> None:
        with pytest.raises(FieldEvidenceError, match="latitude"):
            measurement_from(_payload(latitude=91.0), Basin.SCOTT)


class TestTheStoreIsAppendOnlyAndIdempotent:
    def test_the_same_key_twice_stores_once(self) -> None:
        """The device cannot tell a retry from a repeat, so the key decides.

        iOS has no Background Sync, so a flush is interrupted by the screen locking.
        Retrying must not invent a second measurement.
        """
        store = InMemoryFieldStore()
        one = measurement_from(_payload(), Basin.SCOTT)
        rows, appended = store.append(one)
        assert appended and len(rows) == 1
        rows, appended = store.append(one)
        assert not appended, "the same idempotency key appended twice"
        assert len(rows) == 1

    def test_a_different_key_with_the_same_reading_is_a_second_measurement(self) -> None:
        """Two people can measure the same flow. Dedup is on the key, not the value."""
        store = InMemoryFieldStore()
        store.append(measurement_from(_payload(), Basin.SCOTT))
        rows, appended = store.append(
            measurement_from(_payload(idempotency_key="zzzz9999"), Basin.SCOTT)
        )
        assert appended and len(rows) == 2

    def test_basins_do_not_bleed_into_each_other(self) -> None:
        store = InMemoryFieldStore()
        store.append(measurement_from(_payload(), Basin.SCOTT))
        assert store.load(Basin.SHASTA) == []

    def test_the_store_exposes_no_update_or_delete(self) -> None:
        """Append-only is a property of the INTERFACE, not a promise in a docstring.

        A store with an edit method eventually gets an edit call, and an edited
        measurement is an edited piece of evidence.
        """
        store = InMemoryFieldStore()
        for forbidden in ("update", "delete", "remove", "edit", "set", "replace"):
            assert not hasattr(store, forbidden), f"the evidence store exposes {forbidden}"

    def test_memory_reports_itself_as_not_durable(self) -> None:
        """An empty list from a volatile store must never read as 'nobody has been out'."""
        store = InMemoryFieldStore()
        assert store.durable is False
        assert "not survive" in store.describe() or "does not" in store.describe()


class TestTheDeviceNeverHoldsAuthority:
    """Hard rule 14, asserted against the shipped source rather than trusted.

    The phone authenticates a human and submits append-only evidence. It never signs,
    never mutates order state, never writes the rights ledger. This reads the field
    module and the field routes and fails if any of those verbs appear in them.
    """

    def test_the_field_module_signs_nothing(self) -> None:
        """Matched on IDENTIFIERS in executable code, not on raw substrings.

        The first version searched the file text for "sign" and matched the word
        "design" inside a docstring, which is the imprecise-guard shape this project
        keeps finding in other people's code. Comments and docstrings are stripped, and
        the match is on whole names, so prose about the design cannot trip it and a real
        call to `sign` cannot hide from it.
        """
        source = (REPO / "agents" / "src" / "curtail_agents" / "field_store.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if not isinstance(body, list) or not body:
                continue
            if not isinstance(
                node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
            ):
                continue
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                body.pop(0)
        code = ast.unparse(tree)

        for forbidden in ("QUEUE", "demo_token", "record_order", "sign", "SignatoryRole"):
            assert not re.search(rf"\b{re.escape(forbidden)}\b", code), (
                f"the field evidence module uses {forbidden!r} in executable code, so "
                "the device path touches authority. Hard rule 14: the phone says yes, "
                "the server acts."
            )

    def test_the_field_route_response_says_what_it_does_not_do(self) -> None:
        client = TestClient(app)
        body = client.post(
            "/api/field/measurement/scott", json=_payload(idempotency_key="routecheck1")
        ).json()
        assert body["confers_no_authority"]
        assert body["provenance"] == "field_attested", (
            "a reading typed on a riverbank is labelled as attested, never as a gage record"
        )

    def test_submitting_evidence_does_not_queue_an_order(self) -> None:
        """The clearest possible statement of the rule: nothing appears to sign."""
        client = TestClient(app)
        before = client.get("/api/queue").json()
        client.post("/api/field/measurement/scott", json=_payload(idempotency_key="noqueue0001"))
        after = client.get("/api/queue").json()
        assert before == after, "submitting a field measurement changed the approval queue"


class TestTheInstallableSurface:
    def test_the_field_route_serves(self) -> None:
        response = TestClient(app).get("/field")
        assert response.status_code == 200
        assert "Curtail Field" in response.text

    def test_the_manifest_is_valid_and_installable(self) -> None:
        response = TestClient(app).get("/manifest.webmanifest")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/manifest+json")
        manifest = json.loads(response.text)
        assert manifest["start_url"] == "/field"
        assert manifest["display"] == "standalone"
        sizes = {icon["sizes"] for icon in manifest["icons"]}
        assert {"192x192", "512x512"} <= sizes, "an installable app needs 192 and 512"
        purposes = {icon.get("purpose") for icon in manifest["icons"]}
        assert "maskable" in purposes, (
            "no maskable icon, so Android crops the wordmark into a circle"
        )

    def test_the_worker_is_served_from_the_root_with_scope(self) -> None:
        """A worker's scope cannot exceed its own path.

        Served from anywhere but the root, the field route would be offline-capable in
        name only.
        """
        response = TestClient(app).get("/sw.js")
        assert response.status_code == 200
        assert response.headers.get("service-worker-allowed") == "/"
        assert response.headers.get("cache-control") == "no-cache", (
            "a cached worker pins an old app on a device that is rarely online"
        )

    def test_the_worker_never_caches_an_api_response(self) -> None:
        """Stale evidence is worse than no evidence."""
        worker = (DATA / "sw.js").read_text()
        assert "/api/" in worker and "return;" in worker
        assert 'startsWith("/api/")' in worker, (
            "the worker does not exempt /api/, so it can serve a cached ledger that "
            "shows a watermaster a state that is not the state"
        )

    def test_every_icon_the_manifest_names_is_served(self) -> None:
        client = TestClient(app)
        manifest = json.loads(client.get("/manifest.webmanifest").text)
        for icon in manifest["icons"]:
            got = client.get(icon["src"])
            assert got.status_code == 200, f"{icon['src']} is named and not served"
            assert got.content[:8] == b"\x89PNG\r\n\x1a\n", f"{icon['src']} is not a PNG"

    def test_the_icon_route_is_an_allowlist(self) -> None:
        assert TestClient(app).get("/icons/../api.py").status_code == 404

    def test_the_field_page_states_the_three_sync_states_in_words(self) -> None:
        """Only the third means the evidence entered the record."""
        page = TestClient(app).get("/field").text
        for phrase in ("Saved on device", "Syncing", "Recorded in ledger"):
            assert phrase in page, f"the field route never says {phrase!r}"

    def test_the_field_page_carries_the_demo_disclaimer(self) -> None:
        page = TestClient(app).get("/field").text
        assert "Not an official government system" in page


class TestTheApiRefusesBadSubmissions:
    def test_a_bad_payload_is_422_with_a_reason(self) -> None:
        response = TestClient(app).post(
            "/api/field/measurement/scott", json=_payload(discharge_cfs="banana")
        )
        assert response.status_code == 422
        assert "discharge_cfs" in response.json()["detail"]

    def test_an_unknown_basin_is_404(self) -> None:
        response = TestClient(app).post("/api/field/measurement/rhine", json=_payload())
        assert response.status_code == 404

    def test_reading_back_reports_durability_and_never_a_bare_empty_list(self) -> None:
        client = TestClient(app)
        client.post("/api/field/measurement/shasta", json=_payload(idempotency_key="readback01"))
        body = client.get("/api/field/measurements/shasta").json()
        assert body["count"] >= 1
        assert "durable" in body and "store" in body, (
            "the read does not say where the evidence lives, so an empty result would "
            "be indistinguishable from an unreachable store"
        )
        assert (
            "confers no authority" in body["disclaimer"].lower() or "evidence" in body["disclaimer"]
        )


def test_a_measurement_round_trips_its_state() -> None:
    m = measurement_from(_payload(latitude=41.6, longitude=-122.85), Basin.SCOTT)
    state = m.to_state()
    assert state["basin"] == "scott"
    assert state["discharge_cfs"] == 78.4
    assert datetime.fromisoformat(state["captured_at"]).tzinfo is not None
    assert state["latitude"] == 41.6
    assert datetime.now(UTC) is not None


class TestTheAndroidTrustLink:
    """Digital Asset Links, and its refusal, which is the part that matters."""

    def test_it_refuses_when_no_fingerprint_is_configured(self, monkeypatch) -> None:
        """An empty asset links file fails verification SILENTLY.

        The only symptom of serving `[]` is an installed app showing a browser URL bar,
        with nothing anywhere saying why. A 503 naming the variable is a diagnosis.
        """
        monkeypatch.delenv("TWA_SHA256_FINGERPRINT", raising=False)
        response = TestClient(app).get("/.well-known/assetlinks.json")
        assert response.status_code == 503
        assert "TWA_SHA256_FINGERPRINT" in response.json()["detail"]

    def test_it_serves_a_valid_statement_when_configured(self, monkeypatch) -> None:
        monkeypatch.setenv("TWA_SHA256_FINGERPRINT", "AB:CD:" + ":".join(["00"] * 30))
        response = TestClient(app).get("/.well-known/assetlinks.json")
        assert response.status_code == 200
        statement = json.loads(response.text)
        assert statement[0]["relation"] == ["delegate_permission/common.handle_all_urls"]
        target = statement[0]["target"]
        assert target["namespace"] == "android_app"
        assert target["package_name"] == "app.curtail.field"
        assert target["sha256_cert_fingerprints"][0].startswith("AB:CD:")

    def test_no_signing_material_is_committed(self) -> None:
        """The keystore is a credential. It is not in this repository and must not be."""
        tracked = (REPO / ".gitignore").read_text()
        assert "*.keystore" in tracked or "*.jks" in tracked, (
            "add *.keystore and *.jks to .gitignore before anybody generates one"
        )

"""The console's failure paths, executed in a real browser.

**Why a browser and not a grep.** The three defects these tests cover were found by
an adversarial review of the page, and every one of them lives in control flow that a
structural test cannot reach: an unguarded `await`, a fallback that fires on a missing
field, and two responses racing to write the same element. A test asserting that the
source contains the word `catch` would pass against a `catch` that swallowed the error
and left the calm PENDING label on screen, which is the exact defect. This project has
a rule about that shape, learned from a policy table sitting beside decorators that
enforced nothing: structure present, force absent.

**What is being defended.** A reading below the operative minimum is a legal trigger.
Displaying one, or displaying nothing, in the neutral grey that means "waiting" is the
worst output this surface can produce, because it reads as calm rather than as absent.
So every path that cannot produce a fully trustworthy classification must land on a
state that says so in words.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import traceback
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import uvicorn
from playwright.sync_api import Page, Route

from curtail_agents.api import app

#: Separated from the default run, and section [tool.pytest.ini_options] says why:
#: one Playwright test disables pytest-asyncio's auto mode for everything that runs
#: after it. A dedicated CI job runs these, and `test_ci_still_runs_the_browser_suite`
#: in the default suite fails if that job ever disappears.
pytestmark = pytest.mark.browser


class _Server(uvicorn.Server):
    """Serves the real app on an ephemeral port, so the page under test is the page
    that ships rather than a fixture resembling it."""

    def install_signal_handlers(self) -> None:  # pragma: no cover - threaded server
        return


@pytest.fixture(scope="module")
def console_url() -> Iterator[str]:
    """The real ASGI app over real HTTP, because the defects under test are in the
    browser's own fetch and JSON handling and an in-process test client never
    exercises either.

    `ws="none"` is load-bearing rather than tidiness. This suite runs under
    `filterwarnings = error`, and uvicorn's default protocol autodetection imports
    `websockets.legacy`, which emits a DeprecationWarning that then raises INSIDE
    this thread. The thread dies, `started` never flips, and the only symptom is a
    timeout that says nothing about the cause: the first version of this fixture
    reported "the console server never started" for what was really a warning being
    promoted to an error two frames down. Nothing here speaks websockets, so the
    protocol is switched off rather than the warning gate relaxed.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    server = _Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", ws="none"))

    # The thread's exception is CAPTURED, not lost. A crashed server and a slow one
    # are different failures wanting different fixes, and a bare timeout conflates
    # them: an errored check must never read as a plain negative result.
    crash: list[str] = []

    def run() -> None:
        try:
            server.run()
        except BaseException:  # pragma: no cover - only on a broken fixture
            crash.append(traceback.format_exc())

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 15
    while not server.started:
        if crash:  # pragma: no cover - only on a broken fixture
            raise RuntimeError(f"the console server thread died:\n{crash[0]}")
        if time.monotonic() > deadline:  # pragma: no cover - only on a broken fixture
            raise RuntimeError("the console server did not start within 15 seconds")
        time.sleep(0.02)

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=10)


def _status(page: Page) -> str:
    """What a human actually reads, uppercased by CSS.

    `inner_text` returns the RENDERED text, so `text-transform: uppercase` is
    already applied and the labels come back in caps. Asserting against the raw
    source casing would be asserting against something nobody sees.
    """
    return page.locator("#out .status").inner_text().upper()


def _classes(page: Page) -> str:
    return page.locator("#out .status").get_attribute("class") or ""


def _sound_recommendation() -> dict[str, Any]:
    """A response that passes every check, so a test can break exactly one thing."""
    return {
        "action": "consider_curtailment",
        "deterministic_facts": {
            "observed_cfs": 41.0,
            "operative_minimum_cfs": 50.0,
            "shortfall_cfs": 9.0,
            "recommended_extent_rank": 1,
            "rights_considered": 1,
            "rights_reached": 1,
        },
        "ledger": [
            {
                "right_id": "A031522",
                "priority_date": "2003-07-30",
                "grouping": "Tier A",
                "citation": "23 CCR 875.5(b)(1)(A)",
                "would_be_curtailed": True,
            }
        ],
        "judgment_inputs": [],
        "provenance": {
            "reading": {"source": "unsourced", "note": "typed by the caller"},
            "rights": {"summary": "Addendum 6"},
        },
        "disclaimer": "A recommendation.",
    }


def _fulfil(body: dict[str, Any], status: int = 200) -> Callable[[Route], None]:
    def handler(route: Route) -> None:
        route.fulfill(status=status, content_type="application/json", body=json.dumps(body))

    return handler


def _render_id(page: Page, card: str) -> str:
    """The generation the card's current content belongs to, or empty before any."""
    return page.locator(card).get_attribute("data-render") or ""


def _settle_after(page: Page, card: str, previous: str) -> None:
    """Wait for a card to render a generation NEWER than the one already on screen.

    A check that waits for "a result is present" is satisfied instantly by the PREVIOUS
    result. That is exactly what happened here: the ledger card refuses on page load,
    because the console opens on the Scott basin and no Scott rights table is ingested,
    so a later wait for "rows or a refusal" returned against the load-time refusal and
    asserted against a view that had not moved. Same shape as waiting for the absence
    of a transient state, one layer up.
    """
    page.wait_for_function(
        "([card, previous]) => {"
        "  const el = document.querySelector(card);"
        "  return !!el && !!el.dataset.render && el.dataset.render !== previous;"
        "}",
        arg=[card, previous],
        timeout=20_000,
    )


def _settle(page: Page) -> None:
    """Wait for a TERMINAL state, which is the only thing worth waiting for.

    Deliberately NOT `networkidle`: the defects under test are precisely the ones
    where the network settles and the page stays on the waiting label forever, so a
    network-based wait would hang rather than assert.

    **And deliberately not the absence of the waiting label either, which is what
    this did first.** That condition is already true before `classify` runs at all,
    because the page opens on its own "Awaiting a reading" placeholder, so the wait
    could return immediately and every assertion after it would read the DOM at an
    arbitrary moment. It was caught by driving the DEPLOYED console, where one run
    printed the pending label and the settled class in the same breath: two reads
    that cannot both be true. Nothing failed, which is the problem. A wait keyed to
    the absence of a transient state is a flake with a timer on it, and this
    repository is about to sit frozen through a month of judging.

    Both terminal states are identifiable by structure rather than by wording: a
    rendered classification has a `.reading`, and refused or unavailable has a
    `.refusal`. Neither exists in the placeholder or while waiting.
    """
    page.wait_for_function(
        "() => !!(document.querySelector('#out .reading')"
        "        || document.querySelector('#out .refusal'))",
        timeout=10_000,
    )


class TestTheHappyPathStillWorks:
    """A guard suite that only proves failures is a suite that would pass against a
    page rendering nothing at all."""

    def test_it_classifies_a_real_reading(self, page: Page, console_url: str) -> None:
        page.goto(console_url)
        _settle(page)

        assert "NEAR THRESHOLD" in _status(page)
        assert "RESTRICT" in _status(page)
        assert "48.7 cfs" in page.locator("#out .reading").inner_text()
        assert "50 cfs" in page.locator("#out .against").inner_text()
        assert "s-conditional" in _classes(page)

    def test_a_refusal_is_shown_with_its_reason(self, page: Page, console_url: str) -> None:
        """The engine declining is a real answer, and the reason is the point of it."""
        page.goto(console_url)
        _settle(page)
        page.select_option("#basin", "shasta")
        page.fill("#at", "2021-08-15")
        page.click("#go")

        page.wait_for_function(
            "() => document.querySelector('#out .status').innerText.includes('REFUSED')",
            timeout=10_000,
        )
        assert page.locator("#out .refusal").inner_text().strip(), (
            "the page said Refused and gave no reason, which is the calm-blank shape"
        )
        assert "s-pending" not in _classes(page)


class TestAnUntrustworthyAnswerIsNeverShownAsAResult:
    """The three findings from the adversarial pass, one test each, plus the class of
    defect they share: something that is not a classification rendered as one."""

    def test_a_network_failure_says_unavailable_not_waiting(
        self, page: Page, console_url: str
    ) -> None:
        """Was: `await fetch(...)` unguarded, so a rejection left the grey waiting
        label on screen permanently. A judge would read that as loading."""
        page.route("**/api/classify/**", lambda route: route.abort())
        page.goto(console_url)
        _settle(page)

        assert "UNAVAILABLE" in _status(page)
        assert "s-pending" not in _classes(page)
        assert "s-system" in _classes(page)

    def test_a_non_json_body_says_unavailable(self, page: Page, console_url: str) -> None:
        """A proxy or gateway returning an HTML error page with a 200 is the realistic
        version of this, and `response.json()` rejects on it."""
        page.route(
            "**/api/classify/**",
            lambda route: route.fulfill(
                status=200, content_type="text/html", body="<html>gateway</html>"
            ),
        )
        page.goto(console_url)
        _settle(page)

        assert "UNAVAILABLE" in _status(page)
        assert "s-system" in _classes(page)

    @pytest.mark.parametrize("dropped", ["classification", "direction", "minimum_cfs"])
    def test_a_missing_field_shows_nothing_rather_than_part_of_an_answer(
        self, page: Page, console_url: str, dropped: str
    ) -> None:
        """Was: a missing `classification` fell through to the neutral PENDING label,
        and a missing number printed the word undefined beside the unit."""
        body = {
            "basin": "scott",
            "observed_cfs": 41.0,
            "minimum_cfs": 50.0,
            "classification": "flow_below_minimum",
            "direction": "curtail",
            "disclaimer": "A recommendation.",
        }
        body.pop(dropped)
        page.route(
            "**/api/classify/**",
            lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps(body)
            ),
        )
        page.goto(console_url)
        _settle(page)

        assert "UNAVAILABLE" in _status(page), f"a response missing {dropped} rendered anyway"
        assert dropped in page.locator("#out .refusal").inner_text()
        assert "undefined" not in page.locator("#out").inner_text()
        assert "s-pending" not in _classes(page)

    @pytest.mark.parametrize("blanked", ["direction", "disclaimer"])
    def test_a_present_but_blank_text_field_is_refused(
        self, page: Page, console_url: str, blanked: str
    ) -> None:
        """Presence is not enough for a field that is interpolated into a sentence.

        An `in body` check passes an empty string, which then renders as a blank where
        the direction should be, or as nothing at all on the line that tells a reader
        the system does not self-execute. This guard existed for one round without a
        test, and an untested guard is not a working one.
        """
        body = {
            "basin": "scott",
            "observed_cfs": 41.0,
            "minimum_cfs": 50.0,
            "classification": "flow_below_minimum",
            "direction": "curtail",
            "disclaimer": "A recommendation.",
        }
        body[blanked] = "   "
        page.route(
            "**/api/classify/**",
            lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps(body)
            ),
        )
        page.goto(console_url)
        _settle(page)

        assert "UNAVAILABLE" in _status(page)
        assert f"carries no {blanked}" in page.locator("#out .refusal").inner_text()
        assert "s-pending" not in _classes(page)

    def test_an_unknown_classification_is_not_drawn_as_pending(
        self, page: Page, console_url: str
    ) -> None:
        """The drift case with teeth. If the engine gains an event type and this page
        does not, the neutral fallback would paint a real trigger calm grey."""
        page.route(
            "**/api/classify/**",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "basin": "scott",
                        "observed_cfs": 41.0,
                        "minimum_cfs": 50.0,
                        "classification": "an_event_type_added_later",
                        "direction": "curtail",
                        "disclaimer": "A recommendation.",
                    }
                ),
            ),
        )
        page.goto(console_url)
        _settle(page)

        assert "UNAVAILABLE" in _status(page)
        assert "s-pending" not in _classes(page)
        assert "an_event_type_added_later" in page.locator("#out .refusal").inner_text()

    def test_a_slow_earlier_answer_cannot_repaint_over_a_newer_one(
        self, page: Page, console_url: str
    ) -> None:
        """Every completion wrote the same element, so the automatic request on load
        could land after a click and paint a valid-looking classification for a
        reading other than the one in the inputs.

        **The delay is imposed in the BROWSER, and that is the whole test.** The first
        version slept inside the Playwright route handler, which serializes handlers,
        so the two requests completed strictly in order and never raced at all. It
        passed against a page with the guard deliberately removed, which makes it a
        test that proved nothing while reading as proof. Holding the first `fetch`
        open on the page side is the only way to make the older answer land last.
        """
        page.add_init_script(
            """
            (() => {
              const real = window.fetch;
              let calls = 0;
              window.__releaseFirst = null;
              window.fetch = (...args) => {
                calls += 1;
                if (calls === 1) {
                  return new Promise((resolve, reject) => {
                    window.__releaseFirst = () => real(...args).then(resolve, reject);
                  });
                }
                return real(...args);
              };
            })();
            """
        )

        seen: list[float] = []

        def answer(route: Route) -> None:
            # Echo the reading back rather than counting requests, so which answer is
            # which never depends on arrival order.
            asked = float(parse_qs(urlparse(route.request.url).query)["cfs"][0])
            seen.append(asked)
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "basin": "scott",
                        "observed_cfs": asked,
                        "minimum_cfs": 50.0,
                        "classification": "flow_below_minimum",
                        "direction": "curtail",
                        "disclaimer": "A recommendation.",
                    }
                ),
            )

        page.route("**/api/classify/**", answer)
        page.goto(console_url)

        # The automatic request on load is now held open, so the page is still on the
        # waiting label. A watermaster types a new reading over it.
        page.wait_for_function("() => window.__releaseFirst !== null", timeout=10_000)
        page.fill("#cfs", "222.2")
        page.click("#go")
        page.wait_for_function(
            "() => document.querySelector('#out .reading')"
            "  && document.querySelector('#out .reading').innerText.includes('222.2')",
            timeout=15_000,
        )

        # Now let the older answer land. This is the moment the defect fired.
        page.evaluate("window.__releaseFirst()")
        page.wait_for_timeout(1_500)

        assert "222.2 cfs" in page.locator("#out .reading").inner_text()
        assert "48.7" not in page.locator("#out").inner_text(), (
            "the stale answer from page load repainted the page, so the reading on "
            "screen is not the reading that was asked for"
        )
        assert sorted(seen) == [48.7, 222.2], f"expected both readings to be asked, saw {seen}"


class TestTheLedgerCard:
    """The per-right ledger, rendered. This is the artifact that makes a signed order
    reviewable, so a judge has to be able to see it rather than curl for it."""

    def test_it_renders_a_row_per_right_with_its_authority(
        self, page: Page, console_url: str
    ) -> None:
        page.goto(console_url)
        _settle_after(page, "#rec", "")
        before = _render_id(page, "#rec")
        page.select_option("#basin", "shasta")
        page.fill("#cfs", "46.5")
        page.fill("#at", "2026-06-15")
        page.click("#go")
        _settle_after(page, "#rec", before)

        rows = page.locator("#rec tbody tr")
        assert rows.count() > 50, f"only {rows.count()} ledger rows rendered"
        first = rows.first.inner_text()
        assert "23 CCR 875.5" in first, f"a ledger row carries no authority: {first!r}"

    def test_it_names_the_provenance_of_both_inputs(self, page: Page, console_url: str) -> None:
        """Showing where the rights came from and not where the reading came from
        implies the reading is sourced as carefully. It is a number someone typed."""
        page.goto(console_url)
        _settle_after(page, "#rec", "")
        before = _render_id(page, "#rec")
        page.select_option("#basin", "shasta")
        page.fill("#cfs", "46.5")
        page.fill("#at", "2026-06-15")
        page.click("#go")
        _settle_after(page, "#rec", before)

        shown = page.locator("#rec .js-prov").inner_text()
        assert "not from USGS" in shown, f"the reading's provenance is absent: {shown!r}"
        assert "Addendum 6" in shown, "the rights provenance is absent"

    def test_an_answer_missing_a_provenance_side_is_not_rendered(
        self, page: Page, console_url: str
    ) -> None:
        """A partial answer is shown as untrustworthy rather than rendered, because the
        gap is exactly the thing a reader fills in wrongly."""
        page.route(
            "**/api/recommendation/**",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "action": "consider_curtailment",
                        "deterministic_facts": {
                            "observed_cfs": 41.0,
                            "operative_minimum_cfs": 50.0,
                            "shortfall_cfs": 9.0,
                            "recommended_extent_rank": 1,
                            "rights_considered": 1,
                            "rights_reached": 1,
                        },
                        "ledger": [],
                        "judgment_inputs": [],
                        "provenance": {"rights": {"summary": "Addendum 6"}},
                        "disclaimer": "A recommendation.",
                    }
                ),
            ),
        )
        page.goto(console_url)
        _settle_after(page, "#rec", "")

        status = page.locator("#rec .status").inner_text().upper()
        assert "UNAVAILABLE" in status
        assert "reading came from" in page.locator("#rec .refusal").inner_text()
        assert page.locator("#rec tbody tr").count() == 0

    @pytest.mark.parametrize(
        ("label", "provenance", "expected"),
        [
            (
                "both sides present but empty",
                {"reading": {}, "rights": {}},
                "states no source",
            ),
            (
                "reading names no note",
                {"reading": {"source": "unsourced"}, "rights": {"summary": "Addendum 6"}},
                "states no note",
            ),
            (
                "rights summary is blank",
                {"reading": {"source": "unsourced", "note": "typed"}, "rights": {"summary": "   "}},
                "states no summary",
            ),
            (
                "a side is a string rather than a record",
                {"reading": "unsourced", "rights": {"summary": "Addendum 6"}},
                "does not say where its reading came from",
            ),
        ],
    )
    def test_an_empty_or_malformed_provenance_side_is_not_rendered(
        self,
        page: Page,
        console_url: str,
        label: str,
        provenance: dict[str, Any],
        expected: str,
    ) -> None:
        """Checking that a side is PRESENT was a truthy test, so `{reading: {}}` passed
        it and the page printed "Reading: undefined". A validator that checks a
        container rather than its contents proves the shape and not the answer."""
        page.route(
            "**/api/recommendation/**",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "action": "consider_curtailment",
                        "deterministic_facts": {
                            "observed_cfs": 41.0,
                            "operative_minimum_cfs": 50.0,
                            "shortfall_cfs": 9.0,
                            "recommended_extent_rank": 1,
                            "rights_considered": 1,
                            "rights_reached": 1,
                        },
                        "ledger": [],
                        "judgment_inputs": [],
                        "provenance": provenance,
                        "disclaimer": "A recommendation.",
                    }
                ),
            ),
        )
        page.goto(console_url)
        _settle_after(page, "#rec", "")

        assert "UNAVAILABLE" in page.locator("#rec .status").inner_text().upper(), label
        assert expected in page.locator("#rec .refusal").inner_text(), label
        assert "undefined" not in page.locator("#rec").inner_text()

    @pytest.mark.parametrize("dropped", ["observed_cfs", "shortfall_cfs", "rights_reached"])
    def test_a_missing_fact_is_not_printed_as_undefined(
        self, page: Page, console_url: str, dropped: str
    ) -> None:
        """Same class, on the numbers. The fact tiles are the one place on this page
        whose whole job is to be exact."""
        facts = {
            "observed_cfs": 41.0,
            "operative_minimum_cfs": 50.0,
            "shortfall_cfs": 9.0,
            "recommended_extent_rank": 1,
            "rights_considered": 1,
            "rights_reached": 1,
        }
        facts.pop(dropped)
        page.route(
            "**/api/recommendation/**",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "action": "consider_curtailment",
                        "deterministic_facts": facts,
                        "ledger": [],
                        "judgment_inputs": [],
                        "provenance": {
                            "reading": {"source": "unsourced", "note": "typed"},
                            "rights": {"summary": "Addendum 6"},
                        },
                        "disclaimer": "A recommendation.",
                    }
                ),
            ),
        )
        page.goto(console_url)
        _settle_after(page, "#rec", "")

        assert "UNAVAILABLE" in page.locator("#rec .status").inner_text().upper()
        assert dropped in page.locator("#rec .refusal").inner_text()
        assert "undefined" not in page.locator("#rec").inner_text()

    def test_an_extent_of_none_is_a_real_answer_and_still_renders(
        self, page: Page, console_url: str
    ) -> None:
        """`recommended_extent_rank: null` means curtailment reaches nobody, which the
        Core returns whenever flow is at or above the minimum. Treating it as missing
        would refuse to display the most common good-news answer there is."""
        page.route(
            "**/api/recommendation/**",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "action": "consider_suspension",
                        "deterministic_facts": {
                            "observed_cfs": 90.0,
                            "operative_minimum_cfs": 50.0,
                            "shortfall_cfs": 0.0,
                            "recommended_extent_rank": None,
                            "rights_considered": 2,
                            "rights_reached": 0,
                        },
                        "ledger": [],
                        "judgment_inputs": [],
                        "provenance": {
                            "reading": {"source": "unsourced", "note": "typed"},
                            "rights": {"summary": "Addendum 6"},
                        },
                        "disclaimer": "A recommendation.",
                    }
                ),
            ),
        )
        page.goto(console_url)
        _settle_after(page, "#rec", "")

        assert "CONSIDER SUSPENSION" in page.locator("#rec .status").inner_text().upper()
        assert "none" in page.locator("#rec .facts").inner_text().lower()

    @pytest.mark.parametrize(
        ("label", "mutate", "expected"),
        [
            ("no disclaimer", {"disclaimer": None}, "carries no disclaimer"),
            ("blank disclaimer", {"disclaimer": "   "}, "carries no disclaimer"),
        ],
    )
    def test_a_missing_disclaimer_is_not_interpolated_as_undefined(
        self,
        page: Page,
        console_url: str,
        label: str,
        mutate: dict[str, Any],
        expected: str,
    ) -> None:
        """It is interpolated straight into the provenance line, and it is the sentence
        telling a reader that nothing here self-executes. Absent, it printed the word
        "undefined" on exactly that line."""
        page.route("**/api/recommendation/**", _fulfil(_sound_recommendation() | mutate))
        page.goto(console_url)
        _settle_after(page, "#rec", "")

        assert "UNAVAILABLE" in page.locator("#rec .status").inner_text().upper(), label
        assert expected in page.locator("#rec .refusal").inner_text()
        assert "undefined" not in page.locator("#rec").inner_text()

    @pytest.mark.parametrize(
        ("label", "line", "expected"),
        [
            (
                "no disposition",
                {"right_id": "A1", "grouping": "Tier A", "citation": "23 CCR 875.5(b)(1)(A)"},
                "states no disposition",
            ),
            (
                "no right id",
                {"grouping": "Tier A", "citation": "23 CCR 875.5", "would_be_curtailed": True},
                "states no right_id",
            ),
            (
                "no authority",
                {"right_id": "A1", "grouping": "Tier A", "would_be_curtailed": True},
                "states no citation",
            ),
        ],
    )
    def test_a_ledger_line_missing_a_field_is_not_rendered(
        self,
        page: Page,
        console_url: str,
        label: str,
        line: dict[str, Any],
        expected: str,
    ) -> None:
        """`would_be_curtailed` is read through a ternary, so an absent field rendered
        as "not reached": a right the engine DID reach, shown as untouched. That is
        worse than a visible gap, because it is a plausible wrong answer."""
        page.route(
            "**/api/recommendation/**", _fulfil(_sound_recommendation() | {"ledger": [line]})
        )
        page.goto(console_url)
        _settle_after(page, "#rec", "")

        assert "UNAVAILABLE" in page.locator("#rec .status").inner_text().upper(), label
        assert expected in page.locator("#rec .refusal").inner_text()
        assert page.locator("#rec tbody tr").count() == 0
        assert "not reached" not in page.locator("#rec").inner_text()

    @pytest.mark.parametrize(
        ("label", "facts", "expected"),
        [
            ("fractional count", {"rights_considered": 2.5}, "not a whole count"),
            ("negative count", {"rights_reached": -1}, "not a whole count"),
            (
                "more reached than considered",
                {"rights_considered": 1, "rights_reached": 4},
                "more rights are reached than were considered",
            ),
            ("negative discharge", {"observed_cfs": -3.0}, "negative"),
            ("fractional extent", {"recommended_extent_rank": 1.5}, "neither a rank"),
        ],
    )
    def test_an_impossible_count_is_not_shown_as_a_fact(
        self,
        page: Page,
        console_url: str,
        label: str,
        facts: dict[str, Any],
        expected: str,
    ) -> None:
        """Finite is not the same as possible.

        These numbers land in the status line as a statement about how many diverters
        are shut off. 2.5 rights, or more reached than considered, is not a gap a reader
        would notice; it is an authoritative-looking wrong answer.
        """
        body = _sound_recommendation()
        body["deterministic_facts"] = body["deterministic_facts"] | facts
        page.route("**/api/recommendation/**", _fulfil(body))
        page.goto(console_url)
        _settle_after(page, "#rec", "")

        assert "UNAVAILABLE" in page.locator("#rec .status").inner_text().upper(), label
        assert expected in page.locator("#rec .refusal").inner_text(), label

    @pytest.mark.parametrize(
        ("label", "judgment", "expected"),
        [
            ("not a list", "a string", "not a list"),
            ("a list of objects", [{"note": "x"}], "not readable text"),
            ("a blank entry", ["  "], "not readable text"),
        ],
    )
    def test_garbled_judgment_inputs_are_refused(
        self,
        page: Page,
        console_url: str,
        label: str,
        judgment: Any,
        expected: str,
    ) -> None:
        """This list is the discretion the regulation reserves to a human. Joining an
        object into it puts "[object Object]" where a statutory consideration belongs,
        which is worse than most fields rather than better."""
        page.route(
            "**/api/recommendation/**",
            _fulfil(_sound_recommendation() | {"judgment_inputs": judgment}),
        )
        page.goto(console_url)
        _settle_after(page, "#rec", "")

        assert "UNAVAILABLE" in page.locator("#rec .status").inner_text().upper(), label
        assert expected in page.locator("#rec .refusal").inner_text(), label
        assert "[object Object]" not in page.locator("#rec").inner_text()

    def test_a_ledger_line_with_an_unreadable_priority_is_refused(
        self, page: Page, console_url: str
    ) -> None:
        """`text()` renders whatever it is handed, so an object arrives on screen as
        "[object Object]" in the priority column beside a real right id."""
        line = _sound_recommendation()["ledger"][0] | {"priority_date": {"year": 2003}}
        page.route(
            "**/api/recommendation/**", _fulfil(_sound_recommendation() | {"ledger": [line]})
        )
        page.goto(console_url)
        _settle_after(page, "#rec", "")

        assert "UNAVAILABLE" in page.locator("#rec .status").inner_text().upper()
        assert "no readable priority date" in page.locator("#rec .refusal").inner_text()
        assert "[object Object]" not in page.locator("#rec").inner_text()

    @pytest.mark.parametrize("body", ["null", '{"detail": {"why": "nested"}}', "{}"])
    def test_a_refusal_with_no_usable_reason_still_lands_on_a_refusal(
        self, page: Page, console_url: str, body: str
    ) -> None:
        """`body.detail` was read straight. A JSON `null` body THREW here, after the
        try block, so the card stayed on the waiting label forever, which reads as
        loading rather than as refused."""
        page.route(
            "**/api/recommendation/**",
            lambda route: route.fulfill(status=422, content_type="application/json", body=body),
        )
        page.goto(console_url)
        _settle_after(page, "#rec", "")

        shown = page.locator("#rec .status").inner_text().upper()
        assert "REFUSED" in shown or "UNAVAILABLE" in shown
        assert "Asking the engine" not in page.locator("#rec").inner_text()
        assert page.locator("#rec .refusal").inner_text().strip()
        assert "[object Object]" not in page.locator("#rec").inner_text()

    def test_a_superseded_run_does_not_repaint_either_card(
        self, page: Page, console_url: str
    ) -> None:
        """The two cards share one token per RUN.

        Each used to mint its own, and `run` called the second card unconditionally, so
        a stale classify that had already been superseded still started a recommendation
        and claimed the newest generation. The cards could then show different requests
        while both carried valid-looking terminal stamps.
        """
        page.add_init_script(
            """
            (() => {
              const real = window.fetch;
              let calls = 0;
              window.__releaseFirst = null;
              window.fetch = (...args) => {
                calls += 1;
                if (calls === 1) {
                  return new Promise((resolve, reject) => {
                    window.__releaseFirst = () => real(...args).then(resolve, reject);
                  });
                }
                return real(...args);
              };
            })();
            """
        )
        page.goto(console_url)
        page.wait_for_function("() => window.__releaseFirst !== null", timeout=15_000)

        # A second run overtakes the first, which is still held open on its classify.
        page.select_option("#basin", "shasta")
        page.fill("#cfs", "46.5")
        page.fill("#at", "2026-06-15")
        page.click("#go")
        _settle_after(page, "#rec", "")
        settled = _render_id(page, "#rec")

        page.evaluate("window.__releaseFirst()")
        page.wait_for_timeout(2_500)

        assert _render_id(page, "#rec") == settled, (
            "a superseded run repainted the ledger card after being overtaken"
        )
        assert "46.5" in page.locator("#out .reading").inner_text()
        assert page.locator("#rec tbody tr").count() > 50, (
            "the ledger card lost the newer run's result"
        )

    def test_a_basin_with_no_table_shows_the_refusal_not_an_empty_ledger(
        self, page: Page, console_url: str
    ) -> None:
        """An empty ledger would read as "no right is affected", which is a real answer
        and the wrong one.

        **The default basin used to BE the fixture, because Scott had no rights table.**
        It has one now, 384 rights from the Board's own Attachment A, so loading the page
        exercises the happy path and this assertion started measuring the wrong thing. The
        refusal still matters, so the gap is served deliberately instead of borrowed.
        """
        page.route(
            "**/api/recommendation/**",
            _fulfil({"detail": "no rights table could be loaded for this basin"}, status=503),
        )
        page.goto(console_url)
        _settle_after(page, "#rec", "")

        assert page.locator("#rec tbody tr").count() == 0
        assert "REFUSED" in page.locator("#rec .status").inner_text().upper()
        assert "no rights table" in page.locator("#rec .refusal").inner_text()

    def test_the_default_basin_now_renders_a_full_ledger(
        self, page: Page, console_url: str
    ) -> None:
        """The other half: the basin that used to refuse now paints the Board's own
        groups, which is what a judge opening the console sees first."""
        page.goto(console_url)
        _settle_after(page, "#rec", "")
        assert page.locator("#rec tbody tr").count() == 384

    def test_an_unreachable_engine_says_unavailable_on_the_ledger_card_too(
        self, page: Page, console_url: str
    ) -> None:
        page.route("**/api/recommendation/**", lambda route: route.abort())
        page.goto(console_url)
        _settle_after(page, "#rec", "")

        status = page.locator("#rec .status")
        assert "UNAVAILABLE" in status.inner_text().upper()
        assert "s-pending" not in (status.get_attribute("class") or "")


class TestTheApprovalQueueCard:
    """The signature, driven the way a watermaster would drive it.

    The review is INLINE rather than behind a confirm dialog, and that is a design
    decision with evidence under it: a 2025 npj Digital Medicine study found 35 to 45
    percent of erroneous drafts were submitted entirely unedited by clinicians reviewing
    them. Every entry in this field has a human-in-the-loop checkbox, and a checkbox is
    what that study measured failing. The draft, its digest and every blocking finding go
    on the page, with the signature underneath them.
    """

    def _queued(self, page: Page) -> None:
        page.route(
            "**/api/queue",
            _fulfil(
                {
                    "persistence": "in-process, not durable",
                    "pending": [
                        {
                            "order_id": "DRAFT-SHASTA-2026-06-15-pass",
                            "state": "awaiting_signature",
                            "requires_role": "deputy_director",
                            "digest": "a" * 64,
                            "created_at": "2026-06-15T12:00:00+00:00",
                            "blocking_violations": [],
                            "guard_reason": "draft matches the computed ledger",
                        }
                    ],
                    "decided": [],
                }
            ),
        )

    def test_it_lists_what_is_awaiting_signature(self, page: Page, console_url: str) -> None:
        self._queued(page)
        page.goto(console_url)
        _settle_after(page, "#queue", "")
        assert "DRAFT-SHASTA-2026-06-15-pass" in page.locator("#queue").inner_text()
        assert "AWAITING SIGNATURE" in page.locator("#queue").inner_text().upper()

    def test_the_draft_and_its_digest_are_shown_before_the_signature(
        self, page: Page, console_url: str
    ) -> None:
        """The reviewer reads the order, not a summary of it."""
        self._queued(page)
        page.route(
            "**/api/queue/DRAFT-SHASTA-2026-06-15-pass",
            _fulfil(
                {
                    "persistence": "in-process, not durable",
                    "order_id": "DRAFT-SHASTA-2026-06-15-pass",
                    "draft_text": "DRAFT ORDER under 23 CCR 875. Rights curtailed: A031522.",
                    "digest": "a" * 64,
                    "state": "awaiting_signature",
                    "requires_role": "deputy_director",
                    "created_at": "2026-06-15T12:00:00+00:00",
                    "blocking_violations": [],
                    "guard_reason": "draft matches the computed ledger",
                    "decided": None,
                    "disclaimer": "A draft for signature. Nothing here is in force.",
                }
            ),
        )
        page.goto(console_url)
        _settle_after(page, "#queue", "")
        before = _render_id(page, "#queue")
        page.get_by_role("button", name="Open and sign").click()
        _settle_after(page, "#queue", before)

        shown = page.locator("#queue").inner_text()
        assert "A031522" in shown, "the draft text was not put in front of the officer"
        assert "a" * 64 in shown, "the digest binding the approval was not shown"
        assert "Nothing here is in force" in shown

    def test_an_unverified_draft_names_what_signing_would_override(
        self, page: Page, console_url: str
    ) -> None:
        """875(b)(3) preserves the discretion to override, and a signature against
        findings nobody saw is not an exercise of it."""
        page.route(
            "**/api/queue",
            _fulfil(
                {
                    "persistence": "in-process, not durable",
                    "pending": [
                        {
                            "order_id": "DRAFT-SHASTA-2026-06-15-escalate",
                            "state": "unverified",
                            "requires_role": "deputy_director",
                            "digest": "b" * 64,
                            "created_at": "2026-06-15T12:00:00+00:00",
                            "blocking_violations": ["unsupported right: A999999"],
                            "guard_reason": "1 right asserted with no basis in the ledger",
                        }
                    ],
                    "decided": [],
                }
            ),
        )
        page.route(
            "**/api/queue/DRAFT-SHASTA-2026-06-15-escalate",
            _fulfil(
                {
                    "persistence": "in-process, not durable",
                    "order_id": "DRAFT-SHASTA-2026-06-15-escalate",
                    "draft_text": "DRAFT ORDER under 23 CCR 875 naming A999999.",
                    "digest": "b" * 64,
                    "state": "unverified",
                    "requires_role": "deputy_director",
                    "created_at": "2026-06-15T12:00:00+00:00",
                    "blocking_violations": ["unsupported right: A999999"],
                    "guard_reason": "1 right asserted with no basis in the ledger",
                    "decided": None,
                    "disclaimer": "A draft for signature. Nothing here is in force.",
                }
            ),
        )
        page.goto(console_url)
        _settle_after(page, "#queue", "")
        before = _render_id(page, "#queue")
        page.get_by_role("button", name="Open and sign").click()
        _settle_after(page, "#queue", before)

        shown = page.locator("#queue").inner_text()
        assert "UNVERIFIED" in shown.upper()
        assert "A999999" in shown, "the finding being overridden was not named"
        assert "overrid" in page.locator("#do-sign").inner_text().lower(), (
            "the button did not say that pressing it overrides a finding"
        )

    def test_a_refused_signature_says_why(self, page: Page, console_url: str) -> None:
        """Every refusal here is a decision the domain layer made: the wrong officer, a
        stale digest, an unacknowledged finding."""
        self._queued(page)
        page.route(
            "**/api/queue/DRAFT-SHASTA-2026-06-15-pass",
            _fulfil(
                {
                    "persistence": "in-process, not durable",
                    "order_id": "DRAFT-SHASTA-2026-06-15-pass",
                    "draft_text": "DRAFT ORDER under 23 CCR 875.",
                    "digest": "a" * 64,
                    "state": "awaiting_signature",
                    "requires_role": "deputy_director",
                    "created_at": "2026-06-15T12:00:00+00:00",
                    "blocking_violations": [],
                    "guard_reason": "draft matches the computed ledger",
                    "decided": None,
                    "disclaimer": "A draft for signature. Nothing here is in force.",
                }
            ),
        )
        page.route(
            "**/api/session**",
            _fulfil({"detail": "CURTAIL_SIGNING_KEY is not set"}, status=503),
        )
        page.goto(console_url)
        _settle_after(page, "#queue", "")
        before = _render_id(page, "#queue")
        page.get_by_role("button", name="Open and sign").click()
        _settle_after(page, "#queue", before)
        opened = _render_id(page, "#queue")
        page.locator("#do-sign").click()
        _settle_after(page, "#queue", opened)

        assert "REFUSED" in page.locator("#queue .status").inner_text().upper()
        assert "CURTAIL_SIGNING_KEY" in page.locator("#queue .refusal").inner_text()

    def test_the_queue_says_it_is_not_durable(self, page: Page, console_url: str) -> None:
        """No database is wired, and a surface that looked durable would claim what the
        deployment cannot support."""
        self._queued(page)
        page.goto(console_url)
        _settle_after(page, "#queue", "")
        assert "not durable" in page.locator("#queue").inner_text()


def _sound_traversal() -> dict[str, Any]:
    """One full traversal that passes every check, so a test can break exactly one thing.

    The reading is 45.3 cfs, which Addendum 6 actually records, against the 50 cfs June
    minimum. **The obvious candidate was 39.3, and it is retired**: it propagated out of
    a research haul into the constitution and four artifacts, and reading the primary
    document showed it appears nowhere in it. The constitution still carries it, so the
    citation guard, not the constitution, is what a fixture should be checked against.

    The distribution below mirrors the shape the deployed service returns: the party
    served by certified mail counts, the diverter reached by email does not, so the
    result is NOT reportable as service.
    """
    return {
        "correlation_id": "fleet-shasta-2026-06-16T12:00:00+00:00",
        "basin": "shasta",
        "observed_cfs": 45.3,
        "observed_at": "2026-06-16T12:00:00+00:00",
        "nodes": [
            {"node": "gage_sentinel", "error": "", "produced": ["direction", "event"]},
            {"node": "allocation_core", "error": "", "produced": ["recommendation"]},
            {"node": "order_scribe", "error": "", "produced": ["draft"]},
            {"node": "herald", "error": "", "produced": ["artifact_action", "delivery"]},
        ],
        "classification": {
            "event_type": "flow_below_minimum",
            "observed_cfs": 45.3,
            "minimum_cfs": 50.0,
            "direction": "restrict",
        },
        "recommendation": {
            "action": "consider_curtailment",
            "determination_belongs_to": "deputy_director",
            "recommended_extent_rank": 2,
            "rights_considered": 71,
            "rights_reached": 71,
            "judgment_inputs": [],
        },
        "recommendation_unavailable": None,
        "draft": {
            "verdict": "pass",
            "guard_reason": "draft matches the computed ledger",
            "attempts": 1,
            "model": "gemini-3.5-flash",
            "may_reach_pdf": True,
            "text": "DRAFT CURTAILMENT ORDER",
        },
        "draft_unavailable": None,
        "artifact_action": "impose",
        "delivery": {
            "lane": "legal_service",
            "may_report_as_served": False,
            "synthetic": True,
            "legally_served": ["SYNTHETIC-party-1"],
            "escalations": [
                "SYNTHETIC-diverter-1 was reached over email but that did not effect "
                "service under Water Code 1121: the channel is not a permitted method"
            ],
            "attempts": [],
            "skipped_as_duplicate": [],
            "possible_duplicate_service": [],
            "records": [],
            "lane_note": "Water Code 1121 permits four methods and an email is none of them.",
        },
        "delivery_unavailable": None,
        "recipients_are_synthetic": True,
        "persistence": "in-process and per-request",
        "disclaimer": "A recommendation and a draft.",
    }


class TestTheFleetCardNeverDrawsATraversalItCannotTrust:
    """The only control that runs the GRAPH rather than a function the graph wraps.

    It carries the same refusal discipline as the other two cards, and one obligation
    they do not have: it must never draw a synthetic distribution as though it might be
    real, and it must never let a successful notification read as legal service.
    """

    def _run(self, page: Page, console_url: str) -> None:
        page.goto(console_url)
        page.get_by_role("button", name="Run the fleet").click()
        _settle_after(page, "#fleetout", "")

    def test_a_traversal_is_attributed_to_each_member_by_name(
        self, page: Page, console_url: str
    ) -> None:
        """Attribution is the product. A payload carrying only the final answer is
        indistinguishable from one function computing it."""
        page.route("**/api/fleet/**", _fulfil(_sound_traversal()))
        self._run(page, console_url)
        shown = page.locator("#fleetout").inner_text()
        for member in ("gage_sentinel", "allocation_core", "order_scribe", "herald"):
            assert member in shown, f"{member} produced output and the card does not name it"

    def test_a_delivery_that_is_not_service_is_never_labelled_served(
        self, page: Page, console_url: str
    ) -> None:
        """The sharpest claim this project makes. A notification-lane delivery can be
        entirely successful and is never service, so the word is gated on its own field
        rather than inferred from a records list that looks green."""
        page.route("**/api/fleet/**", _fulfil(_sound_traversal()))
        self._run(page, console_url)
        shown = page.locator("#fleetout").inner_text()
        assert "NOT reportable as served".upper() in shown.upper()
        assert "Water Code 1121" in shown, "the card does not say why it is not service"

    def test_a_refused_traversal_says_why_rather_than_waiting(
        self, page: Page, console_url: str
    ) -> None:
        """The calm-grey defect. A refusal left on the waiting label reads as calm."""
        page.route(
            "**/api/fleet/**",
            _fulfil({"detail": "the Scribe is unavailable: the model refused"}, status=503),
        )
        self._run(page, console_url)
        assert "REFUSED" in page.locator("#fleetout .status").inner_text().upper()
        assert "the model refused" in page.locator("#fleetout .refusal").inner_text()

    def test_a_traversal_that_names_no_nodes_is_not_drawn(
        self, page: Page, console_url: str
    ) -> None:
        """Attribution missing is a DIFFERENT claim, not a weaker one. Drawing it would
        let a partial response read as a fleet run."""
        body = _sound_traversal()
        body["nodes"] = []
        page.route("**/api/fleet/**", _fulfil(body))
        self._run(page, console_url)
        text_shown = page.locator("#fleetout").inner_text().upper()
        assert "UNAVAILABLE" in text_shown
        assert "gage_sentinel".upper() not in text_shown

    def test_a_distribution_not_confirmed_synthetic_is_not_drawn(
        self, page: Page, console_url: str
    ) -> None:
        """Rule 4 permits synthetic notification contacts only and requires that they be
        visibly labelled. If the service stops asserting it, the card refuses rather than
        quietly dropping the label, which is the shape that lets a demo imply real
        recipients."""
        body = _sound_traversal()
        body["recipients_are_synthetic"] = False
        page.route("**/api/fleet/**", _fulfil(body))
        self._run(page, console_url)
        assert "UNAVAILABLE" in page.locator("#fleetout .status").inner_text().upper()
        assert "synthetic" in page.locator("#fleetout .refusal").inner_text()

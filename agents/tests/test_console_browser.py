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
from types import MappingProxyType
from typing import Any, ClassVar
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
                # A priority has three states, so the line carries the raw year field
                # and the composed rendering the table shows. The fixture has to hold
                # the real shape or every test mocking it would be asserting against a
                # response the service does not produce.
                "priority_year_only": None,
                "priority_as_stated": "2003-07-30",
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
              // Holds the first CLASSIFY open, matched by url rather than by call
              // order. Keying on "call number one" made this a hostage to whatever
              // else the page fetches on load: adding the live gage strip made call
              // one a gage read, so the classify was never held, the superseded
              // scenario never occurred, and the assertion failed against a page that
              // was behaving correctly.
              const real = window.fetch;
              let held = false;
              window.__releaseFirst = null;
              window.fetch = (...args) => {
                const url = String(args[0] || "");
                if (!held && url.includes("/api/classify/")) {
                  held = true;
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

    def test_a_year_only_priority_reaches_the_screen_as_a_year(
        self, page: Page, console_url: str
    ) -> None:
        """Against the REAL service, because this defect only exists where it renders.

        The table read `entry.priority_date`, and `text()` turns a null into the words
        "not stated". So `SG005441`, whose 1977 is precisely what puts it after the 1932
        Shasta decree and therefore in Tier A, was displayed as a right with no priority
        at all. Not blank: the console actively printed the denial. The Scribe and the
        API were both taught the distinction before this surface was.
        """
        page.goto(console_url)
        _settle_after(page, "#rec", "")
        before = _render_id(page, "#rec")
        page.select_option("#basin", "shasta")
        page.fill("#cfs", "46.5")
        page.fill("#at", "2026-06-15")
        page.click("#go")
        _settle_after(page, "#rec", before)

        row = page.locator("#rec tbody tr", has_text="SG005441")
        assert row.count() == 1, "the year-only right is not on screen at all"
        rendered = row.first.inner_text()
        assert "1977" in rendered, f"the year the placement rests on is missing: {rendered!r}"
        assert "year only" in rendered, (
            f"a bare 1977 in a priority column reads as a date: {rendered!r}"
        )
        assert "not stated" not in rendered, (
            f"the console denies knowing a year it was given: {rendered!r}"
        )
        assert "Tier A" in rendered

    def test_a_year_only_line_rendered_as_nothing_is_refused(
        self, page: Page, console_url: str
    ) -> None:
        """The guard has to catch the original defect, not just the fixed code.

        A server that states a year and renders it as "not stated" is contradicting
        itself, and that is the exact payload the console used to produce. Refusing is
        the only safe answer: a priority column reading "not stated" beside a curtailed
        right is a plausible wrong answer, not a visible gap.
        """
        line = _sound_recommendation()["ledger"][0] | {
            "priority_date": None,
            "priority_year_only": 1977,
            "priority_as_stated": "not stated",
        }
        page.route(
            "**/api/recommendation/**", _fulfil(_sound_recommendation() | {"ledger": [line]})
        )
        page.goto(console_url)
        _settle_after(page, "#rec", "")

        assert "UNAVAILABLE" in page.locator("#rec .status").inner_text().upper()
        assert "does not say its year is a year alone" in (
            page.locator("#rec .refusal").inner_text()
        )

    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            pytest.param(
                {
                    "priority_date": None,
                    "priority_year_only": 9999,
                    "priority_as_stated": "9999 (year only, no month or day in the record)",
                },
                "unreadable priority year",
                id="a year that has not happened",
            ),
            pytest.param(
                {
                    "priority_date": None,
                    "priority_year_only": 1650,
                    "priority_as_stated": "1650 (year only, no month or day in the record)",
                },
                "unreadable priority year",
                id="a year before appropriation existed here",
            ),
            pytest.param(
                {
                    "priority_date": None,
                    "priority_year_only": 1977,
                    "priority_as_stated": "1977 (approximately)",
                },
                "does not say its year is a year alone",
                id="a qualifier asserting precision the record lacks",
            ),
            pytest.param(
                {"priority_date": None, "priority_year_only": 1977, "priority_as_stated": "1977"},
                "does not say its year is a year alone",
                id="a bare year, which reads as a date",
            ),
            pytest.param(
                {
                    "priority_date": None,
                    "priority_year_only": 1977,
                    "priority_as_stated": "1977 (year only, actually a full date)",
                },
                "does not say its year is a year alone",
                id="a qualifier that contradicts itself",
            ),
            pytest.param(
                {
                    "priority_date": None,
                    "priority_year_only": 1977,
                    "priority_as_stated": (
                        "1977 (year only, no month or day in the record) and probably July"
                    ),
                },
                "does not say its year is a year alone",
                id="the correct wording with an assertion appended",
            ),
            pytest.param(
                {
                    "priority_date": None,
                    "priority_year_only": 1977,
                    "priority_as_stated": "1978 (year only, no month or day in the record)",
                },
                "does not say its year is a year alone",
                id="a rendering naming a different year than its own field",
            ),
            pytest.param(
                {
                    "priority_date": "2003-07-30",
                    "priority_year_only": 1977,
                    "priority_as_stated": "2003-07-30",
                },
                "states both a date and a bare year",
                id="both a date and a year",
            ),
            pytest.param(
                {
                    "priority_date": "2003-07-30",
                    "priority_year_only": None,
                    "priority_as_stated": "1999-01-01",
                },
                "renders a priority its date contradicts",
                id="a rendering disagreeing with its own date",
            ),
            pytest.param(
                {"priority_date": None, "priority_year_only": None, "priority_as_stated": "1977"},
                "claims a priority the record lacks",
                id="a priority invented from nothing",
            ),
        ],
    )
    def test_the_priority_guard_refuses_each_way_it_can_be_wrong(
        self, page: Page, console_url: str, line: dict[str, Any], expected: str
    ) -> None:
        """One case per branch, because a guard is only as good as its loosest clause.

        The first version bounded the year with `> 1500` and checked only that the
        rendering STARTED with it. So the year 9999 passed, and so did
        "1977 (approximately)". The unbounded upper end is the dangerous one: a priority
        is senior BECAUSE it is early, so an absurd future year sorts a right to the
        junior end of the ladder, which is the end that gets curtailed.
        """
        payload = _sound_recommendation()
        payload["ledger"] = [payload["ledger"][0] | line]
        page.route("**/api/recommendation/**", _fulfil(payload))
        page.goto(console_url)
        _settle_after(page, "#rec", "")

        assert "UNAVAILABLE" in page.locator("#rec .status").inner_text().upper()
        assert expected in page.locator("#rec .refusal").inner_text()

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
              // Holds the first CLASSIFY open, matched by url rather than by call
              // order. Keying on "call number one" made this a hostage to whatever
              // else the page fetches on load: adding the live gage strip made call
              // one a gage read, so the classify was never held, the superseded
              // scenario never occurred, and the assertion failed against a page that
              // was behaving correctly.
              const real = window.fetch;
              let held = false;
              window.__releaseFirst = null;
              window.fetch = (...args) => {
                const url = String(args[0] || "");
                if (!held && url.includes("/api/classify/")) {
                  held = true;
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


class TestTheLiveGageStrip:
    """The strip that makes the project's opening sentence true on screen.

    Every case stubs `/api/gage/*` rather than reaching USGS. The point under test is
    what the PAGE does with an answer, and a browser test that depends on a federal
    agency's uptime is a flake with a deadline attached.
    """

    LIVE: MappingProxyType[str, Any] = MappingProxyType(
        {
            "basin": "scott",
            "gage_id": "USGS-11519500",
            "observed_cfs": 4.91,
            "unit": "ft^3/s",
            "observed_at": "2026-08-16T02:30:00+00:00",
            "qualifier": "P",
            "provisional": True,
            "minimum_cfs": 30.0,
            "classification": "flow_below_minimum",
            "direction": "toward_restriction",
            "provenance": "usgs_live",
            "fetched_at": "2026-08-16T02:46:00+00:00",
            "age_seconds": 0.0,
            "recommendation_only": True,
            "disclaimer": "A recommendation.",
        }
    )

    def _strip(self, page: Page, console_url: str, body: Any, status: int = 200) -> None:
        # dict(), because the fixture is a mappingproxy so it cannot be mutated by one
        # test and silently change another, and json.dumps refuses to serialise one.
        page.route("**/api/gage/**", _fulfil(dict(body), status))
        page.goto(console_url)
        page.wait_for_selector('#live-strip[data-loaded="1"]', timeout=20_000)

    def test_a_live_reading_renders_value_rule_and_verdict(
        self, page: Page, console_url: str
    ) -> None:
        self._strip(page, console_url, self.LIVE)
        card = page.locator('#live-strip .gage[data-basin="scott"]')
        assert card.get_attribute("data-state") == "flow_below_minimum"
        text = card.inner_text()
        assert "4.91 cfs" in text
        assert "30 cfs minimum" in text
        assert "BELOW MINIMUM" in text.upper()

    def test_the_timestamp_is_pacific_whatever_timezone_the_judge_is_in(
        self, page: Page, console_url: str
    ) -> None:
        """The same instant is Aug 16 in UTC and Aug 15 in Pacific, and the flow
        schedule changes on specific calendar dates, so a bare local timestamp can
        print the wrong DATE beside a reading. The page pins the zone and names it.
        """
        self._strip(page, console_url, self.LIVE)
        prov = page.locator('#live-strip .gage[data-basin="scott"] .prov').inner_text()
        assert "PDT" in prov, f"the zone is not named: {prov}"
        assert "Aug 15, 2026" in prov, f"rendered in the viewer's zone, not Pacific: {prov}"

    def test_a_cached_reading_says_cached_and_shows_its_age(
        self, page: Page, console_url: str
    ) -> None:
        """A five-minute-old value and a five-hour-old value are indistinguishable
        without this, and the difference is whether what is on screen is the river."""
        self._strip(
            page, console_url, {**self.LIVE, "provenance": "usgs_cached", "age_seconds": 900}
        )
        prov = page.locator('#live-strip .gage[data-basin="scott"] .prov').inner_text()
        assert "cached" in prov.lower()
        assert "15 min ago" in prov

    def test_an_unavailable_gage_shows_the_reason_and_no_number(
        self, page: Page, console_url: str
    ) -> None:
        """The worst output this surface can produce is a zero standing in for a
        sensor that did not answer, and the second worst is a blank with no reason."""
        self._strip(page, console_url, {"detail": "USGS-11519500 could not be read"}, status=503)
        card = page.locator('#live-strip .gage[data-basin="scott"]')
        assert card.get_attribute("data-state") == "unavailable"
        text = card.inner_text()
        assert "could not be read" in text
        assert "cfs" not in text, f"a number was rendered for an unreadable gage: {text}"

    def test_an_incomplete_answer_is_refused_rather_than_part_rendered(
        self, page: Page, console_url: str
    ) -> None:
        """Same rule the classifier card already follows: a reading below the minimum
        drawn in a calm neutral is the worst thing this page can show."""
        partial = {k: v for k, v in self.LIVE.items() if k != "minimum_cfs"}
        self._strip(page, console_url, partial)
        card = page.locator('#live-strip .gage[data-basin="scott"]')
        assert card.get_attribute("data-state") == "unavailable"
        assert "missing minimum_cfs" in card.inner_text()

    def test_an_unknown_classification_is_not_drawn_as_a_verdict(
        self, page: Page, console_url: str
    ) -> None:
        self._strip(page, console_url, {**self.LIVE, "classification": "invented_state"})
        card = page.locator('#live-strip .gage[data-basin="scott"]')
        assert card.get_attribute("data-state") == "unavailable"
        assert "cannot render" in card.inner_text()

    def test_both_gages_render_independently(self, page: Page, console_url: str) -> None:
        """One failing gage must not blank the other. A watermaster losing Scott
        still needs Shasta."""

        def handler(route: Route) -> None:
            if "shasta" in route.request.url:
                route.fulfill(
                    status=503,
                    content_type="application/json",
                    body=json.dumps({"detail": "gage down"}),
                )
            else:
                route.fulfill(
                    status=200, content_type="application/json", body=json.dumps(dict(self.LIVE))
                )

        page.route("**/api/gage/**", handler)
        page.goto(console_url)
        page.wait_for_selector('#live-strip[data-loaded="1"]', timeout=20_000)
        assert (
            page.locator('#live-strip .gage[data-basin="scott"]').get_attribute("data-state")
            == "flow_below_minimum"
        )
        assert (
            page.locator('#live-strip .gage[data-basin="shasta"]').get_attribute("data-state")
            == "unavailable"
        )


class TestTheHydrograph:
    """The chart, and the one defect in it that no unit test could have seen.

    The near-threshold band was filled with `origin: "start"`, which paints from the
    axis zero up to minimum + 10 and therefore shades the entire BELOW-MINIMUM region
    as though it were the caution band. Every value in the payload was correct, the
    endpoint's tests all passed, and the meaning of the most important region on the
    chart was inverted. It was obvious within a second of opening the render.

    So this asserts on the ECharts option the page actually builds, which is the
    closest a machine can get to what a reader sees.
    """

    #: A window carrying a real schedule boundary: Scott's July minimum of 50 cfs
    #: stepping down to the August minimum of 30. The step assertion is meaningless
    #: against a window with only one minimum in it.
    PAYLOAD: ClassVar[dict[str, Any]] = {
        "basin": "scott",
        "gage_id": "USGS-11519500",
        "since": "2026-07-17T00:00:00+00:00",
        "until": "2026-08-16T00:00:00+00:00",
        "count": 3,
        "series": [
            {"t": "2026-07-17T00:00:00+00:00", "cfs": 17.9, "provisional": True},
            {"t": "2026-08-01T00:00:00+00:00", "cfs": 8.2, "provisional": True},
            {"t": "2026-08-16T00:00:00+00:00", "cfs": 4.91, "provisional": True},
        ],
        "minimum_steps": [
            {"from": "2026-07-17", "minimum_cfs": 50.0},
            {"from": "2026-08-01", "minimum_cfs": 30.0},
        ],
        "near_threshold_band_cfs": 10.0,
        "unit": "ft^3/s",
        "provenance": "usgs_live",
        "disclaimer": "A recommendation.",
    }

    def _drawn(self, page: Page, console_url: str) -> None:
        """Draw from a STUBBED payload, never from the live endpoint.

        The first version let these tests fetch through to USGS. Six browser tests
        in a row then made six real calls to a federal agency, and the class became
        a flake with a deadline attached: passing alone, failing together. That is
        the exact dependency this file's own docstring warns against, committed one
        class below the warning.

        Stubbing also makes the step assertion possible at all, because whether a
        live 30 day window happens to contain a schedule boundary depends on what
        day the suite runs.
        """
        page.route("**/api/hydrograph/**", _fulfil(dict(self.PAYLOAD)))
        page.goto(console_url)
        page.wait_for_function(
            "() => document.getElementById('graph')?.dataset.state === 'drawn'",
            timeout=45_000,
        )

    def test_the_chart_draws_from_real_readings(self, page: Page, console_url: str) -> None:
        self._drawn(page, console_url)
        note = page.locator("#graph-note").inner_text()
        assert "readings from USGS-11519500" in note
        assert "17.9 cfs to 4.91 cfs" in note
        assert "changed 1 time" in note, "the step boundary was not reported to the reader"
        assert page.locator("#chart canvas").count() >= 1, "no canvas was rendered"

    def test_the_caution_band_is_a_band_and_not_a_fill_from_zero(
        self, page: Page, console_url: str
    ) -> None:
        """The defect this class exists for.

        A stacked pair draws the fill between the minimum and minimum + 10. A single
        series with an area fill draws it from the axis origin, which paints the
        below-minimum region in the caution colour and inverts what the chart means.
        """
        self._drawn(page, console_url)
        option = page.evaluate(
            "() => { const c = window.__chartOption; return c ? JSON.parse(c) : null; }"
        )
        assert option is not None, "the page did not expose the option it built"
        band = [s for s in option["series"] if s.get("stack") == "band"]
        assert len(band) == 2, (
            "the caution band is not a stacked pair, so it fills from the axis zero "
            "and shades the below-minimum region as near-threshold"
        )
        filled = [s for s in band if s.get("areaStyle")]
        assert len(filled) == 1, "exactly one half of the band carries the fill"
        # The filled half must carry the CONSTANT band width, not the minimum itself.
        values = {round(point[1], 3) for point in filled[0]["data"]}
        assert values == {10.0}, f"the band's height is not a constant 10 cfs: {values}"

    def test_the_rule_is_drawn_as_a_step_not_a_ramp(self, page: Page, console_url: str) -> None:
        """A ramp between two minimums implies the rule slid gradually from 50 to 30
        cfs across late July. It changed on one day, and every reading before that
        day is judged against 50."""
        self._drawn(page, console_url)
        option = page.evaluate(
            "() => { const c = window.__chartOption; return c ? JSON.parse(c) : null; }"
        )
        rule = next(s for s in option["series"] if s["name"] == "Minimum in force")
        assert rule.get("step") == "end", "the minimum is drawn as a diagonal ramp"

    def test_animation_is_off_because_the_values_are_compliance_critical(
        self, page: Page, console_url: str
    ) -> None:
        """Rule 10. Animating a number implies uncertainty about it, and these are
        the numbers an order rests on."""
        self._drawn(page, console_url)
        option = page.evaluate(
            "() => { const c = window.__chartOption; return c ? JSON.parse(c) : null; }"
        )
        assert option["animation"] is False

    def test_a_failed_fetch_says_why_and_draws_nothing(self, page: Page, console_url: str) -> None:
        page.route("**/api/hydrograph/**", _fulfil({"detail": "USGS could not be read"}, 503))
        page.goto(console_url)
        page.wait_for_function(
            "() => document.getElementById('graph')?.dataset.state === 'unavailable'",
            timeout=30_000,
        )
        assert "USGS could not be read" in page.locator("#graph-note").inner_text()
        assert page.locator("#chart canvas").count() == 0

    def test_an_empty_window_says_so_rather_than_drawing_a_flat_zero(
        self, page: Page, console_url: str
    ) -> None:
        """A chart of nothing looks like a river at zero, which is the single most
        dangerous thing this surface can imply."""
        page.route(
            "**/api/hydrograph/**",
            _fulfil(
                {
                    "basin": "scott",
                    "gage_id": "USGS-11519500",
                    "since": "2026-08-01T00:00:00+00:00",
                    "until": "2026-08-16T00:00:00+00:00",
                    "count": 0,
                    "series": [],
                    "minimum_steps": [{"from": "2026-08-01", "minimum_cfs": 30.0}],
                    "near_threshold_band_cfs": 10.0,
                    "unit": "ft^3/s",
                    "provenance": "usgs_live",
                    "disclaimer": "A recommendation.",
                }
            ),
        )
        page.goto(console_url)
        page.wait_for_function(
            "() => document.getElementById('graph')?.dataset.state === 'empty'",
            timeout=30_000,
        )
        note = page.locator("#graph-note").inner_text()
        assert "not a river at zero" in note
        assert page.locator("#chart canvas").count() == 0


class TestTheDiversionMap:
    """294 real points of diversion, and the 30 percent the map cannot show.

    Asserted on the GeoJSON the page builds rather than on pixels. At this density a
    screenshot cannot tell one dot's colour from another's, and the thing that matters
    most here is not visible at all: whether the caveat about the missing 139 rights
    is rendered beside the dots.
    """

    def _drawn(self, page: Page, console_url: str) -> None:
        page.goto(console_url)
        page.locator("#mapcard").scroll_into_view_if_needed()
        page.wait_for_function(
            "() => document.getElementById('mapcard')?.dataset.state === 'drawn'",
            timeout=90_000,
        )

    def test_it_draws_more_points_than_rights(self, page: Page, console_url: str) -> None:
        """A right diverts in more than one place, and an earlier generator kept one
        point each and discarded 41 real ones."""
        self._drawn(page, console_url)
        counts = page.evaluate(
            "() => ({points: window.__mapSource.features.length,"
            " rights: new Set(window.__mapSource.features.map(f => f.properties.right)).size})"
        )
        assert counts["points"] > counts["rights"], (
            "one point per right means the collision that plotted a Scott right near "
            "Sacramento has returned"
        )

    def test_the_missing_groundwater_rights_are_counted_on_screen(
        self, page: Page, console_url: str
    ) -> None:
        """**The most important assertion in this class.** 139 of 469 curtailed rights
        have no coordinate anywhere in the Board's layer. A map drawing the other 330
        and saying nothing under-reports who is curtailed by 30 percent, which is a
        calm wrong answer of exactly the kind this console exists to prevent."""
        self._drawn(page, console_url)
        note = page.locator("#map-note").inner_text()
        assert "no point of diversion" in note
        assert "groundwater" in note
        assert "does not pretend to" in note

    def test_the_cross_dataset_disagreement_is_drawn_and_named(
        self, page: Page, console_url: str
    ) -> None:
        """One right is listed in this basin by the order and placed in another
        watershed by the Board's own layer. Both statements are the Board's. It is
        drawn where the layer puts it, in the neutral rather than a river status
        colour, and named in the note."""
        self._drawn(page, console_url)
        grey = page.evaluate(
            "() => window.__mapSource.features"
            ".filter(f => f.properties.status === 's-pending')"
            ".map(f => ({right: f.properties.right, watershed: f.properties.watershed}))"
        )
        assert grey, "the disagreement is no longer drawn"
        assert grey[0]["right"] in page.locator("#map-note").inner_text()
        assert grey[0]["watershed"] != "Scott"

    def test_no_feature_carries_an_owner_name(self, page: Page, console_url: str) -> None:
        """The owner column is never requested from the source. This asserts none of
        it reached the browser either, which is where it would actually be exposed."""
        self._drawn(page, console_url)
        keys = page.evaluate(
            "() => [...new Set(window.__mapSource.features"
            ".flatMap(f => Object.keys(f.properties)))]"
        )
        for forbidden in ("owner", "primary_owner", "name", "address", "phone", "email"):
            assert forbidden not in keys, f"the map carries {forbidden}"

    def test_both_attributions_are_shown(self, page: Page, console_url: str) -> None:
        """The basemap licence requires its string verbatim, and redistributing the
        Board's data should credit the Board even though it is public domain."""
        self._drawn(page, console_url)
        attribution = page.locator("#map-attr").inner_text()
        assert "Water Resources Control Board" in attribution
        assert "OpenFreeMap" in attribution and "OpenStreetMap" in attribution

    def test_a_failed_fetch_says_why_and_draws_no_map(self, page: Page, console_url: str) -> None:
        page.route("**/api/diversions/**", _fulfil({"detail": "not packaged"}, 503))
        page.goto(console_url)
        page.locator("#mapcard").scroll_into_view_if_needed()
        page.wait_for_function(
            "() => document.getElementById('mapcard')?.dataset.state === 'unavailable'",
            timeout=45_000,
        )
        assert "not packaged" in page.locator("#map-note").inner_text()

    def test_the_library_is_not_fetched_before_the_card_is_reached(
        self, page: Page, console_url: str
    ) -> None:
        """MapLibre is 1.15 MB across three modules and a worker. Pulling it for every
        visitor to serve a card most never scroll to would slow the surface a judge
        actually lands on."""
        requested: list[str] = []
        page.on("request", lambda r: requested.append(r.url))
        page.goto(console_url)
        page.wait_for_function(
            "() => document.getElementById('graph')?.dataset.state", timeout=60_000
        )
        assert not any("maplibre" in url for url in requested), (
            "the map library loaded before the map card was anywhere near the viewport"
        )


@pytest.fixture(scope="module")
def console_source() -> str:
    """The page as served, for invariants that are true of the code rather than of a
    render."""
    from curtail_agents.api import CONSOLE

    return CONSOLE.read_text()


class TestASlowerAnswerNeverRepaintsOverANewerOne:
    """The race a second-model review found on the map, and which was equally present
    on the chart.

    Switch basin twice quickly and two fetches are in flight. Without a generation
    gate the SLOWER one repaints last, so the select reads Shasta while the card shows
    Scott's data, with a note underneath naming Scott's gage and counting Scott's
    rights. Every number on screen is internally consistent and belongs to the wrong
    basin, which is the worst kind of wrong: nothing looks broken.

    **The first version of these tests was vacuous and mutation testing caught it.**
    It delayed the first response with `page.wait_for_timeout` inside a route handler,
    which does not delay anything: removing the gate under test changed no result. A
    test for a race that cannot lose the race proves only that the page renders.

    So the response is HELD OPEN in the page, using the same `window.fetch` wrapper
    this file already uses for the superseded-classification test. The second request
    completes while the first is still pending, and only then is the first released.
    That ordering is deterministic rather than timing-dependent.
    """

    HOLD = """
        (() => {
          const real = window.fetch;
          window.__release = null;
          window.fetch = (...args) => {
            const url = String(args[0] || "");
            if (!window.__release && url.includes(window.__holdPath)) {
              return new Promise((resolve, reject) => {
                window.__release = () => real(...args).then(resolve, reject);
              });
            }
            return real(...args);
          };
        })();
    """

    def _arm(self, page: Page, path: str) -> None:
        page.add_init_script(f"window.__holdPath = {json.dumps(path)};")
        page.add_init_script(self.HOLD)

    def test_the_hydrograph_keeps_the_newer_basin(self, page: Page, console_url: str) -> None:
        scott = dict(TestTheHydrograph.PAYLOAD)
        shasta = {
            **dict(TestTheHydrograph.PAYLOAD),
            "basin": "shasta",
            "gage_id": "USGS-11517500",
            "minimum_steps": [{"from": "2026-07-17", "minimum_cfs": 50.0}],
        }

        def handler(route: Route) -> None:
            body = shasta if "shasta" in route.request.url else scott
            route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

        page.route("**/api/hydrograph/**", handler)
        self._arm(page, "/api/hydrograph/")
        page.goto(console_url)
        page.wait_for_function("() => window.__release !== null", timeout=30_000)

        # The newer selection overtakes the held one and settles.
        page.select_option("#graph-basin", "shasta")
        page.wait_for_function(
            "() => document.getElementById('graph')?.dataset.state === 'drawn'", timeout=60_000
        )
        assert "USGS-11517500" in page.locator("#graph-note").inner_text()

        # Now let the superseded Scott request finish. It must not repaint.
        page.evaluate("window.__release()")
        page.wait_for_timeout(2_000)
        note = page.locator("#graph-note").inner_text()
        assert "USGS-11517500" in note, (
            f"a superseded Scott response repainted over the newer Shasta selection: {note}"
        )

    def test_the_map_keeps_the_newer_basin(self, page: Page, console_url: str) -> None:
        def payload(basin: str, gage: str, right: str) -> dict[str, Any]:
            return {
                "basin": basin,
                "gage": {"id": gage, "lon": -122.9, "lat": 41.6},
                "rights_total": 10,
                "located_rights": 1,
                "points_total": 1,
                "without_coordinate": 9,
                "without_coordinate_groundwater": 9,
                "without_coordinate_note": "wells excluded",
                "watershed_discrepancies": [],
                "watershed_discrepancy_note": "none",
                "located": [
                    {
                        "application_number": right,
                        "points": [{"lon": -122.9, "lat": 41.6, "huc8_name": "Scott"}],
                    }
                ],
                "attribution": "California State Water Resources Control Board",
                "licence": "Public domain",
                "disclaimer": "A recommendation.",
            }

        def handler(route: Route) -> None:
            body = (
                payload("shasta", "USGS-11517500", "B000002")
                if "shasta" in route.request.url
                else payload("scott", "USGS-11519500", "A000001")
            )
            route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

        page.route("**/api/diversions/**", handler)
        self._arm(page, "/api/diversions/")
        page.goto(console_url)
        page.locator("#mapcard").scroll_into_view_if_needed()
        page.wait_for_function("() => window.__release !== null", timeout=45_000)

        page.select_option("#map-basin", "shasta")
        page.wait_for_function(
            "() => document.getElementById('mapcard')?.dataset.state === 'drawn'", timeout=90_000
        )
        assert page.evaluate("() => window.__mapSource.features.map(f => f.properties.right)") == [
            "B000002"
        ]

        page.evaluate("window.__release()")
        page.wait_for_timeout(2_500)
        drawn = page.evaluate("() => window.__mapSource.features.map(f => f.properties.right)")
        assert drawn == ["B000002"], (
            f"a superseded Scott response repainted over the newer Shasta selection: {drawn}"
        )

    def test_no_map_callback_reaches_for_the_module_level_instance(
        self, console_source: str
    ) -> None:
        """Asserted on the SOURCE, and the docstring says why rather than pretending.

        A review pointed out that the `load` callback read the module-level `map`, so a
        superseded callback firing after a newer `drawMap` had replaced it would add its
        sources to the NEWER map, and the newer map's own load would then throw "There
        is already a source with this ID".

        **I could not construct that failure.** `drawMap` calls `map.remove()` before
        creating the replacement, and MapLibre aborts a pending style load on removal,
        so the superseded callback does not fire. A behavioural test I wrote for it
        passed with the defect reintroduced, which means it discriminated nothing, and a
        test that cannot fail is worse than no test because it reads as coverage.

        The fix is still right: a callback that closes over its own instance cannot
        touch another one, whatever a future edit does to the removal path. So the
        invariant is asserted where it is actually true, in the code, and the honest
        limit is written down instead of dressed up as a runtime proof.
        """
        import re

        block = console_source[
            console_source.index("const instance = new gl.Map(") : console_source.index(
                "mapObserver"
            )
        ]
        offenders = re.findall(r"^\s+map\.\w+\(", block, re.MULTILINE)
        assert not offenders, (
            f"a map callback reaches for the module-level instance: {offenders}. "
            "It must use the local `instance` it belongs to."
        )

    def test_the_load_callback_checks_generation_before_mutating_anything(
        self, console_source: str
    ) -> None:
        """Gating at the END of a callback cannot help: by then the sources are added
        and the bounds are fitted. The check has to be the first statement."""
        body = console_source[console_source.index('instance.on("load"') :]
        body = body[: body.index('instance.on("click"')]
        gate = body.index("generation.map")
        for mutating in ("addSource", "addLayer", "fitBounds"):
            assert body.index(mutating) > gate, (
                f"{mutating} runs before the generation check in the load callback"
            )


class TestTheRightsLedger:
    """469 real rights across two basins, and the 14 the ladder cannot place."""

    def _loaded(self, page: Page, console_url: str) -> None:
        page.goto(console_url)
        page.wait_for_function(
            "() => document.getElementById('ledgercard')?.dataset.state === 'loaded'",
            timeout=60_000,
        )

    def test_it_lists_every_right_the_order_names(self, page: Page, console_url: str) -> None:
        """Scott's attachment names 384. A ledger showing fewer under-reports the
        order's scope, which is the failure the map's caveat exists to prevent."""
        self._loaded(page, console_url)
        assert page.locator("#ledgercard").get_attribute("data-shown") == "384"
        assert "Addendum 12" in page.locator("#ledger-source").inner_text()

    def test_shasta_shows_all_eighty_five_including_the_unplaceable(
        self, page: Page, console_url: str
    ) -> None:
        """71 are placeable and 85 are named. Rendering only the placeable list
        dropped 14 rights, and that is exactly what the first version did."""
        self._loaded(page, console_url)
        page.select_option("#ledger-basin", "shasta")
        page.wait_for_function(
            "() => document.getElementById('ledgercard')?.dataset.shown === '85'",
            timeout=30_000,
        )

    def test_the_unplaceable_filter_finds_them(self, page: Page, console_url: str) -> None:
        """The check that caught the original bug. The filter returned zero while the
        note beside it said 14, because the flag compared an id to a sentence."""
        self._loaded(page, console_url)
        page.select_option("#ledger-basin", "shasta")
        page.wait_for_function(
            "() => document.getElementById('ledgercard')?.dataset.shown === '85'",
            timeout=30_000,
        )
        page.check("#ledger-only")
        page.wait_for_function(
            "() => document.getElementById('ledgercard')?.dataset.shown === '14'",
            timeout=15_000,
        )
        row = page.locator("#ledger-body tr").first
        assert row.get_attribute("data-placed") == "false"
        assert "cannot place" in row.inner_text()

    def test_an_unplaceable_row_carries_its_reason_where_a_reader_can_reach_it(
        self, page: Page, console_url: str
    ) -> None:
        self._loaded(page, console_url)
        page.select_option("#ledger-basin", "shasta")
        page.wait_for_function(
            "() => document.getElementById('ledgercard')?.dataset.shown === '85'",
            timeout=30_000,
        )
        page.check("#ledger-only")
        page.wait_for_function(
            "() => document.getElementById('ledgercard')?.dataset.shown === '14'",
            timeout=15_000,
        )
        title = page.locator("#ledger-body tr").first.locator("td").nth(4).get_attribute("title")
        assert title and "priority" in title

    def test_absence_is_written_out_rather_than_left_blank(
        self, page: Page, console_url: str
    ) -> None:
        """A blank cell reads as an oversight. A stated absence reads as a fact, and
        here it is one: the Board's attachment does not carry a group for Shasta."""
        self._loaded(page, console_url)
        page.select_option("#ledger-basin", "shasta")
        page.wait_for_function(
            "() => document.getElementById('ledgercard')?.dataset.shown === '85'",
            timeout=30_000,
        )
        text = page.locator("#ledger-body").inner_text()
        assert "not stated" in text
        assert "\t\t" not in page.locator("#ledger-body tr").first.inner_text(), (
            "a cell rendered empty instead of naming its absence"
        )

    def test_the_reasons_are_grouped_rather_than_repeated_fourteen_times(
        self, page: Page, console_url: str
    ) -> None:
        """All 14 carry an identical sentence. Printing it 14 times is a wall a
        reader skips, and every id must still be on screen."""
        self._loaded(page, console_url)
        page.select_option("#ledger-basin", "shasta")
        page.wait_for_function(
            "() => document.getElementById('ledgercard')?.dataset.shown === '85'",
            timeout=30_000,
        )
        text = page.locator("#ledger-open").inner_text()
        assert "14 rights cannot be placed" in text
        for right in ("SG005433", "SG005922", "SG005957"):
            assert right in text, f"{right} vanished when the reasons were grouped"
        assert text.count("no priority precise enough") <= 3, (
            "the identical reason is still printed once per right"
        )

    def test_the_filter_narrows_without_reloading(self, page: Page, console_url: str) -> None:
        self._loaded(page, console_url)
        page.fill("#ledger-filter", "A0273")
        page.wait_for_timeout(300)
        shown = int(page.locator("#ledgercard").get_attribute("data-shown") or "0")
        assert 0 < shown < 384
        for row in page.locator("#ledger-body tr").all()[:5]:
            assert "A0273" in row.inner_text()

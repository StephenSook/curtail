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
from collections.abc import Iterator
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

    def test_a_basin_with_no_table_shows_the_refusal_not_an_empty_ledger(
        self, page: Page, console_url: str
    ) -> None:
        """An empty ledger would read as "no right is affected", which is a real answer
        and the wrong one."""
        page.goto(console_url)
        _settle_after(page, "#rec", "")

        assert page.locator("#rec tbody tr").count() == 0
        assert "REFUSED" in page.locator("#rec .status").inner_text().upper()
        assert "no rights table" in page.locator("#rec .refusal").inner_text()

    def test_an_unreachable_engine_says_unavailable_on_the_ledger_card_too(
        self, page: Page, console_url: str
    ) -> None:
        page.route("**/api/recommendation/**", lambda route: route.abort())
        page.goto(console_url)
        _settle_after(page, "#rec", "")

        status = page.locator("#rec .status")
        assert "UNAVAILABLE" in status.inner_text().upper()
        assert "s-pending" not in (status.get_attribute("class") or "")

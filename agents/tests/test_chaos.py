"""The chaos drill, and the only question that matters about it: can it fail?

A demo script that prints success unconditionally is a green light wired to nothing.
This repository has already shipped that shape three times: a CI guard that skipped
instead of failing, a watch command whose exit code lied, and a test asserting a
policy table rather than the running system. So the tests here are weighted toward
DISARMING each guard and proving the drill goes red, not toward watching it go green.
"""

from __future__ import annotations

import pytest

from curtail_agents import chaos
from curtail_agents.chaos import (
    DrillNotDemonstratedError,
    force_scribe_hallucination,
    inject_poisoned_order_pdf,
    kill_herald_mid_run,
    run_drill,
)


class TestEveryScenarioDemonstratesItsGuard:
    @pytest.mark.parametrize(
        "scenario", [kill_herald_mid_run, inject_poisoned_order_pdf, force_scribe_hallucination]
    )
    def test_it_fires(self, scenario: object) -> None:
        result = scenario()  # type: ignore[operator]
        assert result.demonstrated, result.render()
        assert result.evidence, "a scenario with no evidence proves nothing on camera"

    def test_the_drill_runs_all_three(self) -> None:
        assert len(run_drill()) == 3

    def test_the_entry_point_exits_zero_when_every_guard_fires(self) -> None:
        assert chaos.main() == 0


class TestTheDrillGoesRedWhenAGuardIsDisarmed:
    """Each guard removed in turn. If the drill still passes, it is theatre.

    Monkeypatched at the module boundary rather than by editing source, so the
    disarming is exact, reversible, and cannot leave a mutated file behind. An
    in-place mutation harness has already poisoned one run of this suite through a
    stale bytecode cache.
    """

    def test_a_dedup_table_that_forgets_is_caught_at_the_first_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A table granting every claim fails window 1 before it can reach window 2:
        the lease is not held, so a concurrent consumer would double-send."""

        class ForgetfulTable:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            def claim(self, key: str) -> bool:
                return True  # every redelivery is treated as new work

            def complete(self, key: str) -> None:
                return None

        monkeypatch.setattr(chaos, "DedupTable", ForgetfulTable)
        result = kill_herald_mid_run()
        assert not result.demonstrated
        assert any("LEASE NOT HELD" in line for line in result.evidence)

    def test_a_table_that_ignores_completion_lets_the_rancher_be_notified_twice(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Window 2 specifically: the lease works, but `complete()` does nothing, so
        a redelivery after a successful send is admitted and the notice goes twice."""
        real = chaos.DedupTable  # type: ignore[attr-defined]

        class NeverCompletes:
            def __init__(self, *args: object, **kwargs: object) -> None:
                self._inner = real(*args, **kwargs)  # type: ignore[arg-type]

            def claim(self, key: str) -> bool:
                return bool(self._inner.claim(key))

            def complete(self, key: str) -> None:
                return None  # the write is dropped

        monkeypatch.setattr(chaos, "DedupTable", NeverCompletes)
        result = kill_herald_mid_run()
        assert not result.demonstrated
        assert any("REDELIVERY WAS NOT SUPPRESSED" in line for line in result.evidence)

    def test_a_lease_that_never_lapses_loses_the_notice_silently(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The half people forget. A permanent claim looks correct on the happy path
        and drops every message whose worker died before sending it."""
        real = chaos.DedupTable  # type: ignore[attr-defined]

        def permanent_claims(*args: object, **kwargs: object) -> object:
            kwargs["lease_seconds"] = float("inf")
            return real(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(chaos, "DedupTable", permanent_claims)
        result = kill_herald_mid_run()
        assert not result.demonstrated
        assert any("LEASE NEVER LAPSED" in line for line in result.evidence)

    def test_a_sanitizer_that_detects_nothing_fails_the_scenario(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from curtail_agents.sanitize import SanitizedText

        monkeypatch.setattr(chaos, "sanitize_document", lambda text: SanitizedText(text=text))
        result = inject_poisoned_order_pdf()
        assert not result.demonstrated
        assert any("NOTHING WAS DETECTED" in line for line in result.evidence)

    def test_a_sanitizer_that_eats_the_findings_fails_the_scenario(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The failure that would otherwise look like success.

        Deleting the whole document removes every injection, so a scenario checking
        only "were hits found" would pass while the order lost its findings. That is
        strictly worse than the attack: a curtailment reaching the wrong rights.
        """
        from curtail_agents.sanitize import InjectionHit, InjectionKind, SanitizedText

        def destroys_everything(text: str) -> SanitizedText:
            return SanitizedText(
                text="",
                hits=(
                    InjectionHit(
                        kind=InjectionKind.INSTRUCTION_OVERRIDE,
                        matched="x",
                        source_start=0,
                        source_end=1,
                    ),
                ),
            )

        monkeypatch.setattr(chaos, "sanitize_document", destroys_everything)
        result = inject_poisoned_order_pdf()
        assert not result.demonstrated
        assert any("LEGAL CONTENT DESTROYED" in line for line in result.evidence)

    def test_a_citation_scrubber_that_passes_inventions_fails_the_scenario(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(chaos, "scrub_citations", lambda text: (text, ()))
        result = force_scribe_hallucination()
        assert not result.demonstrated
        assert any("FABRICATED CITATION SURVIVED" in line for line in result.evidence)

    def test_a_scrubber_that_strips_verified_authority_too_fails_the_scenario(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Over-broad is its own failure. A guard that eats 23 CCR 875 alongside the
        fabrication blocks lawful orders, and a guard that blocks lawful orders gets
        switched off, which is how you end up with no guard at all."""
        monkeypatch.setattr(chaos, "scrub_citations", lambda text: ("", ("everything",)))
        result = force_scribe_hallucination()
        assert not result.demonstrated
        assert any("VERIFIED AUTHORITY ALSO STRIPPED" in line for line in result.evidence)

    def test_a_ledger_guard_that_always_passes_fails_the_scenario(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from curtail_agents.routing import GuardResult, Verdict

        monkeypatch.setattr(
            chaos, "validate_draft", lambda *a, **k: GuardResult(verdict=Verdict.PASS)
        )
        result = force_scribe_hallucination()
        assert not result.demonstrated
        assert any("EXPECTED RETRY" in line for line in result.evidence)

    def test_a_ledger_guard_blind_to_omission_fails_the_scenario(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One-directional guards cannot see the failure this project's headline
        artifact is about: Order WR 2026-0005-DWR exists because rights were LEFT OUT
        of a curtailment order, not because extra ones were added."""
        from curtail_agents.routing import GuardResult, Verdict

        monkeypatch.setattr(
            chaos,
            "validate_draft",
            lambda *a, **k: GuardResult(
                verdict=Verdict.RETRY, unsupported_rights=("A-003",), missing_rights=()
            ),
        )
        result = force_scribe_hallucination()
        assert not result.demonstrated
        assert any("OMISSION NOT DETECTED" in line for line in result.evidence)


class TestTheDrillReportsFailureRatherThanSwallowingIt:
    def test_run_drill_raises_when_a_scenario_cannot_demonstrate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(chaos, "scrub_citations", lambda text: (text, ()))
        with pytest.raises(DrillNotDemonstratedError) as caught:
            run_drill()
        assert "Scribe hallucination" in str(caught.value)

    def test_main_exits_non_zero_so_a_makefile_target_can_fail(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`make chaos` has to be able to go red in front of a judge. That is the
        only reason to believe it when it goes green."""
        monkeypatch.setattr(chaos, "scrub_citations", lambda text: (text, ()))
        assert chaos.main() == 1

    def test_the_failure_output_names_which_guard_did_not_fire(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(chaos, "scrub_citations", lambda text: (text, ()))
        chaos.main()
        printed = capsys.readouterr().out
        assert "DRILL FAILED" in printed
        assert "FABRICATED CITATION SURVIVED" in printed

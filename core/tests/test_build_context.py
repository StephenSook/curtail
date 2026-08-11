"""What may enter a Cloud Run build context, guarded.

`.gcloudignore` REPLACES the default behaviour rather than adding to it. With no
such file, gcloud falls back to `.gitignore`; the moment one exists, that fallback
stops. So an incomplete `.gcloudignore` silently unprotects everything `.gitignore`
was covering, and nothing in the deploy output says so.

That is exactly what happened here. The first version listed the corpus and the
research folder and nothing else, so every credential pattern in `.gitignore` had
quietly stopped applying to build uploads. A review caught it before a deploy
carried one into an image pushed to a registry.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GCLOUDIGNORE = REPO / ".gcloudignore"


@pytest.fixture(scope="module")
def rules() -> list[str]:
    return [
        line.strip()
        for line in GCLOUDIGNORE.read_text().splitlines()
        if line.strip() and not (line.startswith("#") and not line.startswith("#!"))
    ]


class TestTheDefaultFallbackIsRestored:
    def test_it_is_tracked_at_all(self) -> None:
        """It was not, at first. A control that lives only on one machine protects
        only that machine, and `git add` refused it inside a commit that otherwise
        succeeded."""
        import subprocess

        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", ".gcloudignore"],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        assert tracked.returncode == 0, ".gcloudignore is not tracked by git"

    def test_it_includes_gitignore_explicitly(self, rules: list[str]) -> None:
        """The directive that restores the fallback this file switches off."""
        assert "#!include:.gitignore" in rules, (
            "without the include directive this file REPLACES .gitignore for build "
            "uploads, so every pattern there stops applying and nothing says so."
        )


class TestCredentialPatternsAreStatedHereToo:
    """Duplicated from .gitignore deliberately: this is the file somebody reads when
    asking what a build uploads, and a credential inside a registry image is not
    recoverable by deleting the file later."""

    @pytest.mark.parametrize(
        "pattern",
        [
            ".env",
            ".env.*",
            "*.pem",
            "*.key",
            "id_rsa*",
            "service-account*.json",
            "application_default_credentials.json",
        ],
    )
    def test_the_pattern_is_present(self, rules: list[str], pattern: str) -> None:
        assert pattern in rules, f"{pattern} is not excluded from the build context"


class TestInternalMaterialCannotShipInAnImage:
    @pytest.mark.parametrize("folder", ["_research/", "_private/", "data/corpus/"])
    def test_it_is_excluded(self, rules: list[str], folder: str) -> None:
        """Internal strategy documents are banned from any public artifact by the
        build constitution, and an image pushed to a registry is public in every
        sense that matters."""
        assert folder in rules, f"{folder} could enter a build context"

    def test_every_gitignored_secret_pattern_is_covered(self) -> None:
        """The completeness check, rather than a list somebody remembered to update.

        Any credential-shaped pattern in .gitignore must either appear here or be
        covered by the include directive, and the include must therefore be present.
        """
        text = GCLOUDIGNORE.read_text()
        gitignore = (REPO / ".gitignore").read_text().splitlines()
        secretish = [
            line.strip()
            for line in gitignore
            if line.strip()
            and not line.startswith("#")
            and any(
                marker in line.lower()
                for marker in (
                    "env",
                    "pem",
                    "key",
                    "secret",
                    "credential",
                    "rsa",
                    "npmrc",
                    "pypirc",
                )
            )
            and not line.startswith("!")
        ]
        assert secretish, "no credential patterns found in .gitignore, so this proves nothing"
        assert "#!include:.gitignore" in text, (
            f"these credential patterns rely on the include directive: {secretish[:6]}"
        )

"""Session-wide guards that must run BEFORE any test module is imported.

pytest imports the root `conftest.py` before it collects anything, which is the only
point early enough to change the environment that module-level code will read.
"""

from __future__ import annotations

import os

# **The suite must never export a span, on anybody's machine.**
#
# `curtail_agents.api` calls `configure_tracing()` at import, deliberately, so the very
# first request already has somewhere to send spans. On a developer machine with
# GOOGLE_CLOUD_PROJECT exported, importing the API from a test would therefore build a
# real Cloud Trace exporter and start shipping test spans to a real project.
#
# On the machine this was written on the variable is unset, so the suite passed and the
# hazard was invisible: the guard would have gone off only on somebody else's laptop, or
# in a CI job that happened to set it. That is the same shape as a guard whose
# precondition holds only where it was authored.
#
# Set here rather than in a fixture because fixtures run after collection, and the
# import that matters has already happened by then.
os.environ.setdefault("CURTAIL_DISABLE_TRACING", "1")

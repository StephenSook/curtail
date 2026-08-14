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

# **And the suite must never call Model Armor**, for the same reason and one more.
#
# The Scribe node now screens untrusted document text through Model Armor as its second
# injection layer, which is a real network call against a real Google project. Left
# unguarded, every sanitiser test would bill for an opinion, fail on a plane, and make a
# unit test depend on a regional API being up.
#
# The extra reason: screening is SLOW relative to a unit test, and a suite that quietly
# grew a network round trip per case is a suite people stop running.
os.environ.setdefault("CURTAIL_DISABLE_MODEL_ARMOR", "1")

# **And the suite must never reach Cloud Firestore.**
#
# The Season Ledger now has a durable store, and `store_for()` builds a real Firestore
# client whenever GOOGLE_CLOUD_PROJECT is set. Same hazard as the two above and the same
# blind spot: on a machine with the variable unset the suite would pass while quietly
# depending on that being true everywhere else. Worse than the tracing case, because a
# test that wrote to the real seasons collection would be editing a legal record.
#
# The store honours this by returning the in-memory implementation, which reports
# `durable: False` and says why, so nothing in the suite can mistake it for the real one.
os.environ.setdefault("CURTAIL_DISABLE_FIRESTORE", "1")

"""Curtail deterministic allocation core.

Nothing in this package calls a language model. The allocation decision is
deterministic and inspectable line by line, which is what makes the agent
fleet's output auditable rather than merely plausible.
"""

__all__ = ["__version__"]
__version__ = "0.1.0"

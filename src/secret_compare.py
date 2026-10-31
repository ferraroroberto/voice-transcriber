"""Constant-time comparison of user-supplied secrets.

``hmac.compare_digest`` raises ``TypeError`` on a ``str`` holding a non-ASCII
character, so a password or token typed with an accent would surface as an
unhandled 500 instead of a rejection. Comparing the UTF-8 bytes accepts any
text. Every secret comparison in the repo goes through here
(``tests/test_secret_compare.py`` enforces it).
"""

from __future__ import annotations

# Standard library imports
import hmac


def secrets_equal(presented: str, expected: str) -> bool:
    """Whether ``presented`` equals ``expected``, in constant time, for any text."""
    return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))

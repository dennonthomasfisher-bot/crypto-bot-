"""
Enforces a minimum interval between posts to avoid bursting the Twitter
rate limit.  Import and call allow_post() before posting; call record_post()
after a successful post.
"""

import time
import logging

import config

logger = logging.getLogger(__name__)

_last_post_time: float = 0.0


def allow_post(bypass: bool = False) -> bool:
    """Return True if posting is allowed.

    If bypass is True the check is skipped entirely – use this for
    scheduled events (e.g. morning recap) that must always fire.
    """
    if bypass:
        return True
    elapsed = time.time() - _last_post_time
    if elapsed < config.MIN_POST_INTERVAL:
        logger.info(
            "Posting guard: %.0fs since last post (min %ds) – skipping.",
            elapsed,
            config.MIN_POST_INTERVAL,
        )
        return False
    return True


def record_post() -> None:
    """Reset the interval timer after a successful post."""
    global _last_post_time
    _last_post_time = time.time()

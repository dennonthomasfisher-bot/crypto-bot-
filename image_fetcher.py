"""
image_fetcher.py – External image fetching for tweet attachments.

NOTE: Unsplash source API (source.unsplash.com) was deprecated and shut down.
Image fetching is disabled until a replacement source is configured.
Re-enable should_add_image() and implement fetch_image_for_topic() once a new
image source (e.g. Pexels API with key, or self-hosted assets) is set up.
"""


def should_add_image() -> bool:
    # Disabled: Unsplash source API shut down. Re-enable when a new image source is configured.
    return False


def fetch_image_for_topic(topic: str) -> str | None:
    """Fetch an image relevant to `topic`. Currently disabled."""
    return None

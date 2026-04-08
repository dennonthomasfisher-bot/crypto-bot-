#!/usr/bin/env python3
"""
Post a one-off Twitter thread immediately.

Usage:
    python post_thread.py                        # uses default topic
    python post_thread.py "your topic here"      # custom topic
    python post_thread.py --dry-run              # print without posting
    python post_thread.py --tweets 6 "topic"     # 6-tweet thread
"""
from __future__ import annotations

import argparse
import logging
import sys

import ai_writer
import config
import twitter_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("post_thread")

DEFAULT_TOPIC = (
    "Why crypto market structure has fundamentally changed in 2025-2026: "
    "ETF flows, institutional custody, on-chain transparency, and what it means "
    "for retail traders watching price action"
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Post a crypto thread to X/Twitter")
    parser.add_argument("topic", nargs="?", default=DEFAULT_TOPIC, help="Thread topic")
    parser.add_argument("--dry-run", action="store_true", help="Print tweets, don't post")
    parser.add_argument("--tweets", type=int, default=5, metavar="N", help="Number of tweets (default 5)")
    args = parser.parse_args()

    if not config.ANTHROPIC_API_KEY:
        logger.critical("ANTHROPIC_API_KEY not set in .env")
        sys.exit(1)

    if not args.dry_run:
        try:
            twitter_client.get_client()
        except RuntimeError as exc:
            logger.critical("Twitter credentials error: %s", exc)
            sys.exit(1)

    logger.info("Generating %d-tweet thread on: %s", args.tweets, args.topic)
    tweets = ai_writer.generate_thread(args.topic, n_tweets=args.tweets)

    if not tweets:
        logger.error("Thread generation failed — no tweets returned")
        sys.exit(1)

    print(f"\n{'─'*60}")
    for i, tweet in enumerate(tweets, 1):
        print(f"[{i}/{len(tweets)}] ({len(tweet)} chars)\n{tweet}\n")
    print('─'*60)

    if args.dry_run:
        logger.info("DRY RUN — not posting.")
        return

    confirm = input(f"\nPost this {len(tweets)}-tweet thread? [y/N] ").strip().lower()
    if confirm != "y":
        logger.info("Aborted.")
        return

    ok = twitter_client.post_thread(tweets)
    if ok:
        logger.info("Thread posted successfully.")
    else:
        logger.error("Thread posting failed (partial post possible — check X).")
        sys.exit(1)


if __name__ == "__main__":
    main()

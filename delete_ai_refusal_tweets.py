#!/usr/bin/env python3
"""Delete tweets containing AI refusal language.

Run on production machine:
    python3 delete_ai_refusal_tweets.py

Fetches recent tweets from the authenticated account and deletes any
that contain leaked AI refusal phrases.
"""
import tweepy
import config

AI_REFUSAL_PHRASES = [
    "i need to",
    "i appreciate",
    "i cannot",
    "i'm unable",
    "as an ai",
    "my core directive",
    "conflicts with",
    "i must decline",
    "i can't generate",
    "i can't create",
    "against my guidelines",
    "i'm not able",
]

def main():
    client = tweepy.Client(
        bearer_token=config.TWITTER_BEARER_TOKEN,
        consumer_key=config.TWITTER_API_KEY,
        consumer_secret=config.TWITTER_API_SECRET,
        access_token=config.TWITTER_ACCESS_TOKEN,
        access_token_secret=config.TWITTER_ACCESS_TOKEN_SECRET,
    )

    me = client.get_me()
    uid = me.data.id
    print(f"Authenticated as: {me.data.username} (ID: {uid})")

    tweets = client.get_users_tweets(uid, max_results=100, tweet_fields=["created_at", "text"])
    if not tweets.data:
        print("No tweets found.")
        return

    deleted = 0
    for t in tweets.data:
        text_lower = t.text.lower()
        if any(phrase in text_lower for phrase in AI_REFUSAL_PHRASES):
            print(f"\n  DELETING: {t.id}")
            print(f"  Text: {t.text[:120]}")
            print(f"  Created: {t.created_at}")
            try:
                client.delete_tweet(t.id)
                deleted += 1
                print("  ✓ Deleted")
            except Exception as exc:
                print(f"  ✗ Failed: {exc}")

    print(f"\nDone. Deleted {deleted} tweet(s) containing AI refusal text.")


if __name__ == "__main__":
    main()

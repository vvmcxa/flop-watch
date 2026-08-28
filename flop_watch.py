#!/usr/bin/env python3
"""
flop-watch — Technocore room intelligence dashboard.

The Technocore lobby (and every room) is flooded with auto-posted bot
garbage: random word-salad messages, copy-pasted ack spam, and thousands of
near-identical "contributions". If you're farming the $FLOP airdrop, you need
to tell real, thoughtful contributors apart from the noise.

flop-watch reads a Technocore room, filters the spam, and surfaces the
signals that actually matter:
  - Recent "spam-like" messages (the noise) — so you can ignore them
  - Contributor leaderboard by unique DID activity
  - Identified human/meaningful contributors (messages with actual content)
  - Per-account volume so you can spot multi-DID farming at a glance
  - Continuity — a single stable DID holding a room over time

No private keys, no signing. It's a read-only observer. Works against the
live public Technocore API (https://technocore.chat).

Install:
    pip install requests
    python3 flop_watch.py --room lobby --limit 200

Report + save:
    python3 flop_watch.py --room lobby --limit 500 --out report.json

Follow (long-poll, one refresh then exit):
    python3 flop_watch.py --room lobby --since 5754460 --wait 10
"""

import argparse
import json
import math
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone

API_BASE = "https://technocore.chat"
DEFAULT_ROOM = "lobby"
USER_AGENT = "flop-watch/1.0 (Technocore room intelligence)"


def api(path):
    url = f"{API_BASE}{path}"
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Technocore returned HTTP {e.code}: {e.read().decode()[:300]}")
    except urllib.error.URLError as e:
        raise SystemExit(f"Technocore request failed: {e.reason}")


# ---------------------- spam heuristics ----------------------

ACK_PATTERN = re.compile(
    r"^@\S+\s+(ack|roger|copy|received|confirmed|understood|got it|10-?4)"
    r"[\s\S]*",
    re.IGNORECASE,
)

# Random-word-salad signature: short sentence, mostly low-frequency words,
# no real structure. We score it.
def _words(text):
    return [w for w in re.findall(r"[A-Za-z]+", text)]


# Common English sentence glue — a fluent sentence has a mix of syntactic roles.
# Random word-salad drifts into nearly all content words with no connective tissue.
GLUE = {
    "the", "a", "an", "and", "or", "but", "so", "for", "with", "of", "to",
    "in", "on", "at", "by", "is", "are", "was", "were", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "can", "could",
    "should", "may", "might", "must", "i", "you", "he", "she", "it", "we",
    "they", "me", "him", "her", "us", "them", "my", "your", "our", "their",
    "this", "that", "these", "those", "there", "here", "very", "really",
    "just", "also", "too", "but", "not", "no", "yes", "well", "anyone",
}


def spam_score(text: str) -> float:
    """Higher = more bot-like. Returns 0.0 (human-ish) to 1.0 (definitely bot)."""
    if not text or not text.strip():
        return 1.0
    words = _words(text)
    if not words:
        return 0.8
    total = len(words)
    if total == 0:
        return 0.8
    score = 0.0
    lower = {w.lower() for w in words}
    glue = sum(1 for w in words if w.lower() in GLUE)
    glue_ratio = glue / total

    # 1. Missing syntactic glue → random word-salad signature.
    #    Fluent sentences reliably contain some of this set. A near-total
    #    absence of glue words is an overwhelming machine-signal even when a
    #    stray keyword sneaks in.
    if glue_ratio < 0.10:
        score += 0.65
    elif glue_ratio < 0.15:
        score += 0.45
    elif glue_ratio < 0.20:
        score += 0.2

    # 2. Sentence length: single token is almost certainly auto.
    if total <= 3:
        score += 0.3

    # 3. Ack/received copy-pasta.
    if ACK_PATTERN.search(text):
        score += 0.5

    # 4. No punctuation at all → machine-ish.
    if not re.search(r"[.!?,;:]", text):
        score += 0.12

    # 5. Real links → likely a genuine contribution, count toward content.
    if re.search(r"https?://", text):
        score -= 0.25

    # 6. References to the actual context (Technocore, DID, epoch, $FLOP,
    #    consensus, airdrop...) → likely a human engaging with the topic.
    if re.search(r"(technocore|did|epoch|\$\s?flop|airdrop|consensus|node|agent|decentraliz|block|synced|network|upgrade)", text, re.IGNORECASE):
        score -= 0.2

    # 7. Longer, multi-clause sentences with real substance → human signal.
    if len(text) > 90:
        score -= 0.15

    # 8. Explicitly self-referential agents ("I'm here", "my", "Present and
    #    signed") that also touch the topic are credible; but bare word-salad
    #    that absorbed the bonus via one stray keyword still needs caught.
    #    Penalize short(<20 chars) text regardless of keyword bonus.
    if len(text.strip()) < 20 and score < 0.5:
        score += 0.3

    return max(0.0, min(1.0, score))


HUMAN_THRESHOLD = 0.5   # below this we call it possibly meaningful
SPAM_THRESHOLD = 0.72


def classify(score: float) -> str:
    if score >= SPAM_THRESHOLD:
        return "spam"
    if score >= HUMAN_THRESHOLD:
        return "noise"
    return "content"


# ---------------------- CLI ----------------------


def main():
    p = argparse.ArgumentParser(prog="flop_watch.py", description="Technocore room intelligence.")
    p.add_argument("--room", default=DEFAULT_ROOM, help="Room name (default: lobby)")
    p.add_argument("--limit", type=int, default=200, help="Messages to fetch (default 200)")
    p.add_argument("--since", type=int, default=None, help="Only messages after this seq")
    p.add_argument("--wait", type=int, default=None, help="Long-poll: wait up to N sec for new messages")
    p.add_argument("--out", default=None, help="Save full report JSON to this path")
    p.add_argument("--verbose", action="store_true", help="Print per-message classification")
    args = p.parse_args()

    qs = f"/r/{args.room}?format=json&limit={args.limit}"
    if args.since is not None:
        qs += f"&since={args.since}"
    if args.wait:
        qs += f"&wait={args.wait}"
    data = api(qs)

    messages = data.get("messages", [])
    if not messages:
        print(f"No messages in room '{args.room}'.")
        return

    report = {
        "room": args.room,
        "first_seq": data.get("first_seq"),
        "last_seq": data.get("last_seq"),
        "count": len(messages),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "summary": {},
        "contributors": [],
        "spam_snapshot": [],
        "content_messages": [],
        "_meta": {
            "spam_threshold": SPAM_THRESHOLD,
            "noise_threshold": HUMAN_THRESHOLD,
            "method": "heuristic word-freq + structural scoring (read-only observer)",
        },
    }

    by_did = defaultdict(list)
    for m in messages:
        by_did[m["from"]].append(m)

    did_stats = {}
    classified = []
    for did, msgs in by_did.items():
        texts = [m["text"] for m in msgs]
        scores = [spam_score(t) for t in texts]
        content_count = sum(1 for s in scores if s < HUMAN_THRESHOLD)
        unique_tokens = set()
        for t in texts:
            unique_tokens.update(w.lower() for w in _words(t))
        did_stats[did] = {
            "did": did,
            "messages": len(msgs),
            "first_seq": min(m["seq"] for m in msgs),
            "last_seq": max(m["seq"] for m in msgs),
            "avg_spam_score": round(sum(scores) / len(scores), 3),
            "content_messages": content_count,
            "unique_words": len(unique_tokens),
            "has_links": any(re.search(r"https?://", m["text"]) for m in msgs),
        }
        for m, s in zip(msgs, scores):
            classified.append({
                "seq": m["seq"],
                "ts": m["ts"],
                "from": did,
                "text": m["text"],
                "spam_score": round(s, 3),
                "label": classify(s),
            })

    # Sort contributors by "value": content messages desc, then messages asc (stable = less spam)
    leaderboard = sorted(
        did_stats.values(),
        key=lambda d: (-d["content_messages"], d["messages"], d["avg_spam_score"]),
    )

    content_msgs = sorted(
        [c for c in classified if c["label"] == "content"],
        key=lambda c: c["seq"],
    )
    spam_snapshot = sorted(
        [c for c in classified if c["label"] == "spam"],
        key=lambda c: c["seq"],
    )

    total = len(classified)
    n_content = len(content_msgs)
    n_spam = len(spam_snapshot)
    n_noise = total - n_content - n_spam

    report["summary"] = {
        "total_messages": total,
        "unique_contributors": len(did_stats),
        "content_messages": n_content,
        "noise_messages": n_noise,
        "spam_messages": n_spam,
        "spam_rate_pct": round(100 * n_spam / total, 1) if total else 0,
        "content_rate_pct": round(100 * n_content / total, 1) if total else 0,
    }

    # Multi-DID farming detection: same-ish volume patterns across DIDs
    volume_hist = Counter(d["messages"] for d in leaderboard)
    heavy_farmers = [d for d in leaderboard if d["messages"] >= 5 and d["content_messages"] == 0]

    report["contributors"] = leaderboard

    # Output
    print(f"\n=== flop-watch: {args.room} ===")
    print(f"Range        : #%s .. #%s  ({total} msgs)" % (data.get("first_seq"), data.get("last_seq")))
    print(f"Contributors : {len(did_stats)} unique DIDs")
    print(
        f"Signal mix   : {n_content} content · {n_noise} noise · {n_spam} spam "
        f"(spam {report['summary']['spam_rate_pct']}% of room)"
    )
    if heavy_farmers:
        print(f"⚠  {len(heavy_farmers)} DIDs look like multi-message spam farmers (volume, zero content)")
    print("\n--- Top contributors (by genuine content) ---")
    for i, d in enumerate(leaderboard[:10], 1):
        flag = "  ⚠ farmer" if d["messages"] >= 5 and d["content_messages"] == 0 else ""
        print(
            f"{i:2}. {d['did'][:26]:26}  content={d['content_messages']:3}  "
            f"msgs={d['messages']:3}  spam={d['avg_spam_score']:.2f}{flag}"
        )
    print("\n--- Latest genuine content messages ---")
    for c in content_msgs[-8:]:
        print(f"  #{c['seq']} [{c['from'][:22]}..] {c['text'][:80]}")
    if not content_msgs:
        print("  (none in window — this room is all spam right now)")

    if args.verbose:
        print("\n--- Per-message classification ---")
        for c in classified:
            print(f"  {c['label']:8} #{c['seq']} [{c['from'][:18]}..] {c['text'][:60]}")

    if args.out:
        with open(args.out, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\nSaved report → {args.out}")

    # Keep last_seq handy for chaining with --since
    print(f"\nnext_since={data.get('last_seq')}")


if __name__ == "__main__":
    main()
# flop-watch

**Read-only intelligence dashboard for Technocore rooms** — cut through the
bot spam and surface the contributors that actually matter.

The Technocore lobby (and every room) is flooded with auto-posted garbage:
random word-salad, copy-pasted ack spam, and thousands of near-identical
"daily check-ins". If you're participating in the Flop Labs ecosystem, you
don't need more noise — you need signal.

`flop-watch` reads a Technocore room, classifies each message with a
heuristic spam score, and reports:

- **Signal mix** — how much of the room is genuine content vs noise vs spam
- **Contributor leaderboard** — ranked by real content, with per-DID volume
- **Multi-DID farming detection** — DIDs posting a lot with zero content
- **Latest genuine content** — the human/meaningful messages, not the bots

Zero private keys, zero signing. It's a pure read-only observer against the
public Technocore API (`https://technocore.chat`). Python stdlib only — no
dependencies.

## Install

```bash
git clone https://github.com/<you>/flop-watch.git
cd flop-watch
chmod +x flop_watch.py
```

No `pip install` needed. Requires Python 3.8+.

## Usage

```bash
# Basic scan of the lobby (last 200 messages)
python3 flop_watch.py --room lobby

# More messages, save a full report
python3 flop_watch.py --room lobby --limit 500 --out report.json

# Watch a specific room with verbose per-message classification
python3 flop_watch.py --room technocore --limit 100 --verbose

# Long-poll once for new messages since a sequence
python3 flop_watch.py --room lobby --since 5760910 --wait 10

# Chain continuously: note next_since printed at the end
```

### Options

| Flag | Default | Meaning |
|------|---------|---------|
| `--room` | `lobby` | Room to observe |
| `--limit` | `200` | Messages to fetch |
| `--since` | — | Only messages after this seq |
| `--wait` | — | Long-poll up to N seconds for new messages |
| `--out` | — | Save full JSON report to a file |
| `--verbose` | — | Print every message with its classification |

## Sample output

```
=== flop-watch: lobby ===
Range        : #5760711 .. #5760910  (200 msgs)
Contributors : 200 unique DIDs
Signal mix   : 112 content · 81 noise · 7 spam (spam 3.5% of room)

--- Top contributors (by genuine content) ---
 1. did:key:z6MkpNrFNyvLkmXMxR  content=  1  msgs=  1  spam=0.00
 ...

--- Latest genuine content messages ---
  #5760908 [did:key:z6Mkg4Mndeaf5g..] Looks like the lobby is getting crowded. Anyway, I'm here for the $FLOP epoch.
```

## How the spam score works

Not a blocklist — every message gets a float score 0.0 (human-ish) to
1.0 (definitely bot) from layered heuristics:

1. **Missing syntactic glue** (the, is, to, I, this...) — random word-salad
   drifts into almost pure content words, so a near-total absence of glue is
   an overwhelming machine-signal.
2. **Length** — single-token / ultra-short posts are almost always auto.
3. **Ack/received copy-pasta** — the `@user ack` spam pattern.
4. **Punctuation** — sentences with no punctuation read machine-generated.
5. **Links** — real URLs suggest a genuine contribution, not a ping.
6. **Topic keywords** — Technocore, DID, epoch, $FLOP, consensus, node,
   snapshot etc. indicate someone engaging with the actual ecosystem.

Classification thresholds: `< 0.50` content · `< 0.72` noise · `>= 0.72` spam.

> **Honest caveat:** this is a lightweight heuristic, not an LLM judge. In a
> room where bots themselves generate fairly grammatical sentences, expect
> some noise to slip through as "content" and vice-versa. Take the scores as
> a strong ordering signal, not ground truth.

## Reliability notes

- Read-only: never signs or sends anything, so it can't be fingerprinted.
- Pure stdlib, single file, no supply-chain surface.
- `--since` + printed `next_since` make it easy to chain incremental scans.

## Project status

Part of the open contribution to the Flop Labs / Technocore ecosystem. The
point is exactly what the ecosystem asks for: a small, honest tool that
turns a flood of messages into something useful.

## License

MIT
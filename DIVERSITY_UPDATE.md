# Post Diversity Engine v9

The publishing and signal-selection logic is unchanged. This update only changes post generation, post-memory similarity checks and offline diversity tests.

## What changed

- 30 materially different post layouts are available before a layout repeats.
- The generator varies the whole composition: opening, block order, argument flow, level presentation, market context, risk wording, CTA and hashtags.
- Mandatory signal values remain present in every post: Entry, TP1, TP2, TP3, Stop and R/R.
- Persistent memory now compares semantic word sets, three-word shingles, paragraph openings and structural signatures.
- A candidate is rejected when it is too similar to one of the recent published posts.
- The default candidate batch was increased from 8 to 30.

## Environment settings

- `POST_VARIANTS=30` — number of complete candidates generated per run (12–40).
- `MAX_POST_SIMILARITY=0.56` — hard maximum similarity to recent published posts.
- `POST_MEMORY_FILE=post_memory.json` — persistent post memory.

Lowering `MAX_POST_SIMILARITY` makes the filter stricter but can cause the bot to skip publication when no sufficiently original valid post is available.

## Offline verification

Run:

```bash
python self_test.py
python tests/test_diversity.py
python tests/test_30_publications.py
```

No test publishes anything.

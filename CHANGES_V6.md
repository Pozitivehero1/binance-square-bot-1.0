# Human Feed / W2E v6 — changes

This build is focused on the problem visible in the real BICO post: technically correct content that still reads like a bot.

## Copy

- Rebuilt the default feed around short human-first formats.
- Removed canned labels such as “Направление у идеи”, “Диапазон контроля”, “Граница ошибки” and terminal-like parameter dumps.
- Kept only the useful trade contract: one decision level, first target and invalidation.
- Questions are optional and rare instead of being forced on every post.
- Hashtags are rare; the cashtag remains the main discoverability element.
- Hot moves are direction-aware. A strong rise with a SHORT setup is treated as a possible counter-trend reversal that still needs confirmation, not as a move to “chase SHORT”. The inverse applies to LONG after a sell-off.
- `ai_first` uses Mistral only for short number-free editorial prose. Price, direction, target, stop and market facts remain code-controlled, and deterministic copy remains the fallback.

## Selection / W2E

- Reweighted W2E score toward fresh attention and away from static 24h popularity.
- Added safe hot-market and attention overrides.
- W2E gating now rejects candidates individually rather than killing the entire run because the top candidate missed the threshold.
- Post ranking uses quality + feed appeal + conversion intent + novelty.

## Visuals

- Adaptive human-first posts prefer real charts.
- Human charts were simplified from ENTRY/TP1/TP2/TP3/STOP dashboards to only УРОВЕНЬ / ЦЕЛЬ / ОТМЕНА.
- Two-scenario human posts also use the simpler chart in adaptive mode.

## Anti-repetition

- Similarity compares the editorial idea instead of punishing required price/risk facts.
- Stress-tested through 150 sequential posts with a 0.52 similarity gate.

See `TEST_RESULTS.txt`, `HUMAN_FEED_EDITION.md` and `W2E_OPTIMIZATION.md` for details.

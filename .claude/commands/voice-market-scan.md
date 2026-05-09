---
description: Re-scan voice-dictation market (Wispr Flow, Superwhisper, Aqua Voice) and diff against this repo's current feature set.
---

# Voice market scan

Goal: produce a fresh comparison of this repo's features vs. the three reference apps (Wispr Flow, Superwhisper, Aqua Voice), surface what's *new* on their side since the last scan, and recommend high-ROI features to build.

## Steps

1. **Find the previous scan, if any.** Look in `docs/market-scan/` for the most recent `YYYY-MM-DD-*.md` (sort by filename descending). Read it so you can highlight *deltas* rather than re-listing everything.

2. **Read the current state of this repo.** At minimum: `README.md`, `config/config.json` (or `.sample.json`), `config/webapp_config.sample.json`, `config/polish_prompts.json`. The README's feature surface is the source of truth — do not rely on memory.

3. **Web-search the three reference apps**, in parallel, with the current year in the query:
   - Wispr Flow features / changelog / what's new
   - Superwhisper features / changelog / what's new
   - Aqua Voice features / changelog / what's new

   Prefer the vendor's own `/features`, `/changelog`, `/whats-new` pages and their App Store / Play Store release notes. Cross-check against one independent review from the current year. Three searches is the floor — do more if the vendor pages are thin.

4. **Build the comparison table.** Columns: feature, this repo, Wispr, Superwhisper, Aqua, effort (S/M/L), verdict (build / parity / skip / already-win). Group rows: Core, Polish/AI, Dictionary & snippets, Hotkeys & UX, Privacy, Remote/sharing.

5. **Diff vs. previous scan.** If a previous scan exists, add a `## What's new since <date>` section listing only features that are new on the vendor side, OR features this repo has shipped since (read git log between then and now: `git log --since=<previous-scan-date> --pretty=format:"%h %s"`).

6. **Recommendations.** Order by ROI: Phase 1 (high ROI, low effort), Phase 2 (medium effort, high daily payoff), Phase 3 (defer/skip with reason). Be specific — name the file or module the feature would land in. Skip generic "would be nice" entries.

7. **Write the report** to `docs/market-scan/YYYY-MM-DD-scan.md` (today's date). Use the user's auto-memory `currentDate` if present, otherwise `Get-Date -Format "yyyy-MM-dd"`.

8. **Print a summary to chat:** the three highest-ROI new recommendations and one sentence on whether the market has meaningfully shifted.

## Don'ts

- Don't edit any source code or config — this is a read-only research pass.
- Don't re-list features that are unchanged from the previous scan; the value is in deltas.
- Don't include vague "AI-powered" marketing claims as features. A feature must be a concrete capability the user can invoke.
- Don't propose features that contradict this repo's design constraints: local-first, no subscription, multi-surface parity (webapp + tk + tray + CLI).

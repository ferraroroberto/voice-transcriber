---
description: Re-scan voice-dictation market (Wispr Flow, Superwhisper, Aqua Voice) and diff against this repo's current feature set.
---

# Voice market scan

Goal: refresh the single durable comparison note (`docs/market-scan/initial-scan.md`) against the three reference apps (Wispr Flow, Superwhisper, Aqua Voice), surface what's *new* on their side since the last refresh, and file any newly-open ideas as GitHub issues. This command edits `docs/` and creates issues — it is not read-only.

`docs/market-scan/initial-scan.md` is a **living reference doc, not a dated snapshot**: it always lives at that one path, gets edited in place, and never grows a `YYYY-MM-DD-*.md` sibling. Per this repo's (and the fleet's) doc-discipline rule, `docs/` is for durable reference material — roadmap/TODO content belongs in GitHub issues, not in a dated planning file that inevitably goes stale.

## Steps

1. **Read the existing doc.** `docs/market-scan/initial-scan.md` — its "What this repo currently has" and comparison table are the baseline to diff against, not a file to replace wholesale.

2. **Read the current state of this repo.** At minimum: `README.md`, `config/config.json` (or `.sample.json`), `config/webapp_config.sample.json`, `config/polish_prompts.json`. The README's feature surface is the source of truth — do not rely on memory or trust the doc's own claims about this repo without checking.

3. **Web-search the three reference apps**, in parallel, with the current year in the query:
   - Wispr Flow features / changelog / what's new
   - Superwhisper features / changelog / what's new
   - Aqua Voice features / changelog / what's new

   Prefer the vendor's own `/features`, `/changelog`, `/whats-new` pages and their App Store / Play Store release notes. Cross-check against one independent review from the current year. Three searches is the floor — do more if the vendor pages are thin.

4. **Update the comparison table in place.** Columns: feature, this repo, Wispr, Superwhisper, Aqua. No effort/verdict columns and no "Build"/"Skip" roadmap language — those decisions belong in issues, not the doc. A row for a feature this repo now ships gets its status flipped to "Yes" (remove any stale issue link for it). A newly-identified gap gets a "No — tracked in #N" cell (file the issue per step 6 first, then link it).

5. **Check for shipped-since-last-check features.** `git log --since=<doc's last-verified date> --pretty=format:"%h %s"` — anything that closes a gap the doc still lists as open gets flipped to "Yes" and its tracking issue (if any) closed with a comment pointing at the shipping commit.

6. **File issues for genuinely new open ideas.** For each vendor feature that's a real gap and not already covered by an open issue (`gh issue list --state open`), create one via `gh issue create --label enhancement --assignee @me` using this repo's issue template (Why / Scope / Out of scope / How to verify / Constraints). Do not write a "Recommended build order" or ROI-ranked roadmap section into the doc — the issue tracker is the backlog, the doc is the reference.

7. **Update "Where this repo already wins"** if new evidence changes it, and refresh the "Sources" list with anything new you cited.

8. **Print a summary to chat:** what flipped from open-gap to shipped, what new issues were filed (numbers + titles), and one sentence on whether the market has meaningfully shifted. Do not create a new dated file — the diff lives in this chat summary and in `git log` / `git blame` on the one doc.

## Don'ts

- Don't create a new `docs/market-scan/*.md` file — there is exactly one file, edited in place.
- Don't write a dated roadmap or "Build/Skip" verdict into the doc — open ideas are GitHub issues, linked from the doc's "Open ideas" section.
- Don't edit source code or config — this command only touches the doc and issues.
- Don't re-list features that are unchanged; the value is in deltas, surfaced in the chat summary.
- Don't include vague "AI-powered" marketing claims as features. A feature must be a concrete capability the user can invoke.
- Don't propose features that contradict this repo's design constraints: local-first, no subscription, multi-surface parity (webapp + tk + tray + CLI).

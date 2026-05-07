# 2026-05-07 — Polish verification: gemma4-e4b-it works

## Why this exists

Concern raised: when polishing with the local `gemma4-e4b-it` model the
output looked almost identical to the input, while `claude-haiku-4-5`
visibly cleaned things up. Suspicion was that gemma was broken or that
the local-llm-hub route was misbehaving.

This note records the diagnostic that proved gemma is fine — the early
samples just had nothing to remove.

## What was done

1. **Inspected the recent archive entries** under
   `archive/2026/05/07/` and read each `polish_request.json` /
   `polish_response.json` pair.
2. **Found that the gemma runs that "did nothing"** were given inputs
   that were already filler-free, e.g. *"Okay, this is a test to see if
   this is working. I don't want to do a job for nothing..."* — there
   were no `uh / um / like / you know / sort of / kind of` tokens to
   strip, so leaving the text alone was the correct behaviour per the
   system prompt.
3. **Ran a deliberate filler-heavy transcript through gemma** to
   confirm it acts when there is something to remove. Archive folder:
   `archive/2026/05/07/16-27-59-4a8091af/`.

## Result

Gemma cleaned the filler-saturated transcript convincingly. Sentence
structure, vocabulary, and ideas were preserved; only filler words,
hedges, and false starts were removed.

### Input excerpt (from `transcript.txt`)

> Um, so, ah, I was kind of thinking that, you know, maybe we could,
> um, go over the project details again because, ah, I'm not totally
> sure we're all, um, aligned on the same expectations. Like, I mean,
> technically the timeline seems, uh, manageable…

### Polished output excerpt (from `polished.txt`, model `gemma4-e4b-it`)

> I was thinking that maybe we could go over the project details again
> because I'm not totally sure we're all aligned on the same
> expectations. I mean, technically the timeline seems manageable…

All five paragraphs were cleaned consistently across the document. No
summarisation, no reordering, no added content.

## Conclusion

`gemma4-e4b-it` is working as intended for this task. The earlier
"gemma isn't polishing" impression was a sampling artefact: the test
inputs at that time were already clean.

Operational takeaways:

- When evaluating polish quality, always compare against an input that
  contains clear filler. Clean-in / clean-out tells you nothing about
  the model.
- The local model is a reasonable default for everyday polishing; keep
  `claude-haiku-4-5` available in `polish_models_available` for cases
  where the small local model misses something subtle.

## Files modified

- `docs/2026-05-07-polish-gemma-verification.md` (this file). No code
  changes were necessary — this was a verification, not a fix.

## Validation run

- Archive evidence: `archive/2026/05/07/16-27-59-4a8091af/` contains
  the request, response, raw transcript, and polished output for the
  filler-heavy gemma run.
- Hub liveness: `GET http://127.0.0.1:8000/v1/models` → 200 (the
  request reached the hub and returned a valid Anthropic-shaped
  response).

---
name: token-economy
description: Use when you want terse, low-token responses for routine or mechanical work — status updates, confirmations, simple edits — without losing correctness.
---

# Token economy

Answer with the fewest tokens that fully do the job. Precision over prose.

## Do

- Lead with the answer or the result; drop preamble ("Sure!", "Great question", "Here's…").
- Prefer a short list or a single sentence over a paragraph.
- Show only the code that changed, not whole unchanged files.
- Skip restating the request back to the user.
- Stop as soon as the task is done — no summaries of what you just did unless asked.

## Don't

- Don't pad with caveats, apologies, or filler.
- Don't compress away correctness: keep exact identifiers, paths, commands, and numbers verbatim.
- Don't drop steps the user needs to act — brevity ≠ omission.

## Calibrate

Terse for routine/mechanical work. If the task is genuinely complex or safety-relevant, spend the words it needs — economy is the default, not a straitjacket.

<!-- claudectl starter skill. Inspired by the `caveman` token-compression skill
     (via claudemarketplaces.com) and claudectl's own concise-output hook. -->

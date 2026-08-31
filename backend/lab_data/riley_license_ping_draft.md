# GitHub issue for xavriley/hf_midi_transcription — SENT 2026-08-31

Posted as harvlad: https://github.com/xavriley/hf_midi_transcription/issues/3

**Title:** Add LICENSE file to match the declared MIT license?

**Body:**

Hi — thanks for releasing these models, the bass and guitar transcribers
work great.

Both the code repo (`pyproject.toml` classifier `License :: OSI Approved ::
MIT License`) and the weights repo on Hugging Face
(`xavriley/midi-transcription-models`, card metadata `license: mit`)
declare MIT, but neither contains a LICENSE file with the actual license
text. Would you consider adding one to each (with your copyright line)?
That would make the grant unambiguous for downstream users — we're using
the bass/guitar checkpoints in a product and would love the paper trail
to be airtight.

Happy to open a PR adding the standard MIT text if that's easier.

---
Context for us: registry 2026.08.31-5 marks riley_bass/riley_guitar
cleared_production on the strength of the two author declarations; this
ping is the cosmetic residual, not a blocker.

# Listening verdicts v2 — real-band Cambridge-MT, recorded 2026-08-31

Blind picks by Matt, unblinded after entry via ANSWER_KEY.json.
Material: 10 Rock/Indie songs from ds_cambridge*_6src (real electric
bass, distorted guitar), 30 s highest-energy windows, clips prepped on
Hetzner.

| clip | pick | variant | note |
|---|---|---|---|
| AngelsInAmplifiers_I__bass | X1 | specialist (+12) | missing a few notes |
| BalkunBrothers_SoHiS__bass | X1 | spec_raw | timing a little off |
| BannedFromTheZoo_Bla__bass | X1 | incumbent | clicky, few extra notes; pitch/timing correct |
| BigStoneCulture_Frag__bass | X3 | specialist (+12) | |
| BosnianRainbows_Morn__bass | X3 | specialist (+12) | |
| Candlebox_HappyPills__bass | X2 | specialist (+12) | |
| Forkupines_Semantics__bass | X3 | incumbent | "pitch too high" (riley variants off) |
| HollowGround_IllFate__bass | X2 | spec_raw | |
| TheBrew_WhatIWant__bass | X3 | incumbent | |
| AngelsInAmplifiers_I__guitar | X1 | incumbent | pitch correct, notes not correct |
| BalkunBrothers_SoHiS__guitar | none | (lean incumbent) | extra notes; X1=incumbent better pitch |
| BannedFromTheZoo_Bla__guitar | X1 | specialist | |
| BigStoneCulture_Frag__guitar | X1 | specialist | |
| BosnianRainbows_Morn__guitar | none | — | stem didn't separate cleanly |
| BronzeRadioReturn_Mi__guitar | X2 | specialist | |
| Candlebox_HappyPills__guitar | X1 | specialist | |
| Forkupines_Semantics__guitar | X1 | specialist | |
| HollowGround_IllFate__guitar | none | — | |
| TheBrew_WhatIWant__guitar | X2 | specialist | pitch better, sustain not so good |

## Tally

- Bass (n=9): specialist(+12) 4, spec_raw 2, incumbent 3 — riley 6/9
- Guitar (n=10, 7 decisive): specialist 6, incumbent 1, none 3

## Conclusions

1. **riley_guitar CONFIRMED on real distorted guitar** (6/7 decisive) —
   the acoustic-GAPS-training weakness did not materialize at listening
   level. Guitar route validated on both synth (v1) and real-band (v2)
   material.
2. **Bass octave register is CONTENT-DEPENDENT.** v1 synth bass: raw
   5/6, +12 0/6. v2 real electric bass: +12 4, raw 2, incumbent 3.
   Combined riley beats incumbent 11/15, but neither fixed shift wins
   everywhere (raw 7, +12 4). register_passthrough remains the best
   FIXED rule; the correct rule is adaptive — choose shift in {0,+12}
   by matching transcribed register against the separated stem's f0.
   Candidate future normalization: "register_match_audio".
3. Residual quality notes: occasional missed notes (bass), extra notes
   and sustain errors (guitar), and one separation failure — consistent
   with "better, not perfect"; feedback seam should collect these in
   dogfood.

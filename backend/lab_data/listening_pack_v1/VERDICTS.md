# Listening verdicts — recorded 2026-08-31, unblinded after entry

Blind picks by Matt against ref_clean/ref_separated, then mapped via
ANSWER_KEY.json:

| clip | pick | variant | note |
|---|---|---|---|
| 03_-_Simulated_Life__bass | X1 | spec_raw | |
| 12_-_Mr_Dance__bass | X3 | spec_raw | |
| 15_-_Death_by_Nanobots__bass | X3 | spec_raw | |
| 17_-_Winter_Mode__bass | X1 | incumbent | |
| 23_-_Hello_AI_ft_Jason_Hanes__bass | X2 | spec_raw | "notes aren't quite correct though" |
| 24_-_Hello_AI_Ancestry__bass | X2 | spec_raw | |
| 02_-_Jump_and_Die__guitar | X1 | specialist | |

## Tally

- Bass (n=6): **spec_raw 5, incumbent 1, specialist(+12) 0**
- Guitar (n=1): **specialist 1** (weak n — only one guitar multitrack in samples/)

## Conclusions

1. riley_bass beats the incumbent on real separated audio by ear (5/6) —
   the F1 gain is audible.
2. The +12 register shift NEVER won. It is a Slakh written-pitch
   convention, not a real-audio rule. Bass route reverted to
   register_passthrough in registry 2026.08.31-1; register_up_12 kept
   for Slakh-eval reproducibility only.
3. riley_guitar confirmed on the single available clip.

Material caveat: samples/ songs are synth-heavy productions (synth bass
likely); electric/acoustic bass and distorted guitar remain untested by
ear — riley_guitar is acoustic-trained (GAPS) and known weak on
distortion.

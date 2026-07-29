# M0 findings — data-format smoke

**1. What did we build?**
`benchmark/` tree (phrases/references/styles/metadata/results); `suite.json` (v0.1.0,
instrument defaults, provisional weights); one phrase `single.json` (1 fretted note,
fixed fingering); one acceptance-region reference `single.json` (Reference A mechanical +
Reference B behavioural constraints/ranges + acceptable_fingerings); a ~90-line `loader.py`
that parses + validates required fields and prints the parsed suite/phrase/reference.

**2. What did we learn?**
The v2 data format is authorable and loadable. The A/B reference split (mechanical lower
bound vs behavioural acceptance region) encodes cleanly as JSON. Contacts key on
(string, fret) with no pitch anywhere; instrument params live in the phrase (n_strings
parameterized). Loader output:
```
SUITE v0.1.0  phrases=['single']
PHRASE single [single_note/neutral] 1 event, 6-string @ 90bpm
   t=0.0 str3 fret2 middle (fret)
REF single: A.shift=0 B.min_shifts=0 B.acceptable_fingerings=1
LOADER_OK
```

**3. Assumptions confirmed.**
- Data-only schema is usable and validates (missing-field guard works).
- Acceptance-region reference (constraints + ranges + acceptable fingerings) is
  expressible as data — the review's fix #1 is representable, not just theoretical.
- String/fret keying + in-phrase instrument params hold (tuning/N-string forward-compat).

**4. Assumptions disproved.**
None.

**5. Change before M1?**
Nothing. The interface `load_suite/load_phrase/load_reference` returns exactly what a
solver adapter + metric engine will consume. Proceed immediately to M1 (SolverAdapter →
Trajectory).

Note (not a change, an observation for later authoring): writing Reference B even for a
trivial phrase took real judgment (what counts as "compact and ready", the economy band
numbers). Confirms the roadmap's flagged risk that reference authoring is the quiet cost —
budget for it at M4/M7, not the code.

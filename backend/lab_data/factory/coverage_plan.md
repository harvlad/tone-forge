# Coverage Dashboard  (pool = 564 assets)

dimension              coverage   assets  diversity confidence
genre                  ████████████   360       5     0.64
gain                   ████████....   564       2     1.00
guitar_type            ████████████   564       4     1.00
pickup                 ████████████   160       5     0.28
tempo                  ████████████   564       4     1.00
key                    ████████████   564      24     1.00
masking_level          ███.........   564       1     1.00
recording_style        ████████████   564       4     1.00
acoustic_vs_electric   ████████████   564       2     1.00
difficulty             ████████████   564       1     1.00
synthetic_vs_real      ████████████   564       1     1.00
license_class          ████████████   564       1     1.00
amp_family             ............   160       4     0.28
recording_method       █████████...   564       4     1.00
tuning                 ............     0       0     0.00
string_count           ............     0       0     0.00
capo                   ████████████     0       0     0.00
playing_style          ............   404       8     0.72
articulation           ............   404       8     0.72
player_identity        ████████████   404       9     0.72
recording_quality      ████████████   564       3     1.00
provenance_confidence  ████████████   564       2     1.00

# Coverage Heatmap (target regimes; . = empty)

## acoustic_vs_electric
  electric       ██████....  204  (impact 1.5)
  acoustic       ██████████  360  (impact 1.0)

## guitar_type
  distorted      ███.......  120  (impact 1.6)
  acoustic       ██████████  360  (impact 1.0)
  clean          █.........   40  (impact 1.0)

## pickup
  bridge         ██████████   32  (impact 1.2)
  neck           ██████████   32  (impact 1.0)
  middle         ██████████   32  (impact 1.0)

## gain
  med            ..........    0  (impact 1.6)  GAP
  high           ███.......  120  (impact 1.4)
  low            ██████████  444  (impact 1.0)

## tuning
  baritone       ..........    0  (impact 2.4)  GAP
  7-string       ..........    0  (impact 2.4)  GAP
  drop_c         ..........    0  (impact 2.2)  GAP
  8-string       ..........    0  (impact 2.2)  GAP
  drop_d         ..........    0  (impact 1.6)  GAP
  dadgad         ..........    0  (impact 1.4)  GAP
  open_g         ..........    0  (impact 1.4)  GAP
  standard       ..........    0  (impact 1.0)  GAP

## string_count
  7              ..........    0  (impact 2.2)  GAP
  8              ..........    0  (impact 2.2)  GAP
  12             ..........    0  (impact 1.6)  GAP
  6              ..........    0  (impact 1.0)  GAP

## amp_family
  high_gain      ..........    0  (impact 1.6)  GAP
  british_crunch ..........    0  (impact 1.4)  GAP
  distortion     ..........    0  (impact 1.4)  GAP
  blues driver   ..........    0  (impact 1.2)  GAP
  tube screamer  ..........    0  (impact 1.2)  GAP
  clean          ..........    0  (impact 1.0)  GAP
  fender_clean   ..........    0  (impact 1.0)  GAP

## recording_method
  di             ..........    0  (impact 1.8)  GAP
  amp_mic        █.........   22  (impact 1.2)
  di_processed   ████......  160  (impact 1.2)
  acoustic       ██████████  360  (impact 1.0)

## playing_style
  metal          ..........    0  (impact 1.8)  GAP
  funk           ..........    0  (impact 1.4)  GAP
  ambient        ..........    0  (impact 1.4)  GAP
  jazz           ..........    0  (impact 1.2)  GAP
  bossa_nova     ..........    0  (impact 1.2)  GAP
  blues          ..........    0  (impact 1.2)  GAP
  rock           ..........    0  (impact 1.0)  GAP
  singer_songwriter ..........    0  (impact 1.0)  GAP

## articulation
  PinchHarmonics ██████████    4  (impact 1.8)  GAP
  PalmMute       ██████████    4  (impact 1.6)  GAP
  Tapping        ..........    0  (impact 1.6)  GAP
  Harmonics      ██████████    4  (impact 1.4)  GAP
  fingerstyle    ..........    0  (impact 1.4)  GAP
  Bendings       ██████████    4  (impact 1.2)  GAP
  Vibrato        ██████████    4  (impact 1.2)  GAP
  Slides         ..........    0  (impact 1.2)  GAP

## masking_level
  high           ..........    0  (impact 2.0)  GAP
  med            ..........    0  (impact 1.6)  GAP
  low            ..........    0  (impact 1.2)  GAP
  none           ██████████  564  (impact 0.5)


# Gap Report

- tuning=baritone: have 0 (deficit 100%), impact 2.4 -> commission. no dataset has diverse tunings — record DI to spec
- tuning=7-string: have 0 (deficit 100%), impact 2.4 -> commission. no dataset has diverse tunings — record DI to spec
- tuning=drop_c: have 0 (deficit 100%), impact 2.2 -> commission. no dataset has diverse tunings — record DI to spec
- tuning=8-string: have 0 (deficit 100%), impact 2.2 -> commission. no dataset has diverse tunings — record DI to spec
- string_count=7: have 0 (deficit 100%), impact 2.2 -> commission. 7/8-string absent everywhere — commission
- string_count=8: have 0 (deficit 100%), impact 2.2 -> commission. 7/8-string absent everywhere — commission
- masking_level=high: have 0 (deficit 100%), impact 2.0 -> virtual_studio. Virtual Studio scenarios (needs REAL backing pool)
- recording_method=di: have 0 (deficit 100%), impact 1.8 -> green. Guitar-TECHS DI + more green
- playing_style=metal: have 0 (deficit 100%), impact 1.8 -> license. MoisesDB / SourceAudio genres; or commission metal
- amp_family=high_gain: have 0 (deficit 100%), impact 1.6 -> green. mine remaining EGFxSet effects / more recipes (free, on Hetzner)
- articulation=Tapping: have 0 (deficit 100%), impact 1.6 -> green. more of Guitar-TECHS techniques (free) + commission gaps
- masking_level=med: have 0 (deficit 100%), impact 1.6 -> virtual_studio. Virtual Studio scenarios (needs REAL backing pool)
- gain=med: have 0 (deficit 100%), impact 1.6 -> commission. green tier is bimodal (clean vs high); record edge/crunch DI
- tuning=drop_d: have 0 (deficit 100%), impact 1.6 -> commission. no dataset has diverse tunings — record DI to spec
- string_count=12: have 0 (deficit 100%), impact 1.6 -> commission. 7/8-string absent everywhere — commission
- amp_family=british_crunch: have 0 (deficit 100%), impact 1.4 -> green. mine remaining EGFxSet effects / more recipes (free, on Hetzner)
- amp_family=distortion: have 0 (deficit 100%), impact 1.4 -> green. mine remaining EGFxSet effects / more recipes (free, on Hetzner)
- articulation=fingerstyle: have 0 (deficit 100%), impact 1.4 -> green. more of Guitar-TECHS techniques (free) + commission gaps
- playing_style=funk: have 0 (deficit 100%), impact 1.4 -> license. MoisesDB / SourceAudio genres; or commission metal
- playing_style=ambient: have 0 (deficit 100%), impact 1.4 -> license. MoisesDB / SourceAudio genres; or commission metal
- tuning=dadgad: have 0 (deficit 100%), impact 1.4 -> commission. no dataset has diverse tunings — record DI to spec
- tuning=open_g: have 0 (deficit 100%), impact 1.4 -> commission. no dataset has diverse tunings — record DI to spec
- amp_family=blues driver: have 0 (deficit 100%), impact 1.2 -> green. mine remaining EGFxSet effects / more recipes (free, on Hetzner)
- amp_family=tube screamer: have 0 (deficit 100%), impact 1.2 -> green. mine remaining EGFxSet effects / more recipes (free, on Hetzner)
- articulation=Slides: have 0 (deficit 100%), impact 1.2 -> green. more of Guitar-TECHS techniques (free) + commission gaps
- masking_level=low: have 0 (deficit 100%), impact 1.2 -> virtual_studio. Virtual Studio scenarios (needs REAL backing pool)
- playing_style=jazz: have 0 (deficit 100%), impact 1.2 -> license. MoisesDB / SourceAudio genres; or commission metal
- playing_style=bossa_nova: have 0 (deficit 100%), impact 1.2 -> license. MoisesDB / SourceAudio genres; or commission metal
- playing_style=blues: have 0 (deficit 100%), impact 1.2 -> license. MoisesDB / SourceAudio genres; or commission metal
- amp_family=clean: have 0 (deficit 100%), impact 1.0 -> green. mine remaining EGFxSet effects / more recipes (free, on Hetzner)
- amp_family=fender_clean: have 0 (deficit 100%), impact 1.0 -> green. mine remaining EGFxSet effects / more recipes (free, on Hetzner)
- playing_style=rock: have 0 (deficit 100%), impact 1.0 -> license. MoisesDB / SourceAudio genres; or commission metal
- playing_style=singer_songwriter: have 0 (deficit 100%), impact 1.0 -> license. MoisesDB / SourceAudio genres; or commission metal
- tuning=standard: have 0 (deficit 100%), impact 1.0 -> commission. no dataset has diverse tunings — record DI to spec
- string_count=6: have 0 (deficit 100%), impact 1.0 -> commission. 7/8-string absent everywhere — commission
- articulation=Vibrato: have 1 (deficit 68%), impact 1.2 -> green. more of Guitar-TECHS techniques (free) + commission gaps
- articulation=Harmonics: have 2 (deficit 48%), impact 1.4 -> green. more of Guitar-TECHS techniques (free) + commission gaps
- articulation=PalmMute: have 3 (deficit 36%), impact 1.6 -> green. more of Guitar-TECHS techniques (free) + commission gaps
- articulation=Bendings: have 2 (deficit 48%), impact 1.2 -> green. more of Guitar-TECHS techniques (free) + commission gaps
- articulation=PinchHarmonics: have 3 (deficit 28%), impact 1.8 -> green. more of Guitar-TECHS techniques (free) + commission gaps

# Acquisition Priority List (ranked)

#  gap                                strat         cost  impact roi    priority
 1 tuning=baritone                    commission    high     2.4   0.20 2.40
 2 tuning=7-string                    commission    high     2.4   0.20 2.40
 3 tuning=drop_c                      commission    high     2.2   0.18 2.20
 4 tuning=8-string                    commission    high     2.2   0.18 2.20
 5 string_count=7                     commission    high     2.2   0.18 2.20
 6 string_count=8                     commission    high     2.2   0.18 2.20
 7 masking_level=high                 virtual_studio low      2.0   4.00 2.00
 8 recording_method=di                green         low      1.8   3.60 1.80
 9 playing_style=metal                license       med      1.8   0.60 1.80
10 amp_family=high_gain               green         low      1.6   3.20 1.60
11 articulation=Tapping               green         low      1.6   3.20 1.60
12 masking_level=med                  virtual_studio low      1.6   3.20 1.60

# ROI Report (coverage-gain x impact / cost)

  ROI   4.00  masking_level=high           (impact 2.0, cost low, virtual_studio)
  ROI   3.60  recording_method=di             (impact 1.8, cost low, green)
  ROI   3.20  amp_family=high_gain      (impact 1.6, cost low, green)
  ROI   3.20  articulation=Tapping        (impact 1.6, cost low, green)
  ROI   3.20  masking_level=med            (impact 1.6, cost low, virtual_studio)
  ROI   2.80  amp_family=british_crunch (impact 1.4, cost low, green)
  ROI   2.80  amp_family=distortion     (impact 1.4, cost low, green)
  ROI   2.80  articulation=fingerstyle    (impact 1.4, cost low, green)
  ROI   2.40  amp_family=blues driver   (impact 1.2, cost low, green)
  ROI   2.40  amp_family=tube screamer  (impact 1.2, cost low, green)

# Commissioning Briefs (auto-generated from gaps)

> Need ~20 performances: tuning=baritone (DI, bridge humbucker, 90-140 BPM).  [commission, cost~high, impact~2.4]
> Need ~20 performances: tuning=7-string (DI, bridge humbucker, 90-140 BPM).  [commission, cost~high, impact~2.4]
> Need ~20 performances: tuning=drop_c (DI, bridge humbucker, 90-140 BPM).  [commission, cost~high, impact~2.2]
> Need ~20 performances: tuning=8-string (DI, bridge humbucker, 90-140 BPM).  [commission, cost~high, impact~2.2]
> Need ~20 performances: string_count=7 (DI, bridge humbucker, 90-140 BPM).  [commission, cost~high, impact~2.2]
> Need ~20 performances: string_count=8 (DI, bridge humbucker, 90-140 BPM).  [commission, cost~high, impact~2.2]

# Licensing Opportunities

- recording_method=di: green — Guitar-TECHS DI + more green (cost~low, impact 1.8)
- playing_style=metal: license — MoisesDB / SourceAudio genres; or commission metal (cost~med, impact 1.8)
- amp_family=high_gain: green — mine remaining EGFxSet effects / more recipes (free, on Hetzner) (cost~low, impact 1.6)
- articulation=Tapping: green — more of Guitar-TECHS techniques (free) + commission gaps (cost~low, impact 1.6)
- amp_family=british_crunch: green — mine remaining EGFxSet effects / more recipes (free, on Hetzner) (cost~low, impact 1.4)
- amp_family=distortion: green — mine remaining EGFxSet effects / more recipes (free, on Hetzner) (cost~low, impact 1.4)
- articulation=fingerstyle: green — more of Guitar-TECHS techniques (free) + commission gaps (cost~low, impact 1.4)
- playing_style=funk: license — MoisesDB / SourceAudio genres; or commission metal (cost~med, impact 1.4)
- playing_style=ambient: license — MoisesDB / SourceAudio genres; or commission metal (cost~med, impact 1.4)
- amp_family=blues driver: green — mine remaining EGFxSet effects / more recipes (free, on Hetzner) (cost~low, impact 1.2)
- amp_family=tube screamer: green — mine remaining EGFxSet effects / more recipes (free, on Hetzner) (cost~low, impact 1.2)
- articulation=Slides: green — more of Guitar-TECHS techniques (free) + commission gaps (cost~low, impact 1.2)
- playing_style=jazz: license — MoisesDB / SourceAudio genres; or commission metal (cost~med, impact 1.2)
- playing_style=bossa_nova: license — MoisesDB / SourceAudio genres; or commission metal (cost~med, impact 1.2)
- playing_style=blues: license — MoisesDB / SourceAudio genres; or commission metal (cost~med, impact 1.2)
- amp_family=clean: green — mine remaining EGFxSet effects / more recipes (free, on Hetzner) (cost~low, impact 1.0)
- amp_family=fender_clean: green — mine remaining EGFxSet effects / more recipes (free, on Hetzner) (cost~low, impact 1.0)
- playing_style=rock: license — MoisesDB / SourceAudio genres; or commission metal (cost~med, impact 1.0)
- playing_style=singer_songwriter: license — MoisesDB / SourceAudio genres; or commission metal (cost~med, impact 1.0)
- articulation=Vibrato: green — more of Guitar-TECHS techniques (free) + commission gaps (cost~low, impact 1.2)
- articulation=Harmonics: green — more of Guitar-TECHS techniques (free) + commission gaps (cost~low, impact 1.4)
- articulation=PalmMute: green — more of Guitar-TECHS techniques (free) + commission gaps (cost~low, impact 1.6)
- articulation=Bendings: green — more of Guitar-TECHS techniques (free) + commission gaps (cost~low, impact 1.2)
- articulation=PinchHarmonics: green — more of Guitar-TECHS techniques (free) + commission gaps (cost~low, impact 1.8)

# Historical Coverage Trend

  amp_family             diversity 0 -> 4  (+4)
  recording_method       diversity 0 -> 4  (+4)
  playing_style          diversity 0 -> 8  (+8)
  articulation           diversity 0 -> 8  (+8)
  player_identity        diversity 0 -> 9  (+9)
  recording_quality      diversity 0 -> 3  (+3)
  provenance_confidence  diversity 0 -> 2  (+2)

# Recommended Next Actions

## Do first — free / cheap wins (highest $-ROI, exhaust before spending)
1. GENERATE (Virtual Studio): masking_level=high — ROI 4.00, impact 2.0. Virtual Studio scenarios (needs REAL backing pool)
2. INGEST (free green): recording_method=di — ROI 3.60, impact 1.8. Guitar-TECHS DI + more green
3. INGEST (free green): amp_family=high_gain — ROI 3.20, impact 1.6. mine remaining EGFxSet effects / more recipes (free, on Hetzner)
4. INGEST (free green): articulation=Tapping — ROI 3.20, impact 1.6. more of Guitar-TECHS techniques (free) + commission gaps
5. GENERATE (Virtual Studio): masking_level=med — ROI 3.20, impact 1.6. Virtual Studio scenarios (needs REAL backing pool)

## Then invest — highest-impact gaps only $ can fill (rank by strategic priority)
1. COMMISSION: tuning=baritone — impact 2.4, priority 2.40, cost~high. no dataset has diverse tunings — record DI to spec
2. COMMISSION: tuning=7-string — impact 2.4, priority 2.40, cost~high. no dataset has diverse tunings — record DI to spec
3. COMMISSION: tuning=drop_c — impact 2.2, priority 2.20, cost~high. no dataset has diverse tunings — record DI to spec
4. COMMISSION: tuning=8-string — impact 2.2, priority 2.20, cost~high. no dataset has diverse tunings — record DI to spec
5. COMMISSION: string_count=7 — impact 2.2, priority 2.20, cost~high. 7/8-string absent everywhere — commission
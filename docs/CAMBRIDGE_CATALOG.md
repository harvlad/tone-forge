# Cambridge-MT Multitrack Catalog (reference)

Full 'Mixing Secrets' library snapshot: **632 full-multitrack songs**. Two hosts:
- `mtkdata.cambridgemusictechnology.co.uk` — reachable from Hetzner (datacenter). **All attempted (Riley v7/v8 training + diverse eval).**
- `multitracks.cambridge-mt.com` — behind Cloudflare bot-challenge; **blocked from datacenter, needs user-assisted download.**

## Status
- `used` (trained/eval, mtkdata): **414**
- `blocked_cloudflare` (multitracks, untapped): **218**
- `mtk_untapped`: **0**

## By genre (all hosts)
- Rock: 171
- Indie: 149
- Acoustic: 106
- Pop: 102
- Electronica: 81
- HipHop: 23

## Untapped diverse pool (blocked_cloudflare) by genre
- Rock: 94
- Indie: 34
- Pop: 32
- Acoustic: 27
- Electronica: 26
- HipHop: 5

Machine-readable: `cambridge_catalog.json` (genre, artist, title, slug, tracks, size, host, reachable_hetzner, status, url).
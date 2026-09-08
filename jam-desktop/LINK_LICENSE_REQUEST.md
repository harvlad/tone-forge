# Ableton Link — commercial license request (draft)

**To:** link-devs@ableton.com
**Subject:** Commercial Link license request — Jamn (jamn.app)

---

Hi Link team,

We'd like to incorporate Ableton Link into a proprietary application and,
per the Link LICENSE, are reaching out to arrange the commercial license.

**Company / developer:** [YOUR LEGAL NAME OR COMPANY], [COUNTRY]
**Product:** Jamn — https://jamn.app
**Contact:** [YOUR NAME], [EMAIL]

**What Jamn is**
Jamn analyzes a song and turns it into a playable kit — stems, chords,
drum one-shots and loops laid out on a pad grid so a musician can jam
along. It ships as a macOS desktop app plus iOS and web clients.

**How we use Link**
Follow-only, tempo sync. When the user enables Link, Jamn joins the local
session and follows the shared tempo and bar phase so its loops and step
sequencer land in phase with Ableton Live (or any Link peer). V1 never
proposes a tempo — the DAW is always the authority; a solo session just
keeps the song's own tempo. We link the official `ableton/link` C++
library through a thin C facade in the macOS app.

**Distribution**
Closed-source, commercial. The macOS app is distributed via our website
(signed + notarized). Which is why we need the proprietary license rather
than distributing under GPL.

**Where we're at**
Pre-launch / private beta. We want the license in place before any public
release, so this is not blocking existing users — we'd rather line it up
properly first.

Could you let us know the commercial terms and what you need from us
(agreement, Link branding compliance, etc.)? Happy to provide anything
else that helps.

Thanks,
[YOUR NAME]
[TITLE / COMPANY]
[EMAIL] · https://jamn.app

---

## Before sending — fill in:
- [ ] Legal name / company + country
- [ ] Contact name + email
- [ ] Confirm the "follow-only, DAW is authority" description still matches
      the build you're licensing (true as of LinkSync.swift V1)

## Notes (do not paste into the email)
- The plugin (jamn Kit VST3/AU) does NOT use Link — it syncs via the JUCE
  host playhead, so it's outside this license. Only jam-desktop (and the
  internal-only tools/jamn-link-helper via aalink) link Link.
- Ableton also requires Link branding/UX compliance — see
  `third_party/link/Ableton Link Guidelines.pdf`. Expect them to reference it.
- This is separate from the JUCE license (JUCE Personal tier covers the
  plugin). Link = its own agreement with Ableton.

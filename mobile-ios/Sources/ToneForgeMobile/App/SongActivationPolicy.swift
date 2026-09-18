// SongActivationPolicy.swift
//
// Pure, pinned decisions applied when a song bundle activates — the
// inline choice lives here so a test fails CI when it regresses, not
// the user's ears (desktop D-026 style).
//
// Deliberately a per-platform TWIN of
// jam-desktop/Sources/JamDesktopCore/Session/SongActivationPolicy.swift,
// not a shared ToneForgeEngine type: desktop files import
// ToneForgeEngine and JamDesktopCore side by side, so a second
// `SongActivationPolicy` in the engine would turn every unqualified
// desktop reference ambiguous. The pinned test on each platform is
// the drift guard; the DECISION is shared (web 5d1e3bd0, desktop
// 8e56570c, iOS D-038).

import Foundation

enum SongActivationPolicy {

    /// Whether the melody guide (song-melody notes on the wavetable
    /// synth) stays enabled across a song switch: it NEVER does. The
    /// guide is a PER-SONG opt-in — web parity 5d1e3bd0, where the
    /// launchpad driver's 'instrument-melody' mode survived a song
    /// switch unreset and every pad press fired a stale synth note on
    /// top of its loop; desktop twin 8e56570c, where
    /// `melodyGuideEnabled` left on from the previous song silently
    /// played the NEXT song's melody on the synth the moment the
    /// transport rolled. A note/chord guide layer sounds only where
    /// it was explicitly turned on.
    static func melodyGuideEnabledAfterSongLoad(
        wasEnabled: Bool
    ) -> Bool {
        false
    }
}

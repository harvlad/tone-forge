// SongActivationPolicy.swift
//
// Pure, pinned decisions applied when a song is attached (D-026
// style: the inline choice lives here so a test fails CI when it
// regresses, not the user's ears).

import Foundation

public enum SongActivationPolicy {

    /// Whether the melody guide (song-melody notes on the wavetable
    /// synth) stays enabled across a song switch: it NEVER does. The
    /// guide is a PER-SONG opt-in — web parity 5d1e3bd0, where the
    /// launchpad driver's 'instrument-melody' mode survived a song
    /// switch unreset and every pad press fired a stale synth note on
    /// top of its loop. The desktop twin: `melodyGuideEnabled` left on
    /// from the previous song silently played the NEXT song's melody
    /// on the synth the moment the transport rolled. A note/chord
    /// guide layer sounds only where it was explicitly turned on.
    public static func melodyGuideEnabledAfterSongLoad(
        wasEnabled: Bool
    ) -> Bool {
        false
    }
}

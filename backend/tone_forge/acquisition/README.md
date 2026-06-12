# Acquisition

**Purpose**: Pull audio in from a URL (YouTube, future SoundCloud/Bandcamp) or upload, normalize it, and produce an `AcquiredAudio` keyed by content hash.

**Owner**: Platform & Engine attention pool.

**Status**: Partial. `acquisition/youtube.py:download_audio()` is the canonical home for URL download + decode; `unified_pipeline._load_from_url` is an 8-line wrapper that does the thread offload and projects to the legacy `AudioData` shape. Switching the return type to `AcquiredAudio` is deferred until the Jam-facing acquisition route lands (see EXECUTION_PLAN.md §9 item 6).

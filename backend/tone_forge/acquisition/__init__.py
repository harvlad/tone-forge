"""Acquisition: URL/upload → cached AcquiredAudio.

Download / decode behavior lives in :mod:`tone_forge.acquisition.youtube`
(``download_audio()``). The unified pipeline's ``_load_from_url`` is a
thin wrapper that adds a thread offload and projects the result into
the legacy ``AudioData`` shape.

Per ``/EXECUTION_PLAN.md`` §9 item 6, two sub-items remain deferred
until the Jam-facing acquisition route lands:

- ``acquire()`` entry point that emits ``contracts.AcquiredAudio``
  (content hash + duration) instead of the primitive tuple.
- ``acquisition/cache.py`` with content-hash storage.

Both are downstream of route work and are not exposed via
``__all__`` yet — consumers should reach into ``acquisition.youtube``
directly for now.
"""

__all__: list[str] = []

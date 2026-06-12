# Session

**Purpose**: Own `TransportState`. Accept UI intents (play/pause/seek/loop/tempo/mute/gain), produce authoritative state, publish to Connect and UI subscribers.

**Owner**: Jam Experience attention pool.

**Status**: Landed. P5 consolidation complete — `transport.py` (`TransportState` reducer), `protocol.py` (full v1 schema), `bundle.py` (`SessionBundle.build()`) all in place; `GET /api/session/:id` serves the bundle; Jam UI consumes it. Pinned by 81/81 tests across `test_session_*.py`. See EXECUTION_PLAN.md §6 + §0 entries.

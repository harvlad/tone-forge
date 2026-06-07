"""Session Engine: canonical TransportState owner.

UI dispatches intents; this engine produces ``TransportState``; Connect
and the UI subscribe. There is no other source of truth for transport.

Skeleton. Active transport logic still lives in legacy paths until
Priority 5 of ``/EXECUTION_PLAN.md`` consolidates it here.
"""

__all__: list[str] = []

"""Monitor Chain Bank: curated tone delivery for non-modeler devices.

Ships one ``MonitorChain`` per ``MonitorChainFamily``. Chain specs are
YAML files under ``monitor/chains/``. The loader here parses and
validates those specs; Connect consumes ``MonitorChain.parameters``
directly to construct its AVAudioEngine graph deterministically.

The chain-tuning work (dialing the actual EQ / drive / reverb values)
is a listening engagement owned by the founder, not an engineering
ticket. This subsystem owns the schema and the loading code; the
content the loader returns lives in ``monitor/chains/*.yaml`` and
evolves out-of-band from the engineering branch.
"""

from tone_forge.monitor.loader import (
    CHAIN_ID_NAMESPACE,
    ChainNotFoundError,
    ChainSpecError,
    list_chain_ids,
    load_all,
    load_chain,
)

__all__ = [
    "CHAIN_ID_NAMESPACE",
    "ChainNotFoundError",
    "ChainSpecError",
    "list_chain_ids",
    "load_all",
    "load_chain",
]

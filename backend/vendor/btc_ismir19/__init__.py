"""BTC chord-recognition transformer (Park & Choi, ISMIR 2019).

Vendored from https://github.com/jayg996/BTC-ISMIR19 at commit
2682317be668032e6e4b269ded36adaa2ad57df0. MIT License (code AND the
released checkpoints live in the same repo) — see LICENSE alongside.

Why vendored, not pip: upstream publishes no package. Only the model
definition and pretrained checkpoints are kept; training code,
dataset loaders and the lab/midi writer were dropped.

Local patches (documented in each file header):
  * package-relative imports
  * np.float -> np.float64 (numpy >= 1.24)
  * HParams/yaml dependency removed (config passed as plain dict)

Use through ``tone_forge.analysis.btc_chords`` — that adapter owns
feature extraction, checkpoint loading and label mapping.
"""
from .btc_model import BTC_model

__all__ = ["BTC_model"]

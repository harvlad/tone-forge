"""ModelAdapter interface.

An adapter is a thin wrapper around a model's OFFICIAL inference path.
It must never reimplement inference — the historical timing-stretch bug
came from custom windowing around Basic Pitch.

Contract:
  - `predict(audio_path)` returns a list of note dicts:
        {pitch, onset, offset, velocity?, confidence?, instrument?}
    in seconds, absolute from audio start.
  - `inference_config()` returns the canonical dict of every parameter
    that changes output.  It is part of the cache key.
  - `checkpoint_hash()` identifies the weights.  Part of the cache key.
  - Bump ADAPTER_VERSION whenever wrapper behavior changes output.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from .. import config
from ..hashing import file_sha256


class ModelAdapter:
    model_id: str = ""
    model_version: str = ""
    adapter_version: str = "v1"
    display_name: str = ""
    # "cpu", "mps", "cuda" — devices the adapter can run on
    supported_devices: tuple = ("cpu",)
    requires_gpu: bool = False           # True -> lab won't run it locally by default
    input_sample_rate: Optional[int] = None
    environment: str = "default"         # python env / docker image tag

    def available(self) -> bool:
        """Are this adapter's dependencies importable in this environment?"""
        raise NotImplementedError

    def inference_config(self) -> dict:
        raise NotImplementedError

    def checkpoint_hash(self) -> str:
        raise NotImplementedError

    def predict(self, audio_path: Path) -> List[dict]:
        raise NotImplementedError

    # -- helpers ------------------------------------------------------------

    def _memoized_file_hash(self, path: Path) -> str:
        """sha256 of a checkpoint file, memoized on (path, size, mtime) so a
        183 MB checkpoint isn't rehashed every run."""
        memo_path = config.MODELS_DIR / "checkpoint_hashes.json"
        memo = {}
        if memo_path.exists():
            try:
                memo = json.loads(memo_path.read_text())
            except Exception:
                memo = {}
        st = path.stat()
        entry = memo.get(str(path))
        if entry and entry["size"] == st.st_size and entry["mtime"] == st.st_mtime:
            return entry["sha256"]
        digest = file_sha256(path)
        memo[str(path)] = {"size": st.st_size, "mtime": st.st_mtime, "sha256": digest}
        memo_path.parent.mkdir(parents=True, exist_ok=True)
        memo_path.write_text(json.dumps(memo, indent=1, sort_keys=True))
        return digest

    def _memoized_dir_hash(self, root: Path) -> str:
        """Stable hash of a checkpoint directory (sorted relative paths + file
        hashes), memoized per file."""
        import hashlib
        h = hashlib.sha256()
        for p in sorted(root.rglob("*")):
            if p.is_file():
                h.update(str(p.relative_to(root)).encode())
                h.update(self._memoized_file_hash(p).encode())
        return h.hexdigest()

"""Beat Capture (D-024) trained-model distribution store.

Holds versioned CoreML drum classifiers so both apps can pull the newest
one. A model is a compiled ``.mlmodelc`` *directory*. CI/the trainer
publishes it as a single zip (convenient upload); this store unzips it
server-side into per-file objects plus a ``manifest.json`` listing every
member with its sha256. Clients then reconstruct the directory by
downloading each file — no client-side unzip dependency (iOS has no
public zip API).

R2 when configured (prefix ``beat-model/``), else a local directory under
``backend/data/beat_model/``. A small ``latest.json`` pointer names the
current version so clients need one round-trip to check freshness.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Optional

from . import r2_storage

_R2_PREFIX = "beat-model/"
_LATEST_KEY = f"{_R2_PREFIX}latest.json"
_LOCAL_DIR = Path(__file__).parent.parent / "data" / "beat_model"

# Versions are trainer-assigned; keep them filesystem/URL safe.
_VERSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
# Member paths inside the .mlmodelc: relative, no traversal, forward
# slashes only.
_MEMBER_RE = re.compile(r"^[A-Za-z0-9._/-]{1,256}$")


class ModelStoreError(ValueError):
    """A publish/fetch argument failed validation."""


def _valid_version(version: str) -> bool:
    return bool(_VERSION_RE.match(version))


def _valid_member(path: str) -> bool:
    if not _MEMBER_RE.match(path):
        return False
    if path.startswith("/") or ".." in path.split("/"):
        return False
    return True


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_key(version: str, member: str) -> str:
    return f"{_R2_PREFIX}{version}/files/{member}"


def _manifest_key(version: str) -> str:
    return f"{_R2_PREFIX}{version}/manifest.json"


def _extract_members(zip_bytes: bytes) -> list[tuple[str, bytes]]:
    """Return (member_path, bytes) for every file in the zip.

    Flattens a single top-level ``*.mlmodelc/`` wrapper directory if
    present so paths are relative to the model root. Raises
    ModelStoreError on traversal or empty archives.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise ModelStoreError(f"not a valid zip: {exc}")

    names = [n for n in zf.namelist() if not n.endswith("/")]
    if not names:
        raise ModelStoreError("zip contains no files")

    # Strip a common ``something.mlmodelc/`` prefix if every entry shares
    # it, so members are rooted at the model directory.
    prefix = ""
    tops = {n.split("/", 1)[0] for n in names if "/" in n}
    if len(tops) == 1:
        only = next(iter(tops))
        if only.endswith(".mlmodelc") and all(
            n.startswith(only + "/") for n in names
        ):
            prefix = only + "/"

    out: list[tuple[str, bytes]] = []
    for name in names:
        member = name[len(prefix):] if prefix else name
        if not member or not _valid_member(member):
            raise ModelStoreError(f"unsafe member path: {name!r}")
        out.append((member, zf.read(name)))
    return out


def publish(version: str, zip_bytes: bytes) -> dict:
    """Store a model zip (exploded to per-file objects) under `version`
    and point `latest` at it.

    Returns the pointer dict ``{version, sha256, files}`` where `sha256`
    is over the canonical manifest JSON. Raises ModelStoreError on a bad
    version, empty payload, or invalid archive.
    """
    if not _valid_version(version):
        raise ModelStoreError(f"invalid version: {version!r}")
    if not zip_bytes:
        raise ModelStoreError("empty model payload")

    members = _extract_members(zip_bytes)
    manifest_files = [
        {"path": path, "sha256": sha256_hex(data), "size": len(data)}
        for path, data in members
    ]
    manifest = {"version": version, "files": manifest_files}
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode("utf-8")
    pointer = {
        "version": version,
        "sha256": sha256_hex(manifest_bytes),
        "files": len(manifest_files),
    }

    if r2_storage.is_configured():
        try:
            client = r2_storage._client()
            bucket = r2_storage.bucket_name()
            for (path, data) in members:
                client.put_object(
                    Bucket=bucket,
                    Key=_file_key(version, path),
                    Body=data,
                    CacheControl="public, max-age=31536000, immutable",
                )
            client.put_object(
                Bucket=bucket,
                Key=_manifest_key(version),
                Body=manifest_bytes,
                ContentType="application/json",
                CacheControl="public, max-age=31536000, immutable",
            )
            client.put_object(
                Bucket=bucket,
                Key=_LATEST_KEY,
                Body=json.dumps(pointer).encode("utf-8"),
                ContentType="application/json",
                CacheControl="no-cache",
            )
            return pointer
        except Exception as exc:  # noqa: BLE001 — fall back to local
            print(f"[beat_model] R2 publish failed for {version}: {exc}")

    version_dir = _LOCAL_DIR / version
    for (path, data) in members:
        dest = version_dir / "files" / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    version_dir.mkdir(parents=True, exist_ok=True)
    (version_dir / "manifest.json").write_bytes(manifest_bytes)
    (_LOCAL_DIR / "latest.json").write_text(
        json.dumps(pointer), encoding="utf-8"
    )
    return pointer


def latest() -> Optional[dict]:
    """Return the current pointer ``{version, sha256, files}`` or None."""
    if r2_storage.is_configured():
        try:
            from botocore.exceptions import ClientError

            client = r2_storage._client()
            try:
                resp = client.get_object(
                    Bucket=r2_storage.bucket_name(), Key=_LATEST_KEY
                )
                return json.loads(resp["Body"].read().decode("utf-8"))
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code")
                if code in ("404", "NoSuchKey", "NotFound"):
                    return None
                raise
        except Exception as exc:  # noqa: BLE001 — fall back to local
            print(f"[beat_model] R2 latest failed: {exc}")

    pointer_path = _LOCAL_DIR / "latest.json"
    if pointer_path.is_file():
        try:
            return json.loads(pointer_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
    return None


def read_manifest(version: str) -> Optional[dict]:
    """Return the manifest dict for `version`, or None if absent/invalid."""
    if not _valid_version(version):
        return None

    if r2_storage.is_configured():
        try:
            from botocore.exceptions import ClientError

            client = r2_storage._client()
            try:
                resp = client.get_object(
                    Bucket=r2_storage.bucket_name(),
                    Key=_manifest_key(version),
                )
                return json.loads(resp["Body"].read().decode("utf-8"))
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code")
                if code in ("404", "NoSuchKey", "NotFound"):
                    return None
                raise
        except Exception as exc:  # noqa: BLE001 — fall back to local
            print(f"[beat_model] R2 read_manifest {version} failed: {exc}")

    path = _LOCAL_DIR / version / "manifest.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
    return None


def read_file(version: str, member: str) -> Optional[bytes]:
    """Return the bytes of one model member file, or None if
    absent/invalid."""
    if not _valid_version(version) or not _valid_member(member):
        return None

    if r2_storage.is_configured():
        try:
            from botocore.exceptions import ClientError

            client = r2_storage._client()
            try:
                resp = client.get_object(
                    Bucket=r2_storage.bucket_name(),
                    Key=_file_key(version, member),
                )
                return resp["Body"].read()
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code")
                if code in ("404", "NoSuchKey", "NotFound"):
                    return None
                raise
        except Exception as exc:  # noqa: BLE001 — fall back to local
            print(f"[beat_model] R2 read_file {version}/{member} failed: {exc}")

    path = _LOCAL_DIR / version / "files" / member
    if path.is_file():
        return path.read_bytes()
    return None

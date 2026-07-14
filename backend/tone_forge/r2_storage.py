"""Cloudflare R2 storage helpers for the mobile bundle path.

R2 is S3-compatible, so this uses ``boto3`` under the hood with a custom
endpoint URL. Zero egress fees make it the right home for stem downloads
consumed by phones on cellular (see DECISIONS.md → D-002).

Behaviour when R2 is not configured
-----------------------------------
Every entry point returns cleanly (``is_configured()`` returns ``False``,
``upload_stem`` returns ``None``) so callers can wrap this in a
best-effort try/except and fall back to the existing local
``/api/admin/serve-file`` path. That keeps `python -m uvicorn` working
with no env setup for local dev.

Environment variables (all required for uploads)
------------------------------------------------
- ``R2_ACCOUNT_ID``       — Cloudflare account ID (used to build the endpoint URL).
- ``R2_ACCESS_KEY_ID``    — S3 access key issued by R2.
- ``R2_SECRET_ACCESS_KEY``— S3 secret key issued by R2.
- ``R2_BUCKET``           — bucket name. Defaults to ``tone-forge-stems``.
- ``R2_PUBLIC_HOST``      — optional. If set, public URLs are built as
                            ``https://{R2_PUBLIC_HOST}/{key}``. If unset,
                            uploads still work but ``public_url_for`` falls
                            back to a presigned GET (7-day TTL).

Object key layout
-----------------
``bundles/{analysisId}/stems/{role}.{ext}``

Where ``ext`` is derived from the source filename. In v1 we upload WAV
directly; a later patch will pipe through ffmpeg for AAC-in-M4A.
"""

from __future__ import annotations

import functools
import json
import mimetypes
import os
import threading
import time
from pathlib import Path
from typing import Optional


_DEFAULT_BUCKET = "tone-forge-stems"
_HISTORY_KEY = "data/history.json"
_HISTORY_CACHE_TTL_SEC = 30  # Re-fetch from R2 if cache is older than this

# In-memory cache for history to avoid R2 fetch on every request
_history_cache: dict = {"data": None, "fetched_at": 0.0, "lock": threading.Lock()}
_PRESIGN_TTL_SEC = 7 * 24 * 3600  # 7 days — R2's presign hard cap is 7d


def is_configured() -> bool:
    """True iff the three credential env vars are present.

    Bucket name defaults are fine, so those aren't required.
    """
    return all(
        os.environ.get(k)
        for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
    )


def bucket_name() -> str:
    return os.environ.get("R2_BUCKET") or _DEFAULT_BUCKET


def _endpoint_url() -> str:
    account = os.environ["R2_ACCOUNT_ID"]
    return f"https://{account}.r2.cloudflarestorage.com"


@functools.lru_cache(maxsize=1)
def _client():
    """Lazy boto3 client. Cached because construction is heavy (loads
    botocore data files) and the credentials never change at runtime.
    """
    # Import inside the function so importing this module is free when
    # boto3 isn't installed (e.g. running the test suite without R2 deps).
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=_endpoint_url(),
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",  # R2 ignores region but boto3 requires a value
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


def _content_type_for(path: Path) -> str:
    # Override the system mimetypes DB for our known audio formats: on
    # some systems it returns non-canonical types like "audio/x-wav" or
    # "audio/mp4a-latm" that some CDNs/browsers won't accept. We only
    # ever upload a small set of extensions here, so an explicit map is
    # simpler than a mimetypes.add_type() dance.
    ext = path.suffix.lower()
    if ext == ".wav":
        return "audio/wav"
    if ext == ".m4a":
        return "audio/mp4"
    if ext == ".mp3":
        return "audio/mpeg"
    if ext == ".flac":
        return "audio/flac"
    if ext == ".ogg":
        return "audio/ogg"
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def object_exists(key: str) -> bool:
    """HEAD check. Used to make ``upload_stem`` idempotent."""
    if not is_configured():
        return False
    from botocore.exceptions import ClientError

    try:
        _client().head_object(Bucket=bucket_name(), Key=key)
        return True
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def public_url_for(key: str) -> str:
    """Build the URL that clients (iOS app) should fetch.

    If ``R2_PUBLIC_HOST`` is set (custom domain wired up in the R2
    dashboard), we hand out a plain public URL — the client can cache
    it forever. Otherwise we fall back to a 7-day presigned URL so the
    bundle still works during bring-up before the custom domain lands.
    """
    host = os.environ.get("R2_PUBLIC_HOST")
    if host:
        host = host.rstrip("/")
        return f"https://{host}/{key.lstrip('/')}"
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket_name(), "Key": key},
        ExpiresIn=_PRESIGN_TTL_SEC,
    )


# TTL for URLs re-presigned at read time. Short because the read path
# regenerates a fresh URL on every bundle/session fetch — the client
# only needs long enough to download the stems it was just handed.
_READ_PRESIGN_TTL_SEC = 24 * 3600


def key_from_url(url: str) -> Optional[str]:
    """Extract the object key from a URL that points into our bucket.

    Recognises both URL shapes this module can emit:

    - presigned endpoint form (path-style or virtual-hosted):
      ``https://{account}.r2.cloudflarestorage.com/{bucket}/{key}?X-Amz-…``
      ``https://{bucket}.{account}.r2.cloudflarestorage.com/{key}?X-Amz-…``
    - public-host form: ``https://{R2_PUBLIC_HOST}/{key}``

    Returns ``None`` for anything else (local paths, third-party URLs,
    other buckets) so callers can safely feed it arbitrary stored
    values.
    """
    if not isinstance(url, str) or not url.startswith("https://"):
        return None
    from urllib.parse import unquote, urlparse

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = unquote(parsed.path or "")

    public = os.environ.get("R2_PUBLIC_HOST")
    if public and host == public.strip().rstrip("/").lower():
        return path.lstrip("/") or None

    if host.endswith(".r2.cloudflarestorage.com"):
        bucket = bucket_name().lower()
        if host.startswith(bucket + "."):
            return path.lstrip("/") or None
        prefix = f"/{bucket}/"
        if path.startswith(prefix):
            return path[len(prefix):] or None
    return None


def refresh_url(url: str, ttl_sec: int = _READ_PRESIGN_TTL_SEC) -> str:
    """Re-issue a fresh URL for a stored our-bucket URL.

    Presigned URLs written into history at upload time expire (R2 caps
    presigns at 7 days), so a stored URL eventually goes dead.
    Refreshing at read time makes the stored value's age irrelevant:
    every bundle/session fetch hands the client a fresh short-TTL URL.

    Returns the input unchanged when the URL isn't ours, R2 isn't
    configured, or presigning fails — callers never need a fallback.
    """
    key = key_from_url(url)
    if not key:
        return url
    host = os.environ.get("R2_PUBLIC_HOST")
    if host:
        return f"https://{host.strip().rstrip('/')}/{key.lstrip('/')}"
    if not is_configured():
        return url
    try:
        return _client().generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket_name(), "Key": key},
            ExpiresIn=ttl_sec,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[r2] refresh_url failed for key {key}: {exc}")
        return url


def stem_key(analysis_id: str, role: str, extension: str) -> str:
    """Deterministic object key for a stem.

    Deterministic so re-runs are idempotent — the second upload with
    the same analysis_id + role hits ``object_exists`` and skips.
    """
    ext = extension.lstrip(".").lower() or "bin"
    safe_role = role.replace("/", "_").replace(" ", "_")
    return f"bundles/{analysis_id}/stems/{safe_role}.{ext}"


def delete_analysis_objects(analysis_id: str) -> int:
    """Delete every R2 object under ``bundles/{analysis_id}/``.

    Used by the deep-delete path (DELETE /api/history/{id}) and the
    retention purge so server-side copies of a user's audio actually go
    away, not just the history row. Returns the number of objects
    deleted.

    Best-effort like the rest of this module: returns 0 when R2 isn't
    configured or on any error (logged), so callers never need their own
    try/except.
    """
    if not analysis_id:
        # An empty id would make the prefix "bundles//" which matches
        # nothing in practice but is one typo away from "bundles/" —
        # refuse outright rather than trust the caller.
        return 0
    if not is_configured():
        return 0

    prefix = f"bundles/{analysis_id}/"
    try:
        client = _client()
        deleted = 0
        token: Optional[str] = None
        while True:
            kwargs = {"Bucket": bucket_name(), "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            page = client.list_objects_v2(**kwargs)
            keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if keys:
                client.delete_objects(
                    Bucket=bucket_name(),
                    Delete={"Objects": keys},
                )
                deleted += len(keys)
            if not page.get("IsTruncated"):
                return deleted
            token = page.get("NextContinuationToken")
    except Exception as exc:  # noqa: BLE001
        print(f"[r2] delete failed for prefix {prefix}: {exc}")
        return 0


def upload_stem(
    local_path: str | Path,
    analysis_id: str,
    role: str,
) -> Optional[str]:
    """Upload a single stem and return its public URL.

    Idempotent: if the target key already exists, we skip the PUT and
    just build the URL. That's what lets the bundle endpoint call this
    on every request cheaply — first request pays the upload cost,
    subsequent requests are a HEAD + URL build.

    Returns ``None`` if R2 isn't configured, if the file is missing, or
    if any part of the upload path fails. Callers should treat this as
    "no R2, fall back to local URL".
    """
    if not is_configured():
        return None
    src = Path(local_path)
    if not src.is_file():
        return None

    key = stem_key(analysis_id, role, src.suffix)
    try:
        if not object_exists(key):
            _client().upload_file(
                Filename=str(src),
                Bucket=bucket_name(),
                Key=key,
                ExtraArgs={
                    "ContentType": _content_type_for(src),
                    # Long cache — the deterministic key means content is
                    # immutable per (analysis_id, role, ext) tuple.
                    "CacheControl": "public, max-age=31536000, immutable",
                },
            )
        return public_url_for(key)
    except Exception as exc:  # noqa: BLE001
        # Non-fatal: the caller falls back to the local serve-file URL.
        # We log to stderr rather than the app logger to avoid an import
        # cycle with tone_forge_api's log configuration.
        print(f"[r2] upload failed for {src} → {key}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Shared history storage
#
# When R2 is configured, history.json is stored on R2 so multiple backends
# (local dev machine, VPS) share the same song library. Analysis on local
# shows up on mobile immediately without manual sync.
# ---------------------------------------------------------------------------


def load_history() -> Optional[list]:
    """Load history from R2, with in-memory caching.

    Returns None if R2 isn't configured or on any error (caller should
    fall back to local history.json).

    Cache TTL is 30s - frequent enough for multi-device updates to feel
    near-instant, cheap enough that most bundle/chops requests hit cache.
    """
    if not is_configured():
        return None

    now = time.time()
    with _history_cache["lock"]:
        if (_history_cache["data"] is not None and
            now - _history_cache["fetched_at"] < _HISTORY_CACHE_TTL_SEC):
            return _history_cache["data"]

    try:
        from botocore.exceptions import ClientError
        import io

        client = _client()
        try:
            response = client.get_object(Bucket=bucket_name(), Key=_HISTORY_KEY)
            content = response["Body"].read().decode("utf-8")
            data = json.loads(content)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("404", "NoSuchKey", "NotFound"):
                # No history on R2 yet - return empty list
                data = []
            else:
                raise

        with _history_cache["lock"]:
            _history_cache["data"] = data
            _history_cache["fetched_at"] = now
        return data
    except Exception as exc:
        print(f"[r2] load_history failed: {exc}")
        return None


def save_history(history: list) -> bool:
    """Save history to R2.

    Returns True on success, False if R2 isn't configured or on error.
    Updates the in-memory cache on success.
    """
    if not is_configured():
        return False

    try:
        content = json.dumps(history, indent=2, default=str)
        _client().put_object(
            Bucket=bucket_name(),
            Key=_HISTORY_KEY,
            Body=content.encode("utf-8"),
            ContentType="application/json",
        )

        # Update cache immediately so subsequent reads see the new data
        with _history_cache["lock"]:
            _history_cache["data"] = history
            _history_cache["fetched_at"] = time.time()

        return True
    except Exception as exc:
        print(f"[r2] save_history failed: {exc}")
        return False


def invalidate_history_cache() -> None:
    """Force next load_history() to fetch from R2.

    Call this after another backend might have modified history (e.g.,
    after receiving a webhook or before showing critical data).
    """
    with _history_cache["lock"]:
        _history_cache["fetched_at"] = 0.0

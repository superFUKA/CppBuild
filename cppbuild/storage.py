"""Publish immutable type files before atomically replacing their manifest.

A failed publish can leave unreferenced type files, never a half-updated manifest.
Concurrent writers cooperate using an exclusive lock; stale locks are diagnosed.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .models import SettingsConflictError, SettingsError


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SettingsError(f"Invalid JSON: {path}") from exc


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def write_lock(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / ".write.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise SettingsConflictError(f"Settings are locked: {lock}") from exc
    try:
        os.close(fd)
        yield
    finally:
        lock.unlink()


def contained(root, value):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise SettingsError("Expected a nonempty path string")
    root = Path(root).resolve()
    result = (root / value).resolve()
    if not result.is_relative_to(root):
        raise SettingsError(f"Path outside managed root: {value}")
    return result


def object_fields(value, required):
    if not isinstance(value, dict) or set(value) != set(required):
        raise SettingsError(f"Expected fields: {sorted(required)}")


def manifest(path, kind):
    result = read_json(path)
    object_fields(result, {"schema_version", "kind", "data"})
    if type(result["schema_version"]) is not int or result["schema_version"] != 1 or result["kind"] != kind:
        raise SettingsError(f"Unsupported schema or document kind: {path}")
    return result["data"]


def envelope(kind, data):
    return {"schema_version": 1, "kind": kind, "data": data}

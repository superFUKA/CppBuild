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


CURRENT_SCHEMA = 4


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


def publish_documents(documents):
    """Restore published documents on failure; callers hold all writer locks."""
    written = []
    try:
        for path, content in documents.items():
            previous = path.read_bytes() if path.exists() else None
            if previous == content:
                continue
            written.append((path, previous))
            atomic_write(path, content)
    except BaseException as failure:
        try:
            for path, previous in reversed(written):
                if previous is None:
                    path.unlink(missing_ok=True)
                elif not path.exists() or path.read_bytes() != previous:
                    atomic_write(path, previous)
        except OSError as recovery:
            raise SettingsError(f"Settings rollback failed: {recovery}") from failure
        raise
    return tuple(str(path) for path, _ in written)


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


def manifest(path, kind, *, root=None, with_version=False):
    result = read_json(path)
    object_fields(result, {"schema_version", "kind", "data"})
    if type(result["schema_version"]) is not int or result["schema_version"] not in {1, 2, 3, 4} or result["kind"] != kind:
        raise SettingsError(f"Unsupported schema or document kind: {path}")
    from .paths import decode
    data = decode(path, kind, result["data"], result["schema_version"], root=root)
    return (data, result["schema_version"]) if with_version else data


def document(path, kind, data, *, version=CURRENT_SCHEMA):
    from .paths import encode
    return encoded({"schema_version": version, "kind": kind, "data": encode(path, kind, data)})


def needs_migration(path):
    return read_json(path).get("schema_version") < CURRENT_SCHEMA


def envelope(kind, data):
    return {"schema_version": 1, "kind": kind, "data": data}

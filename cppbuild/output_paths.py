"""Ephemeral output roots, with owned leaves shared by whole/individual builds."""
import hashlib
import os
from pathlib import Path, PureWindowsPath

from . import storage
from .models import SettingsError


MARKER = ".cppbuild-output.json"


def validate(value):
    if not isinstance(value, str) or not value or any(c in value for c in ("\x00", "\n", "\r", ";", "$<")):
        raise SettingsError("Output directory must be a nonempty literal path string")
    windows = PureWindowsPath(value)
    if (windows.drive and not windows.root) or (os.name == "nt" and windows.root and not windows.drive):
        raise SettingsError("Drive-relative output paths are not supported")


def root(owner, value):
    validate(value)
    return (owner.root / ".cppbuild" / value).resolve()


def identity(owner, context=""):
    # A copied Project retains its GUID; include its location to isolate its cache.
    guid = getattr(owner.settings._data, "guid", "solution")
    return hashlib.sha256((str(owner.root) + "\0" + guid + "\0" + context).encode()).hexdigest()[:10]


def area(owner, value, context=""):
    # One short leaf keeps CMake's own compiler probes below Windows path limits.
    return storage.contained(root(owner, value), identity(owner, context))


def claim(owner, value, context=""):
    directory = area(owner, value, context)
    marker = directory / MARKER
    expected = {"root": str(owner.root), "guid": getattr(owner.settings._data, "guid", None),
                "identity": identity(owner, context), "context": context}
    if marker.exists():
        if storage.read_json(marker) != expected:
            raise SettingsError(f"Output directory ownership does not match: {directory}")
    elif directory.exists() and any(directory.iterdir()):
        raise SettingsError(f"Refusing an unowned output directory: {directory}")
    else:
        storage.atomic_write(marker, storage.encoded(expected))
    return directory


def generated_directory(path):
    """Recognize old output leaves too, after settings changes or reopening."""
    return (Path(path) / MARKER).is_file()

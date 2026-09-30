"""Relative paths on disk, with existing root-relative settings at the API boundary."""
from copy import deepcopy
import os
from pathlib import Path, PureWindowsPath

from .dependencies import LegacyDependency
from .models import CMakePackage, CMakeSource, ImportedLibrary, SettingsError


TYPE_KINDS = {"static_library", "shared_library", "executable", "interface_library", "header_only", "test"}


def relative(base, target):
    try:
        return Path(os.path.relpath(target, base)).as_posix()
    except ValueError as exc:
        raise SettingsError("Managed paths must be on the same drive/share as their settings file") from exc


def project_values(values, convert):
    values.source_directories = [convert(p) for p in values.source_directories]
    values.project_headers = [convert(p) for p in values.project_headers]
    for kind in values.types.values():
        kind.include_directories = [convert(p) for p in kind.include_directories]
    for dependency in values.dependencies.values():
        if isinstance(dependency, LegacyDependency) and dependency.solution_directory is not None:
            dependency.solution_directory = convert(dependency.solution_directory)
        elif isinstance(dependency, (CMakePackage, CMakeSource)):
            dependency.directory = convert(dependency.directory)
        elif isinstance(dependency, ImportedLibrary):
            dependency.locations = {k: convert(p) for k, p in dependency.locations.items()}
            dependency.import_libraries = {k: convert(p) for k, p in dependency.import_libraries.items()}
            dependency.include_directories = [convert(p) for p in dependency.include_directories]


def rebase(value, before, after, *, stored=False):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise SettingsError("Expected a nonempty path string")
    windows = PureWindowsPath(value)
    if stored and (Path(value).is_absolute() or windows.drive or windows.root):
        raise SettingsError("Stored paths must be relative to their settings file")
    if windows.drive and not windows.root:
        raise SettingsError("Drive-relative paths are not supported")
    return relative(after, (before / value).resolve())


def _list(data, key, convert):
    if key in data:
        if not isinstance(data[key], list):
            raise SettingsError(f"{key} must be a list")
        data[key] = [convert(value) for value in data[key]]


def _mapping(data, key, convert):
    if key in data:
        if not isinstance(data[key], dict):
            raise SettingsError(f"{key} must be a mapping")
        data[key] = {name: convert(value) for name, value in data[key].items()}


def transform(kind, payload, convert):
    data = deepcopy(payload)
    if not isinstance(data, dict):
        raise SettingsError("Expected settings object")
    if kind == "solution":
        _mapping(data, "projects", convert)
        _mapping(data, "file_templates", convert)
        _list(data, "dependency_directories", convert)
        references = data.get("references", {})
        if not isinstance(references, dict):
            raise SettingsError("references must be a mapping")
        for reference in references.values():
            if not isinstance(reference, dict) or "solution_directory" not in reference:
                raise SettingsError("Expected ProjectReference")
            reference["solution_directory"] = convert(reference["solution_directory"])
    elif kind == "project":
        _list(data, "source_directories", convert)
        _list(data, "project_headers", convert)
        dependencies = data.get("dependencies", {})
        if not isinstance(dependencies, dict):
            raise SettingsError("dependencies must be a mapping")
        for dependency in dependencies.values():
            if not isinstance(dependency, dict):
                raise SettingsError("Expected dependency object")
            category = dependency.get("kind", "project")
            if not isinstance(category, str):
                raise SettingsError("Expected dependency kind string")
            values = dependency.get("values", dependency)
            if not isinstance(values, dict):
                raise SettingsError("Expected dependency values")
            if category == "project" and values.get("solution_directory") is not None:
                values["solution_directory"] = convert(values["solution_directory"])
            elif category in {"package", "source"} and "directory" in values:
                values["directory"] = convert(values["directory"])
            elif category == "imported":
                _mapping(values, "locations", convert)
                _mapping(values, "import_libraries", convert)
                _list(values, "include_directories", convert)
    elif kind in TYPE_KINDS:
        _list(data, "include_directories", convert)
    return data


def root_for(path, kind):
    return path.parent.parent.parent if kind in TYPE_KINDS else path.parent.parent


def encode(path, kind, payload):
    path = Path(path)
    root = root_for(path, kind)
    data = transform(kind, payload, lambda value: rebase(value, root, path.parent))
    if kind == "project":
        _mapping(data, "types", lambda value: rebase(value, path.parent / "types", path.parent))
    return data


def decode(path, kind, payload, version, *, root=None):
    path = Path(path)
    root = root_for(path, kind) if root is None else Path(root)
    before = path.parent if version >= 2 else root
    data = transform(kind, payload, lambda value: rebase(value, before, root, stored=version >= 2))
    if kind == "project" and version >= 2:
        _mapping(data, "types", lambda value: rebase(value, path.parent, path.parent / "types", stored=True))
    return data

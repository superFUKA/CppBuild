"""Paths of the management files and the rules for where Projects may be.

A Solution's settings directory (.cppbuild) holds every management and generated file of
the Solution. On disk, the Solution's paths are relative to that directory; a Project's
paths are relative to the Project's directory, so moving either touches one entry. At the
API, the Solution's paths are relative to its base directory (Solution.root) and a
Project's paths to the Project's directory.
"""
from copy import deepcopy
import os
from pathlib import Path, PureWindowsPath

from .models import CMakePackage, CMakeSource, ImportedLibrary, SettingsError

MANAGEMENT = ".cppbuild"
TYPE_KINDS = {"static_library", "shared_library", "executable", "interface_library", "test"}


def relative(base, target):
    try:
        return Path(os.path.relpath(target, base)).as_posix()
    except ValueError as exc:
        raise SettingsError(f"{target} must be on the same drive as {base} to be recorded as a relative path") from exc


def resolve(base, value):
    """value (relative to base, or absolute) as an absolute path; it may lie anywhere."""
    if not isinstance(value, str) or not value or "\x00" in value:
        raise SettingsError("Expected a nonempty path string")
    windows = PureWindowsPath(value)
    if windows.drive and not windows.root:
        raise SettingsError("Drive-relative paths are not supported")
    return (Path(base) / value).resolve()


def key(path):
    """A comparable form: Windows paths differ only in case where resolve() kept a given spelling."""
    return Path(os.path.normcase(str(path)))


def inside(path, directory):
    """path is directory or below it."""
    return key(path).is_relative_to(key(directory))


def overlap(first, second):
    return inside(first, second) or inside(second, first)


def in_management(path):
    """Whether path is a .cppbuild directory or lies inside one."""
    return any(part.casefold() == MANAGEMENT for part in Path(path).parts)


def project_values(values, convert):
    values.source_directories = [convert(p) for p in values.source_directories]
    values.project_headers = [convert(p) for p in values.project_headers]
    values.output_files = [convert(p) for p in values.output_files]
    for kind in values.types.values():
        kind.include_directories = [convert(p) for p in kind.include_directories]
    for dependency in values.dependencies.values():
        if isinstance(dependency, (CMakePackage, CMakeSource)):
            dependency.directory = convert(dependency.directory)
        elif isinstance(dependency, ImportedLibrary):
            dependency.locations = {k: convert(p) for k, p in dependency.locations.items()}
            dependency.import_libraries = {k: convert(p) for k, p in dependency.import_libraries.items()}
            dependency.include_directories = [convert(p) for p in dependency.include_directories]


def rebase(value, before, after, *, stored=False):
    """value relative to before (or absolute) as a path relative to after."""
    if not isinstance(value, str) or not value or "\x00" in value:
        raise SettingsError("Expected a nonempty path string")
    windows = PureWindowsPath(value)
    if stored and (Path(value).is_absolute() or windows.drive or windows.root):
        raise SettingsError("Stored paths must be relative")
    return relative(after, resolve(before, value))


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
        _list(data, "output_files", convert)
        dependencies = data.get("dependencies", {})
        if not isinstance(dependencies, dict):
            raise SettingsError("dependencies must be a mapping")
        for dependency in dependencies.values():
            if (not isinstance(dependency, dict) or not isinstance(dependency.get("kind"), str)
                    or not isinstance(dependency.get("values"), dict)):
                raise SettingsError("Expected dependency object")
            values = dependency["values"]
            if dependency.get("kind") in {"package", "source"} and "directory" in values:
                values["directory"] = convert(values["directory"])
            elif dependency.get("kind") == "imported":
                _mapping(values, "locations", convert)
                _mapping(values, "import_libraries", convert)
                _list(values, "include_directories", convert)
    elif kind in TYPE_KINDS:
        _list(data, "include_directories", convert)
    return data


def encode(path, kind, payload, root):
    """API values (relative to root) as stored values: the Solution's relative to its settings
    directory, a Project's relative to root, its directory."""
    after = Path(path).parent if kind == "solution" else root
    return transform(kind, payload, lambda value: rebase(value, root, after))


def decode(path, kind, payload, root):
    before = Path(path).parent if kind == "solution" else root
    return transform(kind, payload, lambda value: rebase(value, before, root, stored=True))

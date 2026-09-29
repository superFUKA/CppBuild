"""Serialization and CMake adapters for explicitly registered dependencies."""
from dataclasses import asdict
from pathlib import Path
import re
import uuid

from .models import CMakePackage, CMakeSource, Dependency, ImportedLibrary, ProjectType, SettingsError


KINDS = {"project": Dependency, "package": CMakePackage, "source": CMakeSource, "imported": ImportedLibrary}


def guid(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, AttributeError) as exc:
        raise SettingsError("Expected a canonical Project GUID") from exc


def reference_name(value):
    if not isinstance(value, str) or not value or value != value.strip() or any(ord(c) < 32 for c in value):
        raise SettingsError("Expected a nonempty reference name without control characters")


def encode(value):
    for kind, cls in KINDS.items():
        if isinstance(value, cls):
            return {"kind": kind, "values": asdict(value)}
    raise SettingsError("Unknown dependency data")


def decode(raw):
    if not isinstance(raw, dict):
        raise SettingsError("Dependency record must be an object")
    if "kind" not in raw:  # M3b management data
        raw = {"kind": "project", "values": raw}
    try:
        if set(raw) != {"kind", "values"}:
            raise ValueError("Invalid fields")
        cls = KINDS[raw["kind"]]
        values = dict(raw["values"])
        if "project_type" in values:
            values["project_type"] = ProjectType(values["project_type"])
        return cls(**values)
    except (TypeError, ValueError, KeyError) as exc:
        raise SettingsError("Invalid dependency record") from exc


def path(project, value):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise SettingsError("Expected dependency path")
    return (project.root / value).resolve()


def validate(project, value):
    if isinstance(value, Dependency):
        if not isinstance(value.project, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", value.project):
            raise SettingsError("Invalid dependency Project name")
        if value.solution_directory is not None:
            path(project, value.solution_directory)
        if value.reference is not None:
            reference_name(value.reference)
            if value.solution_directory is not None or value.project_guid is not None:
                raise SettingsError("Named references store their target only in the Solution")
        if value.project_guid is not None:
            guid(value.project_guid)
    elif isinstance(value, (CMakePackage, CMakeSource)):
        path(project, value.directory)
        if not isinstance(value.target, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_:.+-]*", value.target):
            raise SettingsError("Invalid external CMake target")
        if isinstance(value, CMakePackage) and (not isinstance(value.name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", value.name)):
            raise SettingsError("Invalid CMake package name")
        return
    elif isinstance(value, ImportedLibrary):
        if not isinstance(value.locations, dict) or not isinstance(value.import_libraries, dict) or not isinstance(value.include_directories, list):
            raise SettingsError("Invalid imported library paths")
        for configuration, filename in [*value.locations.items(), *value.import_libraries.items()]:
            if configuration not in {"Debug", "Release", "RelWithDebInfo", "MinSizeRel"}:
                raise SettingsError("Invalid imported configuration")
            path(project, filename)
        for directory in value.include_directories:
            path(project, directory)
    else:
        raise SettingsError("Unknown dependency")
    if not isinstance(value.project_type, ProjectType) or value.project_type not in {ProjectType.STATIC_LIBRARY, ProjectType.SHARED_LIBRARY, ProjectType.HEADER_ONLY}:
        raise SettingsError("Only library types can be linked")


def cmake(project, settings, key, value, target, scope):
    from .engine import _path, _quote
    alias = "external_" + key
    lines = []
    if isinstance(value, CMakePackage):
        directory = path(project, value.directory)
        lines += [f"find_package({value.name} CONFIG REQUIRED PATHS {_path(directory)} NO_DEFAULT_PATH)"]
        alias = value.target
    elif isinstance(value, CMakeSource):
        lines += [f"add_subdirectory({_path(path(project, value.directory))} {_quote('_external/' + key)} EXCLUDE_FROM_ALL)"]
        alias = value.target
    elif isinstance(value, ImportedLibrary):
        kind = value.project_type
        if kind == ProjectType.HEADER_ONLY:
            lines += [f"add_library({alias} INTERFACE IMPORTED)"]
        else:
            if settings.configuration not in value.locations:
                raise SettingsError("No imported artifact for selected configuration")
            lines += [f"add_library({alias} {'STATIC' if kind == ProjectType.STATIC_LIBRARY else 'SHARED'} IMPORTED)"]
            for config in ("Debug", "Release", "RelWithDebInfo", "MinSizeRel"):
                # Unprovided configurations point at a deliberately missing path,
                # never silently use another configuration's binary.
                location = path(project, value.locations[config]) if config in value.locations else project.root / ".cppbuild/missing" / config
                if config == settings.configuration and not location.is_file():
                    raise SettingsError(f"Imported artifact does not exist: {location}")
                lines.append(f"set_property(TARGET {alias} PROPERTY IMPORTED_LOCATION_{config.upper()} {_path(location)})")
                if kind == ProjectType.SHARED_LIBRARY:
                    if settings.configuration not in value.import_libraries:
                        raise SettingsError("A Windows shared library requires its import library")
                    implib = path(project, value.import_libraries[config]) if config in value.import_libraries else project.root / ".cppbuild/missing" / (config + ".lib")
                    if config == settings.configuration and not implib.is_file():
                        raise SettingsError(f"Import library does not exist: {implib}")
                    lines.append(f"set_property(TARGET {alias} PROPERTY IMPORTED_IMPLIB_{config.upper()} {_path(implib)})")
        for directory in value.include_directories:
            lines.append(f"target_include_directories({alias} INTERFACE {_path(path(project, directory))})")
    else:
        return []
    lines = [f"if(NOT TARGET {alias})", *lines, "endif()"]
    lines += [f"if(NOT TARGET {alias})", f"  message(FATAL_ERROR {_quote('Missing external target ' + alias)})", "endif()",
              f"target_link_libraries({target} {scope} {alias})"]
    return lines

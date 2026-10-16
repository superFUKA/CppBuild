"""Serialization and CMake adapters for explicitly registered dependencies."""
from dataclasses import asdict
import re
import uuid

from .models import (AlreadyRegisteredError, CMakePackage, CMakeSource, CONFIGURATIONS, Dependency, ImportedLibrary,
                     InvalidValueError, LIBRARY_TYPES, ProjectType, SettingsError)


KINDS = {"project": Dependency, "package": CMakePackage, "source": CMakeSource, "imported": ImportedLibrary}


def guid(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, AttributeError) as exc:
        raise SettingsError("Expected a canonical Project GUID") from exc


def encode(value):
    for kind, cls in KINDS.items():
        if isinstance(value, cls):
            return {"kind": kind, "values": asdict(value)}
    raise SettingsError("Unknown dependency data")


def decode(raw):
    if not isinstance(raw, dict):
        raise SettingsError("Dependency record must be an object")
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


def registered(values, dependency):
    """Refuse a link the Project already has; the error names the existing dependency id."""
    existing = next((key for key, value in values.dependencies.items() if value == dependency), None)
    if existing is not None:
        raise AlreadyRegisteredError("Dependency is already registered", kind="dependency", name=existing)


def path(project, value):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise SettingsError("Expected dependency path")
    return (project.root / value).resolve()


def validate(project, value):
    if isinstance(value, Dependency):
        guid(value.project_guid)
    elif isinstance(value, (CMakePackage, CMakeSource)):
        path(project, value.directory)
        if not isinstance(value.target, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_:.+-]*", value.target):
            raise InvalidValueError("Invalid external CMake target", field="target", value=value.target)
        if isinstance(value, CMakePackage) and (not isinstance(value.name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", value.name)):
            raise InvalidValueError("Invalid CMake package name", field="name", value=value.name)
        return
    elif isinstance(value, ImportedLibrary):
        if not isinstance(value.locations, dict) or not isinstance(value.import_libraries, dict) or not isinstance(value.include_directories, list):
            raise SettingsError("Invalid imported library paths")
        for configuration, filename in [*value.locations.items(), *value.import_libraries.items()]:
            if configuration not in CONFIGURATIONS:
                raise InvalidValueError("Invalid imported configuration", field="configuration", value=configuration,
                                        allowed=CONFIGURATIONS)
            path(project, filename)
        for directory in value.include_directories:
            path(project, directory)
    else:
        raise SettingsError("Unknown dependency")
    if not isinstance(value.project_type, ProjectType) or value.project_type not in LIBRARY_TYPES:
        raise InvalidValueError("Only library types can be linked", field="project_type", value=value.project_type,
                                allowed=sorted(LIBRARY_TYPES))


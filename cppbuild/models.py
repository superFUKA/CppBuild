"""Management data and ephemeral build settings (initial implementation)."""
from dataclasses import dataclass, field
from enum import Enum


class ProjectType(str, Enum):
    STATIC_LIBRARY = "static_library"
    SHARED_LIBRARY = "shared_library"
    EXECUTABLE = "executable"
    HEADER_ONLY = "header_only"
    TEST = "test"


class Inheritance(Enum):
    INHERIT = "inherit"


INHERIT = Inheritance.INHERIT


@dataclass
class TypeSettingsData:
    compile_definitions: list[str] = field(default_factory=list)


@dataclass
class ProjectSettingsData:
    name: str
    types: dict[ProjectType, TypeSettingsData]
    source_directories: list[str] = field(default_factory=lambda: ["src", "include"])


@dataclass
class SolutionSettingsData:
    name: str
    projects: dict[str, str] = field(default_factory=dict)
    main_project: str | None = None


@dataclass
class ProjectBuildSettings:
    configuration: str | Inheritance = INHERIT
    architecture: str | Inheritance = INHERIT
    cpp_standard: int | Inheritance = INHERIT
    project_type: ProjectType | None = None
    parallel: int = 1
    run_arguments: list[str] = field(default_factory=list)


@dataclass
class SolutionBuildSettings:
    configuration: str = "Debug"
    architecture: str = "x64"
    cpp_standard: int = 20
    build_projects: list[str] = field(default_factory=list)
    run_projects: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ChangeReport:
    changed_paths: tuple[str, ...]
    pending_update: bool = True


class SettingsError(ValueError):
    """Invalid or unsupported management/build data."""


class SettingsConflictError(SettingsError):
    """Saved settings changed since this object last read them."""

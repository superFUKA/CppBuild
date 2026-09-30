"""Management data and ephemeral build settings (initial implementation)."""
from dataclasses import dataclass, field
from enum import Enum
import uuid


class ProjectType(str, Enum):
    STATIC_LIBRARY = "static_library"
    SHARED_LIBRARY = "shared_library"
    EXECUTABLE = "executable"
    INTERFACE_LIBRARY = "interface_library"
    TEST = "test"


LIBRARY_TYPES = frozenset({ProjectType.STATIC_LIBRARY, ProjectType.SHARED_LIBRARY,
                           ProjectType.INTERFACE_LIBRARY})


class Inheritance(Enum):
    INHERIT = "inherit"


INHERIT = Inheritance.INHERIT


@dataclass
class TypeSettingsData:
    compile_definitions: list[str] = field(default_factory=list)
    public_definitions: list[str] = field(default_factory=list)
    include_directories: list[str] = field(default_factory=lambda: ["include"])


@dataclass
class Dependency:
    project_guid: str
    project_type: ProjectType


@dataclass
class ProjectReference:
    project_guid: str
    solution_directory: str


@dataclass
class CMakePackage:
    name: str
    target: str
    directory: str


@dataclass
class CMakeSource:
    directory: str
    target: str


@dataclass
class ImportedLibrary:
    project_type: ProjectType
    locations: dict[str, str] = field(default_factory=dict)
    include_directories: list[str] = field(default_factory=list)
    import_libraries: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class LinkReport:
    dependency_id: str
    target: str
    project_type: ProjectType | None


@dataclass
class ProjectSettingsData:
    name: str
    types: dict[ProjectType, TypeSettingsData]
    source_directories: list[str] = field(default_factory=lambda: ["src", "include"])
    dependencies: dict[str, Dependency] = field(default_factory=dict)
    project_headers: list[str] = field(default_factory=list)
    system_headers: list[str] = field(default_factory=list)
    guid: str = field(default_factory=lambda: str(uuid.uuid4()))
    initial_type: ProjectType | None = None


@dataclass
class SolutionFolderSettings:
    projects: str = "Projects"
    linked_projects: str = "LinkedProjects"
    project_folders: dict[str, str] = field(default_factory=dict)


@dataclass
class SolutionSettingsData:
    name: str
    projects: dict[str, str] = field(default_factory=dict)
    main_project: str | None = None
    file_templates: dict[str, str] = field(default_factory=dict)
    solution_folders: SolutionFolderSettings | None = None
    references: dict[str, ProjectReference] = field(default_factory=dict)
    # Directories whose direct child Solutions resolve dependency GUIDs first (e.g. "deps").
    dependency_directories: list[str] = field(default_factory=list)


@dataclass
class ToolSettings:
    cmake: str = "cmake"
    ctest: str = "ctest"
    environment: dict[str, str] = field(default_factory=dict)


@dataclass
class CMakeSettings:
    """Generator and compiler selection. Ephemeral like every build setting.

    generator None resolves per host OS (Windows: Visual Studio 17 2022,
    others: Ninja Multi-Config), never to CMake's own default.
    """
    generator: str | None = None
    toolset: str | None = None
    c_compiler: str | None = None
    cxx_compiler: str | None = None
    toolchain_file: str | None = None


@dataclass
class ProjectBuildSettings:
    configuration: str | Inheritance = INHERIT
    architecture: str | None | Inheritance = INHERIT
    cpp_standard: int | Inheritance = INHERIT
    project_type: ProjectType | None = None
    parallel: int = 1
    run_arguments: list[str] = field(default_factory=list)
    run_wait: bool = True
    test_parallel: int = 1
    googletest_archive: str | None = None
    tools: ToolSettings | Inheritance = INHERIT
    cmake: CMakeSettings | Inheritance = INHERIT


@dataclass
class SolutionBuildSettings:
    configuration: str = "Debug"
    architecture: str | None = None
    cpp_standard: int = 20
    build_projects: list[str] | None = None
    run_projects: list[str] = field(default_factory=list)
    external_build_settings: dict[str, "SolutionBuildSettings"] = field(default_factory=dict)
    parallel: int = 1
    run_parallel: int = 1
    run_wait: bool = True
    run_continue_on_failure: bool = False
    test_projects: list[str] | None = None
    test_continue_on_failure: bool = True
    tools: ToolSettings = field(default_factory=ToolSettings)
    cmake: CMakeSettings = field(default_factory=CMakeSettings)
    # Project GUID -> STATIC_LIBRARY/SHARED_LIBRARY, overriding saved link types in this operation.
    project_types: dict[str, ProjectType] = field(default_factory=dict)


@dataclass(frozen=True)
class ChangeReport:
    changed_paths: tuple[str, ...]
    pending_update: bool = True


class SettingsError(ValueError):
    """Invalid or unsupported management/build data."""


class SettingsConflictError(SettingsError):
    """Saved settings changed since this object last read them."""


@dataclass(frozen=True)
class MissingDependency:
    project_guid: str
    project_type: ProjectType
    required_by: tuple[str, ...]
    registered_locations: tuple = ()


class MissingDependenciesError(SettingsError):
    """Dependencies found neither in the dependency directories nor at their registered locations."""

    def __init__(self, missing):
        self.missing = tuple(missing)
        super().__init__("Missing dependency Project GUIDs: " + ", ".join(m.project_guid for m in self.missing))

    @property
    def project_guids(self):
        return tuple(m.project_guid for m in self.missing)

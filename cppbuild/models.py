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
class GitSource:
    """A linked Solution's repository: its URL, the recorded commit and the Solution's .cppbuild in it."""
    url: str
    revision: str
    path: str = ".cppbuild"


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
    # WINDOWS_EXPORT_ALL_SYMBOLS on the shared library: a DLL and its import library
    # without __declspec(dllexport). Other platforms export every symbol anyway.
    windows_export_all_symbols: bool = False


@dataclass
class SolutionFolderSettings:
    projects: str = "Projects"
    linked_projects: str = "LinkedProjects"
    project_folders: dict[str, str] = field(default_factory=dict)
    # Targets CppBuild did not write, for this Solution and its linked Solutions: <external>/CMake
    # (ALL_BUILD, ZERO_CHECK, ...) and <external>/GoogleTest (gtest, gtest_main).
    external: str = "External"


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
    # Place name (directly under the first dependency directory) -> repository cloned there.
    git_sources: dict[str, GitSource] = field(default_factory=dict)


@dataclass
class ToolSettings:
    cmake: str = "cmake"
    ctest: str = "ctest"
    git: str = "git"
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
    # Final outputs of this Project; None keeps CMake's layout in the Solution build tree.
    artifact_directory: str | None = None


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
    # The Solution's CMake build trees, one per generation environment.
    intermediate_directory: str = "output/intermediate"
    # Clone missing git_sources (and their own) before an operation, as the generated CMake does.
    fetch_git: bool = True


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


@dataclass(frozen=True)
class FetchFailure:
    name: str
    directory: str
    url: str
    revision: str
    reason: str


class GitFetchError(SettingsError):
    """Linked Solutions that could not be cloned; each can be placed by hand instead."""

    def __init__(self, failures):
        self.failures = tuple(failures)
        lines = ["Linked Solutions could not be fetched with git. Place each one by hand:"]
        for f in self.failures:
            lines.append(f"  {f.directory}: {f.url} at {f.revision} ({f.reason})")
            lines.append(f"    git clone {f.url} {f.directory} && git -C {f.directory} checkout {f.revision}")
        super().__init__("\n".join(lines))


class GitSourceConflictError(SettingsError):
    """Linked Solutions record one repository at different commits and the top level records none."""

    def __init__(self, conflicts):
        # (url, ((recorded_by, revision, path), ...)) per repository
        self.conflicts = tuple(conflicts)
        lines = ["Linked Solutions record the same repository differently; record it in this Solution "
                 "with Solution.set_git_source to choose:"]
        for url, records in self.conflicts:
            lines.append(f"  {url}: " + ", ".join(f"{revision} ({path}) by {owner}" for owner, revision, path in records))
        super().__init__("\n".join(lines))
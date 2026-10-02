"""One Solution, configured as one CMake project with its linked Solutions.

A plan resolves the Solution's members and the Projects they need from linked
Solutions (with the same GUID rules as before), writes the CMake files of every
involved Solution, and turns the non-saved build settings into a CMake initial
cache script. Generated files never contain those settings.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import cached_property
import hashlib
from pathlib import Path

from . import cmake_files, storage
from .graph import resolve
from .models import Dependency, ImportedLibrary, ProjectType, SettingsError

@dataclass
class Request:
    """What one provider creates for the Projects that link it.

    built: link types requested by Projects of the default build.
    listed: link types requested by Projects listed only for the IDE (solution_folders);
    those targets exist for the IDE but stay out of the default build unless also built.
    """
    built: set = field(default_factory=set)
    listed: set = field(default_factory=set)

    def tokens(self):
        """The CMake request list (see cmake_files._requested_types)."""
        return (sorted(cmake_files.KIND[k] for k in self.built)
                + sorted("DISPLAY_" + cmake_files.KIND[k] for k in self.listed - self.built))


@dataclass
class Plan:
    solution: object
    roots: list
    nodes: list
    projects: dict = field(default_factory=dict)  # GUID -> Project, every Project in the CMake project
    files: dict = field(default_factory=dict)  # GUID -> scanned files

    # Requests ------------------------------------------------------------------

    @cached_property
    def listed(self):
        """GUIDs of linked Solutions' Projects listed only for the IDE (solution_folders)."""
        needed = set()

        def visit(node):
            if node.project.settings._data.guid not in needed:
                needed.add(node.project.settings._data.guid)
                for child in node.dependencies:
                    visit(child)
        for node in self.roots:
            visit(node)
        return {guid for guid in self.projects if guid not in needed}

    def _requests(self):
        """(every request, cross-Solution requests) by provider GUID."""
        every, crossing = {}, {}
        for project in self.projects.values():
            listed = project.settings._data.guid in self.listed
            for dependency in project.settings._data.dependencies.values():
                if isinstance(dependency, Dependency) and dependency.project_guid in self.projects:
                    provider = self.projects[dependency.project_guid]
                    tables = [every] if project.solution.root == provider.solution.root else [every, crossing]
                    for table in tables:
                        request = table.setdefault(dependency.project_guid, Request())
                        (request.listed if listed else request.built).add(dependency.project_type)
        return every, crossing

    def external_requests(self):
        every, _ = self._requests()
        result = []
        for project in self._external_projects():
            guid = project.settings._data.guid
            tokens = (["DISPLAY"] if guid in self.listed else []) + (every[guid].tokens() if guid in every else [])
            if tokens:
                result.append((project, tokens))
        return result

    def member_requests(self):
        _, crossing = self._requests()
        return [(p, crossing[p.settings._data.guid].tokens())
                for p in sorted(self.solution.projects(), key=lambda p: p.name) if p.settings._data.guid in crossing]
    def _external_projects(self):
        return sorted((p for p in self.projects.values() if p.solution.root != self.solution.root),
                      key=lambda p: (str(p.solution.root), p.name))

    def external_solutions(self):
        result = {}
        for project in self._external_projects():
            result.setdefault(project.solution.root, project.solution)
        return [result[root] for root in sorted(result, key=str)]

    def linked_solutions(self):
        """(Solution, relative directory, binary subdirectory, IDE folder) per linked Solution."""
        solutions = self.external_solutions()
        folders = self.solution.settings._data.solution_folders
        names = {}
        for other in solutions:
            name = other.settings._data.name
            if sum(o.settings._data.name.casefold() == name.casefold() for o in solutions) > 1:
                name += " (" + hashlib.sha256(str(other.root).encode()).hexdigest()[:12] + ")"
            names[other.root] = name
        used, result = set(), []
        for other in solutions:
            binary = other.settings._data.name
            index = 2
            while binary.casefold() in used:
                binary = f"{other.settings._data.name}_{index}"
                index += 1
            used.add(binary.casefold())
            directory = cmake_files._relative(self.solution.root, other.root, "A linked Solution")
            folder = None if folders is None else folders.linked_projects + "/" + names[other.root]
            result.append((other, directory, binary, folder))
        return result

    # Files ---------------------------------------------------------------------

    def documents(self):
        """Generated files of every involved Solution, keyed by path, decided once per plan."""
        return dict(self._all_documents)

    @cached_property
    def _all_documents(self):
        result = {}
        self._documents(result, set())
        return result

    def _documents(self, result, visited):
        # Files already written from an including Solution's point of view are kept.
        visited.add(self.solution.root)
        result.setdefault(self.solution.root / cmake_files.ENTRY, cmake_files.solution_entry(self.solution))
        result.setdefault(self.solution.root / cmake_files.TOP_LEVEL, cmake_files.top_level(self))
        for project in self.projects.values():
            result.setdefault(project.root / cmake_files.ENTRY, cmake_files.project_entry(
                project, self.files[project.settings._data.guid], self.projects))
        for other in self.external_solutions():
            result.setdefault(other.root / cmake_files.ENTRY, cmake_files.solution_entry(other))
        for other in self.external_solutions():
            if other.root in visited:
                continue
            # Each linked Solution also builds alone with its own linked Solutions. Refresh its files
            # when its own links resolve here; otherwise leave them for an operation on that Solution.
            try:
                own = plan(other)
            except (OSError, SettingsError):
                visited.add(other.root)
                continue
            own._documents(result, visited)

    def write(self):
        """Write every generated file or none: CppBuild never replaces a file it did not generate,
        and a failed write restores the files already replaced. Callers hold lock()."""
        documents = dict(sorted(self._all_documents.items(), key=lambda item: str(item[0])))
        foreign = [str(path) for path in documents if path.exists() and not cmake_files.generated_file(path)]
        if foreign:
            raise SettingsError("Files exist that were not generated by CppBuild; move them away to let CppBuild "
                                "manage these directories: " + ", ".join(foreign))
        return tuple(Path(path) for path in
                     storage.publish_documents({path: text.encode("utf-8") for path, text in documents.items()}))

    @contextmanager
    def lock(self):
        """The operation locks of this Solution, its Projects and every directory written."""
        roots = {self.solution.root, *(p.root for p in self.projects.values())}
        roots |= {path.parent for path in self._all_documents}
        with storage.operation_lock(*roots):
            yield

    # Settings ------------------------------------------------------------------

    def self_settings(self, project):
        return project._resolved_build_settings(None, self.solution._build_settings.project_types)

    def cache_script(self, toolchain):
        """An initial cache (cmake -C) holding every non-saved value, set explicitly on each configure."""
        from . import output_paths
        settings = self.solution._build_settings
        # CMake's own regeneration stays on, as without CppBuild; clean avoids triggering it.
        values = [("CMAKE_SUPPRESS_REGENERATION", "BOOL", "OFF"),
                  ("BUILD_TESTING", "BOOL", "ON"),
                  ("BUILD_SHARED_LIBS", "BOOL", "OFF"),
                  ("CMAKE_CXX_STANDARD", "STRING", str(settings.cpp_standard)),
                  ("CPPBUILD_GOOGLETEST_URL", "STRING", self.googletest())]
        for project in sorted(self.projects.values(), key=cmake_files.base_name):
            own = self.self_settings(project)
            if project.settings._data.initial_type not in {ProjectType.EXECUTABLE, ProjectType.TEST}:
                forced = settings.project_types.get(project.settings._data.guid) or project._build_settings.project_type
                values.append((cmake_files.type_variable(project), "STRING", cmake_files.KIND[forced] if forced else ""))
            values.append((cmake_files.standard_variable(project), "STRING", str(own.cpp_standard)))
            directory = ""
            if project._build_settings.artifact_directory is not None:
                # The ownership marker keeps the outputs out of source scans and templates.
                directory = output_paths.claim(project, project._build_settings.artifact_directory, toolchain.context).as_posix()
            values.append((cmake_files.output_variable(project), "PATH", directory))
        lines = ["# Written by CppBuild for this build tree: the non-saved build settings of the last configure.",
                 f'set(CMAKE_CONFIGURATION_TYPES "{";".join(cmake_files.CONFIGURATIONS)}" CACHE STRING "" FORCE)']
        for name, kind, value in values:
            lines.append(f'set({name} {cmake_files.arg(value)} CACHE {kind} "" FORCE)')
        return "\n".join(lines) + "\n"

    def googletest(self):
        archives = set()
        for project in self.projects.values():
            if project.settings._data.initial_type == ProjectType.TEST:
                own = self.self_settings(project)
                if own.googletest_archive is not None:
                    source = (project.root / own.googletest_archive).resolve()
                    if not source.is_file():
                        raise SettingsError("googletest_archive must be an existing v1.14.0 ZIP")
                    archives.add(source.as_posix())
        if len(archives) > 1:
            raise SettingsError("TEST Projects of one Solution must use the same googletest_archive")
        return archives.pop() if archives else cmake_files.GOOGLETEST_URL

    # Checks --------------------------------------------------------------------

    def _check_names(self):
        """Target, ALIAS and file names that would collide in the one CMake project or its bin directory."""
        targets, aliases = [], []
        for project in sorted(self.projects.values(), key=lambda p: str(p.root)):
            own_targets, own_aliases = _names(project)
            targets += [(name, project) for name in own_targets]
            aliases += [(name, project) for name in own_aliases]
        _unique(targets, "target")
        _unique(aliases, "ALIAS")
        # A separate directory for IDE-listed DLLs does not make same-named DLLs safe to load.
        shared = {}
        for node in self.nodes:
            if node.settings.project_type == ProjectType.SHARED_LIBRARY:
                previous = shared.setdefault(node.project.name.casefold(), node.project)
                if previous is not node.project:
                    raise SettingsError(f"Two shared libraries would both be named {node.project.name}: "
                                        f"{previous.root} and {node.project.root}")
        # Programs and DLLs share the runtime directory; the linkers' App.pdb and App.ilk would collide.
        for node in self.nodes:
            if node.settings.project_type in {ProjectType.EXECUTABLE, ProjectType.TEST} and node.project.settings._data.guid not in self.listed:
                library = shared.get(node.project.name.casefold())
                if library is not None and library.settings._data.guid not in self.listed:
                    raise SettingsError(f"The program {node.project.name} and a shared library of the same name "
                                        f"({library.root}) would overwrite each other's debug files; rename one of them")

    def check(self, configuration_of):
        """Validation that previously happened while generating, before any tool runs."""
        self._check_names()
        for node in self.nodes:
            kind = node.settings.project_type
            if kind != ProjectType.INTERFACE_LIBRARY and not cmake_files._compiled_sources(node.project, self.files[node.project.settings._data.guid]):
                raise SettingsError(f"No C++ sources found for {node.project.name}")
            if node.project.solution.root == self.solution.root:
                forced = self.solution._build_settings.project_types.get(node.project.settings._data.guid)
                if forced is not None and node.project._build_settings.project_type == ProjectType.INTERFACE_LIBRARY:
                    raise SettingsError(f"project_types selects {forced.value} for {node.project.name}, "
                                        "which also selects INTERFACE_LIBRARY")
            configuration = configuration_of(node)
            for value in node.project.settings._data.dependencies.values():
                if isinstance(value, ImportedLibrary) and value.project_type != ProjectType.INTERFACE_LIBRARY:
                    _check_imported(node.project, value, configuration)


def _names(project):
    """Every CMake target and ALIAS name the Project's generated file can create.

    Library types can be switched without CppBuild (<Solution>_<Project>_TYPE), so
    every type the Project supports counts, not only the ones this operation needs.
    """
    initial = project.settings._data.initial_type
    if initial in {ProjectType.EXECUTABLE, ProjectType.TEST}:
        return [cmake_files.base_name(project)], []
    kinds = [k for k in cmake_files.LIBRARY_ORDER if k in project.settings._data.types]
    return ([cmake_files.target_name(project, k) for k in kinds],
            [cmake_files.alias_name(project)] + [cmake_files.link_name(project, k) for k in kinds])


# Targets that CMake's generators or GoogleTest define in every tree with tests.
RESERVED = {name.casefold() for name in ("ALL_BUILD", "ZERO_CHECK", "RUN_TESTS", "INSTALL", "PACKAGE", "edit_cache",
                                         "rebuild_cache", "list_install_components", "gtest", "gtest_main", "gmock",
                                         "gmock_main")}


def _unique(entries, what):
    seen = {}
    for name, project in entries:
        if name.casefold() in RESERVED:
            raise SettingsError(f"{project.root} maps to the reserved CMake {what} {name}; rename the Solution or Project")
        previous = seen.setdefault(name.casefold(), project)
        if previous is not project:
            raise SettingsError(f"Two Projects map to the CMake {what} {name}: {previous.root} and {project.root}; "
                                "rename one of the Solutions or Projects")


def _check_imported(project, value, configuration):
    from .dependencies import path
    if configuration not in value.locations:
        raise SettingsError("No imported artifact for selected configuration")
    location = path(project, value.locations[configuration])
    if not location.is_file():
        raise SettingsError(f"Imported artifact does not exist: {location}")
    if value.project_type == ProjectType.SHARED_LIBRARY and value.import_libraries:
        if configuration not in value.import_libraries:
            raise SettingsError("No import library for selected configuration")
        implib = path(project, value.import_libraries[configuration])
        if not implib.is_file():
            raise SettingsError(f"Import library does not exist: {implib}")


def plan(solution):
    """Resolve the whole Solution; every operation configures the same CMake project."""
    from .engine import _scan
    solution.settings.reload()
    display = solution.settings._data.solution_folders is not None
    roots, nodes = resolve(solution.projects(), include_external_members=display)
    result = Plan(solution, roots, nodes)
    for node in nodes:
        result.projects.setdefault(node.project.settings._data.guid, node.project)
    for guid, project in result.projects.items():
        result.files[guid] = _scan(project)
    return result

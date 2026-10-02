"""One Solution, configured as one CMake project with its linked Solutions.

A plan resolves the Solution's members and the Projects they need from linked
Solutions (with the same GUID rules as before), writes the CMake files of every
involved Solution, and turns the non-saved build settings into a CMake initial
cache script. Generated files never contain those settings.
"""
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from functools import cached_property
import hashlib

from . import cmake_files, storage
from .graph import resolve
from .models import Dependency, ImportedLibrary, ProjectType, SettingsError

@dataclass
class Plan:
    solution: object
    roots: list
    nodes: list
    projects: dict = field(default_factory=dict)  # GUID -> Project, every Project in the CMake project
    files: dict = field(default_factory=dict)  # GUID -> scanned files

    # Requests ------------------------------------------------------------------

    def _requests(self):
        """(all, cross-Solution) requests by provider GUID, each split into (used, display only)."""
        display = self._display_guids
        requests, external = {}, {}
        for project in self.projects.values():
            index = 1 if project.settings._data.guid in display else 0
            for dependency in project.settings._data.dependencies.values():
                if isinstance(dependency, Dependency) and dependency.project_guid in self.projects:
                    provider = self.projects[dependency.project_guid]
                    requests.setdefault(dependency.project_guid, (set(), set()))[index].add(dependency.project_type)
                    if project.solution.root != provider.solution.root:
                        external.setdefault(dependency.project_guid, (set(), set()))[index].add(dependency.project_type)
        return requests, external

    @staticmethod
    def _kinds(request):
        """Requested link types; those requested only by IDE-listed Projects stay out of the default build."""
        used, shown = request
        return sorted(cmake_files.KIND[k] for k in used) + sorted("DISPLAY_" + cmake_files.KIND[k] for k in shown - used)

    @cached_property
    def _display_guids(self):
        """Projects of linked Solutions listed only for the IDE (solution_folders)."""
        needed = set()

        def visit(node):
            if node.project.settings._data.guid not in needed:
                needed.add(node.project.settings._data.guid)
                for child in node.dependencies:
                    visit(child)
        for node in self.roots:
            visit(node)
        return {guid for guid in self.projects if guid not in needed}

    def external_requests(self):
        requests, _ = self._requests()
        display = self._display_guids
        result = []
        for project in self._external_projects():
            guid = project.settings._data.guid
            kinds = self._kinds(requests[guid]) if guid in requests else []
            if guid in display:
                kinds = ["DISPLAY"] + kinds
            if kinds:
                result.append((project, kinds))
        return result

    def member_requests(self):
        _, external = self._requests()
        return [(p, self._kinds(external[p.settings._data.guid]))
                for p in sorted(self.solution.projects(), key=lambda p: p.name) if p.settings._data.guid in external]

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
        """Generated files of every involved Solution, keyed by path."""
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
        return tuple(path for path, text in sorted(self.documents().items(), key=lambda item: str(item[0]))
                     if cmake_files.write(path, text))

    @contextmanager
    def lock(self):
        with ExitStack() as stack:
            directories = {self.solution.root / ".cppbuild/operations"}
            directories |= {s.root / ".cppbuild/operations" for s in self.external_solutions()}
            directories |= {p.root / ".cppbuild/operations" for p in self.projects.values()}
            for directory in sorted(directories):
                stack.enter_context(storage.write_lock(directory))
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

    def check(self, configuration_of):
        """Validation that previously happened while generating, before any tool runs."""
        names = {}
        for project in self.projects.values():
            base = cmake_files.base_name(project)
            previous = names.setdefault(base.casefold(), project)
            if previous is not project:
                raise SettingsError(f"Two Projects map to the CMake name {base}: {previous.root} and {project.root}; "
                                    "rename one of the Solutions or Projects")
        shared = {}
        for node in self.nodes:
            # A separate directory for IDE-listed DLLs does not make same-named DLLs safe to load.
            if node.settings.project_type == ProjectType.SHARED_LIBRARY:
                previous = shared.setdefault(node.project.name.casefold(), node.project)
                if previous is not node.project:
                    raise SettingsError(f"Two shared libraries would both be named {node.project.name}: "
                                        f"{previous.root} and {node.project.root}")
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

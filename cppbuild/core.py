"""Solution/Project management, separate from CMake execution."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import os
from pathlib import Path
import re
import uuid

from .models import (
    ChangeReport, INHERIT, ProjectBuildSettings, ProjectSettingsData, ProjectType,
    SettingsConflictError, SettingsError, SolutionBuildSettings, SolutionSettingsData,
    TypeSettingsData, Dependency, LinkReport, SolutionFolderSettings,
)
from . import storage
from . import dependencies as dependency_data
from .events import Dispatcher, operation
from . import tooling


def _name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", value):
        raise SettingsError("Name must match [A-Za-z_][A-Za-z0-9_-]*")


def _strings(values):
    if not isinstance(values, list) or any(not isinstance(v, str) or "\x00" in v for v in values):
        raise SettingsError("Expected a list of strings without NUL")


def _build_settings(value, seen=None):
    seen = set() if seen is None else seen
    if id(value) in seen:
        raise SettingsError("Recursive external build settings")
    seen = seen | {id(value)}
    if value.tools is INHERIT:
        if isinstance(value, SolutionBuildSettings):
            raise SettingsError("Solution tools cannot inherit")
    else:
        tooling.validate(value.tools)
    for key, allowed in (("configuration", {"Debug", "Release", "RelWithDebInfo", "MinSizeRel"}),
                         ("architecture", {"x64", "Win32", "ARM64"}),
                         ("cpp_standard", {17, 20, 23})):
        item = getattr(value, key)
        if item is not INHERIT:
            try:
                valid = item in allowed and type(item) in (str, int)
            except TypeError:
                valid = False
            if not valid:
                raise SettingsError(f"Unsupported {key}: {item!r}")
    for key in ("parallel", "test_parallel") if isinstance(value, ProjectBuildSettings) else ("parallel", "run_parallel"):
        if type(getattr(value, key)) is not int or getattr(value, key) < 1:
            raise SettingsError(f"{key} must be positive")
    for key in ("run_wait",) if isinstance(value, ProjectBuildSettings) else ("run_wait", "run_continue_on_failure", "test_continue_on_failure"):
        if type(getattr(value, key)) is not bool:
            raise SettingsError(f"{key} must be boolean")
    if isinstance(value, ProjectBuildSettings):
        if value.project_type is not None and not isinstance(value.project_type, ProjectType):
            raise SettingsError("project_type must be a ProjectType")
        _strings(value.run_arguments)
        if value.googletest_archive is not None and (not isinstance(value.googletest_archive, str) or not value.googletest_archive or "\x00" in value.googletest_archive):
            raise SettingsError("googletest_archive must be a local archive path")
    else:
        if any(getattr(value, k) is INHERIT for k in ("configuration", "architecture", "cpp_standard")):
            raise SettingsError("Solution has no parent to inherit from")
        if value.build_projects is not None:
            _strings(value.build_projects)
        _strings(value.run_projects)
        if value.test_projects is not None:
            _strings(value.test_projects)
        for names in (value.build_projects, value.run_projects, value.test_projects):
            if names is not None and len(set(names)) != len(names):
                raise SettingsError("Duplicate Project selection")
        if not isinstance(value.external_build_settings, dict):
            raise SettingsError("external_build_settings must be a mapping")
        for directory, data in value.external_build_settings.items():
            if not isinstance(directory, str) or not directory or not isinstance(data, SolutionBuildSettings):
                raise SettingsError("Expected external .cppbuild path -> SolutionBuildSettings")
            _build_settings(data, seen)


class _Settings:
    def get(self):
        self.owner._check_active()
        return deepcopy(self._data)

    def _assert_unchanged(self):
        for path, fingerprint in self._revision.items():
            if not path.is_file() or storage.digest(path) != fingerprint:
                raise SettingsConflictError(f"Settings changed externally; reload first: {path}")


class ProjectSettings(_Settings):
    def __init__(self, owner):
        self.owner = owner
        self.path = owner.root / ".cppbuild" / "project.json"
        self._revision = {}

    def _validate(self, values):
        if not isinstance(values, ProjectSettingsData):
            raise SettingsError("Expected ProjectSettingsData")
        if values.name != self.owner.name:
            raise SettingsError("Project renaming is not implemented")
        _name(values.name)
        if not isinstance(values.types, dict) or not values.types:
            raise SettingsError("At least one project type is required")
        for kind, data in values.types.items():
            if not isinstance(kind, ProjectType) or not isinstance(data, TypeSettingsData):
                raise SettingsError("Expected ProjectType -> TypeSettingsData")
            _strings(data.compile_definitions)
            _strings(data.public_definitions)
            _strings(data.include_directories)
            for directory in data.include_directories:
                storage.contained(self.owner.root, directory)
        if not isinstance(values.dependencies, dict):
            raise SettingsError("dependencies must be a mapping")
        for key, dependency in values.dependencies.items():
            if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{32}", key):
                raise SettingsError("Expected dependency id -> Dependency")
            dependency_data.validate(self.owner, dependency)
        _strings(values.project_headers)
        _strings(values.system_headers)
        for header in values.project_headers:
            storage.contained(self.owner.root, header)
        for header in values.system_headers:
            if not header or any(c in header for c in '<>\r\n;"'):
                raise SettingsError("Invalid system header")
        _strings(values.source_directories)
        for path in values.source_directories:
            resolved = storage.contained(self.owner.root, path)
            if resolved.is_relative_to(self.path.parent):
                raise SettingsError("Management files cannot be source directories")

    def _read(self):
        raw = storage.manifest(self.path, "project")
        raw.setdefault("dependencies", {})
        raw.setdefault("project_headers", [])
        raw.setdefault("system_headers", [])
        storage.object_fields(raw, {"name", "source_directories", "types", "dependencies", "project_headers", "system_headers"})
        if not isinstance(raw["types"], dict):
            raise SettingsError("types must be an object")
        types, revisions = {}, {self.path: storage.digest(self.path)}
        for key, relative in raw["types"].items():
            try:
                kind = ProjectType(key)
            except ValueError as exc:
                raise SettingsError(f"Unsupported project type: {key}") from exc
            path = storage.contained(self.path.parent / "types", relative)
            data = storage.manifest(path, key)
            data.setdefault("public_definitions", [])
            data.setdefault("include_directories", ["include"])
            storage.object_fields(data, {"compile_definitions", "public_definitions", "include_directories"})
            types[kind] = TypeSettingsData(**data)
            revisions[path] = storage.digest(path)
        dependencies = {}
        if not isinstance(raw["dependencies"], dict):
            raise SettingsError("dependencies must be a mapping")
        for key, data in raw["dependencies"].items():
            dependencies[key] = dependency_data.decode(data)
        values = ProjectSettingsData(raw["name"], types, raw["source_directories"], dependencies, raw["project_headers"], raw["system_headers"])
        self._validate(values)
        return values, revisions

    def reload(self):
        self.owner._check_active()
        values, revisions = self._read()
        if hasattr(self, "_data") and self._data != values:
            from .information import invalidate
            invalidate(self.owner)
        self._data, self._revision = values, revisions

    def save(self, values):
        self.owner._check_active()
        values = deepcopy(values)
        self._validate(values)
        with storage.write_lock(self.path.parent):
            self._assert_unchanged()
            return self._publish(values)

    def _documents(self, values):
        documents, refs = {}, {}
        for kind, data in values.types.items():
            content = storage.encoded(storage.envelope(kind.value, asdict(data)))
            filename = f"{kind.value}-{hashlib.sha256(content).hexdigest()}.json"
            path = self.path.parent / "types" / filename
            refs[kind.value] = filename
            documents[path] = content
        payload = {"name": values.name, "source_directories": values.source_directories, "types": refs,
                   "dependencies": {key: dependency_data.encode(value) for key, value in values.dependencies.items()},
                   "project_headers": values.project_headers, "system_headers": values.system_headers}
        documents[self.path] = storage.encoded(storage.envelope("project", payload))
        return documents

    def _publish(self, values):
        documents = self._documents(values)
        for path, content in documents.items():
            storage.atomic_write(path, content)
        if hasattr(self, "_data") and self._data != values:
            from .information import invalidate
            invalidate(self.owner)
        self._data = values
        self._revision = {p: storage.digest(p) for p in documents}
        return ChangeReport(tuple(str(p) for p in [self.path, *(p for p in documents if p != self.path)]))

    def link_project(self, other_project, link_type):
        self.owner._check_active()
        other_project._check_active()
        if other_project.solution is not self.owner.solution:
            raise SettingsError("Use link_solution for external Solutions")
        if not isinstance(link_type, ProjectType) or link_type not in other_project.settings.get().types:
            raise SettingsError("Dependency does not support the requested type")
        values = self.get()
        dependency = Dependency(other_project.name, link_type)
        if dependency in values.dependencies.values():
            raise SettingsError("Dependency is already registered")
        dependency_id = uuid.uuid4().hex
        values.dependencies[dependency_id] = dependency
        self.save(values)
        return LinkReport(dependency_id, other_project.name, link_type)

    def unlink(self, dependency_id):
        values = self.get()
        del values.dependencies[dependency_id]
        return self.save(values)

    def _link(self, value):
        values = self.get()
        dependency_data.validate(self.owner, value)
        if value in values.dependencies.values():
            raise SettingsError("Dependency is already registered")
        key = uuid.uuid4().hex
        values.dependencies[key] = deepcopy(value)
        self.save(values)
        return LinkReport(key, getattr(value, "target", getattr(value, "project", "imported")), getattr(value, "project_type", None))

    def link_solution(self, config_directory, link_type):
        config = dependency_data.path(self.owner, str(config_directory))
        other = Solution.open(config)
        name = other.settings.get().main_project
        if name is None or link_type not in other.get_project(name).settings.get().types:
            raise SettingsError("External Solution needs a main Project of the requested type")
        return self._link(Dependency(name, link_type, str(config)))

    def link_package(self, package):
        from .models import CMakePackage
        if not isinstance(package, CMakePackage):
            raise SettingsError("Expected CMakePackage")
        return self._link(package)

    def link_cmake_source(self, source):
        from .models import CMakeSource
        if not isinstance(source, CMakeSource):
            raise SettingsError("Expected CMakeSource")
        return self._link(source)

    def link_imported_library(self, library):
        from .models import ImportedLibrary
        if not isinstance(library, ImportedLibrary):
            raise SettingsError("Expected ImportedLibrary")
        return self._link(library)

    def set_pch(self, *, project_headers=(), system_headers=()):
        values = self.get()
        values.project_headers, values.system_headers = list(project_headers), list(system_headers)
        return self.save(values)

    def clear_pch(self):
        return self.set_pch()


class SolutionSettings(_Settings):
    def __init__(self, owner):
        self.owner = owner
        # Keep the existing entry name until the explicit solution.json proposal is resolved.
        self.path = owner.root / ".cppbuild" / "project.json"
        self._revision = {}

    def _validate(self, values):
        if not isinstance(values, SolutionSettingsData):
            raise SettingsError("Expected SolutionSettingsData")
        _name(values.name)
        if not isinstance(values.projects, dict):
            raise SettingsError("projects must be a mapping")
        from .solution_folders import validate
        validate(values.solution_folders, values.projects)
        roots = set()
        for name, relative in values.projects.items():
            _name(name)
            root = storage.contained(self.owner.root, relative)
            if root == self.owner.root or root.is_relative_to(self.path.parent) or root in roots:
                raise SettingsError("Project roots must be distinct subdirectories")
            if any(root.is_relative_to(r) or r.is_relative_to(root) for r in roots):
                raise SettingsError("Nested project roots are not supported yet")
            roots.add(root)
        if values.main_project is not None and (not isinstance(values.main_project, str) or values.main_project not in values.projects):
            raise SettingsError("main_project must refer to a member")
        if not isinstance(values.file_templates, dict):
            raise SettingsError("file_templates must be a mapping")
        from .templates import material_path
        for name, filename in values.file_templates.items():
            _name(name)
            if not isinstance(filename, str):
                raise SettingsError("Expected a material path")
            material_path(self.owner, filename)

    def _read(self):
        raw = storage.manifest(self.path, "solution")
        raw.setdefault("file_templates", {})
        raw.setdefault("solution_folders", None)
        storage.object_fields(raw, {"name", "projects", "main_project", "file_templates", "solution_folders"})
        if raw["solution_folders"] is not None:
            folder = raw["solution_folders"]
            if not isinstance(folder, dict):
                raise SettingsError("Expected solution folder object")
            storage.object_fields(folder, {"projects", "linked_projects", "project_folders"})
            raw["solution_folders"] = SolutionFolderSettings(**folder)
        values = SolutionSettingsData(**raw)
        self._validate(values)
        return values

    def reload(self):
        values = self._read()
        # Read/validate every child before replacing any live state.
        projects, loaded = {}, {}
        for name, relative in values.projects.items():
            root = storage.contained(self.owner.root, relative)
            previous = self.owner._projects.get(name)
            project = previous if previous and previous.root == root else Project(self.owner, root, name)
            loaded[name] = project.settings._read()
            projects[name] = project
        for name, project in projects.items():
            if hasattr(project.settings, "_data") and project.settings._data != loaded[name][0]:
                from .information import invalidate
                invalidate(project)
            project.settings._data, project.settings._revision = loaded[name]
        self.owner._projects = projects
        self._data = values
        self._revision = {self.path: storage.digest(self.path)}

    def save(self, values):
        values = deepcopy(values)
        self._validate(values)
        if values.projects != self._data.projects:
            raise SettingsError("Use add_project/remove_project/move_project to change membership")
        with storage.write_lock(self.path.parent):
            self._assert_unchanged()
            return self._publish(values)

    def _publish(self, values):
        storage.atomic_write(self.path, storage.encoded(storage.envelope("solution", asdict(values))))
        self._data = values
        self._revision = {self.path: storage.digest(self.path)}
        return ChangeReport((str(self.path),))

    def set_file_template(self, name, template_file):
        from .templates import material_path
        _name(name)
        path = material_path(self.owner, template_file)
        if not path.is_file():
            raise FileNotFoundError(path)
        values = self.get()
        values.file_templates[name] = path.relative_to(self.owner.root).as_posix()
        return self.save(values)

    def file_templates(self):
        return self.get().file_templates

    def remove_file_template(self, name):
        values = self.get()
        del values.file_templates[name]
        return self.save(values)


class Project:
    def __init__(self, solution, root, name):
        self.solution, self.root, self.name = solution, Path(root).resolve(), name
        self.settings = ProjectSettings(self)
        self._build_settings = ProjectBuildSettings()
        self._last_update = None
        self._last_operation = None
        self._file_revision = 0
        self._known_files, self._known_artifacts = (), ()
        self._generation_signature = self._build_signature = None
        self._generation_state = self._build_state = "unknown"

    def _check_active(self):
        if self.solution._projects.get(self.name) is not self:
            raise SettingsError("Project is no longer registered in this Solution")

    def set_build_settings(self, values):
        self._check_active()
        if not isinstance(values, ProjectBuildSettings):
            raise SettingsError("Expected ProjectBuildSettings")
        _build_settings(values)
        self._build_settings = deepcopy(values)

    def _resolved_build_settings(self, requested_type=None):
        self._check_active()
        values = deepcopy(self._build_settings)
        if requested_type is not None:
            values.project_type = requested_type
        for key in ("configuration", "architecture", "cpp_standard", "tools"):
            if getattr(values, key) is INHERIT:
                setattr(values, key, deepcopy(getattr(self.solution._build_settings, key)))
        if values.project_type is None:
            if len(self.settings._data.types) != 1:
                raise SettingsError("Select project_type explicitly for a multi-type Project")
            values.project_type = next(iter(self.settings._data.types))
        if values.project_type not in self.settings._data.types:
            raise SettingsError("Selected type is not supported by the Project")
        return values

    @operation
    def update(self):
        from .engine import update
        return update(self)

    @operation
    def build(self):
        from .engine import operate
        return operate(self, "build")

    @operation
    def clean(self):
        from .engine import operate
        return operate(self, "clean")

    @operation
    def rebuild(self):
        from .engine import operate
        return operate(self, "rebuild")

    @operation
    def run(self):
        from .engine import operate
        return operate(self, "run")

    @operation
    def test(self):
        from .testing import project_test
        return project_test(self)

    def add_file(self, destination, *, content=None, template_name=None, replacements=None, auto_update=True):
        from .engine import file_path, file_report
        self.solution._events.check_file_change()
        if template_name is not None:
            if content is not None:
                raise SettingsError("content and template_name are mutually exclusive")
            from .templates import expand
            data = expand(self, template_name, replacements)
        else:
            if not isinstance(content, str) or replacements is not None:
                raise SettingsError("Pass text content; replacements require a template")
            data = content.encode("utf-8")
        path = file_path(self, destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
        return file_report(self, [path], auto_update)

    def remove_file(self, path, *, auto_update=True):
        from .engine import file_path, file_report
        self.solution._events.check_file_change()
        path = file_path(self, path)
        if not path.is_file():
            raise FileNotFoundError(path)
        path.unlink()
        return file_report(self, [path], auto_update)

    def move_file(self, source, destination, *, auto_update=True):
        from .engine import file_path, file_report
        self.solution._events.check_file_change()
        source, destination = file_path(self, source), file_path(self, destination)
        if not source.is_file():
            raise FileNotFoundError(source)
        if destination.exists():
            raise FileExistsError(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Windows rename refuses a destination created concurrently as well.
        if os.name != "nt":
            raise NotImplementedError("Move currently targets Windows")
        source.rename(destination)
        return file_report(self, [source, destination], auto_update)

    def check_environment(self):
        from .environment import check_owner
        return check_owner(self)


class Solution:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self._projects = {}
        self.settings = SolutionSettings(self)
        self._build_settings = SolutionBuildSettings()
        self._last_update = None
        self._last_operation = None
        self._events = Dispatcher(self)

    @operation
    def update(self):
        from .solution_engine import update
        return update(self)

    @operation
    def build(self):
        from .solution_engine import operate
        return operate(self, "build")

    @operation
    def clean(self):
        from .solution_engine import operate
        return operate(self, "clean")

    @operation
    def rebuild(self):
        from .solution_engine import operate
        return operate(self, "rebuild")

    @operation
    def run(self):
        from .solution_engine import operate
        return operate(self, "run")

    @operation
    def test(self):
        from .testing import solution_test
        return solution_test(self)

    def _check_active(self):
        pass

    @classmethod
    def create(cls, destination_directory, solution_name, *, template=None):
        if template is not None:
            from .templates import instantiate
            return instantiate(template, destination_directory, solution_name)
        _name(solution_name)
        instance = cls(destination_directory)
        with storage.write_lock(instance.settings.path.parent):
            if instance.settings.path.exists():
                raise FileExistsError(instance.settings.path)
            instance.settings._publish(SolutionSettingsData(solution_name))
        return instance

    @classmethod
    def open(cls, config_directory):
        config = Path(config_directory).resolve()
        if config.name != ".cppbuild":
            raise SettingsError("Pass the Solution .cppbuild directory")
        instance = cls(config.parent)
        instance.settings.reload()
        return instance

    def get_project(self, name):
        return self._projects[name]

    def projects(self):
        return list(self._projects.values())

    def create_file_template(self, name, source_file, *, replacements=None):
        from .templates import create_file
        return create_file(self, name, source_file, replacements)

    def on(self, event, callback):
        return self._events.on(event, callback)

    def off(self, registration_id):
        return self._events.off(registration_id)

    def info(self):
        from .information import snapshot
        return snapshot(self)

    def check_environment(self):
        from .environment import check_owner
        return check_owner(self)

    def add_project(self, directory, name, project_type, settings=None):
        _name(name)
        if not isinstance(project_type, ProjectType):
            raise SettingsError("Expected ProjectType")
        root = storage.contained(self.root, str(directory))
        values = self.settings.get()
        if name in values.projects:
            raise SettingsError(f"Duplicate Project: {name}")
        values.projects[name] = root.relative_to(self.root).as_posix()
        if len(values.projects) == 1:
            values.main_project = name
        self.settings._validate(values)
        project = Project(self, root, name)
        data = deepcopy(settings) if settings is not None else ProjectSettingsData(name, {project_type: TypeSettingsData()})
        project.settings._validate(data)
        if project_type not in data.types:
            raise SettingsError("The requested type must exist in settings.types")
        with storage.write_lock(self.settings.path.parent), storage.write_lock(project.settings.path.parent):
            self.settings._assert_unchanged()
            if project.settings.path.exists():
                raise FileExistsError(project.settings.path)
            project.settings._publish(data)
            try:
                self.settings._publish(values)
            except BaseException:
                # Only remove the exact unpublished manifest created by this call.
                project.settings.path.unlink()
                raise
            self._projects[name] = project
        return project

    def remove_project(self, name):
        values = self.settings.get()
        if name == values.main_project:
            raise SettingsError("Change main_project before removing it")
        for project in self.projects():
            project.settings.reload()
            if any(isinstance(d, Dependency) and d.solution_directory is None and d.project == name for d in project.settings._data.dependencies.values()):
                raise SettingsError("Unlink references before removing this Project")
        del values.projects[name]
        if values.solution_folders is not None:
            values.solution_folders.project_folders.pop(name, None)
        with storage.write_lock(self.settings.path.parent):
            self.settings._assert_unchanged()
            report = self.settings._publish(values)
            del self._projects[name]
        return report

    def move_project(self, name, destination, *, auto_update=True):
        from .relocation import move_project
        return move_project(self, name, destination, auto_update=auto_update)

    def set_build_settings(self, values):
        if not isinstance(values, SolutionBuildSettings):
            raise SettingsError("Expected SolutionBuildSettings")
        _build_settings(values)
        for name in [*(values.build_projects or []), *values.run_projects, *(values.test_projects or [])]:
            if name not in self._projects:
                raise SettingsError(f"Unknown Project: {name}")
        self._build_settings = deepcopy(values)

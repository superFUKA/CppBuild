"""Solution/Project management, separate from CMake execution."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
from pathlib import Path
import re

from .models import (
    ChangeReport, INHERIT, ProjectBuildSettings, ProjectSettingsData, ProjectType,
    SettingsConflictError, SettingsError, SolutionBuildSettings, SolutionSettingsData,
    TypeSettingsData,
)
from . import storage


def _name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", value):
        raise SettingsError("Name must match [A-Za-z_][A-Za-z0-9_-]*")


def _strings(values):
    if not isinstance(values, list) or any(not isinstance(v, str) or "\x00" in v for v in values):
        raise SettingsError("Expected a list of strings without NUL")


def _build_settings(value):
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
    if isinstance(value, ProjectBuildSettings):
        if value.project_type is not None and not isinstance(value.project_type, ProjectType):
            raise SettingsError("project_type must be a ProjectType")
        if type(value.parallel) is not int or value.parallel < 1:
            raise SettingsError("parallel must be positive")
        _strings(value.run_arguments)
    else:
        if any(getattr(value, k) is INHERIT for k in ("configuration", "architecture", "cpp_standard")):
            raise SettingsError("Solution has no parent to inherit from")
        _strings(value.build_projects)
        _strings(value.run_projects)


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
        _strings(values.source_directories)
        for path in values.source_directories:
            resolved = storage.contained(self.owner.root, path)
            if resolved.is_relative_to(self.path.parent):
                raise SettingsError("Management files cannot be source directories")

    def _read(self):
        raw = storage.manifest(self.path, "project")
        storage.object_fields(raw, {"name", "source_directories", "types"})
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
            storage.object_fields(data, {"compile_definitions"})
            types[kind] = TypeSettingsData(**data)
            revisions[path] = storage.digest(path)
        values = ProjectSettingsData(raw["name"], types, raw["source_directories"])
        self._validate(values)
        return values, revisions

    def reload(self):
        self.owner._check_active()
        values, revisions = self._read()
        self._data, self._revision = values, revisions

    def save(self, values):
        self.owner._check_active()
        values = deepcopy(values)
        self._validate(values)
        with storage.write_lock(self.path.parent):
            self._assert_unchanged()
            return self._publish(values)

    def _publish(self, values):
        paths, refs = [], {}
        for kind, data in values.types.items():
            content = storage.encoded(storage.envelope(kind.value, asdict(data)))
            filename = f"{kind.value}-{hashlib.sha256(content).hexdigest()}.json"
            path = self.path.parent / "types" / filename
            storage.atomic_write(path, content)
            refs[kind.value] = filename
            paths.append(path)
        payload = {"name": values.name, "source_directories": values.source_directories, "types": refs}
        storage.atomic_write(self.path, storage.encoded(storage.envelope("project", payload)))
        self._data = values
        self._revision = {p: storage.digest(p) for p in [self.path, *paths]}
        return ChangeReport(tuple(str(p) for p in [self.path, *paths]))


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

    def _read(self):
        raw = storage.manifest(self.path, "solution")
        storage.object_fields(raw, {"name", "projects", "main_project"})
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
            project.settings._data, project.settings._revision = loaded[name]
        self.owner._projects = projects
        self._data = values
        self._revision = {self.path: storage.digest(self.path)}

    def save(self, values):
        values = deepcopy(values)
        self._validate(values)
        if values.projects != self._data.projects:
            raise SettingsError("Use add_project/remove_project to change membership")
        with storage.write_lock(self.path.parent):
            self._assert_unchanged()
            return self._publish(values)

    def _publish(self, values):
        storage.atomic_write(self.path, storage.encoded(storage.envelope("solution", asdict(values))))
        self._data = values
        self._revision = {self.path: storage.digest(self.path)}
        return ChangeReport((str(self.path),))


class Project:
    def __init__(self, solution, root, name):
        self.solution, self.root, self.name = solution, Path(root).resolve(), name
        self.settings = ProjectSettings(self)
        self._build_settings = ProjectBuildSettings()

    def _check_active(self):
        if self.solution._projects.get(self.name) is not self:
            raise SettingsError("Project is no longer registered in this Solution")

    def set_build_settings(self, values):
        self._check_active()
        if not isinstance(values, ProjectBuildSettings):
            raise SettingsError("Expected ProjectBuildSettings")
        _build_settings(values)
        self._build_settings = deepcopy(values)

    def _resolved_build_settings(self):
        self._check_active()
        values = deepcopy(self._build_settings)
        for key in ("configuration", "architecture", "cpp_standard"):
            if getattr(values, key) is INHERIT:
                setattr(values, key, getattr(self.solution._build_settings, key))
        if values.project_type is None:
            if len(self.settings._data.types) != 1:
                raise SettingsError("Select project_type explicitly for a multi-type Project")
            values.project_type = next(iter(self.settings._data.types))
        if values.project_type not in self.settings._data.types:
            raise SettingsError("Selected type is not supported by the Project")
        return values


class Solution:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self._projects = {}
        self.settings = SolutionSettings(self)
        self._build_settings = SolutionBuildSettings()

    def _check_active(self):
        pass

    @classmethod
    def create(cls, destination_directory, solution_name, *, template=None):
        if template is not None:
            raise NotImplementedError("Solution templates are scheduled for M6")
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

    def add_project(self, directory, name, project_type, settings=None):
        _name(name)
        if not isinstance(project_type, ProjectType):
            raise SettingsError("Expected ProjectType")
        root = storage.contained(self.root, str(directory))
        values = self.settings.get()
        if name in values.projects:
            raise SettingsError(f"Duplicate Project: {name}")
        values.projects[name] = root.relative_to(self.root).as_posix()
        if not values.projects or len(values.projects) == 1:
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
        del values.projects[name]
        with storage.write_lock(self.settings.path.parent):
            self.settings._assert_unchanged()
            report = self.settings._publish(values)
            del self._projects[name]
        return report

    def set_build_settings(self, values):
        if not isinstance(values, SolutionBuildSettings):
            raise SettingsError("Expected SolutionBuildSettings")
        _build_settings(values)
        for name in [*values.build_projects, *values.run_projects]:
            if name not in self._projects:
                raise SettingsError(f"Unknown Project: {name}")
        self._build_settings = deepcopy(values)

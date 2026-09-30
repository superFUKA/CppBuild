"""Portable management snapshots and shared single-file materials."""
from copy import deepcopy
import os
from pathlib import Path
import re
import shutil
import tempfile

from . import storage
from .models import CMakePackage, CMakeSource, Dependency, ImportedLibrary, SettingsError


EXCLUDED = {".cppbuild", ".git", "build", "dist", ".test-work", "__pycache__", ".venv", ".cache"}
TOKEN = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")


def _replacements(values):
    if not isinstance(values, dict) or any(not isinstance(k, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k)
                                           or not isinstance(v, str) for k, v in values.items()):
        raise SettingsError("replacements must map placeholder names to strings")


def material_path(solution, value):
    path = storage.contained(solution.root, str(value))
    base = solution.root / ".cppbuild/templates"
    if not path.is_relative_to(base) or path == base:
        raise SettingsError("Registered materials must be under .cppbuild/templates")
    return path


def create_file(solution, name, source_file, replacements=None):
    from .core import _name
    _name(name)
    source = (solution.root / source_file).resolve()
    data = source.read_bytes()
    if replacements is not None:
        _replacements(replacements)
        if any(not v for v in replacements.values()) or len(set(replacements.values())) != len(replacements):
            raise SettingsError("Material replacement values must be nonempty and unique")
        text = data.decode("utf-8")
        inverse = {v: "{{" + k + "}}" for k, v in replacements.items()}
        if inverse:
            pattern = re.compile("|".join(re.escape(v) for v in sorted(inverse, key=len, reverse=True)))
            text = pattern.sub(lambda m: inverse[m.group()], text)
        data = text.encode("utf-8")
    values = solution.settings.get()
    if name in values.file_templates:
        raise SettingsError("File template is already registered")
    destination = material_path(solution, ".cppbuild/templates/" + name + source.suffix)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with storage.write_lock(solution.settings.path.parent):
        solution.settings._assert_unchanged()
        with destination.open("xb") as stream:
            stream.write(data)
        values.file_templates[name] = destination.relative_to(solution.root).as_posix()
        try:
            return solution.settings._publish(values)
        except BaseException:
            destination.unlink()
            raise


def expand(project, name, replacements):
    values = project.solution.settings.get()
    if name not in values.file_templates:
        raise SettingsError(f"Unknown file template: {name}")
    data = material_path(project.solution, values.file_templates[name]).read_bytes()
    if replacements is None:
        return data
    _replacements(replacements)
    text = data.decode("utf-8")
    required = set(TOKEN.findall(text))
    if required != set(replacements):
        raise SettingsError("Replacement keys must match the material placeholders")
    return TOKEN.sub(lambda m: replacements[m.group(1)], text).encode("utf-8")


def _copy_tree(source, destination, *, exclude=True):
    if source.is_symlink() or (hasattr(source, "is_junction") and source.is_junction()):
        raise SettingsError("Template roots cannot be filesystem links")
    def inaccessible(error):
        raise error
    for current, directories, files in os.walk(source, followlinks=False, onerror=inaccessible):
        relative = Path(current).relative_to(source)
        directories[:] = [d for d in directories if not exclude or d not in EXCLUDED]
        for name in [*directories, *files]:
            path = Path(current) / name
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise SettingsError(f"Templates cannot contain filesystem links: {path}")
        (destination / relative).mkdir(parents=True, exist_ok=True)
        for name in files:
            shutil.copyfile(Path(current) / name, destination / relative / name)


def _portable(project, data):
    data = deepcopy(data)
    def internal(value):
        return (project.root / value).resolve().relative_to(project.root).as_posix() or "."
    def dependency_path(value):
        path = (project.root / value).resolve()
        if path.is_relative_to(project.solution.root):
            if any(part in EXCLUDED for part in path.relative_to(project.solution.root).parts):
                # A linked external Solution may intentionally refer to its .cppbuild.
                if path.name != ".cppbuild" or path.parent.is_relative_to(project.root):
                    raise SettingsError("Template dependency points at excluded generated/management data")
            return os.path.relpath(path, project.root)
        return str(path)
    data.source_directories = [internal(v) for v in data.source_directories]
    data.project_headers = [internal(v) for v in data.project_headers]
    for kind in data.types.values():
        kind.include_directories = [internal(v) for v in kind.include_directories]
    for value in data.dependencies.values():
        if isinstance(value, (CMakeSource, CMakePackage)):
            value.directory = dependency_path(value.directory)
        elif isinstance(value, ImportedLibrary):
            value.locations = {k: dependency_path(v) for k, v in value.locations.items()}
            value.import_libraries = {k: dependency_path(v) for k, v in value.import_libraries.items()}
            value.include_directories = [dependency_path(v) for v in value.include_directories]
    return data


def _snapshot(solution, destination, name, *, template):
    from .core import Solution, _name
    _name(name)
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError(destination)
    if destination.is_relative_to(solution.root) or solution.root.is_relative_to(destination):
        raise SettingsError("Template source and destination must be disjoint")
    # Always use saved management data; never transplant ephemeral settings.
    source = Solution.open(solution.root / ".cppbuild")
    values = source.settings.get()
    project_data = [(p, _portable(p, p.settings.get())) for p in source.projects()]
    destination.parent.mkdir(parents=True, exist_ok=True)
    # This private temporary directory is the only recursive cleanup target.
    with tempfile.TemporaryDirectory(prefix=".cppbuild-template-", dir=destination.parent) as temporary:
        stage = Path(temporary) / "snapshot"
        _copy_tree(source.root, stage)
        clone = Solution.create(stage, name)
        cloned = clone.settings.get()
        cloned.references = deepcopy(values.references)
        for reference in cloned.references.values():
            reference.solution_directory = str((source.root / reference.solution_directory).resolve())
        clone.settings._publish(cloned)
        identities = {}
        for project, data in project_data:
            created = clone.add_project(values.projects[project.name], project.name, data.initial_type, data)
            identities[data.guid] = created.settings.get().guid
        for project in clone.projects():
            data = project.settings.get()
            for dependency in data.dependencies.values():
                if isinstance(dependency, Dependency) and dependency.project_guid in identities:
                    dependency.project_guid = identities[dependency.project_guid]
            project.settings.save(data)
        materials = source.root / ".cppbuild/templates"
        if materials.exists():
            _copy_tree(materials, stage / ".cppbuild/templates", exclude=False)
        cloned = clone.settings.get()
        cloned.main_project = values.main_project
        cloned.solution_folders = values.solution_folders
        cloned.file_templates = {key: material_path(source, value).relative_to(source.root).as_posix()
                                 for key, value in values.file_templates.items()}
        clone.settings.save(cloned)
        if template:
            marker = stage / ".cppbuild/template.json"
            storage.atomic_write(marker, storage.document(marker, "solution_template", {}))
        # Serialize external paths for the final location, not the temporary stage.
        from .core import Project
        from .paths import project_values, relative
        final_owner = Solution(destination)
        final_values = clone.settings.get()
        for reference in final_values.references.values():
            reference.solution_directory = relative(destination, (stage / reference.solution_directory).resolve())
        for project in clone.projects():
            final_root = destination / project.root.relative_to(stage)
            data = project.settings.get()
            def final_path(value):
                absolute = (project.root / value).resolve()
                if absolute.is_relative_to(stage):
                    absolute = destination / absolute.relative_to(stage)
                return relative(final_root, absolute)
            project_values(data, final_path)
            shadow = Project(final_owner, final_root, project.name)
            for path, content in shadow.settings._documents(data).items():
                storage.atomic_write(stage / path.relative_to(destination), content)
        storage.atomic_write(clone.settings.path, final_owner.settings._document(final_values))
        storage.rename_no_replace(stage, destination)
    return destination


class TemplateTools:
    @staticmethod
    def create_solution_template(solution, destination_directory):
        return _snapshot(solution, destination_directory, solution.settings.get().name, template=True)


def instantiate(template, destination, name):
    from .core import Solution
    root = Path(template).resolve()
    storage.manifest(root / ".cppbuild/template.json", "solution_template")
    source = Solution.open(root / ".cppbuild")
    result = _snapshot(source, destination, name, template=False)
    return Solution.open(result / ".cppbuild")

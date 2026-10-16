"""Portable management snapshots and shared single-file materials."""
from copy import deepcopy
import os
from pathlib import Path
import re
import shutil
import tempfile

from . import storage, output_paths, cmake_files, logs, paths
from .models import AlreadyRegisteredError, CMakePackage, CMakeSource, Dependency, ImportedLibrary, NotRegisteredError, SettingsError


EXCLUDED = {".cppbuild", ".git", "build", "dist", ".test-work", "__pycache__", ".venv", ".cache"}
TOKEN = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")


def _replacements(values):
    if not isinstance(values, dict) or any(not isinstance(k, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k)
                                           or not isinstance(v, str) for k, v in values.items()):
        raise SettingsError("replacements must map placeholder names to strings")


def material_path(solution, name):
    """The file of a registered material: its name is relative to .cppbuild/templates."""
    base = solution.config_directory / "templates"
    path = storage.contained(base, str(name))
    if path == base:
        raise SettingsError("Registered materials must be files below .cppbuild/templates")
    return path


def material_name(solution, value):
    """The registered name of a file below .cppbuild/templates (value: relative to the base directory, or absolute)."""
    base = solution.config_directory / "templates"
    path = paths.resolve(solution.root, str(value))
    if not path.is_relative_to(base) or path == base:
        raise SettingsError("Registered materials must be files below .cppbuild/templates")
    if not path.is_file():
        raise FileNotFoundError(path)
    return path.relative_to(base).as_posix()


def create_file(solution, name, source_file, replacements=None):
    from .core import _name
    _name(name)
    source = paths.resolve(solution.root, str(source_file))
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
        raise AlreadyRegisteredError("File template is already registered", kind="file_template", name=name)
    filename = name + source.suffix
    destination = material_path(solution, filename)
    recorded = storage.before_write(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with storage.write_lock(solution.settings.path.parent):
        solution.settings._assert_unchanged()
        with destination.open("xb") as stream:
            stream.write(data)
        storage.after_write(recorded, data)
        values.file_templates[name] = filename
        try:
            return solution.settings._publish(values)
        except BaseException:
            destination.unlink()
            raise


def expand(project, name, replacements):
    values = project.solution.settings.get()
    if name not in values.file_templates:
        raise NotRegisteredError(f"Unknown file template: {name}", kind="file_template", name=name)
    data = material_path(project.solution, values.file_templates[name]).read_bytes()
    if replacements is None:
        return data
    _replacements(replacements)
    text = data.decode("utf-8")
    required = set(TOKEN.findall(text))
    if required != set(replacements):
        raise SettingsError("Replacement keys must match the material placeholders")
    return TOKEN.sub(lambda m: replacements[m.group(1)], text).encode("utf-8")


def _copy_tree(source, destination, *, exclude=True, skip=()):
    if source.is_symlink() or (hasattr(source, "is_junction") and source.is_junction()):
        raise SettingsError("Template roots cannot be filesystem links")
    def inaccessible(error):
        raise error
    for current, directories, files in os.walk(source, followlinks=False, onerror=inaccessible):
        relative = Path(current).relative_to(source)
        directories[:] = [d for d in directories if (not exclude or d not in EXCLUDED)
                          and (not exclude or not output_paths.generated_directory(Path(current) / d))
                          and (Path(current) / d).resolve() not in skip]
        for name in [*directories, *files]:
            path = Path(current) / name
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise SettingsError(f"Templates cannot contain filesystem links: {path}")
        (destination / relative).mkdir(parents=True, exist_ok=True)
        for name in files:
            # Generated CMake files depend on names and GUIDs; the new Solution regenerates them.
            if exclude and cmake_files.generated_file(Path(current) / name):
                continue
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
    """Copy the base directory tree to destination with a new Solution in it; returns its .cppbuild.
    .cppbuild keeps its place in the tree, so it and every Project must be in the base directory."""
    from .core import Solution, _name
    _name(name)
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError(destination)
    # Always use saved management data; never transplant ephemeral settings.
    source = Solution.open(solution.config_directory)
    if paths.overlap(destination, source.root):
        raise SettingsError("Template source and destination must be disjoint")
    outside = [p.name for p in source.projects() if not paths.inside(p.root, source.root)]
    if not paths.inside(source.config_directory, source.root) or outside:
        raise SettingsError("A template copies the base directory; .cppbuild and every Project must be inside it"
                            + (f" (outside: {', '.join(outside)})" if outside else ""))
    config = source.config_directory.relative_to(source.root)
    values = source.settings.get()
    project_data = [(p, _portable(p, p.settings.get())) for p in source.projects()]
    destination.parent.mkdir(parents=True, exist_ok=True)
    # This private temporary directory is the only recursive cleanup target.
    with tempfile.TemporaryDirectory(prefix=".cppbuild-template-", dir=destination.parent) as temporary:
        stage = Path(temporary) / "snapshot"
        # Dependency directories hold external clones, restored by their owner, not template content.
        dependency_roots = [paths.resolve(source.root, d) for d in values.dependency_directories]
        _copy_tree(source.root, stage, skip=set(dependency_roots))
        clone = Solution.create(stage / config.parent, name, base_directory=paths.relative(stage / config.parent, stage))
        cloned = clone.settings.get()
        cloned.references = deepcopy(values.references)
        for reference in cloned.references.values():
            location = (source.root / reference.solution_directory).resolve()
            if any(paths.inside(location, root) for root in dependency_roots) and paths.inside(location, source.root):
                # Clones in the source's dependency directories are expected in the new Solution's own.
                location = destination / location.relative_to(source.root)
            reference.solution_directory = str(location)
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
        materials = source.config_directory / "templates"
        if materials.exists():
            _copy_tree(materials, clone.config_directory / "templates", exclude=False)
        cloned = clone.settings.get()
        cloned.main_project = values.main_project
        cloned.solution_folders = values.solution_folders
        # The layout setting is copied; cloned dependencies under it are not (they are external Solutions).
        cloned.dependency_directories = list(values.dependency_directories)
        cloned.msvc_runtime = values.msvc_runtime
        # Git records travel with the template; the new Solution clones them on its first operation.
        cloned.git_sources = deepcopy(values.git_sources)
        cloned.file_templates = dict(values.file_templates)
        clone.settings.save(cloned)
        if template:
            marker = clone.config_directory / "template.json"
            storage.atomic_write(marker, storage.document(marker, "solution_template", {}, root=stage))
        # Serialize external paths for the final location, not the temporary stage.
        from .core import Project
        from .paths import project_values, relative
        final_owner = Solution(destination / config, destination)
        final_values = clone.settings.get()
        for reference in final_values.references.values():
            absolute = (stage / reference.solution_directory).resolve()
            if paths.inside(absolute, stage):
                absolute = destination / absolute.relative_to(stage)
            reference.solution_directory = relative(destination, absolute)
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
    return destination / config


@logs.api
class TemplateTools:
    @staticmethod
    def create_solution_template(solution, destination_directory):
        return _snapshot(solution, destination_directory, solution.settings.get().name, template=True)


def instantiate(template, destination, name):
    """template: the template's .cppbuild (as create_solution_template returns it)."""
    from .core import Solution
    config = Path(template).resolve()
    storage.manifest(config / "template.json", "solution_template", root=config)
    source = Solution.open(config)
    return Solution.open(_snapshot(source, destination, name, template=False))

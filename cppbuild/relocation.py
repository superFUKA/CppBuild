"""Move a registered Project's directory, or a Solution's .cppbuild, keeping every recorded path."""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import os
from pathlib import Path

from . import cmake_files, information, logs, paths, storage
from .models import CMakePackage, CMakeSource, ChangeReport, ImportedLibrary, INHERIT, SettingsError


def _plain_path(path):
    # Check the spelling before resolve() hides junctions/symlinks.
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise SettingsError(f"Relocation does not support filesystem links: {part}")


def _plain_tree(root):
    _plain_path(root)
    def inaccessible(error):
        raise error
    for current, directories, files in os.walk(root, followlinks=False, onerror=inaccessible):
        for name in [*directories, *files]:
            _plain_path(Path(current) / name)


def _rebase(value, before, after, source, destination):
    absolute = (before / value).resolve()
    if paths.inside(absolute, source):
        absolute = destination / absolute.relative_to(source)
    if Path(value).is_absolute():
        return str(absolute)
    # Keep unchanged spellings (including '.') when the referent is unchanged.
    if (after / value).resolve() == absolute:
        return value
    try:
        return Path(os.path.relpath(absolute, after)).as_posix()
    except ValueError:  # An external dependency may be on another Windows drive.
        return str(absolute)


def _settings(project, after, source, destination):
    values = project.settings.get()
    def path(value):
        return _rebase(value, project.root, after, source, destination)
    values.source_directories = [path(p) for p in values.source_directories]
    values.project_headers = [path(p) for p in values.project_headers]
    values.output_files = [path(p) for p in values.output_files]
    for data in values.types.values():
        data.include_directories = [path(p) for p in data.include_directories]
    for dependency in values.dependencies.values():
        if isinstance(dependency, (CMakePackage, CMakeSource)):
            dependency.directory = path(dependency.directory)
        elif isinstance(dependency, ImportedLibrary):
            dependency.locations = {k: path(v) for k, v in dependency.locations.items()}
            dependency.import_libraries = {k: path(v) for k, v in dependency.import_libraries.items()}
            dependency.include_directories = [path(p) for p in dependency.include_directories]
    build = deepcopy(project._build_settings)
    if build.googletest_archive is not None:
        build.googletest_archive = path(build.googletest_archive)
    _tools(build.tools, source, destination)
    return values, build


def _tools(tools, source, destination):
    if tools is INHERIT:
        return
    for field in ("cmake", "ctest"):
        value = getattr(tools, field)
        if Path(value).is_absolute():
            setattr(tools, field, _rebase(value, source, destination, source, destination))


def _idle(solution, what):
    if solution._events.stack:
        raise SettingsError(f"{what} cannot be called from a callback")
    from .execution import RunReport
    for owner in [solution, *solution.projects()]:
        last = owner._last_operation
        if last and isinstance(last[1], RunReport) and not last[1].done:
            raise SettingsError(f"Wait for running applications before {what}")
    if (solution.config_directory / "operations/.write.lock").exists():
        raise SettingsError(f"Wait for the Solution's operations before {what}")


def move_project(solution, name, destination, *, auto_update=True, move_files=True):
    from .core import Project
    from .engine import FileOperationReport
    from .events import EventCallbackError
    from .references import check_layout

    if type(auto_update) is not bool or type(move_files) is not bool:
        raise SettingsError("auto_update and move_files must be boolean")
    _idle(solution, "moving a Project")
    project = solution.get_project(name)
    source = project.root
    destination = paths.resolve(solution.root, str(destination))
    values = solution.settings.get()
    values.projects[name] = paths.relative(solution.root, destination)
    solution.settings._validate(values)
    if paths.key(destination) == paths.key(source):
        raise SettingsError("The Project is already in that directory")
    if move_files and not source.exists() and not source.is_symlink():
        # Nothing written yet: CppBuild keeps nothing in a Project's directory, so only the record moves.
        move_files = False
    if move_files:
        _plain_path(destination)
        if paths.overlap(destination, source):
            raise SettingsError("Project destination must not overlap its current directory")
        if paths.inside(solution.config_directory, source):
            raise SettingsError("The Project's directory holds the Solution's .cppbuild; move the files yourself "
                                "and pass move_files=False")
        if destination.exists():
            raise FileExistsError(destination)
        if not source.is_dir():
            raise FileNotFoundError(source)
        _plain_tree(source)

    # Internal links use GUIDs; only filesystem paths need rebasing.
    prepared = []
    for member in solution.projects():
        root = destination if member is project else member.root
        data, build = _settings(member, root, source, destination)
        shadow = Project(solution, root, member.name)
        shadow.settings._validate(data)
        documents = shadow.settings._documents(data)
        prepared.append((member, shadow, data, build, documents))
    check_layout(solution, values, {shadow: data for _, shadow, data, _, _ in prepared})
    parent_build = deepcopy(solution._build_settings)
    _tools(parent_build.tools, source, destination)
    solution_content = solution.settings._document(values)
    written, created_parents, moved = [], [], False
    changed = [source, destination] if move_files else []

    with ExitStack() as locks:
        locks.enter_context(storage.write_lock(solution.config_directory / "operations"))
        locks.enter_context(storage.write_lock(solution.settings.path.parent))
        for member in solution.projects():
            locks.enter_context(storage.write_lock(member.settings.path.parent))
            member.settings._assert_unchanged()
        solution.settings._assert_unchanged()
        try:
            if move_files:
                missing_parents = []
                missing = destination.parent
                while not missing.exists():
                    missing_parents.append(missing)
                    missing = missing.parent
                for directory in reversed(missing_parents):
                    directory.mkdir()
                    created_parents.append(directory)
                # Refuses an existing destination, including a race.
                storage.rename_no_replace(source, destination)
                moved = True
            for member, shadow, data, build, documents in prepared:
                if data == member.settings._data:
                    continue
                for path, content in documents.items():
                    previous = path.read_bytes() if path.exists() else None
                    if previous != content:
                        written.append((path, previous))
                        storage.atomic_write(path, content)
            # Publish membership last, after every Project manifest is ready.
            written.append((solution.settings.path, solution.settings.path.read_bytes()))
            storage.atomic_write(solution.settings.path, solution_content)
        except BaseException as failure:
            try:
                for path, previous in reversed(written):
                    if previous is None:
                        path.unlink(missing_ok=True)
                    elif not path.exists() or path.read_bytes() != previous:
                        storage.atomic_write(path, previous)
                if moved:
                    storage.rename_no_replace(destination, source)
                for directory in reversed(created_parents):
                    directory.rmdir()
            except OSError as recovery:
                raise SettingsError(f"Move failed and rollback failed: {recovery}; "
                                    f"Project location: {destination if moved else source}") from failure
            raise

        # Commit the in-memory view only after disk publication succeeds. Keep
        # existing Project/Settings instances and non-persistent configuration.
        for member, shadow, data, build, documents in prepared:
            altered = member is project or data != member.settings._data or build != member._build_settings
            rewritten = data != member.settings._data
            member.root = shadow.root
            member.settings._data = data
            if rewritten:
                member.settings._revision = {p: hashlib.sha256(c).hexdigest() for p, c in documents.items()}
            member._build_settings = build
            if altered:
                information.invalidate(member)
            if member is project:
                member._known_files, member._known_artifacts = (), ()
                member._generation_state = member._build_state = "stale"
                member._last_update = member._last_operation = None
        solution.settings._data = values
        solution.settings._revision = {solution.settings.path: hashlib.sha256(solution_content).hexdigest()}
        solution._build_settings = parent_build
        solution._last_update = solution._last_operation = None
        changed.extend(path for path, _ in written)

    report = FileOperationReport(tuple(dict.fromkeys(changed)))
    if not auto_update:
        return report
    # Generation failure does not undo an already committed file operation,
    # matching add_file/move_file. The caller can fix the cause and update again.
    try:
        update = solution.update()
        return FileOperationReport(report.changed_paths, update, pending_update=not update.success)
    except (OSError, ValueError, NotImplementedError, EventCallbackError) as exc:
        return FileOperationReport(report.changed_paths, update_error=f"{type(exc).__name__}: {exc}")


# Moving .cppbuild ---------------------------------------------------------------------

def _carried(config):
    """Files of .cppbuild that move with it: management files. Generated files are written again
    (their relative paths change), and the output and lock directories stay."""
    from .file_kinds import LOCAL_DIRECTORIES, WRITE_LOCK
    result = []
    def inaccessible(error):
        raise error
    for current, directories, files in os.walk(config, followlinks=False, onerror=inaccessible):
        here = Path(current)
        if here == config:
            directories[:] = [d for d in directories if d not in LOCAL_DIRECTORIES]
        for name in files:
            path = here / name
            if name == WRITE_LOCK or name.startswith(".tmp-"):
                continue
            result.append((path, cmake_files.generated_file(path)))
    return result


def move_config(solution, destination, *, keep_base=True):
    if type(keep_base) is not bool:
        raise SettingsError("keep_base must be boolean")
    _idle(solution, "moving .cppbuild")
    solution.settings.reload()
    old = solution.config_directory
    target = paths.resolve(solution.root, str(destination))
    new = target / paths.MANAGEMENT
    _plain_path(target)
    if paths.in_management(target):
        raise SettingsError("The destination cannot be inside a .cppbuild directory")
    if paths.overlap(new, old):
        raise SettingsError("The destination must be outside the current .cppbuild")
    if new.exists():
        raise FileExistsError(new)
    root = solution.root if keep_base else (new / paths.relative(old, solution.root)).resolve()
    values = solution.settings.get()

    # The same places, recorded from the new .cppbuild (and base directory).
    def place(value):
        return paths.relative(root, paths.resolve(solution.root, value))
    values.projects = {name: place(value) for name, value in values.projects.items()}
    values.dependency_directories = [place(value) for value in values.dependency_directories]
    for reference in values.references.values():
        reference.solution_directory = place(reference.solution_directory)
    from .core import Solution
    shadow = Solution(new, root)
    shadow.settings._validate(values)
    content = shadow.settings._document(values)
    files = _carried(old)

    with ExitStack() as locks:
        locks.enter_context(storage.write_lock(old / "operations"))
        locks.enter_context(storage.write_lock(old))
        for member in solution.projects():
            locks.enter_context(storage.write_lock(member.settings.path.parent))
            member.settings._assert_unchanged()
        solution.settings._assert_unchanged()
        created = []
        try:
            missing = target
            while not missing.exists():
                created.append(missing)
                missing = missing.parent
            new.mkdir(parents=True)
            created.insert(0, new)
            for path, generated in files:
                if generated:
                    continue
                copy = new / path.relative_to(old)
                copy.parent.mkdir(parents=True, exist_ok=True)
                with copy.open("xb") as stream:
                    stream.write(content if path == solution.settings.path else path.read_bytes())
        except BaseException:
            import shutil
            if new.exists():
                shutil.rmtree(new)
            for directory in created[1:]:
                try:
                    directory.rmdir()
                except OSError:
                    pass
            raise
        # The new place is complete; the old files go (output and locks stay until removed by hand).
        removed = []
        for path, _ in files:
            path.unlink()
            logs.removed(path)
            removed.append(str(path))
    # After the locks are released: directories left empty, the old .cppbuild included.
    for current, _, _ in sorted(os.walk(old), key=lambda item: len(item[0]), reverse=True):
        try:
            Path(current).rmdir()
        except OSError:
            pass

    solution.config_directory, solution.root = new, root
    solution.settings.path = new / "project.json"
    for member in solution.projects():
        member.settings.path = new / "projects" / member.name / "project.json"
        information.invalidate(member)
    solution.settings.reload()
    solution._last_update = solution._last_operation = None
    return ChangeReport((str(new), *removed))


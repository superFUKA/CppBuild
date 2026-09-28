"""Move a registered Project, preserving settings and rebasing typed paths."""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import asdict
import hashlib
import os
from pathlib import Path
import uuid

from . import information, storage
from .models import CMakePackage, CMakeSource, Dependency, ImportedLibrary, INHERIT, SettingsError


def _plain_path(path):
    # Check the spelling before resolve() hides junctions/symlinks.
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise SettingsError(f"Project relocation does not support filesystem links: {part}")


def _plain_tree(root):
    _plain_path(root)
    def inaccessible(error):
        raise error
    for current, directories, files in os.walk(root, followlinks=False, onerror=inaccessible):
        for name in [*directories, *files]:
            _plain_path(Path(current) / name)


def _rebase(value, before, after, source, destination):
    absolute = (before / value).resolve()
    if absolute.is_relative_to(source):
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
    for data in values.types.values():
        data.include_directories = [path(p) for p in data.include_directories]
    for dependency in values.dependencies.values():
        if isinstance(dependency, Dependency):
            if dependency.solution_directory is not None:
                dependency.solution_directory = path(dependency.solution_directory)
        elif isinstance(dependency, (CMakePackage, CMakeSource)):
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


@contextmanager
def _moving_lock(location):
    # The lock travels with the directory; release it at its current location.
    lock = location[0] / ".cppbuild/.write.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        from .models import SettingsConflictError
        raise SettingsConflictError(f"Settings are locked: {lock}") from exc
    os.close(fd)
    try:
        yield
    finally:
        (location[0] / ".cppbuild/.write.lock").unlink()


def _idle(solution):
    if solution._events.stack:
        raise SettingsError("Project relocation cannot be called from a callback")
    from .execution import RunReport
    for owner in [solution, *solution.projects()]:
        last = owner._last_operation
        if last and isinstance(last[1], RunReport) and not last[1].done:
            raise SettingsError("Wait for running applications before moving a Project")
    for project in solution.projects():
        if any((project.root / ".cppbuild/generated").rglob(".write.lock")):
            raise SettingsError("Wait for Project operations before moving a Project")


def move_project(solution, name, destination, *, auto_update=True):
    from .core import Project
    from .engine import FileOperationReport
    from .events import EventCallbackError

    if type(auto_update) is not bool:
        raise SettingsError("auto_update must be boolean")
    if os.name != "nt":
        raise NotImplementedError("Project relocation currently targets Windows")
    _idle(solution)
    project = solution.get_project(name)
    source = project.root
    _plain_path(solution.root / str(destination))
    destination = storage.contained(solution.root, str(destination))
    values = solution.settings.get()
    values.projects[name] = destination.relative_to(solution.root).as_posix()
    solution.settings._validate(values)
    if destination == source or destination.is_relative_to(source) or source.is_relative_to(destination):
        raise SettingsError("Project destination must not overlap its current directory")
    if destination.exists():
        raise FileExistsError(destination)
    if not source.is_dir():
        raise FileNotFoundError(source)
    _plain_tree(source)
    _plain_path(solution.root / ".cppbuild/relocations")

    # Prepare and validate every change before touching files. Internal links use
    # names; only typed filesystem paths need rebasing, never arbitrary strings.
    prepared = []
    for member in solution.projects():
        _plain_path(member.settings.path)
        root = destination if member is project else member.root
        data, build = _settings(member, root, source, destination)
        shadow = Project(solution, root, member.name)
        shadow.settings._validate(data)
        documents = shadow.settings._documents(data)
        prepared.append((member, shadow, data, build, documents))
    parent_build = deepcopy(solution._build_settings)
    _tools(parent_build.tools, source, destination)
    solution_content = storage.encoded(storage.envelope("solution", asdict(values)))
    backup = solution.root / ".cppbuild/relocations" / uuid.uuid4().hex
    location, caches, written, created_parents = [source], [], [], []
    changed = [source, destination]

    with ExitStack() as locks:
        locks.enter_context(storage.write_lock(solution.root / ".cppbuild/operations"))
        locks.enter_context(storage.write_lock(solution.settings.path.parent))
        for member in solution.projects():
            if member is project:
                locks.enter_context(_moving_lock(location))
            else:
                locks.enter_context(storage.write_lock(member.settings.path.parent))
            member.settings._assert_unchanged()
        solution.settings._assert_unchanged()
        _idle(solution)
        try:
            # Archive all configurations; old absolute-path caches are never reused.
            for name in ("generated", "build"):
                old = source / ".cppbuild" / name
                if old.exists():
                    backup.mkdir(parents=True, exist_ok=True)
                    saved = backup / name
                    old.rename(saved)
                    caches.append((old, saved))
            missing_parents = []
            missing = destination.parent
            while not missing.exists():
                missing_parents.append(missing)
                missing = missing.parent
            for directory in reversed(missing_parents):
                directory.mkdir()
                created_parents.append(directory)
            # Windows rename refuses an existing destination, including a race.
            source.rename(destination)
            location[0] = destination
            for member, shadow, data, build, documents in prepared:
                if member is not project and data == member.settings._data:
                    continue
                for path, content in documents.items():
                    previous = path.read_bytes() if path.exists() else None
                    if previous != content:
                        written.append((path, previous))
                        storage.atomic_write(path, content)
            # Publish membership last, after every child manifest is ready.
            written.append((solution.settings.path, solution.settings.path.read_bytes()))
            storage.atomic_write(solution.settings.path, solution_content)
        except BaseException as failure:
            try:
                for path, previous in reversed(written):
                    if previous is None:
                        path.unlink(missing_ok=True)
                    elif not path.exists() or path.read_bytes() != previous:
                        storage.atomic_write(path, previous)
                if location[0] == destination:
                    destination.rename(source)
                    location[0] = source
                for old, saved in reversed(caches):
                    saved.rename(old)
                for directory in reversed(created_parents):
                    directory.rmdir()
                if backup.exists():
                    backup.rmdir()
            except OSError as recovery:
                raise SettingsError(f"Move failed and rollback failed: {recovery}; "
                                    f"Project location: {location[0]}; cache backup: {backup}") from failure
            raise

        # Commit the in-memory view only after disk publication succeeds. Keep
        # existing Project/Settings instances and non-persistent configuration.
        for member, shadow, data, build, documents in prepared:
            altered = member is project or data != member.settings._data or build != member._build_settings
            rewritten = member is project or data != member.settings._data
            member.root = shadow.root
            member.settings.path = shadow.settings.path
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
        changed.extend(saved for _, saved in caches)

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

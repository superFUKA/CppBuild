"""Solution-owned external references, maintained by Project settings writes, and the rules
for where member Projects' files may be."""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
from pathlib import Path
import uuid

from . import dependencies, paths, storage
from .models import ChangeReport, Dependency, NoMainProjectError, ProjectReference, SettingsConflictError, SettingsError


def validate(values):
    if not isinstance(values.references, dict):
        raise SettingsError("references must be a mapping")
    for key, reference in values.references.items():
        if not isinstance(reference, ProjectReference):
            raise SettingsError("Expected ProjectReference")
        dependencies.guid(reference.project_guid)
        if key != reference.project_guid:
            raise SettingsError("Reference keys must be their target Project GUID")
        path = reference.solution_directory
        if not isinstance(path, str) or not path or "\x00" in path or Path(path).name != paths.MANAGEMENT:
            raise SettingsError("Expected an external Solution .cppbuild path")


def target(solution, project_guid):
    matches = [p for p in solution.projects() if p.settings._data.guid == project_guid]
    if len(matches) != 1:
        raise SettingsError(f"Missing or duplicate target Project GUID: {project_guid}")
    return matches[0]


def _register(values, reference):
    key = reference.project_guid
    previous = values.references.get(key)
    if previous is not None and previous != reference:
        raise SettingsError(f"The same Project GUID has conflicting locations: {key}")
    values.references[key] = reference
    validate(values)


def source_directories(data):
    """(Project, absolute source directory) of every member, from settings data by Project."""
    return [(project, paths.resolve(project.root, directory))
            for project, item in data.items() for directory in item.source_directories]


def check_layout(solution, values, data):
    """Every file belongs to one Project: members' source directories do not overlap, and none
    holds a dependency directory (its clones would be collected as sources)."""
    sources = source_directories(data)
    for index, (project, first) in enumerate(sources):
        for other, second in sources[index + 1:]:
            if other is not project and paths.overlap(first, second):
                raise SettingsError(f"Source directories of {project.name} and {other.name} overlap: {first}, {second}; "
                                    "set source_directories so that each file belongs to one Project")
    for directory in values.dependency_directories:
        place = paths.resolve(solution.root, directory)
        for project, source in sources:
            if paths.overlap(place, source):
                raise SettingsError(f"The dependency directory {place} overlaps a source directory of {project.name}: "
                                    f"{source}")


def check_loaded(values, data):
    """Checks across the saved Projects of one Solution."""
    guids = set()
    for item in data.values():
        if item.guid in guids:
            raise SettingsError("Duplicate Project GUID in Solution")
        guids.add(item.guid)
    if guids & values.references.keys():
        raise SettingsError("The same Project GUID is registered locally and externally")


def normalize(solution, values, data):
    """Normalized paths, and only the references some Project still uses."""
    for reference in values.references.values():
        reference.solution_directory = paths.rebase(reference.solution_directory, solution.root, solution.root)
    for project, item in data.items():
        paths.project_values(item, lambda value: paths.rebase(value, project.root, project.root))
    check_loaded(values, data)
    used = {d.project_guid for item in data.values() for d in item.dependencies.values() if isinstance(d, Dependency)}
    values.references = {k: v for k, v in values.references.items() if k in used}
    validate(values)
    check_layout(solution, values, data)


def _publish(solution, values, data, originals, revisions, solution_revision):
    documents = {}
    rewritten = set()
    for project, item in data.items():
        project.settings._validate(item)
        if item != originals[project]:
            documents.update(project.settings._documents(item))
            rewritten.add(project)
    solution.settings._validate(values)
    if values != solution.settings._data:
        documents[solution.settings.path] = solution.settings._document(values)
    with ExitStack() as locks:
        locks.enter_context(storage.write_lock(solution.settings.path.parent))
        for project in sorted(data, key=lambda p: str(p.settings.path)):
            locks.enter_context(storage.write_lock(project.settings.path.parent))
        for path, fingerprint in [(solution.settings.path, solution_revision),
                                  *((p, h) for r in revisions.values() for p, h in r.items())]:
            if not path.is_file() or storage.digest(path) != fingerprint:
                raise SettingsConflictError(f"Settings changed externally; reload first: {path}")
        changed = storage.publish_documents(documents)
    # Keep unchanged revisions to detect edits made after the transaction.
    for project in data:
        if project in rewritten:
            revisions[project] = {p: hashlib.sha256(c).hexdigest()
                                  for p, c in project.settings._documents(data[project]).items()}
    return ChangeReport(changed)


def save_project(settings, values, *, additions=(), git_source=None):
    def change(data, parent):
        if git_source is not None:
            name, record = git_source
            parent.git_sources[name] = record
        data[settings.owner] = deepcopy(values)
        for reference in additions:
            _register(parent, reference)
    return save_projects(settings.owner.solution, change)


def save_projects(solution, change):
    """Save change(data, parent) of every Project's settings and the Solution's in one publish;
    references no Project uses any more are dropped."""
    parent = solution.settings.get()
    originals = {p: p.settings.get() for p in solution.projects()}
    data = {p: deepcopy(d) for p, d in originals.items()}
    change(data, parent)
    normalize(solution, parent, data)
    revisions = {p: dict(p.settings._revision) for p in data}
    report = _publish(solution, parent, data, originals, revisions,
                      solution.settings._revision[solution.settings.path])
    from .information import invalidate
    for project, item in data.items():
        if item != originals[project]:
            invalidate(project)
        project.settings._data, project.settings._revision = item, revisions[project]
    if parent != solution.settings._data:
        solution.settings._data = parent
        solution.settings._revision = {solution.settings.path: hashlib.sha256(
            solution.settings._document(parent)).hexdigest()}
    return report


def _portable(other, place):
    """A Solution cloned with git builds from its clone alone: its Projects are in the clone."""
    outside = [project.name for project in other.projects() if not paths.inside(project.root, place)]
    if outside:
        raise SettingsError(f"The Solution in {place} has Projects outside its repository ({', '.join(outside)}); "
                            "a clone alone could not build them")


def link(settings, config_directory, link_type, *, git_source=None, place=None):
    from .core import Solution
    config = dependencies.path(settings.owner, str(config_directory))
    if config.name != paths.MANAGEMENT:
        raise SettingsError("Pass the Solution .cppbuild directory")
    other = Solution.open(config)
    if place is not None:
        _portable(other, place)
    project_name = other.settings._data.main_project
    if project_name is None:
        raise NoMainProjectError("External Solution needs a main Project", solution=other.root)
    project = other.get_project(project_name)
    if link_type not in project.settings._data.types:
        raise NoMainProjectError("External Solution needs a main Project of the requested type",
                                 solution=other.root, project_type=link_type)
    project_guid = project.settings._data.guid
    if other.config_directory == settings.owner.solution.config_directory:
        raise SettingsError("Use link_project for the same Solution")
    dependency = Dependency(project_guid, link_type)
    dependencies.validate(settings.owner, dependency)
    values = settings.get()
    dependencies.registered(values, dependency)
    key = uuid.uuid4().hex
    values.dependencies[key] = dependency
    save_project(settings, values, additions=(ProjectReference(project_guid, paths.relative(settings.owner.solution.root, config)),),
                 git_source=git_source)
    from .models import LinkReport
    return LinkReport(key, project_name, link_type)

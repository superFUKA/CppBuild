"""Solution-owned external references, maintained by Project settings writes."""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
from pathlib import Path
import uuid

from . import dependencies, paths, storage
from .models import ChangeReport, Dependency, ProjectReference, SettingsConflictError, SettingsError


def validate(values):
    if not isinstance(values.references, dict):
        raise SettingsError("references must be a mapping")
    locations = {}
    for name, reference in values.references.items():
        dependencies.reference_name(name)
        if not isinstance(reference, ProjectReference):
            raise SettingsError("Expected ProjectReference")
        dependencies.guid(reference.project_guid)
        path = reference.solution_directory
        if not isinstance(path, str) or not path or "\x00" in path or Path(path).name != ".cppbuild":
            raise SettingsError("Expected an external Solution .cppbuild path")
        previous = locations.setdefault(reference.project_guid, path)
        if previous != path:
            raise SettingsError("The same Project GUID has conflicting locations")


def _identity_solution(config):
    # GUID migration needs only local manifests, so legacy cycles never recurse.
    from .core import Solution
    config = Path(config).resolve()
    if config.name != ".cppbuild":
        raise SettingsError("Pass the Solution .cppbuild directory")
    result = Solution(config.parent)
    result.settings._reload(migrate=False)
    return result


def target(solution, project_guid):
    matches = [p for p in solution.projects() if p.settings._data.guid == project_guid]
    if len(matches) != 1:
        raise SettingsError(f"Missing or duplicate target Project GUID: {project_guid}")
    return matches[0]


def _register(values, name, reference):
    previous = values.references.get(name)
    if previous is not None and previous != reference:
        raise SettingsError(f"Reference name already identifies another target or location: {name}")
    values.references[name] = reference
    validate(values)


def normalize(solution, values, data, *, migrate=True):
    for reference in values.references.values():
        reference.solution_directory = paths.rebase(reference.solution_directory, solution.root, solution.root)
    by_name = {p.name: d for p, d in data.items()}
    guids = set()
    for project, item in data.items():
        paths.project_values(item, lambda value: paths.rebase(value, project.root, project.root))
        if item.guid is None:
            item.guid = str(uuid.uuid4())
        if item.guid in guids:
            raise SettingsError("Duplicate Project GUID in Solution")
        guids.add(item.guid)
    if not migrate:
        return
    used, external = set(), {}
    for project, item in data.items():
        for dependency in item.dependencies.values():
            if not isinstance(dependency, Dependency):
                continue
            if dependency.solution_directory is not None:
                config = dependencies.path(project, dependency.solution_directory)
                if config == solution.root / ".cppbuild":
                    other = by_name.get(dependency.project)
                    if other is None:
                        raise SettingsError("Missing local dependency Project")
                    dependency.project_guid = other.guid
                    dependency.solution_directory = None
                else:
                    if config not in external:
                        external[config] = _identity_solution(config)
                    other_solution = external[config]
                    try:
                        other = (target(other_solution, dependency.project_guid) if dependency.project_guid
                                 else other_solution.get_project(dependency.project))
                    except KeyError as exc:
                        raise SettingsError("Missing legacy dependency Project") from exc
                    key = other.settings._data.guid
                    _register(values, key, ProjectReference(key, paths.relative(solution.root, config)))
                    dependency.reference = key
                    dependency.project_guid = None
                    dependency.solution_directory = None
            if dependency.reference is not None:
                if dependency.reference not in values.references:
                    raise SettingsError(f"Unknown Solution reference: {dependency.reference}")
                used.add(dependency.reference)
            elif dependency.project_guid is None:
                other = by_name.get(dependency.project)
                if other is None:
                    raise SettingsError("Missing local dependency Project")
                dependency.project_guid = other.guid
    values.references = {k: v for k, v in values.references.items() if k in used}
    validate(values)


def _publish(solution, values, data, originals, revisions, solution_revision):
    documents = {}
    rewritten = set()
    for project, item in data.items():
        project.settings._validate(item)
        if item != originals[project] or any(storage.needs_migration(p) for p in revisions[project]):
            documents.update(project.settings._documents(item))
            rewritten.add(project)
    solution.settings._validate(values)
    content = solution.settings._document(values)
    if values != solution.settings._read() or storage.needs_migration(solution.settings.path):
        documents[solution.settings.path] = content
    with ExitStack() as locks:
        locks.enter_context(storage.write_lock(solution.settings.path.parent))
        for project in sorted(data, key=lambda p: str(p.root)):
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


def load(solution, values, projects, loaded, solution_revision, *, migrate):
    previous = deepcopy(values)
    originals = {p: loaded[name][0] for name, p in projects.items()}
    revisions = {p: loaded[name][1] for name, p in projects.items()}
    data = {p: deepcopy(d) for p, d in originals.items()}
    normalize(solution, values, data, migrate=migrate)
    if data != originals or values != previous or storage.needs_migration(solution.settings.path) or any(
            storage.needs_migration(path) for revision in revisions.values() for path in revision):
        report = _publish(solution, values, data, originals, revisions, solution_revision)
        if str(solution.settings.path) in report.changed_paths:
            solution_revision = hashlib.sha256(solution.settings._document(values)).hexdigest()
    return {p.name: (d, revisions[p]) for p, d in data.items()}, solution_revision


def save_project(settings, values, *, additions=None):
    solution = settings.owner.solution
    parent = solution.settings.get()
    originals = {p: p.settings.get() for p in solution.projects()}
    data = {p: deepcopy(d) for p, d in originals.items()}
    data[settings.owner] = deepcopy(values)
    for name, reference in (additions or {}).items():
        _register(parent, name, reference)
    normalize(solution, parent, data)
    revisions = {p: dict(p.settings._revision) for p in data}
    report = _publish(solution, parent, data, originals, revisions,
                      solution.settings._revision[solution.settings.path])
    from .information import invalidate
    for project, item in data.items():
        if item != originals[project]:
            invalidate(project)
        project.settings._data, project.settings._revision = item, revisions[project]
    solution.settings._data = parent
    if str(solution.settings.path) in report.changed_paths:
        solution.settings._revision = {solution.settings.path: hashlib.sha256(
            solution.settings._document(parent)).hexdigest()}
    return report


def link(settings, config_directory, link_type, name):
    config = dependencies.path(settings.owner, str(config_directory))
    other = _identity_solution(config)
    project_name = other.settings._data.main_project
    if project_name is None:
        raise SettingsError("External Solution needs a main Project")
    project = other.get_project(project_name)
    if link_type not in project.settings._data.types:
        raise SettingsError("External Solution needs a main Project of the requested type")
    project_guid = project.settings._data.guid
    name = project_guid if name is None else name
    dependencies.reference_name(name)
    if other.root == settings.owner.solution.root:
        raise SettingsError("Use link_project for the same Solution")
    dependency = Dependency(project_name, link_type, reference=name)
    dependencies.validate(settings.owner, dependency)
    values = settings.get()
    if dependency in values.dependencies.values():
        raise SettingsError("Dependency is already registered")
    key = uuid.uuid4().hex
    values.dependencies[key] = dependency
    save_project(settings, values, additions={name: ProjectReference(project_guid, paths.relative(settings.owner.solution.root, config))})
    from .models import LinkReport
    return LinkReport(key, project_name, link_type)

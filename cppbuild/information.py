"""Snapshots of observed state. Never scan files or run tools from info()."""
from copy import deepcopy
from dataclasses import dataclass

from .models import SettingsError


@dataclass(frozen=True)
class ProjectInfo:
    name: str
    root: object
    settings: object
    build_settings: object
    files: tuple
    artifacts: tuple
    generation_state: str
    build_state: str
    last_operation: str | None
    last_success: bool | None
    external_changes_checked: bool = False


@dataclass(frozen=True)
class SolutionInfo:
    root: object
    settings: object
    build_settings: object
    projects: tuple[ProjectInfo, ...]
    last_operation: str | None
    last_success: bool | None
    external_changes_checked: bool = False


def signature(project, settings=None, seen=None):
    from .models import Dependency
    seen = set() if seen is None else seen
    settings = settings or project._resolved_build_settings()
    key = (project.name, settings.project_type)
    if key in seen:
        raise SettingsError("Dependency cycle in observed settings")
    dependencies = []
    for value in project.settings._data.dependencies.values():
        if isinstance(value, Dependency) and value.project_guid not in project.solution.settings._data.references:
            child = next((p for p in project.solution.projects() if p.settings._data.guid == value.project_guid), None)
            if child is None:
                raise SettingsError("Dependency no longer registered")
            dependencies.append(signature(child, child._resolved_build_settings(value.project_type), seen | {key}))
    return (deepcopy(project.settings._data), deepcopy(settings), project._file_revision, tuple(dependencies))


def generated(project, settings, report):
    project._known_files = report.files
    project._generation_signature = signature(project, settings)
    project._generation_state = "current" if report.success else "failed"


def built(project, settings, artifacts, success=True):
    project._known_artifacts = tuple(artifacts)
    project._build_signature = signature(project, settings)
    project._build_state = "current" if success else "failed"


def invalidate(project, *, bump=True):
    if bump:
        project._file_revision += 1
    # Conservative invalidation includes transitive consumers, without disk I/O.
    from .models import Dependency
    seen = set()
    def visit(changed):
        if changed.name in seen:
            return
        seen.add(changed.name)
        for other in project.solution.projects():
            if any(isinstance(d, Dependency) and d.project_guid == changed.settings._data.guid
                   for d in other.settings._data.dependencies.values()):
                other._build_state = "stale" if other._build_signature is not None else "unknown"
                visit(other)
    visit(project)


def _last(owner):
    if owner._last_operation is None:
        return None, None
    name, result = owner._last_operation
    if result is None:
        return name, False
    try:
        return name, result.success
    except Exception:
        return name, False


def snapshot(solution):
    result = []
    for project in solution.projects():
        try:
            current = signature(project)
        except SettingsError:
            current = None
        generation = project._generation_state
        build = project._build_state
        if project._generation_signature is not None and current != project._generation_signature:
            generation = "stale"
        if project._build_signature is not None and current != project._build_signature:
            build = "stale"
        result.append(ProjectInfo(project.name, project.root, project.settings.get(),
                                  deepcopy(project._build_settings), project._known_files, project._known_artifacts,
                                  generation, build, *_last(project)))
    return SolutionInfo(solution.root, solution.settings.get(), deepcopy(solution._build_settings), tuple(result), *_last(solution))

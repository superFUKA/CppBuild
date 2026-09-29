"""Validate a dependency closure before generating or starting any tools."""
from dataclasses import dataclass
import hashlib

from .models import Dependency, SettingsError


@dataclass
class Node:
    project: object
    settings: object
    dependencies: list

    @property
    def key(self):
        return (self.project.settings._data.guid, self.settings.project_type.value)

    @property
    def label(self):
        suffix = hashlib.sha256(str(self.project.root).encode()).hexdigest()[:12]
        return f"{self.project.name}_{self.settings.project_type.value}_{suffix}"


def resolve(projects, *, include_external_members=False):
    """Return roots and dependency-first nodes with per-operation settings copies."""
    nodes, visiting, ordered, loaded, external = {}, set(), [], set(), {}
    locations, registries = {}, {}
    overrides = {}
    for project in projects:
        for directory, data in project.solution._build_settings.external_build_settings.items():
            overrides[(project.solution.root / directory).resolve()] = data

    def visit(project, requested=None):
        project._check_active()
        if project not in loaded:
            project.settings.reload()
            loaded.add(project)
        if project.solution not in registries:
            from . import storage
            if storage.needs_migration(project.solution.settings.path):
                project.solution.settings.reload()
            registries[project.solution] = project.solution.settings._read().references
        settings = project._resolved_build_settings(requested)
        project_guid = project.settings._data.guid
        previous = locations.setdefault(project_guid, project.root)
        if previous != project.root:
            raise SettingsError("The same Project GUID exists at multiple locations")
        key = (project_guid, settings.project_type.value)
        if key in visiting:
            raise SettingsError(f"Dependency cycle involving {project.name}")
        if key in nodes:
            return nodes[key]
        node = Node(project, settings, [])
        visiting.add(key)
        for reference in project.settings._data.dependencies.values():
            if not isinstance(reference, Dependency):
                continue
            try:
                solution = project.solution
                from .references import target
                if reference.project_guid in registries[solution]:
                    from .core import Solution
                    entry = registries[solution][reference.project_guid]
                    config = (solution.root / entry.solution_directory).resolve()
                    if config not in external:
                        external[config] = Solution.open(config)
                        if config in overrides:
                            external[config].set_build_settings(overrides[config])
                    solution = external[config]
                other = target(solution, reference.project_guid)
            except KeyError as exc:
                raise SettingsError(f"Missing dependency Project GUID: {reference.project_guid}") from exc
            child = visit(other, reference.project_type)
            if any(d.project.root == child.project.root and d.settings.project_type != child.settings.project_type for d in node.dependencies):
                raise SettingsError("One consumer cannot link multiple kinds of the same Project")
            if (settings.configuration, settings.architecture) != (child.settings.configuration, child.settings.architecture):
                raise SettingsError(f"Incompatible configuration/architecture: {project.name} -> {other.name}")
            if child not in node.dependencies:
                node.dependencies.append(child)
        visiting.remove(key)
        nodes[key] = node
        ordered.append(node)
        return node

    roots = [visit(project) for project in projects]
    if include_external_members:
        expanded = set()
        while any(config not in expanded for config in external):
            for config, solution in list(external.items()):
                if config in expanded:
                    continue
                expanded.add(config)
                for project in solution.projects():
                    if any(node.project.root == project.root for node in ordered):
                        continue
                    selected = project._build_settings.project_type
                    kinds = [selected] if selected is not None else project.settings.get().types
                    for kind in kinds:
                        visit(project, kind)
    return roots, ordered

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
        return (str(self.project.root), self.settings.project_type.value)

    @property
    def label(self):
        suffix = hashlib.sha256(str(self.project.root).encode()).hexdigest()[:12]
        return f"{self.project.name}_{self.settings.project_type.value}_{suffix}"


def resolve(projects, *, include_external_members=False):
    """Return roots and dependency-first nodes with per-operation settings copies."""
    nodes, visiting, ordered, loaded, external = {}, set(), [], set(), {}
    overrides = {}
    for project in projects:
        for directory, data in project.solution._build_settings.external_build_settings.items():
            overrides[(project.solution.root / directory).resolve()] = data

    def visit(project, requested=None):
        project._check_active()
        if project not in loaded:
            project.settings.reload()
            loaded.add(project)
        settings = project._resolved_build_settings(requested)
        key = (str(project.root), settings.project_type.value)
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
                if reference.solution_directory is not None:
                    from .core import Solution
                    from .dependencies import path
                    config = path(project, reference.solution_directory)
                    if config not in external:
                        external[config] = Solution.open(config)
                        if config in overrides:
                            external[config].set_build_settings(overrides[config])
                    solution = external[config]
                other = solution.get_project(reference.project)
            except KeyError as exc:
                raise SettingsError(f"Missing dependency Project: {reference.project}") from exc
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

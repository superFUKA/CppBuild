"""Validate a dependency closure before generating or starting any tools."""
from dataclasses import dataclass

from .models import SettingsError


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
        return f"{self.project.name}_{self.settings.project_type.value}"


def resolve(projects):
    """Return roots and dependency-first nodes with per-operation settings copies."""
    nodes, visiting, ordered, loaded = {}, set(), [], set()

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
            try:
                other = project.solution.get_project(reference.project)
            except KeyError as exc:
                raise SettingsError(f"Missing dependency Project: {reference.project}") from exc
            child = visit(other, reference.project_type)
            if (settings.configuration, settings.architecture) != (child.settings.configuration, child.settings.architecture):
                raise SettingsError(f"Incompatible configuration/architecture: {project.name} -> {other.name}")
            if child not in node.dependencies:
                node.dependencies.append(child)
        visiting.remove(key)
        nodes[key] = node
        ordered.append(node)
        return node

    roots = [visit(project) for project in projects]
    return roots, ordered

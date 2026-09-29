"""Persisted, virtual solution folder layout (no filesystem moves)."""
import hashlib

from .models import SettingsError, SolutionFolderSettings


def _folder(value, *, nested=False, empty=False):
    if not isinstance(value, str):
        raise SettingsError("Solution folder must be a string")
    if empty and value == "":
        return
    parts = value.split("/")
    if (not nested and len(parts) != 1) or any(
        part in {"", ".", ".."} or part != part.strip() or
        any(ord(c) < 32 or c in '\\:*?"<>|;$,' for c in part)
        for part in parts
    ):
        raise SettingsError("Expected a relative solution folder name or path")


def validate(settings, projects):
    if settings is None:
        return
    if not isinstance(settings, SolutionFolderSettings):
        raise SettingsError("Expected SolutionFolderSettings or None")
    _folder(settings.projects)
    _folder(settings.linked_projects)
    if settings.projects.casefold() == settings.linked_projects.casefold():
        raise SettingsError("Local and linked solution folders must be distinct")
    if not isinstance(settings.project_folders, dict):
        raise SettingsError("project_folders must be a mapping")
    for name, folder in settings.project_folders.items():
        if name not in projects:
            raise SettingsError("Folder placement must refer to a local Project")
        _folder(folder, nested=True, empty=True)


def dependency_keys(roots):
    keys = set()

    def visit(node):
        if node.key in keys:
            return
        keys.add(node.key)
        for child in node.dependencies:
            visit(child)

    for node in roots:
        visit(node)
    return keys


def placements(solution, nodes):
    settings = solution.settings._data.solution_folders
    if settings is None:
        return {}
    external = {n.project.solution.root: n.project.solution.settings._data.name
                for n in nodes if n.project.solution.root != solution.root}
    names = {}
    for root, name in external.items():
        # Distinct Solutions with equal names must not merge into one folder.
        if sum(other.casefold() == name.casefold() for other in external.values()) > 1:
            name += " (" + hashlib.sha256(str(root).encode()).hexdigest()[:12] + ")"
        names[root] = name
    result = {}
    for node in nodes:
        if node.project.solution.root == solution.root:
            child = settings.project_folders.get(node.project.name, "")
            result[node.key] = settings.projects + ("/" + child if child else "")
        else:
            result[node.key] = settings.linked_projects + "/" + names[node.project.solution.root]
    return result

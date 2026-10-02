"""Persisted, virtual solution folder layout (no filesystem moves)."""
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


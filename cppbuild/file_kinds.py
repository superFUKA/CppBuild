"""Which files CppBuild writes and how a repository treats each: Solution.file_kind()."""
from pathlib import Path

from . import cmake_files, output_paths
from .git_sources import FETCHING
from .models import FileKind, SettingsError

MANAGEMENT = ".cppbuild"
# Below a .cppbuild directory: the default output root and the operation locks.
LOCAL_DIRECTORIES = {"output", "operations"}
WRITE_LOCK = ".write.lock"


def _fetched(solution, path):
    from .git_sources import statuses
    try:
        places = [s.directory for s in statuses(solution)]
    except (OSError, SettingsError):
        # A conflict or unreadable record still leaves the Solution's own places known.
        values = solution.settings._data
        places = []
        if values.git_sources:
            from .git_sources import base
            place = base(solution.root, values)
            places = [place / name for name in values.git_sources]
    for place in places:
        temporary = place.with_name(place.name + FETCHING)
        if path == place or path.is_relative_to(place) or path == temporary or path.is_relative_to(temporary):
            return True
    return False


def restorable(path):
    """Whether a Solution.transaction() restores path: management and generated files, by name and
    place, so before a file exists. Build trees, artifacts, locks and clones are never restored."""
    path = Path(path)
    if path.name == WRITE_LOCK or any(output_paths.generated_directory(p) for p in path.parents):
        return False
    for parent in path.parents:
        if parent.name == MANAGEMENT:
            inside = path.relative_to(parent).parts
            return inside[0] not in LOCAL_DIRECTORIES
    return path.name in {cmake_files.ENTRY, cmake_files.TOP_LEVEL}


def kind(solution, path):
    path = Path(path)
    path = (solution.root / path if not path.is_absolute() else path).resolve()
    if _fetched(solution, path):
        return FileKind.FETCHED
    if path.name == WRITE_LOCK or any(output_paths.generated_directory(p) for p in (path, *path.parents)):
        return FileKind.OUTPUT
    for parent in (path, *path.parents):
        if parent.name == MANAGEMENT:
            inside = path.relative_to(parent).parts
            if inside and inside[0] in LOCAL_DIRECTORIES:
                return FileKind.OUTPUT
            return FileKind.MANAGED
    if cmake_files.generated_file(path):
        return FileKind.GENERATED
    return None

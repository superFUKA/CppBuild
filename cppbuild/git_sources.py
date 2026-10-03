"""Linked Solutions cloned with git: records, the flattened list and fetching.

A Solution records each repository it links with git as a GitSource (URL, commit,
Solution path in the repository) under a place name. Clones live directly under
the Solution's first dependency directory. Linked Solutions' own records are
followed too; the Solution that starts the operation wins, and differing records
it does not override are an error.

The generated CMake (cmake_files.top_level) follows the same rules as fetch():
a missing place is cloned into <place>.cppbuild-fetch, checked out at the
recorded commit and renamed into place; an existing place is never changed, only
compared with the record.
"""
from dataclasses import dataclass, replace
import hashlib
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
from urllib.parse import urlsplit

from . import storage, tooling
from .models import FetchFailure, GitFetchError, GitSource, GitSourceConflictError, SettingsError

COMMIT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
FETCHING = ".cppbuild-fetch"


# Validation --------------------------------------------------------------------

def validate_url(url):
    if (not isinstance(url, str) or not url or url.startswith("-") or any(c.isspace() for c in url)
            or any(c in url for c in ("\x00", ";", '"', "$<"))):
        raise SettingsError(f"Unsupported git URL: {url!r}")
    if "://" in url:
        parts = urlsplit(url)
        # Recorded URLs are committed with the Solution; credentials belong in git's own configuration.
        if parts.password is not None or (parts.scheme in {"http", "https"} and parts.username):
            raise SettingsError("Git URLs must not contain credentials; configure them in git instead")


def validate_path(path):
    """The Solution's .cppbuild directory, relative to the repository root."""
    if not isinstance(path, str) or not path or "\\" in path or "\x00" in path:
        raise SettingsError("Expected a relative .cppbuild path in the repository")
    parts = PurePosixPath(path).parts
    if PurePosixPath(path).is_absolute() or parts[-1] != ".cppbuild" or any(p in {"", ".", ".."} for p in parts):
        raise SettingsError("Expected a relative .cppbuild path in the repository")
    if any(c in path for c in (";", '"', "$", "@")):
        raise SettingsError("The .cppbuild path contains characters CMake cannot quote")


def identity(url):
    """Two records name the same repository when their URLs differ only in a trailing / or .git."""
    value = url.rstrip("/")
    return value[:-4].rstrip("/") if value.endswith(".git") else value


def validate(values):
    if not isinstance(values.git_sources, dict):
        raise SettingsError("git_sources must be a mapping")
    seen = set()
    for name, source in values.git_sources.items():
        if not isinstance(name, str) or not NAME.fullmatch(name) or name.endswith(FETCHING):
            raise SettingsError(f"Unsupported git source name: {name!r}")
        if not isinstance(source, GitSource):
            raise SettingsError("Expected GitSource")
        validate_url(source.url)
        if not isinstance(source.revision, str) or not COMMIT.fullmatch(source.revision):
            raise SettingsError("A git source records a full commit ID")
        validate_path(source.path)
        if identity(source.url) in seen:
            raise SettingsError(f"The same repository is recorded twice: {source.url}")
        seen.add(identity(source.url))
    if values.git_sources and not values.dependency_directories:
        raise SettingsError("git_sources are cloned into the first dependency directory; set dependency_directories")


def default_name(url):
    """The place name from the repository name, the same on every PC."""
    tail = re.split(r"[/:]", identity(url).rstrip("/"))[-1]
    name = re.sub(r"[^A-Za-z0-9._-]", "_", tail).lstrip("._-")
    return name if name and not name.endswith(FETCHING) else "repository"


def unique_name(url, used):
    name = default_name(url)
    if name in used:
        name += "-" + hashlib.sha256(identity(url).encode()).hexdigest()[:8]
    return name


def base(root, values):
    return storage.contained(root, values.dependency_directories[0])


# The flattened list ------------------------------------------------------------------

@dataclass(frozen=True)
class GitSourceStatus:
    name: str
    url: str
    revision: str
    path: str
    directory: Path  # the clone
    recorded_by: Path | None  # None: the Solution itself; else the linked Solution that records it
    present: bool

    @property
    def config(self):
        return self.directory / self.path


@dataclass(frozen=True)
class FetchReport:
    sources: tuple  # every GitSourceStatus, the Solution's own records first
    fetched: tuple  # names cloned by this call
    warnings: tuple  # clones whose HEAD differs from the record


def _records(config):
    """A linked Solution's own git_sources, read without opening its Projects."""
    raw = storage.manifest(config / "project.json", "solution").get("git_sources", {})
    if not isinstance(raw, dict):
        raise SettingsError(f"git_sources must be a mapping: {config}")
    result = {}
    for name, source in raw.items():
        storage.object_fields(source, {"url", "revision", "path"})
        result[name] = GitSource(**source)
    return result


def _present(directory, path):
    return (directory / path / "project.json").is_file()


def walk(root, values, fetch_one=None):
    """Every git source of the Solution at root, its own records first, then breadth first.

    fetch_one(status) may clone a missing place and returns the new status.
    """
    if not values.git_sources:
        return []
    place = base(root, values)
    result, adopted, recorded, names = [], {}, {}, set(values.git_sources)

    def add(name, source, owner):
        status = GitSourceStatus(name, source.url, source.revision, source.path, place / name, owner,
                                 _present(place / name, source.path))
        adopted[identity(source.url)] = status
        result.append(status)

    for name in sorted(values.git_sources):
        add(name, values.git_sources[name], None)
    index = 0
    while index < len(result):
        status = result[index]
        if not status.present and fetch_one is not None:
            status = result[index] = fetch_one(status)
            adopted[identity(status.url)] = status
        index += 1
        if not status.present:
            continue
        for name, source in sorted(_records(status.config).items()):
            key = identity(source.url)
            previous = adopted.get(key)
            if previous is not None and previous.recorded_by is None:
                continue  # The Solution's own record wins.
            # Distinct (commit, path) records of one repository, with the first Solution recording each.
            recorded.setdefault(key, {}).setdefault((source.revision, source.path), status.config.parent)
            if previous is None:
                candidate = name if name not in names else unique_name(source.url, names)
                names.add(candidate)
                add(candidate, source, status.config.parent)
    conflicts = []
    for key, records in sorted(recorded.items()):
        if len(records) > 1:
            conflicts.append((adopted[key].url, tuple(sorted((str(owner), revision, path)
                                                             for (revision, path), owner in records.items()))))
    if conflicts:
        raise GitSourceConflictError(conflicts)
    return result


def statuses(solution):
    return walk(solution.root, solution.settings._data)


# git -----------------------------------------------------------------------------

class _Failure(Exception):
    pass


def _git(tools, args, cwd=None):
    env = tooling.environment(tools)
    executable = shutil.which(tools.git, path=env.get("PATH", ""))
    if executable is None:
        raise _Failure(f"git was not found ({tools.git})")
    result = subprocess.run([executable, *args], cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace")
    return result.returncode, result.stdout.strip(), " ".join(result.stderr.split())


def _resolve_local(tools, directory, revision):
    for candidate in (["HEAD"] if revision is None else [revision, f"origin/{revision}", f"refs/tags/{revision}"]):
        code, out, _ = _git(tools, ["-C", str(directory), "rev-parse", "--verify", "--quiet", candidate + "^{commit}"])
        if code == 0 and COMMIT.fullmatch(out):
            return out
    return None


def _resolve_remote(tools, url, revision):
    code, out, err = _git(tools, ["ls-remote", "--", url, revision or "HEAD"])
    if code != 0:
        raise _Failure(f"git ls-remote failed: {err}")
    refs = dict(reversed(line.split("\t", 1)) for line in out.splitlines() if "\t" in line)
    for ref in (["HEAD"] if revision is None else
                [f"refs/tags/{revision}^{{}}", f"refs/tags/{revision}", f"refs/heads/{revision}", revision]):
        if ref in refs and COMMIT.fullmatch(refs[ref]):
            return refs[ref]
    raise _Failure(f"{revision or 'HEAD'} was not found in {url}")


def resolve(tools, url, revision, directory):
    """A full commit ID for revision (None: the default branch), without changing an existing clone.

    Branches and tags come from the remote, so a clone that was not fetched does not
    record an old commit; an abbreviated commit ID needs the existing clone.
    """
    if revision is not None and COMMIT.fullmatch(revision):
        return revision
    if revision is not None and re.fullmatch(r"[0-9a-f]{4,63}", revision) and directory.exists():
        commit = _resolve_local(tools, directory, revision)
        if commit is not None:
            return commit
    return _resolve_remote(tools, url, revision)


def _remove(path):
    def writable(function, target, *_):
        Path(target).chmod(stat.S_IWRITE)  # git marks its objects read-only on Windows
        function(target)
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=writable)
    else:
        shutil.rmtree(path, onerror=writable)


def clone(tools, url, revision, directory, path):
    """Clone into a temporary place, check out the commit and rename; returns the commit ID."""
    temporary = directory.with_name(directory.name + FETCHING)
    if temporary.exists():
        _remove(temporary)
    try:
        code, _, err = _git(tools, ["clone", "--no-checkout", "--", url, str(temporary)])
        if code != 0:
            raise _Failure(f"git clone failed: {err}")
        commit = _resolve_local(tools, temporary, revision) if revision is None or not COMMIT.fullmatch(revision) else revision
        if commit is None:
            raise _Failure(f"{revision} was not found in {url}")
        code, _, err = _git(tools, ["-c", "advice.detachedHead=false", "-C", str(temporary), "checkout", "--detach", commit])
        if code != 0:
            raise _Failure(f"git checkout failed: {err}")
        if not _present(temporary, path):
            raise _Failure(f"no CppBuild Solution at {path}")
        # Retries while scanners briefly hold the new files; never replaces a place created meanwhile.
        storage.rename_no_replace(temporary, directory)
        return commit
    finally:
        if temporary.exists():
            _remove(temporary)


def fetch(solution, values=None):
    """Clone every missing place, following each clone's own records; existing places stay unchanged."""
    values = solution.settings._data if values is None else values
    tools = solution._build_settings.tools
    failures, fetched = [], []

    def fetch_one(status):
        relative = status.directory.relative_to(solution.root).as_posix()
        if status.directory.exists():
            failures.append(FetchFailure(status.name, relative, status.url, status.revision,
                                         f"the place exists without {status.path}/project.json"))
            return status
        try:
            clone(tools, status.url, status.revision, status.directory, status.path)
        except (_Failure, OSError) as exc:
            failures.append(FetchFailure(status.name, relative, status.url, status.revision, str(exc)))
            return status
        fetched.append(status.name)
        return replace(status, present=True)

    sources = walk(solution.root, values, fetch_one)
    if failures:
        raise GitFetchError(failures)
    warnings = []
    for status in sources:
        if status.name in fetched or not (status.directory / ".git").exists():
            continue
        try:
            code, head, _ = _git(tools, ["-C", str(status.directory), "rev-parse", "HEAD"])
        except _Failure:
            break
        if code == 0 and head != status.revision:
            warnings.append(f"{status.directory.relative_to(solution.root).as_posix()} is at {head}, but {status.revision} "
                            f"is recorded for {status.url}; CppBuild leaves it unchanged")
    return FetchReport(tuple(sources), tuple(fetched), tuple(warnings))


def ensure(solution):
    """Before an operation: clone what is missing, unless SolutionBuildSettings.fetch_git is False."""
    if solution._build_settings.fetch_git and any(not s.present for s in statuses(solution)):
        fetch(solution)


# Records -----------------------------------------------------------------------------

def _recorded(values, url):
    return next((n for n, s in values.git_sources.items() if identity(s.url) == identity(url)), None)


def _place_for(solution, values, url, path, revision, *, keep):
    """(name, record) for url, cloning a new place if it is missing.

    revision None: keep=True keeps an existing record's commit (linking again);
    otherwise it means the latest commit of the default branch.
    """
    validate_url(url)
    if not values.dependency_directories:
        raise SettingsError("Linking with git clones into the first dependency directory; set dependency_directories")
    tools = solution._build_settings.tools
    place = base(solution.root, values)
    existing = _recorded(values, url)
    if path is None:
        path = values.git_sources[existing].path if existing is not None else ".cppbuild"
    validate_path(path)
    name = existing if existing is not None else unique_name(url, set(values.git_sources))
    directory = place / name
    try:
        if existing is not None:
            record = values.git_sources[existing]
            if record.path != path:
                raise SettingsError(f"{url} is already recorded with the Solution path {record.path}")
            if revision is None and keep:
                return name, record
            return name, GitSource(url, resolve(tools, url, revision, directory), path)
        if directory.exists():
            # Someone's own copy: used as it is, never switched.
            if not _present(directory, path):
                raise SettingsError(f"{directory} exists without {path}/project.json")
            return name, GitSource(url, resolve(tools, url, revision, directory), path)
        return name, GitSource(url, clone(tools, url, revision, directory, path), path)
    except _Failure as exc:
        relative = directory.relative_to(solution.root).as_posix()
        raise GitFetchError([FetchFailure(name, relative, url, revision or "HEAD", str(exc))]) from exc


def prepare_link(solution, url, revision, path):
    """(name, record, .cppbuild location) for linking url; checks every git source before anything is saved."""
    from copy import deepcopy
    solution.settings.reload()
    values = deepcopy(solution.settings._data)
    name, record = _place_for(solution, values, url, path, revision, keep=True)
    values.git_sources[name] = record
    validate(values)
    # Clones the linked Solution's own git sources and reports conflicts before anything is saved.
    if solution._build_settings.fetch_git:
        fetch(solution, values)
    else:
        walk(solution.root, values)
    return name, record, base(solution.root, values) / name / record.path


def set_source(solution, url, revision, path):
    """Record (or re-record) url at a commit; revision None is the latest commit of the default branch."""
    solution.settings.reload()
    values = solution.settings.get()
    name, record = _place_for(solution, values, url, path, revision, keep=False)
    values.git_sources[name] = record
    solution.settings.save(values)
    return next(s for s in statuses(solution) if s.name == name)


def remove_source(solution, name):
    solution.settings.reload()
    values = solution.settings.get()
    if name not in values.git_sources:
        raise KeyError(name)
    place = base(solution.root, values) / name
    for reference in values.references.values():
        if (solution.root / reference.solution_directory).resolve().is_relative_to(place):
            raise SettingsError(f"Projects still link {name}; unlink them first")
    del values.git_sources[name]
    return solution.settings.save(values)

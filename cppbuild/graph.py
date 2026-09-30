"""Validate a dependency closure before generating or starting any tools."""
from dataclasses import dataclass
import hashlib

from .models import Dependency, SettingsError


def compatible(first, second):
    """Linked trees need one configuration and one generator/compiler/architecture."""
    from .generators import resolve
    return first.configuration == second.configuration and resolve(first) == resolve(second)


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

    def registry(solution):
        if solution not in registries:
            from . import storage
            if storage.needs_migration(solution.settings.path):
                solution.settings.reload()
            registries[solution] = solution.settings._read().references
        return registries[solution]

    # The Solution that starts the operation chooses the location of a shared GUID,
    # so nested dependencies may register their own copies of the same Project.
    preferred, project_types = {}, {}
    for project in projects:
        project._check_active()
        for project_guid in registry(project.solution):
            preferred.setdefault(project_guid, project.solution)
        for project_guid, kind in project.solution._build_settings.project_types.items():
            project_types.setdefault(project_guid, kind)
    found = _dependency_directories({project.solution for project in projects})
    missing = {}

    def open_external(config):
        from .core import Solution
        if config not in external:
            external[config] = found[1].get(config) or Solution.open(config)
            if config in overrides:
                external[config].set_build_settings(overrides[config])
        return external[config]

    def locate(project, reference):
        """The Solution holding the dependency GUID, or None when it is not available."""
        project_guid = reference.project_guid
        solution = preferred.get(project_guid)
        if solution is not None:
            config = (solution.root / registries[solution][project_guid].solution_directory).resolve()
        elif project_guid in found[0]:
            # Top-level dependency directories win over locations registered by nested Solutions.
            config = found[0][project_guid]
        elif project_guid in registries[project.solution]:
            config = (project.solution.root / registries[project.solution][project_guid].solution_directory).resolve()
        else:
            return project.solution
        if not (config / "project.json").is_file():
            entry = missing.setdefault(project_guid, [reference.project_type, [], []])
            entry[1].append(f"{project.solution.root}:{project.name}")
            if config not in entry[2]:
                entry[2].append(config)
            return None
        return open_external(config)

    def visit(project, requested=None):
        project._check_active()
        if project not in loaded:
            project.settings.reload()
            loaded.add(project)
        registry(project.solution)
        settings = project._resolved_build_settings(requested, project_types)
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
            solution = locate(project, reference)
            if solution is None:
                continue
            try:
                from .references import target
                other = target(solution, reference.project_guid)
            except KeyError as exc:
                raise SettingsError(f"Missing dependency Project GUID: {reference.project_guid}") from exc
            child = visit(other, reference.project_type)
            if any(d.project.root == child.project.root and d.settings.project_type != child.settings.project_type for d in node.dependencies):
                raise SettingsError("One consumer cannot link multiple kinds of the same Project")
            if not compatible(settings, child.settings):
                raise SettingsError(f"Incompatible configuration or generation environment: {project.name} -> {other.name}")
            if child not in node.dependencies:
                node.dependencies.append(child)
        visiting.remove(key)
        nodes[key] = node
        ordered.append(node)
        return node

    roots = [visit(project) for project in projects]
    if missing:
        # Report every unavailable GUID at once, e.g. for a tool deciding what to clone.
        from .models import MissingDependenciesError, MissingDependency
        raise MissingDependenciesError(MissingDependency(guid, kind, tuple(sorted(users)), tuple(sorted(locations)))
                                       for guid, (kind, users, locations) in missing.items())
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
                    visit(project, selected or project.settings._data.initial_type)
    return roots, ordered


def _dependency_directories(solutions):
    """GUID -> Solution config found directly under the top-level dependency directories."""
    from .core import Solution
    from . import storage
    guids, opened = {}, {}
    for solution in sorted(solutions, key=lambda s: str(s.root)):
        local = {p.settings._data.guid for p in solution.projects()}
        for directory in solution.settings._data.dependency_directories:
            root = storage.contained(solution.root, directory)
            if not root.is_dir():
                continue
            for child in sorted(root.iterdir()):
                config = (child / ".cppbuild").resolve()
                manifest = config / "project.json"
                if not manifest.is_file() or storage.read_json(manifest).get("kind") != "solution":
                    continue
                if config not in opened:
                    opened[config] = Solution.open(config)
                for member in opened[config].projects():
                    project_guid = member.settings._data.guid
                    if project_guid in local:
                        raise SettingsError(f"Dependency directory contains a member Project GUID: {project_guid}")
                    previous = guids.setdefault(project_guid, config)
                    if previous != config:
                        raise SettingsError(f"The same Project GUID exists in dependency directories: {project_guid} "
                                            f"({previous.parent}, {config.parent})")
    return guids, opened

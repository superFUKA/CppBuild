"""Project operations on the Solution's CMake build tree.

Every operation writes the Solution's CMake files and configures one build tree
per generation environment; a Project is a target in it. Whole and individual
operations therefore share the same outputs, and CMake orders dependencies.
"""
from dataclasses import dataclass
from pathlib import Path
import locale
import os
import subprocess
import sys

from .models import ProjectType, SettingsError
from . import storage
from . import tooling, information, generators, output_paths, cmake_files

OWNER = "cppbuild-owner.json"
CACHE = "cppbuild-cache.cmake"


@dataclass(frozen=True)
class ProcessReport:
    command: tuple[str, ...]
    returncode: int
    output: str

    @property
    def success(self):
        return self.returncode == 0


@dataclass(frozen=True)
class UpdateReport:
    process: ProcessReport
    files: tuple[Path, ...]
    build_directory: Path
    solution_file: Path | None
    project_file: Path | None
    filters_file: Path | None
    generator: str | None = None
    compiler: object = None

    @property
    def success(self):
        return self.process.success


@dataclass(frozen=True)
class OperationReport:
    processes: tuple[ProcessReport, ...]
    artifacts: tuple[Path, ...] = ()

    @property
    def success(self):
        return bool(self.processes) and all(p.success for p in self.processes)


@dataclass(frozen=True)
class FileOperationReport:
    changed_paths: tuple[Path, ...]
    update: UpdateReport | OperationReport | None = None
    update_error: str | None = None
    pending_update: bool = True
    event_errors: tuple[str, ...] = ()
    related_updates: tuple = ()

    @property
    def success(self):
        return (not self.event_errors and self.update_error is None and (self.update is None or self.update.success)
                and all(error is None and result is not None and result.success for _, result, error in self.related_updates))


def process(command, cwd, env=None):
    command = tuple(str(arg) for arg in command)
    result = subprocess.run(command, cwd=cwd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, shell=False,
                            env=dict(os.environ) if env is None else env)
    return ProcessReport(command, result.returncode, decode_output(result.stdout))


def decode_output(data):
    """Decode tool output independently of Python's UTF-8 mode.

    One Windows stream may mix encodings (MSBuild writes UTF-8; run programs
    write anything), so each line is UTF-8 when valid, else the ANSI code page.
    """
    if os.name == "nt":
        fallback = locale.getencoding()
        lines = data.splitlines(keepends=True)
        text = "".join(_decode_line(line, fallback) for line in lines)
    else:
        text = data.decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _decode_line(line, fallback):
    try:
        return line.decode("utf-8")
    except UnicodeDecodeError:
        return line.decode(fallback, errors="replace")


# Sources ---------------------------------------------------------------------------

def _scan(project):
    files = set()
    excluded = {".cppbuild", ".git", "build", "__pycache__", ".cache", ".venv"}
    for directory in project.settings._data.source_directories:
        root = storage.contained(project.root, directory)
        if any(output_paths.generated_directory(p) for p in (root, *root.parents)):
            raise SettingsError(f"Generated directory cannot be a source root: {directory}")
        if any(part in excluded for part in root.relative_to(project.root).parts):
            raise SettingsError(f"Excluded directory cannot be a source root: {directory}")
        if not root.exists():
            continue
        if not root.is_dir():
            raise SettingsError(f"Source directory is not a directory: {root}")
        for current, directories, names in os.walk(root, followlinks=False):
            directories[:] = sorted(d for d in directories if d not in excluded
                                    and not output_paths.generated_directory(Path(current) / d)
                                    and not _directory_link(Path(current) / d))
            for name in names:
                path = Path(current) / name
                if path.is_symlink() or cmake_files.generated_file(path):
                    continue
                files.add(storage.contained(project.root, str(path)))
    return tuple(sorted(files))


def _directory_link(path):
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


# Build tree ------------------------------------------------------------------------

def tree(solution, settings):
    """The existing or future build tree of settings' generation environment."""
    toolchain = generators.resolve(settings)
    return output_paths.area(solution, solution._build_settings.intermediate_directory, toolchain.context), toolchain


def _owner(solution, toolchain):
    return {"solution_root": str(solution.root), "environment": toolchain.identity()}


def configure(plan, settings):
    """Write the CMake files and configure the build tree of settings' environment."""
    solution = plan.solution
    toolchain = generators.resolve(settings)
    build = output_paths.claim(solution, solution._build_settings.intermediate_directory, toolchain.context)
    marker = build / OWNER
    owner = _owner(solution, toolchain)
    if marker.exists():
        if storage.read_json(marker) != owner:
            raise SettingsError("Build directory ownership does not match")
    elif any(p.name != output_paths.MARKER for p in build.iterdir()):
        raise SettingsError("Refusing to use an unowned build directory")
    plan.write()
    storage.atomic_write(marker, storage.encoded(owner))
    cache = build / CACHE
    storage.atomic_write(cache, plan.cache_script(toolchain).encode("utf-8"))
    query = build / ".cmake/api/v1/query/client-cppbuild/codemodel-v2"
    query.parent.mkdir(parents=True, exist_ok=True)
    query.touch()
    result = tooling.process(settings, [*toolchain.configure(solution.root, build), "-C", cache], solution.root)
    compiler = None
    if result.success:
        compiler = generators.compiler(build)
        error = generators.verify(toolchain, compiler)
        if error is not None:
            result = ProcessReport(result.command, 1, result.output + "\n" + error)
    return result, build, toolchain, compiler


def codemodel(build):
    reply = build / ".cmake/api/v1/reply"
    indexes = sorted(reply.glob("index-*.json"))
    if not indexes:
        raise SettingsError("CMake File API did not return a codemodel")
    index = storage.read_json(indexes[-1])
    reference = index["reply"]["client-cppbuild"]["codemodel-v2"]["jsonFile"]
    return reply, storage.read_json(storage.contained(reply, reference))


def target_data(build, name, configuration):
    """The File API description of one target, or None when the tree has no such target."""
    reply, model = codemodel(build)
    for item in model["configurations"]:
        if item["name"] == configuration:
            for target in item["targets"]:
                if target["name"] == name:
                    return storage.read_json(storage.contained(reply, target["jsonFile"]))
    return None


def artifacts(build, name, configuration):
    data = target_data(build, name, configuration)
    if data is None:
        raise SettingsError(f"Target {name} ({configuration}) is missing from the CMake File API")
    return tuple((build / a["path"]).resolve() for a in data.get("artifacts", []))


def main_artifact(build, name, configuration):
    """The target's own file (exe, dll, lib or so), not its PDB or import library."""
    data = target_data(build, name, configuration)
    if data is None or "nameOnDisk" not in data:
        raise SettingsError(f"Target {name} ({configuration}) has no built file")
    for path in artifacts(build, name, configuration):
        if path.name == data["nameOnDisk"]:
            return path
    raise SettingsError(f"Target {name} ({configuration}) has no built file")


def _node_artifacts(build, node):
    if node.settings.project_type == ProjectType.INTERFACE_LIBRARY:
        return ()
    return artifacts(build, cmake_files.target_name(node.project, node.settings.project_type), node.settings.configuration)


def target(project, settings):
    return cmake_files.target_name(project, settings.project_type)


def buildable(plan, project, settings):
    # An interface library is a build target only when it lists files for the IDE.
    return settings.project_type != ProjectType.INTERFACE_LIBRARY or bool(plan.files[project.settings._data.guid])


def closure(node):
    result, seen = [], set()

    def visit(item):
        if item.key not in seen:
            seen.add(item.key)
            for child in item.dependencies:
                visit(child)
            result.append(item)
    visit(node)
    return result


def build_targets(settings, build, names, parallel=None):
    command = ["cmake", "--build", build, "--config", settings.configuration]
    if names is not None:
        command += ["--target", *names]
    command += ["--parallel", parallel or settings.parallel]
    return tooling.process(settings, command, build)


# Operations ------------------------------------------------------------------------

def project_plan(project):
    from . import workspace
    project._check_active()
    plan = workspace.plan(project.solution, fetch=True)
    node = next(n for n in plan.roots if n.project is project)
    plan.check(lambda n: n.settings.configuration)
    return plan, node


def in_tree(node, settings):
    """Whether the node's own settings select the tree configured with settings."""
    return information.tree_key(node.project, node.settings) == information.tree_key(node.project, settings)


def generated(plan, files_success, settings=None):
    """Record the generation result for the members whose own settings select this tree.

    Whole-Solution operations (settings None) already require every member to match.
    """
    for node in plan.roots:
        if settings is None or in_tree(node, settings):
            information.generated(node.project, node.settings, plan.files[node.project.settings._data.guid], files_success)


def _update(plan, node):
    project, settings = node.project, node.settings
    result, build, toolchain, compiler = configure(plan, settings)
    files = plan.files[project.settings._data.guid]
    solution_file = project_file = filters_file = None
    if result.success and toolchain.visual_studio:
        solution_file = build / f"{plan.solution.settings._data.name}{toolchain.solution_suffix}"
        # add_subdirectory places a member's build files at its source path below the tree.
        folder = build / project.root.relative_to(plan.solution.root)
        candidate = folder / f"{target(project, settings)}.vcxproj"
        if candidate.is_file():
            project_file, filters_file = candidate, folder / f"{target(project, settings)}.vcxproj.filters"
    report = UpdateReport(result, files, build, solution_file, project_file, filters_file, toolchain.generator, compiler)
    project._last_update = report
    generated(plan, result.success, settings)
    return report


def update(project):
    project._last_update = None
    plan, node = project_plan(project)
    with plan.lock():
        return _update(plan, node)


def operate(project, operation):
    project._last_update = None
    if operation == "clean":
        return clean(project)
    plan, node = project_plan(project)
    settings = node.settings
    if operation == "run" and settings.project_type != ProjectType.EXECUTABLE:
        raise SettingsError("Only executable Projects can run")
    with plan.lock():
        report = _update(plan, node)
        results = [report.process]
        if not report.success:
            return OperationReport(tuple(results))
        build = report.build_directory
        if operation == "rebuild":
            removed = clean_targets(project.solution, settings, [target(project, settings)])
            results += removed
            if not all(r.success for r in removed):
                return OperationReport(tuple(results))
        # An interface library's target does not build what it links, so name every
        # buildable target of the closure; the others' dependencies follow anyway.
        names = [target(item.project, item.settings) for item in closure(node)
                 if buildable(plan, item.project, item.settings)]
        if names:
            results.append(build_targets(settings, build, names))
        success = results[-1].success
        for item in closure(node):
            if in_tree(item, settings):
                information.built(item.project, item.settings, _node_artifacts(build, item) if success else (), success)
        if not success:
            return OperationReport(tuple(results))
        produced = _node_artifacts(build, node)
        if operation == "run":
            from .execution import execute
            command, cwd, env = run_command(project, settings, build, closure(node))
            return execute([(command, cwd, env)], results, produced, wait=settings.run_wait)
        return OperationReport(tuple(results), produced)


def clean(project):
    project._check_active()
    project.settings.reload()
    settings = project._resolved_build_settings()
    # The Solution's operation lock, as every operation on its shared build tree takes.
    with storage.operation_lock(project.solution.root, project.root):
        results = clean_targets(project.solution, settings, [target(project, settings)])
    cleaned(project, settings, all(r.success for r in results))
    return OperationReport(tuple(results))


def cleaned(project, settings, success):
    information.invalidate(project, bump=False)
    try:
        project._build_signature = information.signature(project, settings)
    except SettingsError:
        project._build_signature = None
    project._build_state = "cleaned" if success else "failed"
    if success:
        project._known_artifacts = ()


def clean_targets(solution, settings, names):
    """Clean targets (None: the whole tree) of the existing tree for one configuration.

    Never configures: an incomplete or foreign tree is a failed result. Only the
    named Projects' outputs are removed, never those of their dependencies.
    """
    try:
        build, toolchain = tree(solution, settings)
        if not build.exists() or not any(p.name != output_paths.MARKER for p in build.iterdir()):
            return [ProcessReport((), 0, f"Nothing to clean: {build}")]
        marker = build / OWNER
        if not marker.is_file() or storage.read_json(marker) != _owner(solution, toolchain):
            raise SettingsError("Build directory ownership does not match; clean was not run")
        generated = (build / f"{solution.settings._data.name}{toolchain.solution_suffix}") if toolchain.visual_studio else build / "build.ninja"
        if not (build / "CMakeCache.txt").is_file() or not generated.is_file():
            raise SettingsError("Existing build tree is incomplete; clean was not run")
        configuration = settings.configuration
        if names is None:
            if not toolchain.visual_studio:
                # Ninja's clean tool runs without regenerating build.ninja; the clean target would not.
                return [process([toolchain.make_program, "-C", build, "-f", f"build-{configuration}.ninja", "-t", "clean"],
                                build, env=toolchain.env)]
            return [_msbuild_clean(build, toolchain, configuration, build / "ALL_BUILD.vcxproj", references=True)]
        existing = [name for name in names if target_data(build, name, configuration) is not None]
        if not existing:
            return [ProcessReport((), 0, "Nothing to clean: " + ", ".join(names) + " not generated")]
        if toolchain.visual_studio:
            return [_msbuild_clean(build, toolchain, configuration,
                                   storage.contained(build, target_data(build, name, configuration)["paths"]["build"]) / f"{name}.vcxproj",
                                   references=False)
                    for name in existing]
        return [_remove_outputs(build, name, configuration) for name in existing]
    except (OSError, ValueError, KeyError) as exc:
        return [ProcessReport((), 1, f"Cannot clean: {exc}")]


def _msbuild_clean(build, toolchain, configuration, project_file, *, references):
    """MSBuild's Clean target, called directly: `cmake --build` may first build ZERO_CHECK,
    which reconfigures; Clean itself never runs custom build rules."""
    import re
    cache = (build / "CMakeCache.txt").read_text(encoding="utf-8", errors="replace")
    instance = re.search(r"^CMAKE_GENERATOR_INSTANCE:[A-Z]+=(.+)$", cache, re.M)
    platform = re.search(r"^CMAKE_GENERATOR_PLATFORM:[A-Z]+=(.+)$", cache, re.M)
    # The MSBuild of the Visual Studio instance CMake configured with, as `cmake --build` uses.
    candidates = [Path(instance.group(1).strip()) / "MSBuild/Current/Bin" / sub / "MSBuild.exe"
                  for sub in ("amd64", "")] if instance else []
    msbuild = next((p for p in candidates if p.is_file()), None)
    if msbuild is None or platform is None or not project_file.is_file():
        raise SettingsError("Existing build tree is incomplete; clean was not run")
    command = [msbuild, project_file, "-t:Clean", f"-p:Configuration={configuration}",
               f"-p:Platform={platform.group(1).strip()}", "-nologo", "-v:m"]
    if not references:
        command.append("-p:BuildProjectReferences=false")
    return process(command, build, env=toolchain.env)


def _remove_outputs(build, name, configuration):
    """Ninja has no single-target clean (its clean tool also removes dependencies):
    remove the target's own artifacts and object directory; Ninja rebuilds what is missing."""
    import shutil
    data = target_data(build, name, configuration)
    removed = []
    for path in artifacts(build, name, configuration):
        if path.is_file():
            path.unlink()
            removed.append(str(path))
    objects = storage.contained(build, data["paths"]["build"]) / "CMakeFiles" / f"{name}.dir" / configuration
    if objects.is_dir():
        shutil.rmtree(objects)
        removed.append(str(objects))
    return ProcessReport((), 0, f"Cleaned {name} ({configuration}): " + (", ".join(removed) or "nothing built"))


# Running ---------------------------------------------------------------------------

def _library_path_variable():
    if os.name == "nt":
        return "PATH"
    return "DYLD_LIBRARY_PATH" if sys.platform == "darwin" else "LD_LIBRARY_PATH"


def runtime_environment(nodes, settings, build):
    env = dict(generators.resolve(settings).env)
    directories = []
    for node in nodes:
        if node.settings.project_type == ProjectType.SHARED_LIBRARY:
            name = cmake_files.target_name(node.project, node.settings.project_type)
            directory = str(main_artifact(build, name, node.settings.configuration).parent)
            if directory not in directories:
                directories.append(directory)
    # DLLs are placed beside executables unless a Project chose another artifact_directory;
    # PATH (or the build-tree RPATH elsewhere) covers that case.
    variable = _library_path_variable()
    env[variable] = os.pathsep.join(p for p in [*directories, env.get(variable, "")] if p)
    return env


def check_runnable(build, settings, name):
    detected = generators.compiler(build)
    architecture = (detected.architecture if detected else None) or generators.resolve(settings).architecture
    if not generators.runnable(architecture):
        raise SettingsError(f"{name} is built for {architecture} and cannot run on this host")


def run_command(project, settings, build, nodes):
    executable = main_artifact(build, target(project, settings), settings.configuration)
    if not executable.is_file():
        raise SettingsError("Expected one built executable")
    check_runnable(build, settings, project.name)
    return [executable, *settings.run_arguments], project.root, runtime_environment(nodes, settings, build)


# Files -----------------------------------------------------------------------------

def file_path(project, value):
    project._check_active()
    path = storage.contained(project.root, str(value))
    if path == project.root or any(part in {".cppbuild", ".git", "build"} for part in path.relative_to(project.root).parts):
        raise SettingsError("File operations cannot modify management/build directories")
    if any(output_paths.generated_directory(p) for p in (path, *path.parents)):
        raise SettingsError("File operations cannot modify generated output directories")
    if path == project.root / cmake_files.ENTRY:
        raise SettingsError("The Project's CMakeLists.txt is generated by CppBuild")
    return path


def file_report(project, paths, auto_update):
    project._last_update = None
    information.invalidate(project)
    return project.solution._events.file_changed(project, paths, auto_update)

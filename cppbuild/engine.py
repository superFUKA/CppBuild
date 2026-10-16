"""Project operations on the Solution's CMake build tree.

Every operation writes the Solution's CMake files and configures one build tree
per generation environment; a Project is a target in it. Whole and individual
operations therefore share the same outputs, and CMake orders dependencies.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import locale
import os
import subprocess
import sys

from .models import ProjectType, SettingsError
from . import storage
from . import tooling, information, generators, output_paths, cmake_files, logs

_log = logs.logger("engine")

OWNER = "cppbuild-owner.json"
CACHE = "cppbuild-cache.cmake"
# What the last successful configure of a tree was given and read (see configure()).
CONFIGURED = "cppbuild-configured.json"
CONFIGURED_VERSION = 1


@dataclass(frozen=True)
class ProcessReport:
    command: tuple[str, ...]
    returncode: int
    output: str
    # True when CMake was not run because the tree was already configured from the same input.
    skipped: bool = False

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
    with logs.command(command, cwd, env) as finished:
        result = subprocess.run(command, cwd=cwd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, shell=False,
                                env=dict(os.environ) if env is None else env)
        output = decode_output(result.stdout)
        finished(result.returncode, output)
    return ProcessReport(command, result.returncode, output)


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
    # The generated CMake collects the same files again (cmake_files._collect_function).
    excluded = set(cmake_files.SCAN_EXCLUDED)
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
    return {"solution": str(solution.config_directory), "environment": toolchain.identity()}


def configure(plan, settings):
    """Write the CMake files and configure the build tree of settings' environment.

    CMake is not run when the tree's last successful configure had the same input
    (generated files, cache script, command, collected sources, tools) and no file
    CMake read has changed since (File API cmakeFiles-v1), as CMake itself would not.
    """
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
    cache = build / CACHE
    cache_text = plan.cache_script(toolchain)
    command = [*toolchain.configure(solution.config_directory, build), "-C", cache]
    fingerprint = _fingerprint(plan, settings, command, cache_text)
    _log.debug("configure %s with %s (fingerprint %s)", build, toolchain.generator, fingerprint)
    if _configured(plan, build, fingerprint):
        compiler = generators.compiler(build)
        mismatch = generators.verify(toolchain, compiler)
        _log.debug("build tree is current; compiler check: %s", mismatch or "matches")
        if mismatch is None:
            result = ProcessReport(tuple(str(c) for c in command), 0,
                                   "CMake was not run: the build tree is already configured from the same input.\n",
                                   skipped=True)
            return result, build, toolchain, compiler
    # A configure stopped halfway must not look current.
    (build / CONFIGURED).unlink(missing_ok=True)
    plan.write()
    storage.atomic_write(marker, storage.encoded(owner))
    storage.atomic_write(cache, cache_text.encode("utf-8"))
    generators.request_file_api(build, "codemodel-v2", "toolchains-v1", "cmakeFiles-v1")
    result = tooling.process(settings, command, generators.configure_directory(build))
    compiler = None
    if result.success:
        compiler = generators.compiler(build)
        error = generators.verify(toolchain, compiler)
        if error is not None:
            result = ProcessReport(result.command, 1, result.output + "\n" + error)
    if result.success:
        inputs = _inputs(build)
        if inputs is not None:
            storage.atomic_write(build / CONFIGURED, storage.encoded(
                {"version": CONFIGURED_VERSION, "fingerprint": fingerprint, "inputs": inputs}))
    return path_hint(result, build, toolchain), build, toolchain, compiler


def _fingerprint(plan, settings, command, cache_text):
    """Everything CppBuild gives a configure, so a change to any of it configures again."""
    data = {"version": CONFIGURED_VERSION, "format": cmake_files.FORMAT,
            "command": [str(c) for c in command], "cache": cache_text, "tools": asdict(settings.tools),
            "documents": {str(path): hashlib.sha256(text.encode("utf-8")).hexdigest()
                          for path, text in sorted(plan.documents().items(), key=lambda item: str(item[0]))},
            # The generated CMake collects these again, and a Visual Studio project lists them.
            "files": {guid: [str(f) for f in files] for guid, files in sorted(plan.files.items())}}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()


def _stat(path):
    try:
        result = Path(path).stat()
    except OSError:
        return None
    return [result.st_mtime_ns, result.st_size]


def _inputs(build):
    """[path, [mtime, size]] of every file the last configure read (File API); None if unknown."""
    found = generators.file_api_reply(build, "cmakeFiles-v1")
    if found is None:
        return None
    data = found[1]
    source = Path(data.get("paths", {}).get("source", ""))
    result = []
    for item in data.get("inputs", []):
        if item.get("isGenerated"):
            continue  # written by CMake into the tree
        path = Path(item["path"])
        path = path if path.is_absolute() else source / path
        result.append([str(path), _stat(path)])
    return result


def _configured(plan, build, fingerprint):
    try:
        record = storage.read_json(build / CONFIGURED)
    except (OSError, SettingsError) as exc:
        _log.debug("configure needed: no record of the last configure (%s)", exc)
        return False
    if (not isinstance(record, dict) or record.get("version") != CONFIGURED_VERSION
            or record.get("fingerprint") != fingerprint or not (build / "CMakeCache.txt").is_file()
            or generators.file_api_reply(build, "codemodel-v2") is None):
        _log.debug("configure needed: input fingerprint, CMakeCache.txt or File API reply differs")
        return False
    for path, text in plan.documents().items():
        try:
            if Path(path).read_bytes() != text.encode("utf-8"):
                _log.debug("configure needed: generated file changed: %s", path)
                return False
        except OSError as exc:
            _log.debug("configure needed: generated file unreadable: %s (%s)", path, exc)
            return False
    for item in record.get("inputs", []):
        if not (isinstance(item, list) and len(item) == 2 and _stat(item[0]) == item[1]):
            _log.debug("configure needed: CMake input changed: %s", item[0] if isinstance(item, list) and item else item)
            return False
    return True


def codemodel(build):
    found = generators.file_api_reply(build, "codemodel-v2")
    if found is None:
        raise SettingsError("CMake File API did not return a codemodel")
    return found


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
    result = tooling.process(settings, command, build)
    return result if result.success else path_hint(result, build, generators.resolve(settings))


# Measured without long path support (CMake 4.2): VS failed in CMake's own
# compiler checks from about 138 characters of build tree, Ninja from about 172.
LONG_BUILD_TREE = {True: 130, False: 155}


def path_hint(result, build, toolchain):
    """Name the likely cause when a long Windows build tree fails cryptically."""
    length = len(str(build))
    if result.success or os.name != "nt" or length <= LONG_BUILD_TREE[toolchain.visual_studio]:
        return result
    hint = (f"Hint: the build tree path has {length} characters ({build}). Windows path length limits "
            "may have caused this failure; set a shorter intermediate_directory in SolutionBuildSettings.")
    return ProcessReport(result.command, result.returncode, result.output + "\n" + hint)


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
        folder = build / project.name  # the binary directory the Solution entry gives it
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
    with storage.operation_lock(project.solution.config_directory):
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
            logs.removed(path)
            removed.append(str(path))
    objects = storage.contained(build, data["paths"]["build"]) / "CMakeFiles" / f"{name}.dir" / configuration
    if objects.is_dir():
        shutil.rmtree(objects)
        logs.removed(objects, "directory")
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
    return path


def file_report(project, paths, auto_update):
    project._last_update = None
    information.invalidate(project)
    return project.solution._events.file_changed(project, paths, auto_update)

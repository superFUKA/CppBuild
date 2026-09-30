"""Independent VS2022 generation. Dependency binaries remain owned by their Project."""
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from dataclasses import dataclass
from pathlib import Path
import os
import subprocess

from .models import ProjectType, SettingsError
from . import storage
from . import tooling, information


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
                            stderr=subprocess.STDOUT, errors="replace", text=True, shell=False,
                            env=dict(os.environ) if env is None else env)
    return ProcessReport(command, result.returncode, result.stdout)


def _quote(value):
    value = str(value)
    # Semicolons and generator expressions need a separate data model; don't reinterpret them.
    if any(x in value for x in (";", "$<", "\x00", "\n", "\r")):
        raise SettingsError(f"Unsupported CMake literal: {value!r}")
    delimiter = "="
    while f"]{delimiter}]" in value:
        delimiter += "="
    return f"[{delimiter}[{value}]{delimiter}]"


def _path(value):
    return _quote(Path(value).as_posix())


def _scan(project):
    files = set()
    excluded = {".cppbuild", ".git", "build", "__pycache__", ".cache", ".venv"}
    for directory in project.settings._data.source_directories:
        root = storage.contained(project.root, directory)
        if any(part in excluded for part in root.relative_to(project.root).parts):
            raise SettingsError(f"Excluded directory cannot be a source root: {directory}")
        if not root.exists():
            continue
        if not root.is_dir():
            raise SettingsError(f"Source directory is not a directory: {root}")
        for current, directories, names in os.walk(root, followlinks=False):
            directories[:] = sorted(d for d in directories if d not in excluded
                                    and not _directory_link(Path(current) / d))
            for name in names:
                path = Path(current) / name
                if path.is_symlink():
                    continue
                files.add(storage.contained(project.root, str(path)))
    return tuple(sorted(files))


def _directory_link(path):
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _locations(project, settings):
    # Debug/Release intentionally share a multi-config cache. Architecture and kind do not.
    context = f"vs2022-{settings.architecture}-{settings.project_type.value}"
    base = storage.contained(project.root, ".cppbuild")
    source = storage.contained(base, f"generated/{context}")
    build = storage.contained(base, f"build/{context}")
    return source, build


def _cmake(project, settings, files, dependencies=()):
    kind = settings.project_type
    name = project.name
    lines = ["cmake_minimum_required(VERSION 3.24)",
             f"project({name} LANGUAGES CXX)",
             "set(CMAKE_SUPPRESS_REGENERATION ON)",
             'set(CMAKE_CONFIGURATION_TYPES "Debug;Release;RelWithDebInfo;MinSizeRel" CACHE STRING "" FORCE)']
    if kind == ProjectType.TEST:
        # The online and offline paths use the same fixed archive and digest.
        archive = _quote('https://github.com/google/googletest/archive/refs/tags/v1.14.0.zip')
        if settings.googletest_archive is not None:
            source = (project.root / settings.googletest_archive).resolve()
            if not source.is_file():
                raise SettingsError("googletest_archive must be an existing v1.14.0 ZIP")
            archive = _path(source)
        lines += ['include(FetchContent)',
                  'set(BUILD_GMOCK OFF CACHE BOOL "" FORCE)',
                  'set(INSTALL_GTEST OFF CACHE BOOL "" FORCE)',
                  'set(gtest_force_shared_crt ON CACHE BOOL "" FORCE)',
                  f'FetchContent_Declare(googletest URL {archive} URL_HASH SHA256=1f357c27ca988c3f7c6b4bf68a9395005ac6761f034046e9dde0896e3aba00e4 DOWNLOAD_EXTRACT_TIMESTAMP TRUE TIMEOUT 60 INACTIVITY_TIMEOUT 15)',
                  'FetchContent_MakeAvailable(googletest)', 'enable_testing()']
    listed = "\n  ".join(_path(p) for p in files)
    if kind == ProjectType.INTERFACE_LIBRARY:
        lines += [f"add_library({name} INTERFACE)", f"add_custom_target({name}_files SOURCES\n  {listed}\n)"]
    else:
        if not any(p.suffix.lower() in {".cpp", ".cxx", ".cc"} for p in files):
            raise SettingsError(f"No C++ sources found for {name}")
        declaration = f"add_executable({name})" if kind in {ProjectType.EXECUTABLE, ProjectType.TEST} else f"add_library({name} {'STATIC' if kind == ProjectType.STATIC_LIBRARY else 'SHARED'})"
        lines += [declaration, f"target_sources({name} PRIVATE\n  {listed}\n)",
                  f"set_target_properties({name} PROPERTIES CXX_STANDARD {settings.cpp_standard} CXX_STANDARD_REQUIRED YES CXX_EXTENSIONS NO)"]
        display_only = [p for p in files if p.suffix.lower() not in {".cpp", ".cc", ".cxx"}]
        if display_only:
            lines += ["set_source_files_properties(" + " ".join(_path(p) for p in display_only) + " PROPERTIES HEADER_FILE_ONLY TRUE)"]
        definitions = project.settings._data.types[kind].compile_definitions
        if definitions:
            lines += [f"target_compile_definitions({name} PRIVATE " + " ".join(_quote(d) for d in definitions) + ")"]
    scope = "INTERFACE" if kind == ProjectType.INTERFACE_LIBRARY else "PUBLIC"
    data = project.settings._data.types[kind]
    for directory in data.include_directories:
        lines += [f"target_include_directories({name} {scope} {_path(storage.contained(project.root, directory))})"]
    if data.public_definitions:
        lines += [f"target_compile_definitions({name} {scope} " + " ".join(_quote(d) for d in data.public_definitions) + ")"]
    imported = set()
    def import_node(node):
        alias = "dep_" + node.label
        if alias in imported:
            return alias
        imported.add(alias)
        child_kind = node.settings.project_type
        child_data = node.project.settings._data.types[child_kind]
        if child_kind == ProjectType.INTERFACE_LIBRARY:
            lines.append(f"add_library({alias} INTERFACE IMPORTED)")
        else:
            imported_kind = "STATIC" if child_kind == ProjectType.STATIC_LIBRARY else "SHARED"
            lines.append(f"add_library({alias} {imported_kind} IMPORTED)")
            _, child_build = _locations(node.project, node.settings)
            for configuration in ("Debug", "Release", "RelWithDebInfo", "MinSizeRel"):
                artifacts = _artifacts(node.project, replace(node.settings, configuration=configuration), child_build)
                suffix = ".lib" if child_kind == ProjectType.STATIC_LIBRARY else ".dll"
                location = next((p for p in artifacts if p.suffix.lower() == suffix), None)
                if location is None:
                    raise SettingsError(f"Missing {suffix} metadata for {node.project.name}; update dependency first")
                lines.append(f"set_property(TARGET {alias} PROPERTY IMPORTED_LOCATION_{configuration.upper()} {_path(location)})")
                if child_kind == ProjectType.SHARED_LIBRARY:
                    implib = next((p for p in artifacts if p.suffix.lower() == ".lib"), None)
                    if implib is None:
                        raise SettingsError(f"Missing import library metadata for {node.project.name}")
                    lines.append(f"set_property(TARGET {alias} PROPERTY IMPORTED_IMPLIB_{configuration.upper()} {_path(implib)})")
        for directory in child_data.include_directories:
            include = storage.contained(node.project.root, directory)
            if include.is_dir():
                lines.append(f"target_include_directories({alias} INTERFACE {_path(include)})")
        if child_data.public_definitions:
            lines.append(f"target_compile_definitions({alias} INTERFACE " + " ".join(_quote(d) for d in child_data.public_definitions) + ")")
        for child in node.dependencies:
            child_alias = import_node(child)
            lines.append(f"target_link_libraries({alias} INTERFACE {child_alias})")
        from .dependencies import cmake as external_cmake
        for key, value in node.project.settings._data.dependencies.items():
            lines.extend(external_cmake(node.project, node.settings, key, value, alias, "INTERFACE"))
        return alias
    for dependency in dependencies:
        alias = import_node(dependency)
        lines.append(f"target_link_libraries({name} {scope} {alias})")
    from .dependencies import cmake as external_cmake
    for key, value in project.settings._data.dependencies.items():
        lines.extend(external_cmake(project, settings, key, value, name, scope))
    if kind != ProjectType.INTERFACE_LIBRARY:
        headers = [_path(storage.contained(project.root, h)) for h in project.settings._data.project_headers]
        headers += [_quote("<" + h + ">") for h in project.settings._data.system_headers]
        if headers:
            lines.append(f"target_precompile_headers({name} PRIVATE " + " ".join(headers) + ")")
    if kind == ProjectType.TEST:
        # CMake 4.2 requests a JSON path in UTF-8, but GoogleTest's narrow fopen
        # on Windows cannot open that path under a legacy ANSI code page.
        # Disable only discovery's JSON output; CMake also accepts the test list
        # on stdout. CTest's own JUnit writer still produces our result file.
        lines += [f'target_link_libraries({name} PRIVATE GTest::gtest_main)', 'include(GoogleTest)',
                  'set(cppbuild_discovery_args)',
                  'if(CMAKE_VERSION VERSION_GREATER_EQUAL 4.2)',
                  '  set(cppbuild_discovery_args DISCOVERY_EXTRA_ARGS "--gtest_output=")', 'endif()',
                  f'gtest_discover_tests({name} DISCOVERY_MODE PRE_TEST WORKING_DIRECTORY {_path(project.root)} ${{cppbuild_discovery_args}})']
    if files:
        lines += [f"source_group(TREE {_path(project.root)} PREFIX \"\" FILES\n  {listed}\n)"]
    return "\n".join(lines) + "\n"


def _target(project, settings):
    return project.name + ("_files" if settings.project_type == ProjectType.INTERFACE_LIBRARY else "")


def _prepare(project):
    project._check_active()
    project._last_update = None
    project.settings.reload()
    settings = project._resolved_build_settings()
    source, build = _locations(project, settings)
    return settings, source, build


def _generate(project, settings, source, build, dependencies=()):
    project._last_update = None
    files = _scan(project)
    text = _cmake(project, settings, files, dependencies)
    marker = build / "cppbuild-owner.json"
    owner = {"project_root": str(project.root), "source": str(source),
             "architecture": settings.architecture, "type": settings.project_type.value}
    if marker.exists():
        if storage.read_json(marker) != owner:
            raise SettingsError("Build directory ownership does not match")
    elif build.exists() and any(build.iterdir()):
        raise SettingsError("Refusing to use an unowned build directory")
    storage.atomic_write(marker, storage.encoded(owner))
    cmake = source / "CMakeLists.txt"
    if not cmake.exists() or cmake.read_text(encoding="utf-8") != text:
        storage.atomic_write(cmake, text.encode("utf-8"))
    query = build / ".cmake/api/v1/query/client-cppbuild/codemodel-v2"
    query.parent.mkdir(parents=True, exist_ok=True)
    query.touch()
    result = tooling.process(settings, ["cmake", "-S", source, "-B", build, "-G", "Visual Studio 17 2022", "-A", settings.architecture], project.root)
    target = _target(project, settings)
    report = UpdateReport(result, files, build,
                          build / f"{project.name}.sln" if result.success else None,
                          build / f"{target}.vcxproj" if result.success else None,
                          build / f"{target}.vcxproj.filters" if result.success else None)
    project._last_update = report
    information.generated(project, settings, report)
    return report


def update(project):
    from .graph import resolve
    project._last_update = None
    roots, nodes = resolve([project])
    root = roots[0]
    source, build = _locations(project, root.settings)
    # Individual update reads dependency metadata, but never scans/regenerates it.
    with lock_nodes(nodes):
        return _generate(project, root.settings, source, build, root.dependencies)


@contextmanager
def lock_nodes(nodes):
    with ExitStack() as stack:
        for path in sorted({_locations(n.project, n.settings)[0] for n in nodes}):
            stack.enter_context(storage.write_lock(path))
        yield


def _artifacts(project, settings, build):
    if settings.project_type == ProjectType.INTERFACE_LIBRARY:
        return ()
    reply = build / ".cmake/api/v1/reply"
    indexes = sorted(reply.glob("index-*.json"))
    if not indexes:
        raise SettingsError("CMake File API did not return a codemodel")
    index = storage.read_json(indexes[-1])
    reference = index["reply"]["client-cppbuild"]["codemodel-v2"]["jsonFile"]
    model = storage.read_json(storage.contained(reply, reference))
    for configuration in model["configurations"]:
        if configuration["name"] == settings.configuration:
            for target in configuration["targets"]:
                if target["name"] == project.name:
                    data = storage.read_json(storage.contained(reply, target["jsonFile"]))
                    return tuple(storage.contained(build, a["path"]) for a in data.get("artifacts", []))
    raise SettingsError("Target/configuration missing from CMake File API")


def operate(project, operation):
    from .graph import resolve
    project._last_update = None
    if operation == "clean":
        settings, source, build = _prepare(project)
        with storage.write_lock(source):
            return OperationReport((clean_target(project, settings, build),))
    roots, nodes = resolve([project])
    root = roots[0]
    settings = root.settings
    source, build = _locations(project, settings)
    if operation == "run" and settings.project_type != ProjectType.EXECUTABLE:
        raise SettingsError("Only executable Projects can run")
    with lock_nodes(nodes):
        results = []
        for node in nodes:
            node_source, node_build = _locations(node.project, node.settings)
            generated = _generate(node.project, node.settings, node_source, node_build, node.dependencies)
            results.append(generated.process)
            if not generated.success:
                return OperationReport(tuple(results))
        if operation == "rebuild":
            # Dependencies are IMPORTED, so this tree cannot clean their binaries.
            result = clean_target(project, settings, build)
            results.append(result)
            if not result.success:
                return OperationReport(tuple(results))
        for node in nodes:
            _, node_build = _locations(node.project, node.settings)
            result = tooling.process(node.settings, ["cmake", "--build", node_build, "--config", node.settings.configuration,
                              "--target", _target(node.project, node.settings), "--parallel", node.settings.parallel], node.project.root)
            results.append(result)
            information.built(node.project, node.settings, _artifacts(node.project, node.settings, node_build) if result.success else (), result.success)
            if not result.success:
                return OperationReport(tuple(results))
        artifacts = _artifacts(project, settings, build)
        if operation == "run":
            executables = [p for p in artifacts if p.suffix.lower() == ".exe"]
            if len(executables) != 1 or not executables[0].is_file():
                raise SettingsError("Expected one built executable")
            from .execution import execute
            command, cwd, env = run_command(project, settings, artifacts, nodes)
            return execute([(command, cwd, env)], results, artifacts, wait=settings.run_wait)
        return OperationReport(tuple(results), artifacts)


def clean_target(project, settings, build):
    # Never configure to clean: use only the existing, owned multi-config tree.
    source, expected_build = _locations(project, settings)
    marker = build / "cppbuild-owner.json"
    owner = {"project_root": str(project.root), "source": str(source),
             "architecture": settings.architecture, "type": settings.project_type.value}
    try:
        if build != expected_build:
            raise SettingsError("Unexpected clean build directory")
        if not build.exists() or (build.is_dir() and not any(build.iterdir())):
            result = ProcessReport((), 0, f"Nothing to clean: {build}")
        else:
            if not marker.is_file() or storage.read_json(marker) != owner:
                raise SettingsError("Build directory ownership does not match; clean was not run")
            if not (build / "CMakeCache.txt").is_file() or not (build / f"{_target(project, settings)}.vcxproj").is_file():
                raise SettingsError("Existing build tree is incomplete; clean was not run")
            result = tooling.process(settings, ["cmake", "--build", build, "--config", settings.configuration,
                            "--target", _target(project, settings), "--", "/t:Clean", "/p:BuildProjectReferences=false"], project.root)
    except (OSError, SettingsError) as exc:
        result = ProcessReport((), 1, f"Cannot clean {build}: {exc}")
    information.invalidate(project, bump=False)
    try:
        project._build_signature = information.signature(project, settings)
    except SettingsError:
        project._build_signature = None
    project._build_state = "cleaned" if result.success else "failed"
    if result.success:
        project._known_artifacts = ()
    return result


def runtime_environment(nodes, settings=None):
    env = tooling.environment(settings.tools) if settings is not None else os.environ.copy()
    dll_directories = []
    for node in nodes:
        if node.settings.project_type == ProjectType.SHARED_LIBRARY:
            _, build = _locations(node.project, node.settings)
            dll_directories.extend(str(p.parent) for p in _artifacts(node.project, node.settings, build) if p.suffix.lower() == ".dll")
    env["PATH"] = os.pathsep.join([*dll_directories, env.get("PATH", "")])
    return env


def run_command(project, settings, artifacts, nodes):
    executables = [p for p in artifacts if p.suffix.lower() == ".exe"]
    if len(executables) != 1 or not executables[0].is_file():
        raise SettingsError("Expected one built executable")
    return [executables[0], *settings.run_arguments], project.root, runtime_environment(nodes, settings)


def run_artifact(project, settings, artifacts, nodes):
    command, cwd, env = run_command(project, settings, artifacts, nodes)
    return process(command, cwd, env=env)


def file_path(project, value):
    project._check_active()
    path = storage.contained(project.root, str(value))
    if path == project.root or any(part in {".cppbuild", ".git", "build"} for part in path.relative_to(project.root).parts):
        raise SettingsError("File operations cannot modify management/build directories")
    return path


def file_report(project, paths, auto_update):
    project._last_update = None
    information.invalidate(project)
    return project.solution._events.file_changed(project, paths, auto_update)

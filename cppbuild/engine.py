"""Independent, dependency-free VS2022 generation and execution.

Whole-solution integration and linked targets deliberately do not live here yet.
"""
from dataclasses import dataclass
from pathlib import Path
import os
import subprocess

from .models import ProjectType, SettingsError
from . import storage


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
    update: UpdateReport | None = None
    update_error: str | None = None
    pending_update: bool = True

    @property
    def success(self):
        return self.update_error is None and (self.update is None or self.update.success)


def process(command, cwd):
    command = tuple(str(arg) for arg in command)
    result = subprocess.run(command, cwd=cwd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, errors="replace", text=True, shell=False)
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


def _cmake(project, settings, files):
    kind = settings.project_type
    if kind == ProjectType.TEST:
        raise NotImplementedError("GoogleTest is scheduled for M5")
    name = project.name
    lines = ["cmake_minimum_required(VERSION 3.24)",
             f"project({name} LANGUAGES CXX)",
             "set(CMAKE_SUPPRESS_REGENERATION ON)",
             'set(CMAKE_CONFIGURATION_TYPES "Debug;Release;RelWithDebInfo;MinSizeRel" CACHE STRING "" FORCE)']
    listed = "\n  ".join(_path(p) for p in files)
    if kind == ProjectType.HEADER_ONLY:
        lines += [f"add_library({name} INTERFACE)", f"add_custom_target({name}_files SOURCES\n  {listed}\n)"]
    else:
        if not any(p.suffix.lower() in {".cpp", ".cxx", ".cc"} for p in files):
            raise SettingsError(f"No C++ sources found for {name}")
        declaration = f"add_executable({name})" if kind == ProjectType.EXECUTABLE else f"add_library({name} {'STATIC' if kind == ProjectType.STATIC_LIBRARY else 'SHARED'})"
        lines += [declaration, f"target_sources({name} PRIVATE\n  {listed}\n)",
                  f"set_target_properties({name} PROPERTIES CXX_STANDARD {settings.cpp_standard} CXX_STANDARD_REQUIRED YES CXX_EXTENSIONS NO)"]
        display_only = [p for p in files if p.suffix.lower() not in {".cpp", ".cc", ".cxx"}]
        if display_only:
            lines += ["set_source_files_properties(" + " ".join(_path(p) for p in display_only) + " PROPERTIES HEADER_FILE_ONLY TRUE)"]
        definitions = project.settings._data.types[kind].compile_definitions
        if definitions:
            lines += [f"target_compile_definitions({name} PRIVATE " + " ".join(_quote(d) for d in definitions) + ")"]
        lines += [f"target_include_directories({name} PRIVATE {_path(project.root / 'include')})"]
    if files:
        lines += [f"source_group(TREE {_path(project.root)} PREFIX \"\" FILES\n  {listed}\n)"]
    return "\n".join(lines) + "\n"


def _target(project, settings):
    return project.name + ("_files" if settings.project_type == ProjectType.HEADER_ONLY else "")


def _prepare(project):
    project._check_active()
    project._last_update = None
    project.settings.reload()
    settings = project._resolved_build_settings()
    source, build = _locations(project, settings)
    return settings, source, build


def _generate(project, settings, source, build):
    project._last_update = None
    files = _scan(project)
    text = _cmake(project, settings, files)
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
    result = process(["cmake", "-S", source, "-B", build, "-G", "Visual Studio 17 2022", "-A", settings.architecture], project.root)
    target = _target(project, settings)
    report = UpdateReport(result, files, build,
                          build / f"{project.name}.sln" if result.success else None,
                          build / f"{target}.vcxproj" if result.success else None,
                          build / f"{target}.vcxproj.filters" if result.success else None)
    project._last_update = report
    return report


def update(project):
    settings, source, build = _prepare(project)
    with storage.write_lock(source):
        return _generate(project, settings, source, build)


def _artifacts(project, settings, build):
    if settings.project_type == ProjectType.HEADER_ONLY:
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
    settings, source, build = _prepare(project)
    if operation == "run" and settings.project_type != ProjectType.EXECUTABLE:
        raise SettingsError("Only executable Projects can run")
    with storage.write_lock(source):
        update_report = _generate(project, settings, source, build)
        results = [update_report.process]
        if not update_report.success:
            return OperationReport(tuple(results))
        if operation in {"clean", "rebuild"}:
            # Safe only for this dependency-free, privately owned build tree.
            # M3 must replace/revalidate this before introducing dependencies.
            result = process(["cmake", "--build", build, "--config", settings.configuration, "--target", "clean"], project.root)
            results.append(result)
            if not result.success or operation == "clean":
                return OperationReport(tuple(results))
        result = process(["cmake", "--build", build, "--config", settings.configuration,
                          "--target", _target(project, settings), "--parallel", settings.parallel], project.root)
        results.append(result)
        if not result.success:
            return OperationReport(tuple(results))
        artifacts = _artifacts(project, settings, build)
        if operation == "run":
            executables = [p for p in artifacts if p.suffix.lower() == ".exe"]
            if len(executables) != 1 or not executables[0].is_file():
                raise SettingsError("Expected one built executable")
            results.append(process([executables[0], *settings.run_arguments], project.root))
        return OperationReport(tuple(results), artifacts)


def file_path(project, value):
    project._check_active()
    path = storage.contained(project.root, str(value))
    if path == project.root or any(part in {".cppbuild", ".git", "build"} for part in path.relative_to(project.root).parts):
        raise SettingsError("File operations cannot modify management/build directories")
    return path


def file_report(project, paths, auto_update):
    project._last_update = None
    if not auto_update:
        return FileOperationReport(tuple(paths))
    try:
        result = update(project)
        return FileOperationReport(tuple(paths), result, pending_update=not result.success)
    except (OSError, ValueError, NotImplementedError) as exc:
        # File change already happened; retain that fact for callers.
        return FileOperationReport(tuple(paths), update_error=f"{type(exc).__name__}: {exc}")

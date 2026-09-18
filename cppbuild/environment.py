"""Explicit tool and VS2022 compiler checks; never installs or downloads."""
from dataclasses import dataclass, field
from pathlib import Path
import os
import re
import shutil
import tempfile

from . import engine, tooling
from .models import ProjectType, SettingsError, SolutionBuildSettings, ToolSettings


@dataclass
class EnvironmentOptions:
    tools: ToolSettings = field(default_factory=ToolSettings)
    configuration: str = "Debug"
    architecture: str = "x64"
    cpp_standard: int = 20
    require_ctest: bool = False


@dataclass(frozen=True)
class EnvironmentItem:
    name: str
    path: str | None
    version: str | None
    success: bool
    detail: str
    action: str = ""


@dataclass(frozen=True)
class EnvironmentReport:
    items: tuple[EnvironmentItem, ...]
    configuration: str
    architecture: str
    cpp_standard: int
    projects: tuple = ()

    @property
    def success(self):
        return bool(self.items) and all(item.success for item in self.items) and all(r.success for r in self.projects)


class Environment:
    @staticmethod
    def check(options=None):
        from .core import _build_settings
        options = options or EnvironmentOptions()
        if not isinstance(options, EnvironmentOptions) or type(options.require_ctest) is not bool:
            raise SettingsError("Expected EnvironmentOptions")
        settings = SolutionBuildSettings(configuration=options.configuration, architecture=options.architecture,
                                         cpp_standard=options.cpp_standard, tools=options.tools)
        _build_settings(settings)
        env = tooling.environment(options.tools)
        items = []
        names = ("cmake", "ctest") if options.require_ctest else ("cmake",)
        for name in names:
            command = getattr(options.tools, name)
            path = shutil.which(command, path=env.get("PATH", ""))
            if path is None:
                items.append(EnvironmentItem(name, None, None, False, "Executable not found", "Set ToolSettings or install the required tool"))
                continue
            try:
                result = engine.process([path, "--version"], Path.cwd(), env)
                match = re.search(r"version (\d+)\.(\d+)\.(\d+)", result.output)
                version = match.group(0).split()[1] if match else None
                success = result.success and match is not None and tuple(map(int, match.groups())) >= (3, 24, 0)
                items.append(EnvironmentItem(name, path, version, success, result.output, "" if success else "Use CMake/CTest 3.24 or later"))
            except OSError as exc:
                items.append(EnvironmentItem(name, path, None, False, str(exc), "Check executable access"))
        if os.name != "nt":
            items.append(EnvironmentItem("vs2022", None, None, False, "Windows is required", "Use Windows with VS2022 C++ tools"))
        elif items and all(item.success for item in items):
            with tempfile.TemporaryDirectory(prefix="cppbuild-environment-") as temp:
                root = Path(temp)
                (root / "CMakeLists.txt").write_text(
                    'cmake_minimum_required(VERSION 3.24)\nproject(CppBuildEnvironment LANGUAGES CXX)\n'
                    'add_executable(probe main.cpp)\n'
                    f'set_target_properties(probe PROPERTIES CXX_STANDARD {options.cpp_standard} CXX_STANDARD_REQUIRED YES)\n', encoding="utf-8")
                (root / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
                try:
                    configure = tooling.process(settings, ["cmake", "-S", root, "-B", root / "build", "-G", "Visual Studio 17 2022", "-A", options.architecture], root)
                    results = [configure]
                    if configure.success:
                        results.append(tooling.process(settings, ["cmake", "--build", root / "build", "--config", options.configuration, "--target", "probe"], root))
                    cache = (root / "build/CMakeCache.txt").read_text(encoding="utf-8") if (root / "build/CMakeCache.txt").exists() else ""
                    compiler = re.search(r"^CMAKE_CXX_COMPILER:FILEPATH=(.+)$", cache, re.M)
                    compiler_path = compiler.group(1) if compiler else None
                    compiler_version = None
                    for metadata in (root / "build/CMakeFiles").glob("*/CMakeCXXCompiler.cmake"):
                        data = metadata.read_text(encoding="utf-8")
                        location = re.search(r'set\(CMAKE_CXX_COMPILER "([^"]+)"\)', data)
                        version = re.search(r'set\(CMAKE_CXX_COMPILER_VERSION "([^"]+)"\)', data)
                        compiler_path = location.group(1) if location else compiler_path
                        compiler_version = version.group(1) if version else compiler_version
                    success = all(r.success for r in results)
                    items.append(EnvironmentItem("vs2022", compiler_path, compiler_version, success,
                                                 "\n".join(r.output for r in results), "" if success else "Check VS2022 C++ workload, Windows SDK, architecture and tool environment"))
                except OSError as exc:
                    items.append(EnvironmentItem("vs2022", None, None, False, str(exc), "Check VS2022 compiler/tool access"))
        return EnvironmentReport(tuple(items), options.configuration, options.architecture, options.cpp_standard)


def check_owner(owner):
    from .graph import resolve
    from .models import ImportedLibrary, CMakeSource, CMakePackage
    solution = getattr(owner, "solution", owner)
    if owner is solution:
        solution.settings.reload()
        projects = solution.projects()
    else:
        projects = [owner]
    _, nodes = resolve(projects)
    if owner is solution and any((n.settings.configuration, n.settings.architecture, n.settings.tools) !=
                                (solution._build_settings.configuration, solution._build_settings.architecture, solution._build_settings.tools)
                                for n in nodes):
        raise SettingsError("Whole-solution operations require matching configuration, architecture and ToolSettings")
    reports = []
    for node in nodes:
        settings = node.settings
        options = EnvironmentOptions(settings.tools, settings.configuration, settings.architecture, settings.cpp_standard,
                                     settings.project_type == ProjectType.TEST)
        report = Environment.check(options)
        extra = []
        for dependency in node.project.settings._data.dependencies.values():
            paths = []
            if isinstance(dependency, CMakeSource):
                paths = [(node.project.root / dependency.directory) / "CMakeLists.txt"]
            elif isinstance(dependency, CMakePackage):
                paths = [node.project.root / dependency.directory]
            elif isinstance(dependency, ImportedLibrary):
                if dependency.project_type != ProjectType.HEADER_ONLY:
                    paths = [node.project.root / dependency.locations.get(settings.configuration, ".cppbuild/missing")]
                    if dependency.project_type == ProjectType.SHARED_LIBRARY:
                        paths.append(node.project.root / dependency.import_libraries.get(settings.configuration, ".cppbuild/missing"))
                paths += [node.project.root / p for p in dependency.include_directories]
            for path in paths:
                exists = path.exists()
                extra.append(EnvironmentItem("dependency", str(path.resolve()), None, exists, "Path exists; package/target usability requires build" if exists else "Missing dependency path", "" if exists else "Restore or change the dependency"))
        if settings.project_type == ProjectType.TEST and settings.googletest_archive is not None:
            from .storage import digest
            path = (node.project.root / settings.googletest_archive).resolve()
            valid = path.is_file() and digest(path) == "1f357c27ca988c3f7c6b4bf68a9395005ac6761f034046e9dde0896e3aba00e4"
            extra.append(EnvironmentItem("googletest", str(path), "1.14.0", valid, "Fixed archive checksum checked", "" if valid else "Provide the fixed v1.14.0 ZIP"))
        reports.append(EnvironmentReport((*report.items, *extra), report.configuration, report.architecture, report.cpp_standard))
    if not reports:
        data = solution._build_settings
        return Environment.check(EnvironmentOptions(data.tools, data.configuration, data.architecture, data.cpp_standard))
    first = reports[0]
    return EnvironmentReport(first.items, first.configuration, first.architecture, first.cpp_standard, tuple(reports[1:]))

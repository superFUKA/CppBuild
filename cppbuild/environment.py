"""Explicit tool and compiler checks; never installs or downloads."""
from dataclasses import dataclass, field
from pathlib import Path
import re
import shutil
import tempfile

from . import engine, tooling, generators
from .models import CMakeSettings, ProjectType, SettingsError, SolutionBuildSettings, ToolSettings


@dataclass
class EnvironmentOptions:
    tools: ToolSettings = field(default_factory=ToolSettings)
    configuration: str = "Debug"
    architecture: str | None = None
    cpp_standard: int = 20
    require_ctest: bool = False
    cmake: CMakeSettings = field(default_factory=CMakeSettings)


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
    architecture: str | None
    cpp_standard: int
    projects: tuple = ()
    generator: str | None = None

    @property
    def success(self):
        return bool(self.items) and all(item.success for item in self.items) and all(r.success for r in self.projects)


@dataclass(frozen=True)
class GeneratorInfo:
    """A CMake generator. supported means CppBuild implements its operations,
    not that every host/compiler combination was verified."""
    name: str
    platform_support: bool
    toolset_support: bool
    supported: bool


class Environment:
    @staticmethod
    def generators(tools=None):
        tools = tools or ToolSettings()
        tooling.validate(tools)
        return tuple(GeneratorInfo(g["name"], bool(g.get("platformSupport")), bool(g.get("toolsetSupport")),
                                   g["name"] in generators.SUPPORTED)
                     for g in generators.capabilities(tools))

    @staticmethod
    def check(options=None):
        from .core import _build_settings
        options = options or EnvironmentOptions()
        if not isinstance(options, EnvironmentOptions) or type(options.require_ctest) is not bool:
            raise SettingsError("Expected EnvironmentOptions")
        settings = SolutionBuildSettings(configuration=options.configuration, architecture=options.architecture,
                                         cpp_standard=options.cpp_standard, tools=options.tools, cmake=options.cmake)
        _build_settings(settings)
        generator = options.cmake.generator or generators.default_generator()
        # The historical item name is kept for the default Windows generator.
        label = "vs2022" if generator == "Visual Studio 17 2022" else "compiler"
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
        architecture = options.architecture
        try:
            toolchain = generators.resolve(settings)
            architecture = toolchain.architecture
        except (OSError, SettingsError) as exc:
            items.append(EnvironmentItem(label, None, None, False, str(exc),
                                         "Check the generator, compiler, Ninja and Visual Studio installation"))
        else:
            if items and all(item.success for item in items):
                items.append(_probe(settings, toolchain, options, label))
        return EnvironmentReport(tuple(items), options.configuration, architecture, options.cpp_standard, generator=generator)


def _probe(settings, toolchain, options, label):
    with tempfile.TemporaryDirectory(prefix="cppbuild-environment-") as temp:
        root = Path(temp)
        (root / "CMakeLists.txt").write_text(
            'cmake_minimum_required(VERSION 3.24)\nproject(CppBuildEnvironment LANGUAGES CXX)\n'
            'add_executable(probe main.cpp)\n'
            f'set_target_properties(probe PROPERTIES CXX_STANDARD {options.cpp_standard} CXX_STANDARD_REQUIRED YES)\n', encoding="utf-8")
        (root / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
        try:
            configure = tooling.process(settings, toolchain.configure(root, root / "build"), root)
            results = [configure]
            info = None
            if configure.success:
                info = generators.compiler(root / "build")
                error = generators.verify(toolchain, info)
                if error is not None:
                    results.append(engine.ProcessReport((), 1, error))
                else:
                    results.append(tooling.process(settings, ["cmake", "--build", root / "build", "--config", options.configuration, "--target", "probe"], root))
            success = all(r.success for r in results)
            detail = "\n".join(r.output for r in results)
            if info is not None:
                detail = f"{info.id} {info.version} ({info.architecture})\n" + detail
            return EnvironmentItem(label, info.path if info else None, info.version if info else None, success, detail,
                                   "" if success else "Check the compiler installation, SDK, architecture and tool environment")
        except (OSError, SettingsError) as exc:
            return EnvironmentItem(label, None, None, False, str(exc), "Check compiler/tool access")


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
    from .graph import compatible
    if owner is solution and any(not compatible(n.settings, solution._build_settings) or n.settings.tools != solution._build_settings.tools
                                for n in nodes):
        raise SettingsError("Whole-solution operations require matching configuration, generation environment and ToolSettings")
    reports = []
    for node in nodes:
        settings = node.settings
        options = EnvironmentOptions(settings.tools, settings.configuration, settings.architecture, settings.cpp_standard,
                                     settings.project_type == ProjectType.TEST, settings.cmake)
        report = Environment.check(options)
        extra = []
        for dependency in node.project.settings._data.dependencies.values():
            paths = []
            if isinstance(dependency, CMakeSource):
                paths = [(node.project.root / dependency.directory) / "CMakeLists.txt"]
            elif isinstance(dependency, CMakePackage):
                paths = [node.project.root / dependency.directory]
            elif isinstance(dependency, ImportedLibrary):
                if dependency.project_type != ProjectType.INTERFACE_LIBRARY:
                    paths = [node.project.root / dependency.locations.get(settings.configuration, ".cppbuild/missing")]
                    if dependency.project_type == ProjectType.SHARED_LIBRARY and settings.configuration in dependency.import_libraries:
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
        reports.append(EnvironmentReport((*report.items, *extra), report.configuration, report.architecture, report.cpp_standard,
                                         generator=report.generator))
    if not reports:
        data = solution._build_settings
        return Environment.check(EnvironmentOptions(data.tools, data.configuration, data.architecture, data.cpp_standard, cmake=data.cmake))
    first = reports[0]
    return EnvironmentReport(first.items, first.configuration, first.architecture, first.cpp_standard, tuple(reports[1:]), first.generator)

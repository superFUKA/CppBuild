"""Whole-solution orchestration over independently generated VS projects."""
from pathlib import Path
import xml.etree.ElementTree as ET

from . import engine, storage
from . import tooling, information
from .graph import resolve
from .models import ProjectType, SettingsError


def _prepare(solution):
    solution._last_update = None
    solution.settings.reload()
    roots, nodes = resolve(solution.projects(), include_external_members=solution.settings._data.solution_folders is not None)
    for node in nodes:
        if (node.settings.architecture, node.settings.configuration) != (solution._build_settings.architecture, solution._build_settings.configuration):
            raise SettingsError("Whole-solution operations require matching configuration and architecture")
        if node.settings.tools != solution._build_settings.tools:
            raise SettingsError("Whole-solution operations require matching ToolSettings; use individual operations for overrides")
    return roots, nodes


def _selected(solution, roots, operation):
    names = (solution._build_settings.run_projects if operation == "run" else solution._build_settings.build_projects)
    # None means all members; explicit [] means no build targets.
    if names is None:
        names = [n.project.name for n in roots]
    known = {n.project.name: n for n in roots}
    if len(set(names)) != len(names) or any(name not in known for name in names):
        raise SettingsError("Invalid or duplicate Solution selection")
    result = [known[name] for name in names]
    if operation == "run" and any(n.settings.project_type != ProjectType.EXECUTABLE for n in result):
        raise SettingsError("Run selection must contain executable Projects")
    return result


def _generate(solution, nodes, selected):
    processes = []
    for node in nodes:
        source, build = engine._locations(node.project, node.settings)
        report = engine._generate(node.project, node.settings, source, build, node.dependencies)
        processes.append(report.process)
        if not report.success:
            return engine.OperationReport(tuple(processes)), None
    context = "vs2022-" + solution._build_settings.architecture
    source = storage.contained(solution.root, ".cppbuild/generated/" + context)
    build = storage.contained(solution.root, ".cppbuild/build/" + context)
    lines = ["cmake_minimum_required(VERSION 3.24)",
             f"project({solution.settings._data.name} LANGUAGES NONE)",
             "set(CMAKE_SUPPRESS_REGENERATION ON)"]
    from .solution_folders import placements, dependency_keys
    folders = placements(solution, nodes)
    required = dependency_keys([n for n in nodes if n.project.solution.root == solution.root])
    if folders:
        lines.append("set_property(GLOBAL PROPERTY USE_FOLDERS ON)")
    for node in nodes:
        _, child_build = engine._locations(node.project, node.settings)
        project_file = child_build / (engine._target(node.project, node.settings) + ".vcxproj")
        guid = ET.parse(project_file).find(".//{*}ProjectGuid").text.strip("{}")
        lines.append(f"include_external_msproject({node.label} {engine._path(project_file)} GUID {engine._quote(guid)})")
        if node.key in folders:
            lines.append(f"set_property(TARGET {node.label} PROPERTY FOLDER {engine._quote(folders[node.key])})")
        if node.key not in required:
            lines.append(f"set_property(TARGET {node.label} PROPERTY EXCLUDE_FROM_DEFAULT_BUILD TRUE)")
            lines.append(f"set_property(TARGET {node.label} PROPERTY EXCLUDE_FROM_ALL TRUE)")
    for node in nodes:
        if node.dependencies:
            lines.append(f"add_dependencies({node.label} " + " ".join(d.label for d in node.dependencies) + ")")
    sln = build / (solution.settings._data.name + ".sln")
    if selected:
        # Invoke the .sln, not an external .vcxproj, so MSBuild observes the solution dependency graph.
        target_names = [((folders[n.key].replace("/", "\\") + "\\") if n.key in folders else "") + n.label
                        for n in selected]
        cleanse = str.maketrans({c: "_" for c in "%$@;.()'"})
        targets = ";".join(name.translate(cleanse) for name in target_names).replace("\\", "\\\\")
        lines += ['add_custom_target(cppbuild_selected',
                  '  COMMAND "${CMAKE_VS_MSBUILD_COMMAND}" ' + engine._path(sln),
                  f'  "/t:{targets}" "/p:Configuration=$<CONFIG>" "/p:Platform={solution._build_settings.architecture}"',
                  # Child CMake targets are absent from the aggregate .sln; keep the requested configuration.
                  '  /p:ShouldUnsetParentConfigurationAndPlatform=false',
                  f'  /m:{solution._build_settings.parallel} /verbosity:minimal VERBATIM)']
    else:
        lines += ["add_custom_target(cppbuild_selected)"]
    cmake = source / "CMakeLists.txt"
    text = "\n".join(lines) + "\n"
    if not cmake.exists() or cmake.read_text(encoding="utf-8") != text:
        storage.atomic_write(cmake, text.encode("utf-8"))
    owner_path = build / "cppbuild-owner.json"
    owner = {"solution_root": str(solution.root)}
    if owner_path.exists() and storage.read_json(owner_path) != owner:
        raise SettingsError("Whole build directory ownership mismatch")
    if not owner_path.exists() and build.exists() and any(build.iterdir()):
        raise SettingsError("Refusing an unowned whole build directory")
    storage.atomic_write(owner_path, storage.encoded(owner))
    generated = tooling.process(solution._build_settings, ["cmake", "-S", source, "-B", build, "-G", "Visual Studio 17 2022", "-A", solution._build_settings.architecture], solution.root)
    processes.append(generated)
    report = engine.OperationReport(tuple(processes), (sln,) if generated.success else ())
    solution._last_update = report
    return report, build


def update(solution):
    roots, nodes = _prepare(solution)
    selected = _selected(solution, roots, "build")
    with storage.write_lock(solution.root / ".cppbuild/operations"), engine.lock_nodes(nodes):
        return _generate(solution, nodes, selected)[0]


def operate(solution, operation):
    roots, nodes = _prepare(solution)
    selected = _selected(solution, roots, operation)
    with storage.write_lock(solution.root / ".cppbuild/operations"), engine.lock_nodes(nodes):
        generated, build = _generate(solution, nodes, selected)
        results = list(generated.processes)
        if not generated.success:
            return generated
        if operation in {"clean", "rebuild"}:
            # Only explicitly selected roots are cleaned, never their dependency closure.
            # Protect a selected shared dependency still used by an unselected member.
            protected = set()
            def protect(node):
                for child in node.dependencies:
                    if child.key not in protected:
                        protected.add(child.key)
                        protect(child)
            for node in roots:
                if node not in selected:
                    protect(node)
            for node in reversed(selected):
                if node.key in protected:
                    continue
                _, child_build = engine._locations(node.project, node.settings)
                result = engine.clean_target(node.project, node.settings, child_build)
                results.append(result)
                if not result.success:
                    return engine.OperationReport(tuple(results))
            if operation == "clean":
                return engine.OperationReport(tuple(results))
        result = tooling.process(solution._build_settings, ["cmake", "--build", build, "--config", solution._build_settings.configuration, "--target", "cppbuild_selected"], solution.root)
        results.append(result)
        built_keys = set()
        def capture(node):
            if node.key in built_keys:
                return
            built_keys.add(node.key)
            for child in node.dependencies:
                capture(child)
            _, owned_build = engine._locations(node.project, node.settings)
            information.built(node.project, node.settings, engine._artifacts(node.project, node.settings, owned_build) if result.success else (), result.success)
        for node in selected:
            capture(node)
        if not result.success:
            return engine.OperationReport(tuple(results))
        artifacts, commands = [], []
        for node in selected:
            _, child_build = engine._locations(node.project, node.settings)
            owned = engine._artifacts(node.project, node.settings, child_build)
            artifacts.extend(owned)
            if operation == "run":
                commands.append(engine.run_command(node.project, node.settings, owned, nodes))
        if operation == "run":
            from .execution import execute
            settings = solution._build_settings
            return execute(commands, results, artifacts, parallel=settings.run_parallel,
                           wait=settings.run_wait, continue_on_failure=settings.run_continue_on_failure)
        return engine.OperationReport(tuple(results), tuple(artifacts))

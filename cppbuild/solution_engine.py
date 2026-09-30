"""Whole-solution orchestration over independently generated Project trees.

Visual Studio generators also aggregate the Project files into one solution
file (.sln builds through MSBuild); other generators and .slnx build the
dependency closure from Python.
"""
from pathlib import Path
import xml.etree.ElementTree as ET

from . import engine, storage
from . import tooling, information, generators
from .graph import compatible, resolve
from .models import ProjectType, SettingsError


def _prepare(solution):
    solution._last_update = None
    solution.settings.reload()
    visual_studio = generators.resolve(solution._build_settings).visual_studio
    # Unreferenced external members are listed only for the IDE solution view.
    roots, nodes = resolve(solution.projects(),
                           include_external_members=visual_studio and solution.settings._data.solution_folders is not None)
    for node in nodes:
        if not compatible(node.settings, solution._build_settings):
            raise SettingsError("Whole-solution operations require matching configuration and generation environment")
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
    toolchain = generators.resolve(solution._build_settings)
    if not toolchain.visual_studio:
        report = engine.OperationReport(tuple(processes) or (engine.ProcessReport((), 0, "No Projects to generate"),))
        solution._last_update = report
        return report, None
    context = toolchain.context
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
    sln = build / (solution.settings._data.name + toolchain.solution_suffix)
    if selected and _msbuild_selection(toolchain):
        # Invoke the .sln, not an external .vcxproj, so MSBuild observes the solution dependency graph.
        target_names = [((folders[n.key].replace("/", "\\") + "\\") if n.key in folders else "") + n.label
                        for n in selected]
        cleanse = str.maketrans({c: "_" for c in "%$@;.()'"})
        targets = ";".join(name.translate(cleanse) for name in target_names).replace("\\", "\\\\")
        lines += ['add_custom_target(cppbuild_selected',
                  '  COMMAND "${CMAKE_VS_MSBUILD_COMMAND}" ' + engine._path(sln),
                  f'  "/t:{targets}" "/p:Configuration=$<CONFIG>" "/p:Platform={toolchain.architecture}"',
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
    generated = tooling.process(solution._build_settings, toolchain.configure(source, build), solution.root)
    processes.append(generated)
    report = engine.OperationReport(tuple(processes), (sln,) if generated.success else ())
    solution._last_update = report
    return report, build


def _msbuild_selection(toolchain):
    # .slnx names projects by file name only, so same-named Projects of different kinds
    # cannot be selected; those solutions are for the IDE and build like other generators.
    return toolchain.solution_suffix == ".sln"


def update(solution):
    roots, nodes = _prepare(solution)
    selected = _selected(solution, roots, "build")
    with storage.write_lock(solution.root / ".cppbuild/operations"), engine.lock_nodes(nodes):
        return _generate(solution, nodes, selected)[0]


def operate(solution, operation):
    if operation == "clean":
        return clean(solution)
    roots, nodes = _prepare(solution)
    selected = _selected(solution, roots, operation)
    with storage.write_lock(solution.root / ".cppbuild/operations"), engine.lock_nodes(nodes):
        generated, build = _generate(solution, nodes, selected)
        results = list(generated.processes)
        if not generated.success:
            return generated
        if operation == "rebuild":
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
        if build is not None and _msbuild_selection(generators.resolve(solution._build_settings)):
            result = tooling.process(solution._build_settings, ["cmake", "--build", build, "--config", solution._build_settings.configuration, "--target", "cppbuild_selected"], solution.root)
            results.append(result)
            success = result.success
        else:
            # Each tree is built once, dependencies first; the trees are shared with individual builds.
            closure = set()
            def include(node):
                if node.key not in closure:
                    closure.add(node.key)
                    for child in node.dependencies:
                        include(child)
            for node in selected:
                include(node)
            success = True
            for node in nodes:
                if node.key in closure:
                    result = engine.build_node(node)
                    results.append(result)
                    if not result.success:
                        success = False
                        break
        built_keys = set()
        def capture(node):
            if node.key in built_keys:
                return
            built_keys.add(node.key)
            for child in node.dependencies:
                capture(child)
            _, owned_build = engine._locations(node.project, node.settings)
            information.built(node.project, node.settings, engine._artifacts(node.project, node.settings, owned_build) if success else (), success)
        for node in selected:
            capture(node)
        if not success:
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


def clean(solution):
    """Clean selected existing trees without generating or opening their dependencies."""
    from .graph import Node

    solution._last_update = None
    solution.settings.reload()
    roots = [Node(p, p._resolved_build_settings(), []) for p in solution.projects()]
    selected = _selected(solution, roots, "clean")
    if not selected:
        return engine.OperationReport((engine.ProcessReport((), 0, "Nothing to clean: no Projects selected"),))
    protected = set()
    protection_error = None
    unselected = [n.project for n in roots if n not in selected]
    if unselected:
        try:
            _, dependencies = resolve(unselected)
            protected = {n.key for n in dependencies}
        except (OSError, SettingsError) as exc:
            # Without the closure we cannot prove that selected shared outputs are unused.
            protected = {n.key for n in selected}
            protection_error = str(exc)
    results = []
    with storage.write_lock(solution.root / ".cppbuild/operations"), engine.lock_nodes(selected):
        for node in reversed(selected):
            if node.key in protected:
                message = f"Clean skipped for shared dependency: {node.project.name}"
                if protection_error:
                    message += f"; could not check unselected dependencies: {protection_error}"
                results.append(engine.ProcessReport((), 1 if protection_error else 0, message))
                continue
            _, build = engine._locations(node.project, node.settings)
            result = engine.clean_target(node.project, node.settings, build)
            results.append(result)
            if not result.success:
                break
    return engine.OperationReport(tuple(results))

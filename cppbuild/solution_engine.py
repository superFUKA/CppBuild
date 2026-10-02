"""Whole-Solution operations: the same build tree as individual operations.

The selected Projects are CMake targets; CMake builds their dependencies, and
Visual Studio generators open the generated .sln/.slnx directly.
"""
from . import engine, information, storage, workspace
from .graph import compatible
from .models import ProjectType, SettingsError


def _prepare(solution):
    solution._last_update = None
    plan = workspace.plan(solution)
    for node in plan.nodes:
        if not compatible(node.settings, solution._build_settings):
            raise SettingsError("Whole-solution operations require matching configuration and generation environment")
        if node.settings.tools != solution._build_settings.tools:
            raise SettingsError("Whole-solution operations require matching ToolSettings; use individual operations for overrides")
    plan.check(lambda node: solution._build_settings.configuration)
    return plan


def _selected(solution, roots, operation):
    names = (solution._build_settings.run_projects if operation == "run" else solution._build_settings.build_projects)
    # None means all members; explicit [] means no build targets.
    explicit = names is not None
    if names is None:
        names = [n.project.name for n in roots]
    known = {n.project.name: n for n in roots}
    if len(set(names)) != len(names) or any(name not in known for name in names):
        raise SettingsError("Invalid or duplicate Solution selection")
    result = [known[name] for name in names]
    if operation == "run" and any(n.settings.project_type != ProjectType.EXECUTABLE for n in result):
        raise SettingsError("Run selection must contain executable Projects")
    return result, explicit


def _configure(solution, plan):
    result, build, toolchain, _ = engine.configure(plan, solution._build_settings)
    engine.generated(plan, result.success)
    artifacts = ()
    if result.success and toolchain.visual_studio:
        artifacts = (build / f"{solution.settings._data.name}{toolchain.solution_suffix}",)
    report = engine.OperationReport((result,), artifacts)
    solution._last_update = report
    return report, build


def update(solution):
    plan = _prepare(solution)
    with plan.lock():
        return _configure(solution, plan)[0]


def operate(solution, operation):
    if operation == "clean":
        return clean(solution)
    plan = _prepare(solution)
    selected, explicit = _selected(solution, plan.roots, operation)
    settings = solution._build_settings
    with plan.lock():
        generated, build = _configure(solution, plan)
        results = list(generated.processes)
        if not generated.success:
            return generated
        names = [engine.target(n.project, n.settings) for n in selected if engine.buildable(plan, n.project, n.settings)]
        if operation == "rebuild" and selected:
            removed = engine.clean_targets(solution, settings, names if explicit else None)
            results += removed
            if not all(r.success for r in removed):
                return engine.OperationReport(tuple(results))
        if names:
            # Without an explicit selection, build the default target: every member and what it needs.
            results.append(engine.build_targets(settings, build, names if explicit or operation == "run" else None))
        success = all(r.success for r in results)
        captured = set()
        for node in selected:
            for item in engine.closure(node):
                if item.key not in captured:
                    captured.add(item.key)
                    information.built(item.project, item.settings, engine._node_artifacts(build, item) if success else (), success)
        if not success:
            return engine.OperationReport(tuple(results))
        artifacts, commands = [], []
        for node in selected:
            artifacts.extend(engine._node_artifacts(build, node))
            if operation == "run":
                commands.append(engine.run_command(node.project, node.settings, build, engine.closure(node)))
        if operation == "run":
            from .execution import execute
            return execute(commands, results, artifacts, parallel=settings.run_parallel,
                           wait=settings.run_wait, continue_on_failure=settings.run_continue_on_failure)
        return engine.OperationReport(tuple(results), tuple(artifacts))


def clean(solution):
    """Clean the selected Projects (all: the whole tree) without configuring or resolving dependencies."""
    from .graph import Node

    solution._last_update = None
    solution.settings.reload()
    roots = [Node(p, p._resolved_build_settings(), []) for p in solution.projects()]
    selected, explicit = _selected(solution, roots, "clean")
    if not selected:
        return engine.OperationReport((engine.ProcessReport((), 0, "Nothing to clean: no Projects selected"),))
    settings = solution._build_settings
    with storage.operation_lock(solution.root, *(p.root for p in solution.projects())):
        names = [engine.target(n.project, n.settings) for n in selected]
        results = engine.clean_targets(solution, settings, names if explicit else None)
    success = all(r.success for r in results)
    for node in selected:
        engine.cleaned(node.project, node.settings, success)
    return engine.OperationReport(tuple(results))

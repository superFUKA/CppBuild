"""Build TEST projects and read fresh CTest JUnit results."""
from dataclasses import dataclass
import uuid
import xml.etree.ElementTree as ET

from . import engine
from . import tooling, information
from .models import ProjectType, SettingsError


@dataclass(frozen=True)
class TestCaseResult:
    name: str
    status: str
    output: str = ""


@dataclass(frozen=True)
class TestReport:
    project: str | None
    processes: tuple = ()
    cases: tuple[TestCaseResult, ...] = ()
    diagnostics: tuple[str, ...] = ()
    projects: tuple = ()

    @property
    def success(self):
        if self.diagnostics or any(not p.success for p in self.processes):
            return False
        if self.projects:
            return all(p.success for p in self.projects)
        return bool(self.cases) and any(c.status == "passed" for c in self.cases) and all(c.status in {"passed", "skipped"} for c in self.cases)


def read_results(path):
    try:
        root = ET.parse(path).getroot()
        if root.tag not in {"testsuite", "testsuites"}:
            raise ValueError("Unexpected JUnit root")
        cases = []
        for item in root.iter("testcase"):
            name = item.attrib["name"]
            status = "failed" if item.find("failure") is not None or item.find("error") is not None else "skipped" if item.find("skipped") is not None or item.get("status") in {"notrun", "disabled"} else "passed"
            cases.append(TestCaseResult(name, status, "\n".join(e.text or "" for e in item)))
        if not cases:
            return (), ("No tests were reported",)
        return tuple(cases), ()
    except (OSError, ET.ParseError, ValueError, KeyError) as exc:
        return (), (f"Test results unavailable: {exc}",)


def _run(plan, node, build):
    """Build one TEST Project in the configured tree and run its CTest label."""
    project, settings = node.project, node.settings
    name = engine.target(project, settings)
    results = [engine.build_targets(settings, build, [name])]
    success = results[-1].success
    for item in engine.closure(node):
        information.built(item.project, item.settings, engine._node_artifacts(build, item) if success else (), success)
    if not success:
        return TestReport(project.name, tuple(results), diagnostics=("Build failed; tests not run",))
    try:
        engine.check_runnable(build, settings, project.name)
    except SettingsError as exc:
        return TestReport(project.name, tuple(results), diagnostics=(str(exc),))
    output = build / ("cppbuild-tests-" + uuid.uuid4().hex + ".xml")
    # Each TEST Project labels its discovered tests with its CMake target name.
    tested = tooling.process(settings, ["ctest", "--test-dir", build, "-C", settings.configuration, "-L", f"^{name}$",
                             "-j", settings.test_parallel, "--output-on-failure", "--no-tests=error",
                             "--output-junit", output], project.root,
                             env=engine.runtime_environment(engine.closure(node), settings, build))
    results.append(tested)
    cases, diagnostics = read_results(output)
    return TestReport(project.name, tuple(results), cases, diagnostics)


def project_test(project):
    project._last_update = None
    plan, node = engine.project_plan(project)
    if node.settings.project_type != ProjectType.TEST:
        raise SettingsError("test() requires a TEST Project")
    with plan.lock():
        update = engine._update(plan, node)
        if not update.success:
            return TestReport(project.name, (update.process,), diagnostics=("Configure failed; tests not run",))
        report = _run(plan, node, update.build_directory)
        return TestReport(report.project, (update.process, *report.processes), report.cases, report.diagnostics)


def solution_test(solution):
    from .solution_engine import _prepare, _configure
    solution._last_update = None
    solution.settings.reload()
    settings = solution._build_settings
    names = settings.test_projects
    if names is None:
        names = [p.name for p in solution.projects()
                 if ProjectType.TEST in p.settings._data.types and p._resolved_build_settings().project_type == ProjectType.TEST]
    if not names:
        return TestReport(None, diagnostics=("No TEST Projects selected",))
    if len(set(names)) != len(names) or any(n not in solution._projects for n in names):
        raise SettingsError("Invalid or duplicate test selection")
    plan = _prepare(solution)
    roots = {n.project.name: n for n in plan.roots}
    selected = [roots[name] for name in names]
    if any(n.settings.project_type != ProjectType.TEST for n in selected):
        raise SettingsError("Test selection must contain TEST Projects")
    with plan.lock():
        generated, build = _configure(solution, plan)
        if not generated.success:
            return TestReport(None, generated.processes, diagnostics=("Configure failed; tests not run",))
        reports = []
        for node in selected:
            report = _run(plan, node, build)
            reports.append(report)
            if not report.success and not settings.test_continue_on_failure:
                break
    return TestReport(None, (*generated.processes, *(p for r in reports for p in r.processes)),
                      tuple(c for r in reports for c in r.cases), projects=tuple(reports))

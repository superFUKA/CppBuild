"""Build TEST projects and read fresh CTest JUnit results."""
from dataclasses import dataclass
import uuid
import xml.etree.ElementTree as ET

from . import engine
from . import tooling, information
from .graph import resolve
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


def _test(root, nodes):
    project, settings = root.project, root.settings
    results = []
    with engine.lock_nodes(nodes):
        for node in nodes:
            source, build = engine._locations(node.project, node.settings)
            generated = engine._generate(node.project, node.settings, source, build, node.dependencies)
            results.append(generated.process)
            if not generated.success:
                return TestReport(project.name, tuple(results), diagnostics=("Configure failed; tests not run",))
            built = tooling.process(node.settings, ["cmake", "--build", build, "--config", node.settings.configuration,
                                    "--target", engine._target(node.project, node.settings), "--parallel", node.settings.parallel], node.project.root)
            results.append(built)
            information.built(node.project, node.settings, engine._artifacts(node.project, node.settings, build) if built.success else (), built.success)
            if not built.success:
                return TestReport(project.name, tuple(results), diagnostics=("Build failed; tests not run",))
        _, build = engine._locations(project, settings)
        output = build / ("cppbuild-tests-" + uuid.uuid4().hex + ".xml")
        tested = tooling.process(settings, ["ctest", "--test-dir", build, "-C", settings.configuration,
                                 "-j", settings.test_parallel, "--output-on-failure", "--no-tests=error",
                                 "--output-junit", output], project.root, env=engine.runtime_environment(nodes, settings))
        results.append(tested)
        cases, diagnostics = read_results(output)
        return TestReport(project.name, tuple(results), cases, diagnostics)


def project_test(project):
    project._last_update = None
    roots, nodes = resolve([project])
    if roots[0].settings.project_type != ProjectType.TEST:
        raise SettingsError("test() requires a TEST Project")
    return _test(roots[0], nodes)


def solution_test(solution):
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
    roots, nodes = resolve([solution.get_project(name) for name in names])
    if any(n.settings.project_type != ProjectType.TEST for n in roots):
        raise SettingsError("Test selection must contain TEST Projects")
    reports = []
    for root in roots:
        closure = set()
        def visit(node):
            if node.key not in closure:
                closure.add(node.key)
                for child in node.dependencies:
                    visit(child)
        visit(root)
        report = _test(root, [node for node in nodes if node.key in closure])
        reports.append(report)
        if not report.success and not settings.test_continue_on_failure:
            break
    return TestReport(None, tuple(p for r in reports for p in r.processes),
                      tuple(c for r in reports for c in r.cases), projects=tuple(reports))

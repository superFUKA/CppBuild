import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from cppbuild import ProjectBuildSettings, ProjectType as T, Solution, SolutionBuildSettings, SettingsError, TypeSettingsData
from cppbuild import engine, execution, testing


class TestControlTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.solution = Solution.create(self.root, "Demo")
        self.tests = self.solution.add_project("Tests", "Tests", T.TEST)
        self.app = self.solution.add_project("App", "App", T.EXECUTABLE)

    def test_settings_validation_and_non_persistence(self):
        self.solution.set_build_settings(SolutionBuildSettings(test_projects=["Tests"], run_parallel=2, run_wait=False))
        self.tests.set_build_settings(ProjectBuildSettings(test_parallel=3, run_wait=False))
        self.solution.settings.reload()
        self.assertEqual(self.tests._resolved_build_settings().test_parallel, 3)
        opened = Solution.open(self.root / ".cppbuild")
        self.assertIsNone(opened._build_settings.test_projects)
        self.assertTrue(opened._build_settings.run_wait)
        self.assertEqual(opened.get_project("Tests")._build_settings.test_parallel, 1)
        for values in (SolutionBuildSettings(run_parallel=0), SolutionBuildSettings(test_projects=["Tests", "Tests"]),
                       SolutionBuildSettings(run_wait=1), SolutionBuildSettings(test_projects=["Unknown"])):
            with self.assertRaises(SettingsError):
                self.solution.set_build_settings(values)
        with self.assertRaises(SettingsError):
            self.tests.set_build_settings(ProjectBuildSettings(test_parallel=True))

    def test_wrong_kind_and_empty_selection_do_not_start_tools(self):
        with patch.object(engine, "process", side_effect=AssertionError("Unexpected tool")):
            with self.assertRaises(SettingsError):
                self.app.test()
            self.solution.set_build_settings(SolutionBuildSettings(test_projects=["App"]))
            with self.assertRaises(SettingsError):
                self.solution.test()
            self.solution.set_build_settings(SolutionBuildSettings(test_projects=[]))
            self.assertFalse(self.solution.test().success)

    def test_results_missing_empty_malformed_failed_and_skipped(self):
        path = self.root / "result.xml"
        for text in (None, "broken", "<other/>", "<testsuite/>"):
            if text is not None:
                path.write_text(text)
            cases, diagnostics = testing.read_results(path)
            self.assertFalse(testing.TestReport("Tests", cases=cases, diagnostics=diagnostics).success)
        path.write_text('<testsuite><testcase name="ok"/><testcase name="bad"><failure>detail</failure></testcase><testcase name="skip"><skipped/></testcase></testsuite>')
        cases, diagnostics = testing.read_results(path)
        self.assertEqual([c.status for c in cases], ["passed", "failed", "skipped"])
        self.assertFalse(testing.TestReport("Tests", cases=cases).success)
        self.assertFalse(testing.TestReport("Tests", cases=cases[2:]).success)
        self.assertTrue(testing.TestReport("Tests", cases=cases[:1]).success)

    def test_order_and_failure_continuation(self):
        commands = [([str(i)], self.root, {}) for i in range(3)]
        calls = []
        def run(command, cwd, env):
            calls.append(command[0])
            return engine.ProcessReport(command, 1 if command[0] == "1" else 0, "")
        with patch.object(engine, "process", side_effect=run):
            self.assertFalse(execution.execute(commands).success)
            self.assertEqual(calls, ["0", "1"])
            calls.clear()
            execution.execute(commands, continue_on_failure=True)
            self.assertEqual(calls, ["0", "1", "2"])

    def test_parallel_and_pending_wait(self):
        entered = threading.Barrier(3)
        release = threading.Event()
        self.addCleanup(release.set)
        def run(command, cwd, env):
            entered.wait(timeout=5)
            release.wait(timeout=5)
            return engine.ProcessReport(command, 0, "done")
        with patch.object(engine, "process", side_effect=run):
            report = execution.execute([(["a"], self.root, {}), (["b"], self.root, {})], parallel=2, wait=False)
            entered.wait(timeout=5)
            self.assertFalse(report.done)
            self.assertIsNone(report.success)
            release.set()
            self.assertTrue(report.wait(timeout=5).success)
            self.assertEqual([p.command[0] for p in report.processes], ["a", "b"])

    def test_real_process_output_and_exit_code(self):
        result = execution.execute([([sys.executable, "-c", "print('captured'); raise SystemExit(7)"], self.root, dict(os.environ))])
        self.assertFalse(result.success)
        self.assertEqual(result.processes[0].returncode, 7)
        self.assertIn("captured", result.processes[0].output)

    def test_process_gets_an_explicit_environment(self):
        with patch.object(engine.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = ""
            engine.process(["cmake", "--version"], self.root)
            self.assertIsInstance(run.call_args.kwargs["env"], dict)
            self.assertIsNot(run.call_args.kwargs["env"], os.environ)

    def test_test_selection_order_and_continuation(self):
        library = self.solution.add_project("Library", "Library", T.STATIC_LIBRARY)
        data = library.settings.get()
        data.types[T.SHARED_LIBRARY] = TypeSettingsData()
        library.settings.save(data)
        self.solution.add_project("Other", "Other", T.TEST)
        calls = []
        def fake(root, nodes):
            calls.append(root.project.name)
            return testing.TestReport(root.project.name, diagnostics=("failure",))
        with patch.object(testing, "_test", side_effect=fake):
            self.solution.test()
            self.assertEqual(calls, ["Other", "Tests"])
            calls.clear()
            self.solution.set_build_settings(SolutionBuildSettings(test_projects=["Other", "Tests"], test_continue_on_failure=False))
            self.solution.test()
            self.assertEqual(calls, ["Other"])


@unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2022") == "1", "Real VS2022 required")
class GoogleTestIntegrationTests(unittest.TestCase):
    def test_google_test_update_build_results_and_failure(self):
        with tempfile.TemporaryDirectory(prefix="cppbuild-gtest-") as temp:
            solution = Solution.create(Path(temp) / "日本語 space", "Demo")
            math = solution.add_project("Math", "Math", T.SHARED_LIBRARY)
            math.add_file("src/math.cpp", content='extern "C" __declspec(dllexport) int answer() { return 42; }', auto_update=False)
            self.assertTrue(math.update().success)
            tests = solution.add_project("Tests", "Tests", T.TEST)
            tests.set_build_settings(ProjectBuildSettings(googletest_archive=os.environ.get("CPPBUILD_TEST_GTEST_ARCHIVE")))
            tests.settings.link_project(math, T.SHARED_LIBRARY)
            tests.add_file("src/test.cpp", content='#include <gtest/gtest.h>\nextern "C" __declspec(dllimport) int answer();\nTEST(Smoke, Pass) { EXPECT_EQ(answer(), 42); }\n', auto_update=False)
            updated = tests.update()
            self.assertTrue(updated.success, updated.process.output)
            self.assertFalse(any(updated.build_directory.rglob("test.obj")))
            built = solution.build()
            self.assertTrue(built.success, "\n".join(p.output for p in built.processes))
            for configuration in ("Debug", "Release"):
                solution.set_build_settings(SolutionBuildSettings(configuration=configuration, test_projects=["Tests"]))
                tests.set_build_settings(ProjectBuildSettings(test_parallel=2, googletest_archive=os.environ.get("CPPBUILD_TEST_GTEST_ARCHIVE")))
                report = solution.test()
                self.assertTrue(report.success, str(report))
                self.assertEqual([c.name for c in report.cases], ["Smoke.Pass"])
            (tests.root / "src/test.cpp").write_text('#include <gtest/gtest.h>\nTEST(Smoke, Fail) { EXPECT_EQ(1, 2); }\n')
            report = tests.test()
            self.assertFalse(report.success)
            self.assertEqual(report.cases[0].status, "failed")
            (tests.root / "src/test.cpp").write_text('#include <gtest/gtest.h>\n')
            report = tests.test()
            self.assertFalse(report.success)
            self.assertEqual(report.cases, ())
            (tests.root / "src/test.cpp").write_text('this is invalid C++')
            report = tests.test()
            self.assertFalse(report.success)
            self.assertFalse(any(Path(p.command[0]).stem == "ctest" for p in report.processes))

    def test_public_run_order_continue_parallel_and_wait(self):
        with tempfile.TemporaryDirectory(prefix="cppbuild-run-") as temp:
            solution = Solution.create(temp, "Demo")
            for name in ("First", "Second"):
                app = solution.add_project(name, name, T.EXECUTABLE)
                app.add_file("src/main.cpp", content='#include <chrono>\n#include <thread>\n#include <iostream>\n#include <cstdlib>\nint main(int argc, char** argv) { if(argc > 2) std::this_thread::sleep_for(std::chrono::milliseconds(std::atoi(argv[2]))); std::cout << "' + name + '"; return argc > 1 ? std::atoi(argv[1]) : 0; }', auto_update=False)
            first = solution.get_project("First")
            first.set_build_settings(ProjectBuildSettings(run_arguments=["7"]))
            solution.set_build_settings(SolutionBuildSettings(run_projects=["First", "Second"]))
            report = solution.run()
            runs = [p for p in report.processes if Path(p.command[0]).stem in {"First", "Second"}]
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0].returncode, 7)
            solution.set_build_settings(SolutionBuildSettings(run_projects=["First", "Second"], run_continue_on_failure=True))
            runs = [p for p in solution.run().processes if Path(p.command[0]).stem in {"First", "Second"}]
            self.assertEqual([p.output for p in runs], ["First", "Second"])
            first.set_build_settings(ProjectBuildSettings(run_arguments=["0", "500"], run_wait=False))
            report = first.run()
            self.assertTrue(report.wait(timeout=20).success)
            solution.set_build_settings(SolutionBuildSettings(run_projects=["Second", "First"], run_parallel=2, run_wait=False))
            report = solution.run()
            finished = report.wait(timeout=20)
            self.assertTrue(finished.success)
            runs = [p for p in finished.processes if Path(p.command[0]).stem in {"First", "Second"}]
            self.assertEqual([p.output for p in runs], ["Second", "First"])

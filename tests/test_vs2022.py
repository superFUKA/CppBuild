"""Real VS2022 integration tests, opt in with CPPBUILD_TEST_VS2022=1.

No mocks in this suite. An opted-in run fails if VS2022 is unavailable.
"""
import os
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from cppbuild import ProjectBuildSettings, ProjectType as T, Solution, SolutionBuildSettings


@unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2022") == "1", "Set CPPBUILD_TEST_VS2022=1 for real VS2022 builds")
class VS2022Tests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cppbuild-vs-")
        self.addCleanup(temporary.cleanup)
        self.solution = Solution.create(Path(temporary.name) / "日本語 space", "Demo")

    def assertSuccess(self, report):
        if hasattr(report, "processes"):
            output = "\n".join(p.output for p in report.processes)
        else:
            output = report.process.output
        self.assertTrue(report.success, output)

    def test_update_filters_isolation_build_run_clean_rebuild(self):
        app = self.solution.add_project("App", "App", T.EXECUTABLE)
        tool = self.solution.add_project("Tool", "Tool", T.EXECUTABLE)
        app.add_file("src/main.cpp", content='#include <iostream>\nint main(int argc, char**) { std::cout << "hello"; return argc == 2 ? 7 : 0; }\n', auto_update=False)
        tool.add_file("src/main.cpp", content="int main() { return 0; }\n", auto_update=False)
        generated = app.update()
        self.assertSuccess(generated)
        self.assertTrue(generated.solution_file.is_file())
        self.assertTrue(generated.project_file.is_file())
        self.assertTrue(generated.filters_file.is_file())
        # Compiler probes may compile during configure; the user's main.cpp must not.
        self.assertFalse(any(generated.build_directory.rglob("main.obj")))
        built_tool = tool.build()
        self.assertSuccess(built_tool)
        tool_artifacts = {p: p.read_bytes() for p in built_tool.artifacts if p.is_file()}
        tool_project = tool._last_update.project_file
        tool_before = tool_project.read_bytes()
        added = app.add_file("src/nested/helper.cpp", content="int helper() { return 42; }\n")
        self.assertTrue(added.success, str(added))
        self.assertFalse(added.pending_update)
        tree = ET.parse(added.update.filters_file)
        self.assertTrue(any(e.text == "src\\nested" for e in tree.iter()))
        self.assertEqual(tool_project.read_bytes(), tool_before)
        self.solution.set_build_settings(SolutionBuildSettings(configuration="Release"))
        built = app.build()
        self.assertSuccess(built)
        release_exe = next(p for p in built.artifacts if p.suffix == ".exe")
        self.assertIn("Release", release_exe.parts)
        self.assertSuccess(app.run())
        app.set_build_settings(ProjectBuildSettings(configuration="Debug", run_arguments=["argument with spaces"]))
        run = app.run()
        self.assertEqual(run.processes[-1].returncode, 7)
        self.assertIn("hello", run.processes[-1].output)
        debug_exe = next(p for p in run.artifacts if p.suffix == ".exe")
        self.assertSuccess(app.clean())
        self.assertFalse(debug_exe.exists())
        self.assertTrue(release_exe.exists())
        for path, original in tool_artifacts.items():
            self.assertEqual(path.read_bytes(), original)
        self.assertSuccess(app.rebuild())
        self.assertTrue(debug_exe.exists())
        moved = app.move_file("src/nested/helper.cpp", "src/moved/helper.cpp")
        self.assertTrue(moved.success)
        self.assertFalse((app.root / "src/nested/helper.cpp").exists())
        self.assertTrue(app.remove_file("src/moved/helper.cpp").success)
        failed = app.add_file("src/broken.cpp", content="this cannot compile;\n")
        self.assertTrue(failed.success)  # generation is not compilation
        build_failure = app.run()
        self.assertFalse(build_failure.success)
        self.assertEqual(len(build_failure.processes), 2)  # configure/build; no run

    def test_static_shared_header_and_external_setting_reload(self):
        for kind in (T.STATIC_LIBRARY, T.SHARED_LIBRARY, T.HEADER_ONLY):
            with self.subTest(kind=kind):
                name = "Lib_" + kind.value
                project = self.solution.add_project(name, name, kind)
                if kind == T.HEADER_ONLY:
                    project.add_file("include/lib.hpp", content="#pragma once\ninline int value() { return 42; }\n", auto_update=False)
                else:
                    project.add_file("src/lib.cpp", content="__declspec(dllexport) int value() { return 42; }\n", auto_update=False)
                project.set_build_settings(ProjectBuildSettings(configuration="Release"))
                report = project.build()
                self.assertSuccess(report)
                self.assertTrue(project._last_update.project_file.exists())
                self.assertTrue(project._last_update.filters_file.exists())
                if kind == T.STATIC_LIBRARY:
                    self.assertTrue(any(p.suffix == ".lib" and p.exists() for p in report.artifacts))
                if kind == T.SHARED_LIBRARY:
                    self.assertTrue(any(p.suffix == ".dll" and p.exists() for p in report.artifacts))
        # A separate instance changes management data; update must read it itself.
        other = Solution.open(self.solution.root / ".cppbuild").get_project("Lib_static_library")
        changed = other.settings.get()
        changed.source_directories = ["newsrc"]
        other.settings.save(changed)
        other.add_file("newsrc/new.cpp", content="int new_value() { return 5; }", auto_update=False)
        project = self.solution.get_project("Lib_static_library")
        self.assertSuccess(project.build())
        self.assertEqual(project._resolved_build_settings().configuration, "Release")
        self.assertEqual([p.name for p in project._last_update.files], ["new.cpp"])

    def test_real_configure_failure_returns_report(self):
        # CMake reserves this target name; this exercises a real configure error.
        project = self.solution.add_project("Reserved", "all", T.EXECUTABLE)
        project.add_file("src/main.cpp", content="int main() {}", auto_update=False)
        report = project.build()
        self.assertFalse(report.success)
        self.assertEqual(len(report.processes), 1)
        self.assertIsNone(project._last_update.project_file)

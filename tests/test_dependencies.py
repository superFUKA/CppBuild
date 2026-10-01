import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cppbuild import ProjectType as T, ProjectBuildSettings, Solution, SolutionBuildSettings, SettingsError
from cppbuild.graph import resolve


class DependencyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cppbuild-deps-")
        self.addCleanup(temporary.cleanup)
        self.solution = Solution.create(temporary.name, "Demo")
        self.app = self.solution.add_project("App", "App", T.EXECUTABLE)
        self.math = self.solution.add_project("Math", "Math", T.STATIC_LIBRARY)
        self.tool = self.solution.add_project("Tool", "Tool", T.EXECUTABLE)

    def test_link_roundtrip_unlink_and_removal_protection(self):
        report = self.app.settings.link_project(self.math, T.STATIC_LIBRARY)
        reopened = Solution.open(self.solution.root / ".cppbuild")
        with self.assertRaises(SettingsError):
            reopened.remove_project("Math")
        reopened.get_project("App").settings.unlink(report.dependency_id)
        reopened.remove_project("Math")
        self.assertTrue(self.math.settings.path.exists())

    def test_cycle_and_mismatch_stop_before_tool(self):
        self.app.settings.link_project(self.math, T.STATIC_LIBRARY)
        self.math.set_build_settings(ProjectBuildSettings(architecture="Win32"))
        with patch("cppbuild.engine.process") as call, self.assertRaises(SettingsError):
            self.app.build()
        call.assert_not_called()
        self.math.set_build_settings(ProjectBuildSettings())
        self.math.settings.link_project(self.math, T.STATIC_LIBRARY)
        with patch("cppbuild.engine.process") as call, self.assertRaises(SettingsError):
            self.app.build()
        call.assert_not_called()

    def test_non_library_and_duplicate_links_rejected(self):
        with self.assertRaises(SettingsError):
            self.math.settings.link_project(self.app, T.EXECUTABLE)
        self.app.settings.link_project(self.math, T.STATIC_LIBRARY)
        with self.assertRaises(SettingsError):
            self.app.settings.link_project(self.math, T.STATIC_LIBRARY)
        roots, nodes = resolve([self.app, self.math])
        self.assertEqual([n.project.name for n in nodes], ["Math", "App"])

    def test_missing_and_unsupported_dependency_stop_before_process(self):
        self.app.settings.link_project(self.math, T.STATIC_LIBRARY)
        data = self.math.settings.get()
        from cppbuild import TypeSettingsData
        data.types = {T.INTERFACE_LIBRARY: TypeSettingsData()}
        with patch("cppbuild.engine.process") as call, self.assertRaises(SettingsError):
            self.math.settings.save(data)
        call.assert_not_called()

    def test_default_and_explicit_empty_selection(self):
        from cppbuild.solution_engine import _selected
        roots, _ = resolve(self.solution.projects())
        self.assertEqual(len(_selected(self.solution, roots, "build")), 3)
        self.solution.set_build_settings(SolutionBuildSettings(build_projects=[]))
        self.assertEqual(_selected(self.solution, roots, "build"), [])

    def test_invalid_dependency_record_and_recursive_settings(self):
        from cppbuild.dependencies import decode
        with self.assertRaises(SettingsError):
            decode(None)
        settings = SolutionBuildSettings()
        settings.external_build_settings["other/.cppbuild"] = settings
        with self.assertRaises(SettingsError):
            self.solution.set_build_settings(settings)

    @unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2022") == "1", "Real VS2022 required")
    def test_public_api_whole_individual_clean_and_unlink(self):
        self.math.add_file("include/math.hpp", content="int value();\n", auto_update=False)
        self.math.add_file("src/math.cpp", content="int value() { return 42; }\n", auto_update=False)
        self.app.add_file("src/main.cpp", content='#include "math.hpp"\nint main() { return value() == 42 ? 0 : 1; }\n', auto_update=False)
        self.tool.add_file("src/main.cpp", content="int main() { return 0; }\n", auto_update=False)
        link = self.app.settings.link_project(self.math, T.STATIC_LIBRARY)
        self.solution.set_build_settings(SolutionBuildSettings(build_projects=["App"], run_projects=["App"]))

        def success(report):
            self.assertTrue(report.success, "\n".join(p.output for p in report.processes))

        success(self.solution.run())
        self.assertFalse(list((self.tool.root / ".cppbuild/output").rglob("Tool.exe")))
        success(self.tool.build())
        math_lib = next((self.math.root / ".cppbuild/output").rglob("Math.lib"))
        tool_exe = next((self.tool.root / ".cppbuild/output").rglob("Tool.exe"))
        math_bytes, tool_bytes = math_lib.read_bytes(), tool_exe.read_bytes()
        whole_sln = self.solution._last_update.artifacts[0]
        whole_bytes = whole_sln.read_bytes()
        change = self.app.add_file("src/nested/helper.cpp", content="int helper() { return 2; }")
        self.assertTrue(change.success, str(change))
        self.assertEqual(whole_sln.read_bytes(), whole_bytes)
        success(self.app.clean())
        self.assertEqual(math_lib.read_bytes(), math_bytes)
        self.assertEqual(tool_exe.read_bytes(), tool_bytes)
        success(self.app.run())
        # MSBuild may relink in system TEMP. Compare clean with the latest build.
        math_bytes = math_lib.read_bytes()
        success(self.solution.clean())
        self.assertEqual(math_lib.read_bytes(), math_bytes)
        self.assertEqual(tool_exe.read_bytes(), tool_bytes)
        # Unselected App still depends on Math, so Solution clean must protect it.
        self.solution.set_build_settings(SolutionBuildSettings(build_projects=["Math"]))
        success(self.solution.clean())
        self.assertTrue(math_lib.exists())
        self.app.settings.unlink(link.dependency_id)
        (self.app.root / "src/main.cpp").write_text("int main() { return 0; }\n")
        self.solution.set_build_settings(SolutionBuildSettings(build_projects=["App"], configuration="Release"))
        success(self.solution.build())
        self.assertNotIn("dep_Math", next((self.app.root / ".cppbuild/output/intermediate").rglob("CMakeLists.txt")).read_text())

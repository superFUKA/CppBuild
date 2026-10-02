"""The Solution build tree location, Project artifact directories, and real linking with custom outputs."""
from dataclasses import replace
import os
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cppbuild import (CMakeSettings, ProjectBuildSettings, ProjectType as T,
                      SettingsError, Solution, SolutionBuildSettings, TemplateTools)
from cppbuild import engine, generators, output_paths, workspace


class OutputPathTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cb-out-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.solution = Solution.create(self.root / "Demo", "Demo")
        self.app = self.solution.add_project("App", "App", T.EXECUTABLE)
        self.app.add_file("src/main.cpp", content="int main() {}", auto_update=False)

    def test_defaults_validation_and_nonpersistence(self):
        self.assertIsNone(self.app._build_settings.artifact_directory)
        with self.assertRaises(TypeError):
            ProjectBuildSettings(intermediate_directory="work")
        self.solution.set_build_settings(SolutionBuildSettings(intermediate_directory="../whole"))
        tree = engine.tree(self.solution, self.solution._build_settings)[0]
        self.assertEqual(tree.parent, self.solution.root / "whole")
        self.app.set_build_settings(ProjectBuildSettings(artifact_directory=str(self.root / "bin")))
        self.assertEqual(output_paths.root(self.app, self.app._build_settings.artifact_directory), self.root / "bin")
        for value in (None, "", 1, "a;bad", "$<CONFIG>", "C:relative", "bad\npath"):
            with self.subTest(value=value), self.assertRaises(SettingsError):
                self.solution.set_build_settings(SolutionBuildSettings(intermediate_directory=value))
            if value is not None:
                with self.subTest(value=value), self.assertRaises(SettingsError):
                    self.app.set_build_settings(ProjectBuildSettings(artifact_directory=value))
        reopened = Solution.open(self.solution.root / ".cppbuild")
        self.assertEqual(reopened._build_settings.intermediate_directory, "output/intermediate")
        self.assertEqual(reopened.get_project("App")._build_settings, ProjectBuildSettings())
        self.assertNotIn("artifact_directory", self.app.settings.path.read_text())

    def test_one_tree_per_environment_and_separate_artifact_areas(self):
        library = self.solution.add_project("Library", "Library", T.STATIC_LIBRARY)
        base = self.solution._build_settings
        tree = engine.tree(self.solution, base)[0]
        self.assertEqual(engine.tree(self.solution, replace(base, configuration="Release"))[0], tree)
        self.assertEqual(engine.tree(self.solution, library._resolved_build_settings(T.SHARED_LIBRARY))[0], tree)
        if os.name == "nt":
            self.assertNotEqual(engine.tree(self.solution, replace(base, architecture="Win32"))[0], tree)
        shared = str(self.root / "shared")
        self.assertNotEqual(output_paths.area(self.app, shared), output_paths.area(library, shared))
        clone_path = self.root / "template"
        shutil.copytree(self.solution.root, clone_path)
        clone = Solution.open(clone_path / ".cppbuild").get_project("App")
        self.assertEqual(clone.settings.get().guid, self.app.settings.get().guid)
        self.assertNotEqual(output_paths.area(clone, shared), output_paths.area(self.app, shared))

    def test_artifact_directory_reaches_cmake_only_through_the_cache(self):
        self.app.set_build_settings(ProjectBuildSettings(artifact_directory="../custom/bin"))
        plan = workspace.plan(self.solution)
        toolchain = generators.Toolchain("Ninja Multi-Config", None)
        script = plan.cache_script(toolchain)
        expected = output_paths.area(self.app, "../custom/bin", toolchain.context).as_posix()
        self.assertIn(f'set(Demo_App_OUTPUT_DIRECTORY "{expected}" CACHE PATH "" FORCE)', script)
        self.assertNotIn("custom", "".join(plan.documents().values()))

    def test_generated_leaves_excluded_from_scan_and_templates_after_reopen(self):
        data = self.app.settings.get()
        data.source_directories = ["."]
        self.app.settings.save(data)
        for value in ("../custom/work", "../custom/bin"):
            directory = output_paths.claim(self.app, value)
            (directory / "generated.cpp").write_text("#error must_not_compile")
        reopened = Solution.open(self.solution.root / ".cppbuild")
        self.assertEqual([p.name for p in engine._scan(reopened.get_project("App"))], ["main.cpp"])
        with self.assertRaises(SettingsError):
            reopened.get_project("App").remove_file(str(directory / "generated.cpp"), auto_update=False)
        template = self.root / "snapshot"
        TemplateTools.create_solution_template(reopened, template)
        self.assertFalse(list(template.rglob("generated.cpp")))

    def test_moving_project_preserves_external_roots_but_changes_cache_identity(self):
        self.app.set_build_settings(ProjectBuildSettings(artifact_directory="../../work"))
        before = self.app._resolved_build_settings()
        external = output_paths.root(self.app, before.artifact_directory)
        old = output_paths.area(self.app, before.artifact_directory)
        directory = output_paths.claim(self.app, before.artifact_directory)
        (directory / "keep").write_text("old")
        report = self.solution.move_project("App", "nested/App", auto_update=False)
        self.assertTrue(report.success)
        after = self.app._resolved_build_settings()
        self.assertEqual(output_paths.root(self.app, after.artifact_directory), external)
        self.assertNotEqual(output_paths.area(self.app, after.artifact_directory), old)
        self.assertTrue((directory / "keep").is_file())


class OutputBuildScenario:
    generator = None

    def test_custom_output_link_run_clean_and_reconfigure(self):
        # MSBuild disables some incremental tracking beneath system TEMP.
        workspace_root = Path(__file__).resolve().parents[1] / ".test-work"
        workspace_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="out-", dir=workspace_root) as temporary:
            root = Path(temporary)
            solution = Solution.create(root / "Demo", "Demo")
            solution.set_build_settings(SolutionBuildSettings(cmake=CMakeSettings(generator=self.generator), intermediate_directory="../whole work"))
            library = solution.add_project("Library", "Library", T.STATIC_LIBRARY)
            library.add_file("src/lib.cpp", content="int value() { return 42; }", auto_update=False)
            library.set_build_settings(ProjectBuildSettings(artifact_directory=str(root / "products")))
            app = solution.add_project("App", "App", T.EXECUTABLE)
            app.add_file("src/main.cpp", content="int value(); int main() { return value() == 42 ? 0 : 1; }", auto_update=False)
            app.settings.link_project(library, T.STATIC_LIBRARY)
            app.set_build_settings(ProjectBuildSettings(artifact_directory=str(root / "products")))

            def success(report):
                self.assertTrue(report.success, "\n".join(p.output for p in report.processes))
                return report

            run = success(app.run())
            build = engine.tree(solution, app._resolved_build_settings())[0]
            self.assertTrue(build.is_relative_to(solution.root / "whole work"))
            lib_file = engine.main_artifact(build, "Demo_Library", "Debug")
            exe = engine.main_artifact(build, "Demo_App", "Debug")
            self.assertTrue(lib_file.is_relative_to(root / "products"))
            self.assertTrue(exe.is_relative_to(root / "products"))
            self.assertIn(exe, run.artifacts)
            stamp = lib_file.stat().st_mtime_ns
            whole = success(solution.build())
            self.assertEqual(lib_file.stat().st_mtime_ns, stamp)
            self.assertIn(lib_file, whole.artifacts)
            with patch("cppbuild.engine.configure", side_effect=AssertionError("clean must not configure")):
                success(app.clean())
            self.assertFalse(exe.exists())
            self.assertTrue(lib_file.exists())
            success(app.rebuild())
            app.set_build_settings(replace(app._build_settings, artifact_directory="../new products"))
            success(app.run())
            new_exe = engine.main_artifact(build, "Demo_App", "Debug")
            self.assertNotEqual(exe, new_exe)
            self.assertTrue(new_exe.is_relative_to(app.root / "new products"))
            success(app.clean())
            self.assertFalse(new_exe.exists())


@unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2022") == "1", "Real VS2022 required")
class OutputVS2022Tests(OutputBuildScenario, unittest.TestCase):
    generator = "Visual Studio 17 2022"


@unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2026") == "1", "Real VS2026 required")
class OutputVS2026Tests(OutputBuildScenario, unittest.TestCase):
    generator = "Visual Studio 18 2026"


@unittest.skipUnless(os.environ.get("CPPBUILD_TEST_NINJA") == "1", "Real Ninja required")
class OutputNinjaTests(OutputBuildScenario, unittest.TestCase):
    generator = "Ninja Multi-Config"

"""Output ownership, relocation, and real linking outside the CMake binary tree."""
from dataclasses import replace
import os
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cppbuild import (CMakeSettings, INHERIT, ProjectBuildSettings, ProjectType as T,
                      SettingsError, Solution, SolutionBuildSettings, TemplateTools)
from cppbuild import engine, output_paths


class OutputPathTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cb-out-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.solution = Solution.create(self.root / "Demo", "Demo")
        self.app = self.solution.add_project("App", "App", T.EXECUTABLE)
        self.app.add_file("src/main.cpp", content="int main() {}", auto_update=False)

    def test_defaults_independence_validation_and_nonpersistence(self):
        before = self.app._resolved_build_settings()
        self.solution.set_build_settings(SolutionBuildSettings(intermediate_directory="../whole"))
        self.assertEqual(self.app._resolved_build_settings().intermediate_directory, before.intermediate_directory)
        self.app.set_build_settings(ProjectBuildSettings(intermediate_directory="../work", artifact_directory=str(self.root / "bin")))
        self.assertEqual(output_paths.root(self.app, self.app._build_settings.intermediate_directory), self.app.root / "work")
        self.assertEqual(output_paths.root(self.app, self.app._build_settings.artifact_directory), self.root / "bin")
        for field in ("intermediate_directory", "artifact_directory"):
            for value in (None, INHERIT, "", 1, "a;bad", "$<CONFIG>", "C:relative", "bad\npath"):
                with self.subTest(field=field, value=value), self.assertRaises(SettingsError):
                    self.app.set_build_settings(ProjectBuildSettings(**{field: value}))
        reopened = Solution.open(self.solution.root / ".cppbuild")
        self.assertEqual(reopened._build_settings.intermediate_directory, "output/intermediate")
        self.assertEqual(reopened.get_project("App")._build_settings, ProjectBuildSettings())
        self.assertNotIn("intermediate_directory", self.app.settings.path.read_text())

    def test_shared_root_separates_projects_copies_and_types(self):
        library = self.solution.add_project("Library", "Library", T.STATIC_LIBRARY)
        settings = ProjectBuildSettings(intermediate_directory=str(self.root / "shared"), artifact_directory=str(self.root / "shared"))
        for project in (self.app, library):
            project.set_build_settings(settings)
        self.assertNotEqual(output_paths.area(self.app, settings.artifact_directory), output_paths.area(library, settings.artifact_directory))
        static = library._resolved_build_settings(T.STATIC_LIBRARY)
        shared = library._resolved_build_settings(T.SHARED_LIBRARY)
        self.assertNotEqual(engine._locations(library, static), engine._locations(library, shared))
        self.assertNotEqual(engine._artifact_directory(library, static), engine._artifact_directory(library, shared))
        self.assertEqual(engine._locations(library, static), engine._locations(library, replace(static, configuration="Release")))
        clone_path = self.root / "template"
        shutil.copytree(self.solution.root, clone_path)
        clone = Solution.open(clone_path / ".cppbuild").get_project("App")
        self.assertEqual(clone.settings.get().guid, self.app.settings.get().guid)
        self.assertNotEqual(output_paths.area(clone, settings.artifact_directory), output_paths.area(self.app, settings.artifact_directory))

    def test_generated_leaves_excluded_from_scan_and_templates_after_reopen(self):
        self.app.set_build_settings(ProjectBuildSettings(intermediate_directory="../custom/work", artifact_directory="../custom/bin"))
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
        self.app.set_build_settings(ProjectBuildSettings(intermediate_directory="../../work", artifact_directory="output/artifacts"))
        before = self.app._resolved_build_settings()
        external = output_paths.root(self.app, before.intermediate_directory)
        old = engine._locations(self.app, before)
        directory = output_paths.claim(self.app, before.intermediate_directory)
        (directory / "keep").write_text("old")
        report = self.solution.move_project("App", "nested/App", auto_update=False)
        self.assertTrue(report.success)
        after = self.app._resolved_build_settings()
        self.assertEqual(output_paths.root(self.app, after.intermediate_directory), external)
        self.assertNotEqual(engine._locations(self.app, after), old)
        self.assertTrue((directory / "keep").is_file())
        self.assertEqual(after.artifact_directory, "output/artifacts")


class OutputBuildScenario:
    generator = None

    def test_custom_output_link_run_clean_and_reconfigure(self):
        # MSBuild disables some incremental tracking beneath system TEMP.
        workspace = Path(__file__).resolve().parents[1] / ".test-work"
        workspace.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="out-", dir=workspace) as temporary:
            root = Path(temporary)
            solution = Solution.create(root / "Demo", "Demo")
            solution.set_build_settings(SolutionBuildSettings(cmake=CMakeSettings(generator=self.generator), intermediate_directory="../whole work"))
            library = solution.add_project("Library", "Library", T.STATIC_LIBRARY)
            library.add_file("src/lib.cpp", content="#ifdef _WIN32\n__declspec(dllexport)\n#endif\nint value() { return 42; }", auto_update=False)
            library.set_build_settings(ProjectBuildSettings(intermediate_directory="../local work", artifact_directory=str(root / "products")))
            app = solution.add_project("App", "App", T.EXECUTABLE)
            app.add_file("src/main.cpp", content="int value(); int main() { return value() == 42 ? 0 : 1; }", auto_update=False)
            app.settings.link_project(library, T.STATIC_LIBRARY)
            app.set_build_settings(ProjectBuildSettings(intermediate_directory=str(root / "work"), artifact_directory=str(root / "products")))

            def success(report):
                self.assertTrue(report.success, "\n".join(p.output for p in report.processes))
                return report

            success(app.run())
            lib_settings = library._resolved_build_settings()
            _, lib_build = engine._locations(library, lib_settings)
            lib_file = engine._outputs(library, lib_build, "Debug")["file"]
            exe = engine._outputs(app, engine._locations(app, app._resolved_build_settings())[1], "Debug")["file"]
            self.assertTrue(lib_file.is_relative_to(root / "products"))
            self.assertTrue(exe.is_relative_to(root / "products"))
            stamp = lib_file.stat().st_mtime_ns
            success(solution.build())
            self.assertEqual(lib_file.stat().st_mtime_ns, stamp)
            self.assertEqual(engine._outputs(library, lib_build, "Debug")["file"], lib_file)
            self.assertIn(lib_file, engine._artifacts(library, lib_settings, lib_build))
            with patch("cppbuild.engine._generate", side_effect=AssertionError("clean must not configure")):
                success(app.clean())
            self.assertFalse(exe.exists())
            self.assertTrue(lib_file.exists())
            success(app.rebuild())
            app.set_build_settings(replace(app._build_settings, artifact_directory="../new products"))
            success(app.clean())  # No tree yet at the newly selected output location.
            self.assertTrue(exe.exists())
            success(app.run())
            self.assertTrue(exe.exists())
            new_exe = engine._outputs(app, engine._locations(app, app._resolved_build_settings())[1], "Debug")["file"]
            self.assertNotEqual(exe, new_exe)
            success(app.clean())
            self.assertFalse(new_exe.exists())
            self.assertTrue(exe.exists())
            self.assertTrue(lib_file.exists())
            # Switching the provider's kind still resolves its custom output and DLL path.
            library.set_build_settings(replace(library._build_settings, project_type=T.SHARED_LIBRARY))
            success(app.run())
            _, shared_build = engine._locations(library, library._resolved_build_settings())
            shared_file = engine._outputs(library, shared_build, "Debug")["file"]
            self.assertTrue(shared_file.is_file())
            self.assertTrue(shared_file.is_relative_to(root / "products"))
            self.assertNotEqual(shared_file, lib_file)
            success(app.clean())
            self.assertTrue(shared_file.is_file())


@unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2022") == "1", "Real VS2022 required")
class OutputVS2022Tests(OutputBuildScenario, unittest.TestCase):
    generator = "Visual Studio 17 2022"


@unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2026") == "1", "Real VS2026 required")
class OutputVS2026Tests(OutputBuildScenario, unittest.TestCase):
    generator = "Visual Studio 18 2026"


@unittest.skipUnless(os.environ.get("CPPBUILD_TEST_NINJA") == "1", "Real Ninja required")
class OutputNinjaTests(OutputBuildScenario, unittest.TestCase):
    generator = "Ninja Multi-Config"

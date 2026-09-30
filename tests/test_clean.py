"""Clean uses existing owned trees, without configuring or building dependencies."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cppbuild import ProjectBuildSettings, ProjectType as T, Solution, SolutionBuildSettings
from cppbuild import engine, storage


class CleanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cppbuild-clean-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.solution = Solution.create(self.root / "Demo", "Demo")
        self.app = self.solution.add_project("App", "App", T.EXECUTABLE)

    def owned_tree(self, project):
        settings = project._resolved_build_settings()
        source, build = engine._locations(project, settings)
        storage.atomic_write(build / "cppbuild-owner.json", storage.encoded(engine._owner(project, settings, source)))
        (build / "CMakeCache.txt").write_text("test cache")
        (build / f"{project.name}.sln").write_text("test solution")
        return build

    def success(self, report):
        self.assertTrue(report.success, "\n".join(p.output for p in report.processes))

    def test_no_tree_is_success_without_tools_or_sources(self):
        with patch("cppbuild.engine.process") as process:
            for owner in (self.app, self.solution):
                result = owner.clean()
                self.success(result)
                self.assertIn("Nothing to clean", result.processes[0].output)
                self.assertEqual(result.processes[0].command, ())
        process.assert_not_called()
        self.assertFalse((self.app.root / ".cppbuild/build").exists())
        self.assertFalse(list(self.solution.root.rglob("CMakeLists.txt")))

    def test_empty_solution_selection_is_success(self):
        self.solution.set_build_settings(SolutionBuildSettings(build_projects=[]))
        with patch("cppbuild.engine.process") as process:
            self.success(self.solution.clean())
        process.assert_not_called()

    def test_unowned_or_incomplete_tree_fails_without_modification(self):
        build = self.owned_tree(self.app)
        for filename in ("App.sln", "CMakeCache.txt", "cppbuild-owner.json"):
            with self.subTest(filename=filename):
                path = build / filename
                original = path.read_bytes()
                path.unlink()
                before = {p: p.read_bytes() for p in build.iterdir()}
                with patch("cppbuild.engine.process") as process:
                    report = self.app.clean()
                self.assertFalse(report.success)
                process.assert_not_called()
                self.assertEqual(before, {p: p.read_bytes() for p in build.iterdir()})
                path.write_bytes(original)

    def test_wrong_owner_and_invalid_marker_are_reported(self):
        build = self.owned_tree(self.app)
        for content in ('{}', 'invalid json'):
            with self.subTest(content=content):
                (build / "cppbuild-owner.json").write_text(content)
                with patch("cppbuild.engine.process") as process:
                    self.assertFalse(self.app.clean().success)
                process.assert_not_called()

    def test_missing_selected_external_dependency_does_not_block_clean(self):
        provider = Solution.create(self.root / "Provider", "Provider")
        provider.add_project("Lib", "Lib", T.STATIC_LIBRARY)
        self.app.settings.link_solution(provider.root / ".cppbuild", T.STATIC_LIBRARY)
        provider.settings.path.rename(provider.settings.path.with_suffix(".offline"))
        self.owned_tree(self.app)
        for owner in (self.app, self.solution):
            with self.subTest(owner=type(owner).__name__), patch("cppbuild.engine._generate") as generate:
                with patch("cppbuild.tooling.process", return_value=engine.ProcessReport(("cmake",), 0, "cleaned")) as process:
                    self.success(owner.clean())
                generate.assert_not_called()
                self.assertEqual(process.call_count, 1)
                command = process.call_args.args[1]
                self.assertEqual(command[command.index("--target") + 1], "clean")
                self.assertNotIn("-S", command)

    def test_shared_transitive_dependency_is_preserved(self):
        library = self.solution.add_project("Lib", "Lib", T.STATIC_LIBRARY)
        middle = self.solution.add_project("Middle", "Middle", T.STATIC_LIBRARY)
        middle.settings.link_project(library, T.STATIC_LIBRARY)
        self.app.settings.link_project(middle, T.STATIC_LIBRARY)
        self.solution.set_build_settings(SolutionBuildSettings(build_projects=["Lib"]))
        build = self.owned_tree(library)
        artifact = build / "keep.lib"
        artifact.write_bytes(b"keep")
        with patch("cppbuild.engine.process") as process:
            result = self.solution.clean()
        self.success(result)
        self.assertIn("shared dependency", result.processes[0].output)
        process.assert_not_called()
        self.assertEqual(artifact.read_bytes(), b"keep")

    def test_unknown_protection_fails_without_cleaning_selected(self):
        library = self.solution.add_project("Lib", "Lib", T.STATIC_LIBRARY)
        provider = Solution.create(self.root / "Provider", "Provider")
        provider.add_project("External", "External", T.STATIC_LIBRARY)
        self.app.settings.link_solution(provider.root / ".cppbuild", T.STATIC_LIBRARY)
        provider.settings.path.rename(provider.settings.path.with_suffix(".offline"))
        self.solution.set_build_settings(SolutionBuildSettings(build_projects=["Lib"]))
        self.owned_tree(library)
        with patch("cppbuild.engine.process") as process:
            report = self.solution.clean()
        self.assertFalse(report.success)
        self.assertIn("could not check unselected dependencies", report.processes[0].output)
        process.assert_not_called()

    def test_failed_clean_retains_known_artifacts_and_reports_failure(self):
        build = self.owned_tree(self.app)
        self.app._known_artifacts = (build / "App.exe",)
        with patch("cppbuild.tooling.process", return_value=engine.ProcessReport(("cmake",), 1, "broken cache")):
            report = self.app.clean()
        self.assertFalse(report.success)
        self.assertEqual(self.app._build_state, "failed")
        self.assertEqual(self.app._known_artifacts, (build / "App.exe",))
        self.assertIsNone(self.app._last_update)

    @unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2022") == "1", "Real VS2022 required")
    def test_real_clean_does_not_regenerate_and_preserves_other_configuration(self):
        self.app.add_file("src/main.cpp", content="int main() { return 0; }", auto_update=False)
        reports = []
        for configuration in ("Debug", "Release"):
            self.app.set_build_settings(ProjectBuildSettings(configuration=configuration))
            report = self.app.build()
            self.success(report)
            reports.append(report)
        debug_exe = next(p for p in reports[0].artifacts if p.suffix == ".exe")
        release_exe = next(p for p in reports[1].artifacts if p.suffix == ".exe")
        source, build = engine._locations(self.app, self.app._resolved_build_settings())
        cmake = source / "CMakeLists.txt"
        cmake.write_text("message(FATAL_ERROR clean_must_not_configure)\n")
        # Even invalid source inputs and a missing source file must not require generation.
        data = self.app.settings.get()
        data.source_directories = ["build"]
        self.app.settings.save(data)
        (self.app.root / "src/main.cpp").unlink()
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in
                  (cmake, build / "CMakeCache.txt", build / "App.vcxproj")}
        self.app.set_build_settings(ProjectBuildSettings(configuration="Debug"))
        report = self.app.clean()
        self.success(report)
        self.assertEqual(len(report.processes), 1)
        self.assertFalse(debug_exe.exists())
        self.assertTrue(release_exe.exists())
        self.assertFalse(list((build / "App.dir/Debug").glob("*.obj")))
        self.app.set_build_settings(ProjectBuildSettings(configuration="Release"))
        report = self.solution.clean()
        self.success(report)
        self.assertEqual(len(report.processes), 1)
        self.assertFalse(release_exe.exists())
        self.assertFalse((self.solution.root / ".cppbuild/build").exists())
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before})

    @unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2022") == "1", "Real VS2022 required")
    def test_real_clean_with_offline_external_solution(self):
        provider = Solution.create(self.root / "Provider", "Provider")
        lib = provider.add_project("Lib", "Lib", T.STATIC_LIBRARY)
        lib.add_file("src/lib.cpp", content="int value() { return 42; }", auto_update=False)
        self.app.add_file("src/main.cpp", content="int value(); int main() { return value() == 42 ? 0 : 1; }", auto_update=False)
        self.app.settings.link_solution(provider.root / ".cppbuild", T.STATIC_LIBRARY)
        built = self.app.build()
        self.success(built)
        executable = next(p for p in built.artifacts if p.suffix == ".exe")
        library = next((lib.root / ".cppbuild/build").rglob("Lib.lib"))
        original = library.read_bytes()
        provider.settings.path.rename(provider.settings.path.with_suffix(".offline"))
        self.success(self.solution.clean())
        self.assertFalse(executable.exists())
        self.assertEqual(library.read_bytes(), original)

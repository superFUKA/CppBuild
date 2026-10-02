"""Clean uses the existing owned Solution tree, without configuring or resolving dependencies."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cppbuild import ProjectBuildSettings, ProjectType as T, Solution, SolutionBuildSettings
from cppbuild import engine, generators, storage


def fake_codemodel(build, names, configurations=("Debug",)):
    """A minimal CMake File API reply listing targets, as a configured tree has."""
    reply = build / ".cmake/api/v1/reply"
    reply.mkdir(parents=True, exist_ok=True)
    targets = []
    for name in names:
        (reply / f"target-{name}.json").write_text(json.dumps({"name": name, "artifacts": [], "paths": {"build": "."}}))
        targets.append({"name": name, "jsonFile": f"target-{name}.json"})
    (reply / "codemodel-v2-test.json").write_text(json.dumps(
        {"configurations": [{"name": c, "targets": targets} for c in configurations]}))
    (reply / "index-test.json").write_text(json.dumps(
        {"reply": {"client-cppbuild": {"codemodel-v2": {"jsonFile": "codemodel-v2-test.json"}}}}))


class CleanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cppbuild-clean-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.solution = Solution.create(self.root / "Demo", "Demo")
        self.app = self.solution.add_project("App", "App", T.EXECUTABLE)

    def owned_tree(self, *names):
        build, toolchain = engine.tree(self.solution, self.solution._build_settings)
        storage.atomic_write(build / "cppbuild-owner.json", storage.encoded(engine._owner(self.solution, toolchain)))
        msbuild = self.root / "VS/MSBuild/Current/Bin/amd64/MSBuild.exe"
        msbuild.parent.mkdir(parents=True, exist_ok=True)
        msbuild.write_text("fake")
        (build / "CMakeCache.txt").write_text(f"CMAKE_GENERATOR_INSTANCE:INTERNAL={(self.root / 'VS').as_posix()}\n"
                                              "CMAKE_GENERATOR_PLATFORM:INTERNAL=x64\n")
        (build / f"Demo{toolchain.solution_suffix or ''}").write_text("test solution")
        (build / "build.ninja").write_text("test ninja")
        names = names or ("Demo_App",)
        for name in (*names, "ALL_BUILD"):
            (build / f"{name}.vcxproj").write_text("test project")
        fake_codemodel(build, names)
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
        self.assertFalse((self.solution.root / ".cppbuild/output").exists())
        self.assertFalse(list(self.solution.root.rglob("CMakeLists.txt")))

    def test_empty_solution_selection_is_success(self):
        self.solution.set_build_settings(SolutionBuildSettings(build_projects=[]))
        with patch("cppbuild.engine.process") as process:
            self.success(self.solution.clean())
        process.assert_not_called()

    def test_unowned_or_incomplete_tree_fails_without_modification(self):
        build = self.owned_tree()
        suffix = generators.resolve(self.solution._build_settings).solution_suffix
        generated = f"Demo{suffix}" if suffix else "build.ninja"
        for filename in (generated, "CMakeCache.txt", "cppbuild-owner.json"):
            with self.subTest(filename=filename):
                path = build / filename
                original = path.read_bytes()
                path.unlink()
                before = {p: p.read_bytes() for p in build.iterdir() if p.is_file()}
                with patch("cppbuild.engine.process") as process:
                    report = self.app.clean()
                self.assertFalse(report.success)
                process.assert_not_called()
                self.assertEqual(before, {p: p.read_bytes() for p in build.iterdir() if p.is_file()})
                path.write_bytes(original)

    def test_wrong_owner_and_invalid_marker_are_reported(self):
        build = self.owned_tree()
        for content in ('{}', 'invalid json'):
            with self.subTest(content=content):
                (build / "cppbuild-owner.json").write_text(content)
                with patch("cppbuild.engine.process") as process:
                    self.assertFalse(self.app.clean().success)
                process.assert_not_called()

    def test_missing_external_dependency_does_not_block_clean(self):
        provider = Solution.create(self.root / "Provider", "Provider")
        provider.add_project("Lib", "Lib", T.STATIC_LIBRARY)
        self.app.settings.link_solution(provider.root / ".cppbuild", T.STATIC_LIBRARY)
        provider.settings.path.rename(provider.settings.path.with_suffix(".offline"))
        self.owned_tree()
        visual_studio = generators.resolve(self.solution._build_settings).visual_studio
        for owner, whole in ((self.app, False), (self.solution, True)):
            with self.subTest(owner=type(owner).__name__), patch("cppbuild.engine.configure") as configure:
                with patch("cppbuild.tooling.process", return_value=engine.ProcessReport(("cmake",), 0, "cleaned")) as tool, \
                        patch("cppbuild.engine.process", return_value=engine.ProcessReport(("ninja",), 0, "cleaned")) as native:
                    self.success(owner.clean())
                configure.assert_not_called()
                # Clean never goes through cmake, which could reconfigure first.
                tool.assert_not_called()
                if visual_studio:
                    command = [str(c) for c in native.call_args.args[0]]
                    self.assertIn("-t:Clean", command)
                    self.assertEqual(Path(command[1]).name, "ALL_BUILD.vcxproj" if whole else "Demo_App.vcxproj")
                    self.assertEqual("-p:BuildProjectReferences=false" in command, not whole)
                elif whole:
                    self.assertEqual(native.call_args.args[0][-2:], ["-t", "clean"])
                else:
                    native.assert_not_called()

    def test_selected_projects_clean_only_their_targets(self):
        self.solution.add_project("Lib", "Lib", T.STATIC_LIBRARY)
        self.solution.set_build_settings(SolutionBuildSettings(build_projects=["Lib"]))
        self.owned_tree("Demo_App", "Demo_Lib")
        if not generators.resolve(self.solution._build_settings).visual_studio:
            self.skipTest("Visual Studio cleans single Projects with MSBuild")
        with patch("cppbuild.engine.process", return_value=engine.ProcessReport(("msbuild",), 0, "cleaned")) as native:
            self.success(self.solution.clean())
        self.assertEqual(native.call_count, 1)
        self.assertEqual(Path(str(native.call_args.args[0][1])).name, "Demo_Lib.vcxproj")

    def test_target_missing_from_tree_is_nothing_to_clean(self):
        self.owned_tree("Demo_Other")
        with patch("cppbuild.tooling.process") as tool, patch("cppbuild.engine.process") as native:
            report = self.app.clean()
        self.success(report)
        self.assertIn("Nothing to clean", report.processes[0].output)
        tool.assert_not_called()
        native.assert_not_called()

    def test_failed_clean_retains_known_artifacts_and_reports_failure(self):
        build = self.owned_tree()
        self.app._known_artifacts = (build / "App.exe",)
        failed = engine.ProcessReport(("cmake",), 1, "broken cache")
        with patch("cppbuild.tooling.process", return_value=failed), patch("cppbuild.engine.process", return_value=failed):
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
        build = engine.tree(self.solution, self.app._resolved_build_settings())[0]
        cmake = self.app.root / "CMakeLists.txt"
        cmake.write_text("# Generated by CppBuild\nmessage(FATAL_ERROR clean_must_not_configure)\n")
        # Even invalid source inputs and a missing source file must not require generation.
        data = self.app.settings.get()
        data.source_directories = ["build"]
        self.app.settings.save(data)
        (self.app.root / "src/main.cpp").unlink()
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in
                  (cmake, build / "CMakeCache.txt", build / "App/Demo_App.vcxproj")}
        self.app.set_build_settings(ProjectBuildSettings(configuration="Debug"))
        report = self.app.clean()
        self.success(report)
        self.assertEqual(len(report.processes), 1)
        self.assertFalse(debug_exe.exists())
        self.assertTrue(release_exe.exists())
        self.assertFalse(list((build / "App/Demo_App.dir/Debug").glob("*.obj")))
        self.solution.set_build_settings(SolutionBuildSettings(configuration="Release"))
        report = self.solution.clean()
        self.success(report)
        self.assertEqual(len(report.processes), 1)
        self.assertFalse(release_exe.exists())
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
        build = engine.tree(self.solution, self.solution._build_settings)[0]
        library = next(build.rglob("Lib.lib"))
        original = library.read_bytes()
        provider.settings.path.rename(provider.settings.path.with_suffix(".offline"))
        # A Project clean leaves what it links; a whole clean empties the tree's configuration.
        self.success(self.app.clean())
        self.assertFalse(executable.exists())
        self.assertEqual(library.read_bytes(), original)
        self.success(self.solution.clean())
        self.assertFalse(library.exists())

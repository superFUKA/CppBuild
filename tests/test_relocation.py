import os
from concurrent.futures import Future
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cppbuild import (
    CMakePackage, CMakeSource, FileOperationReport, ImportedLibrary, ProjectBuildSettings,
    ProjectType as T, SettingsConflictError, SettingsError, Solution, SolutionBuildSettings,
)
from cppbuild import engine, storage
from cppbuild.graph import resolve


class RelocationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.solution = Solution.create(Path(temporary.name) / "日本語 space", "Demo")
        self.lib = self.solution.add_project("Lib", "Lib", T.STATIC_LIBRARY)
        self.app = self.solution.add_project("App", "App", T.EXECUTABLE)
        self.lib.add_file("src/lib.cpp", content="int value() { return 42; }", auto_update=False)
        self.app.add_file("src/main.cpp", content="int value(); int main() { return value() == 42 ? 0 : 1; }", auto_update=False)
        self.link = self.app.settings.link_project(self.lib, T.STATIC_LIBRARY)

    def test_move_preserves_objects_settings_links_and_reopen(self):
        original = self.lib.root
        settings = self.lib.settings
        self.lib.set_build_settings(ProjectBuildSettings(parallel=3, run_arguments=["opaque"]))
        self.solution.set_build_settings(SolutionBuildSettings(configuration="Release", build_projects=["App"]))
        callback = self.solution.on("after_update", lambda event: None)
        with patch("subprocess.run", side_effect=AssertionError("No tools during deferred move")):
            report = self.solution.move_project("Lib", "libs/移動 space", auto_update=False)
        self.assertIsInstance(report, FileOperationReport)
        self.assertTrue(report.success)
        self.assertTrue(report.pending_update)
        self.assertFalse(original.exists())
        self.assertTrue((self.lib.root / "src/lib.cpp").is_file())
        self.assertIs(self.lib, self.solution.get_project("Lib"))
        self.assertIs(self.lib.settings, settings)
        self.assertEqual(self.lib._resolved_build_settings().parallel, 3)
        self.assertEqual(self.lib._resolved_build_settings().configuration, "Release")
        self.assertTrue(self.solution.off(callback))
        self.assertEqual(self.solution.settings.get().main_project, "Lib")
        self.assertIn(self.link.dependency_id, self.app.settings.get().dependencies)
        self.assertEqual(resolve([self.app])[1][0].project.root, self.lib.root)
        self.lib.settings.save(self.lib.settings.get())
        self.solution.settings.save(self.solution.settings.get())
        reopened = Solution.open(self.solution.root / ".cppbuild")
        self.assertEqual(reopened.get_project("Lib").root, self.lib.root)
        self.assertEqual(reopened.get_project("Lib")._resolved_build_settings().parallel, 1)

    def test_paths_preserve_internal_and_external_referents(self):
        old = self.lib.root
        self.lib.settings.link_cmake_source(CMakeSource("../third_party/source", "vendor"))
        self.lib.settings.link_package(CMakePackage("Vendor", "Vendor::Lib", "vendor/package"))
        imported = self.app.settings.link_imported_library(ImportedLibrary(
            T.SHARED_LIBRARY, {"Debug": "../Lib/vendor/lib.dll"}, [str(old / "vendor/include")],
            {"Debug": "../Lib/vendor/lib.lib"}))
        values = self.lib.settings.get()
        values.source_directories = [str(old / "src")]
        values.project_headers = [str(old / "include/pch.hpp")]
        values.types[T.STATIC_LIBRARY].include_directories = [str(old / "include")]
        self.lib.settings.save(values)
        self.lib.set_build_settings(ProjectBuildSettings(googletest_archive="../archives/gtest.zip"))
        self.solution.move_project("Lib", "nested/Lib", auto_update=False)
        data = self.lib.settings.get()
        self.assertEqual(data.source_directories, ["src"])
        self.assertEqual(data.project_headers, ["include/pch.hpp"])
        deps = list(data.dependencies.values())
        self.assertEqual(next(d for d in deps if isinstance(d, CMakeSource)).directory, "../../third_party/source")
        self.assertEqual(next(d for d in deps if isinstance(d, CMakePackage)).directory, "vendor/package")
        direct = self.app.settings.get().dependencies[imported.dependency_id]
        self.assertEqual(direct.locations["Debug"], "../nested/Lib/vendor/lib.dll")
        self.assertEqual(direct.import_libraries["Debug"], "../nested/Lib/vendor/lib.lib")
        self.assertEqual(direct.include_directories, ["../nested/Lib/vendor/include"])
        self.assertEqual(self.lib._build_settings.googletest_archive, "../../archives/gtest.zip")
        self.app.settings.save(self.app.settings.get())

    def test_all_caches_are_archived_and_observation_is_stale(self):
        old = self.lib.root
        for kind in ("generated", "build"):
            for context in ("vs2022-x64-static_library", "vs2022-Win32-shared_library"):
                path = old / ".cppbuild" / kind / context / "marker"
                path.parent.mkdir(parents=True)
                path.write_text("old cache")
        self.lib._known_artifacts = (old / "old.lib",)
        report = self.solution.move_project("Lib", "new/Lib", auto_update=False)
        self.assertFalse((self.lib.root / ".cppbuild/build").exists())
        self.assertFalse((self.lib.root / ".cppbuild/generated").exists())
        archived = [p for p in report.changed_paths if "relocations" in p.parts]
        self.assertEqual(len(archived), 2)
        self.assertEqual(sum(len(list(p.rglob("marker"))) for p in archived), 4)
        info = next(p for p in self.solution.info().projects if p.name == "Lib")
        self.assertEqual(info.artifacts, ())
        self.assertEqual(info.generation_state, "stale")

    def test_invalid_destinations_leave_original_untouched(self):
        for destination in ("..", ".", ".cppbuild/lib", "Lib", "Lib/inside", "App", "App/Lib"):
            with self.subTest(destination=destination), self.assertRaises((SettingsError, FileExistsError)):
                self.solution.move_project("Lib", destination, auto_update=False)
        existing = self.solution.root / "existing"
        existing.mkdir()
        with self.assertRaises(FileExistsError):
            self.solution.move_project("Lib", existing, auto_update=False)
        self.assertTrue((self.lib.root / "src/lib.cpp").is_file())
        self.assertEqual(self.solution.settings.get().projects["Lib"], "Lib")

    def test_manifest_failure_rolls_back_directory_cache_and_all_settings(self):
        old = self.lib.root
        cache = old / ".cppbuild/build/test/marker"
        cache.parent.mkdir(parents=True)
        cache.write_bytes(b"cached")
        self.app.settings.link_cmake_source(CMakeSource("../Lib/vendor", "vendor"))
        before = {p: p.read_bytes() for p in self.solution.root.rglob("*.json")}
        original = storage.atomic_write
        def fail(path, content):
            if path == self.solution.settings.path:
                raise OSError("injected publish failure")
            return original(path, content)
        with patch("cppbuild.storage.atomic_write", side_effect=fail), self.assertRaises(OSError):
            self.solution.move_project("Lib", "new/Lib", auto_update=False)
        self.assertEqual(self.lib.root, old)
        self.assertEqual(cache.read_bytes(), b"cached")
        self.assertFalse((self.solution.root / "new").exists())
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertFalse(list(self.solution.root.rglob(".write.lock")))
        self.assertEqual(Solution.open(self.solution.root / ".cppbuild").get_project("Lib").root, old)
        self.lib.settings.save(self.lib.settings.get())
        self.app.settings.save(self.app.settings.get())

    def test_conflict_and_busy_operations_are_rejected(self):
        other = Solution.open(self.solution.root / ".cppbuild").get_project("Lib")
        data = other.settings.get()
        data.source_directories.append("extra")
        other.settings.save(data)
        with self.assertRaises(SettingsConflictError):
            self.solution.move_project("Lib", "new/Lib", auto_update=False)
        self.lib.settings.reload()
        with storage.write_lock(self.lib.root / ".cppbuild/generated/context"):
            with self.assertRaises(SettingsError):
                self.solution.move_project("Lib", "new/Lib", auto_update=False)
        self.assertEqual(self.solution.settings.get().projects["Lib"], "Lib")

    def test_automatic_update_and_failure_report_after_committed_move(self):
        result = engine.OperationReport((engine.ProcessReport(("cmake",), 0, ""),))
        with patch.object(self.solution, "update", return_value=result) as update:
            report = self.solution.move_project("Lib", "new/Lib")
        update.assert_called_once_with()
        self.assertIs(report.update, result)
        self.assertTrue(report.success)
        self.assertFalse(report.pending_update)
        with patch.object(self.solution, "update", side_effect=FileNotFoundError("cmake")):
            failed = self.solution.move_project("Lib", "again/Lib")
        self.assertFalse(failed.success)
        self.assertTrue(failed.pending_update)
        self.assertIn("cmake", failed.update_error)
        self.assertEqual(self.solution.get_project("Lib").root, self.solution.root / "again/Lib")
        self.assertEqual(Solution.open(self.solution.root / ".cppbuild").get_project("Lib").root, self.lib.root)

    def test_directory_rename_failure_restores_archived_cache(self):
        old = self.lib.root
        cache = old / ".cppbuild/build/context/marker"
        cache.parent.mkdir(parents=True)
        cache.write_bytes(b"cached")
        original = Path.rename
        def fail(path, target):
            if path == old:
                raise PermissionError("Project file is in use")
            return original(path, target)
        with patch.object(Path, "rename", fail), self.assertRaises(PermissionError):
            self.solution.move_project("Lib", "new/deep/Lib", auto_update=False)
        self.assertEqual(cache.read_bytes(), b"cached")
        self.assertFalse((self.solution.root / "new").exists())
        self.assertEqual(self.lib.root, old)
        self.assertFalse(list(self.solution.root.rglob(".write.lock")))

    def test_pending_run_and_callback_refuse_move(self):
        from cppbuild import RunReport
        pending = Future()
        self.app._last_operation = ("run", RunReport(pending, (), ()))
        with self.assertRaisesRegex(SettingsError, "Wait"):
            self.solution.move_project("Lib", "new/Lib", auto_update=False)
        pending.set_result(engine.OperationReport((engine.ProcessReport(("app",), 0, ""),)))
        self.solution._events.stack.append("file_changed")
        try:
            with self.assertRaisesRegex(SettingsError, "callback"):
                self.solution.move_project("Lib", "new/Lib", auto_update=False)
        finally:
            self.solution._events.stack.pop()
        self.assertTrue(self.solution.move_project("Lib", "new/Lib", auto_update=False).success)


@unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2022") == "1", "Set CPPBUILD_TEST_VS2022=1 for real VS2022 builds")
class RelocationVS2022Tests(unittest.TestCase):
    setUp = RelocationTests.setUp

    def test_real_move_library_then_application_and_rebuild(self):
        def success(report):
            self.assertTrue(report.success, "\n".join(p.output for p in report.processes))
        success(self.solution.build())
        old = self.lib.root
        moved = self.solution.move_project("Lib", "libs/移動 Library")
        self.assertTrue(moved.success, str(moved))
        self.assertFalse(moved.pending_update)
        self.assertFalse(old.exists())
        success(self.app.run())
        success(self.solution.build())
        self.assertNotIn(old.as_posix(), next((self.app.root / ".cppbuild/output/intermediate").rglob("CMakeLists.txt")).read_text(encoding="utf-8"))
        moved = self.solution.move_project("App", "apps/移動 App")
        self.assertTrue(moved.success, str(moved))
        self.solution.set_build_settings(SolutionBuildSettings(configuration="Release"))
        success(self.solution.build())
        success(self.app.run())
        success(self.app.clean())
        success(self.app.rebuild())
        reopened = Solution.open(self.solution.root / ".cppbuild")
        success(reopened.get_project("App").run())

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cppbuild import (
    INHERIT, ProjectBuildSettings, ProjectSettingsData, ProjectType as T,
    SettingsConflictError, SettingsError, Solution, SolutionBuildSettings, TypeSettingsData,
)
from cppbuild import storage


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "日本語 space"
        self.solution = Solution.create(self.root, "Demo")

    def app(self):
        return self.solution.add_project("App", "App", T.EXECUTABLE)

    def test_create_add_open_remove_and_main(self):
        self.assertIsNone(self.solution.settings.get().main_project)
        app = self.app()
        tool = self.solution.add_project("Tool", "Tool", T.EXECUTABLE)
        self.assertEqual(self.solution.settings.get().main_project, "App")
        reopened = Solution.open(self.root / ".cppbuild")
        self.assertEqual([p.name for p in reopened.projects()], ["App", "Tool"])
        self.assertEqual(reopened.get_project("App").root, app.root)
        with self.assertRaises(SettingsError):
            self.solution.remove_project("App")
        self.solution.remove_project("Tool")
        self.assertTrue(tool.settings.path.is_file())
        with self.assertRaises(SettingsError):
            tool.settings.get()

    def test_copy_save_reload_without_process(self):
        with patch("subprocess.run", side_effect=AssertionError("Unexpected process")):
            app = self.app()
            data = app.settings.get()
            data.source_directories.append("extra")
            self.assertNotIn("extra", app.settings.get().source_directories)
            app.settings.save(data)
            data.source_directories.append("later")
            app.settings.reload()
            self.assertEqual(app.settings.get().source_directories, ["src", "include", "extra"])
            self.solution.settings.reload()
            self.assertIs(self.solution.get_project("App"), app)

    def test_build_settings_not_saved_and_inheritance(self):
        app = self.app()
        parent = SolutionBuildSettings(configuration="Release")
        self.solution.set_build_settings(parent)
        parent.configuration = "Debug"
        child = ProjectBuildSettings(cpp_standard=17, run_arguments=["original"])
        app.set_build_settings(child)
        child.run_arguments.append("changed")
        self.assertEqual(app._resolved_build_settings().configuration, "Release")
        self.assertEqual(app._resolved_build_settings().run_arguments, ["original"])
        app.settings.save(app.settings.get())
        self.solution.settings.reload()
        self.assertEqual(app._resolved_build_settings().configuration, "Release")
        app.set_build_settings(ProjectBuildSettings(configuration="Debug"))
        self.assertEqual(app._resolved_build_settings().configuration, "Debug")
        reopened = Solution.open(self.root / ".cppbuild").get_project("App")
        self.assertEqual(reopened._build_settings.configuration, INHERIT)
        self.assertEqual(reopened._build_settings.run_arguments, [])
        for path in self.root.rglob("*.json"):
            content = path.read_text(encoding="utf-8")
            self.assertNotIn('"configuration"', content)
            self.assertNotIn('"cpp_standard"', content)

    def test_multiple_type_files_and_explicit_selection(self):
        data = ProjectSettingsData("Lib", {T.STATIC_LIBRARY: TypeSettingsData(),
                                          T.SHARED_LIBRARY: TypeSettingsData(["BUILD_DLL"])})
        lib = self.solution.add_project("Lib", "Lib", T.STATIC_LIBRARY, data)
        raw = json.loads(lib.settings.path.read_text())
        self.assertEqual(len(raw["data"]["types"]), 3)
        self.assertEqual(lib._resolved_build_settings().project_type, T.STATIC_LIBRARY)
        lib.set_build_settings(ProjectBuildSettings(project_type=T.SHARED_LIBRARY))
        self.assertEqual(lib._resolved_build_settings().project_type, T.SHARED_LIBRARY)
        loaded = Solution.open(self.root / ".cppbuild").get_project("Lib")
        self.assertNotEqual(lib.settings.get().guid, data.guid)
        data.guid = lib.settings.get().guid
        data.initial_type = T.STATIC_LIBRARY
        data.types[T.INTERFACE_LIBRARY] = TypeSettingsData()
        self.assertEqual(loaded.settings.get(), data)

    def test_failed_manifest_publish_preserves_disk_and_memory(self):
        app = self.app()
        before = app.settings.get()
        changed = app.settings.get()
        changed.types[T.EXECUTABLE].compile_definitions.append("NEW")
        original = storage.atomic_write

        def fail(path, content):
            if path == app.settings.path:
                raise OSError("simulated write failure")
            return original(path, content)

        with patch("cppbuild.storage.atomic_write", side_effect=fail):
            with self.assertRaises(OSError):
                app.settings.save(changed)
        self.assertEqual(app.settings.get(), before)
        self.assertEqual(Solution.open(self.root / ".cppbuild").get_project("App").settings.get(), before)

    def test_external_edit_conflict_and_reload(self):
        app = self.app()
        second = Solution.open(self.root / ".cppbuild").get_project("App")
        data = second.settings.get()
        data.source_directories = ["other"]
        second.settings.save(data)
        with self.assertRaises(SettingsConflictError):
            app.settings.save(app.settings.get())
        app.settings.reload()
        self.assertEqual(app.settings.get().source_directories, ["other"])

    def test_invalid_reload_is_atomic(self):
        app = self.app()
        tool = self.solution.add_project("Tool", "Tool", T.EXECUTABLE)
        old = app.settings.get()
        raw = json.loads(app.settings.path.read_text())
        raw["data"]["source_directories"] = ["changed"]
        app.settings.path.write_text(json.dumps(raw))
        tool.settings.path.write_text('{"schema_version": 999}')
        with self.assertRaises(SettingsError):
            self.solution.settings.reload()
        self.assertEqual(app.settings.get(), old)
        self.assertIs(self.solution.get_project("Tool"), tool)

    def test_membership_cannot_be_edited_by_save(self):
        self.app()
        values = self.solution.settings.get()
        values.projects.clear()
        values.main_project = None
        with self.assertRaises(SettingsError):
            self.solution.settings.save(values)

    def test_invalid_paths_types_and_schema(self):
        for directory in ("..", ".", ".cppbuild/private"):
            with self.subTest(directory=directory), self.assertRaises(SettingsError):
                self.solution.add_project(directory, "Bad", T.EXECUTABLE)
        app = self.app()
        with self.assertRaises(SettingsError):
            self.solution.add_project("App/nested", "Nested", T.EXECUTABLE)
        for invalid in ([], "unknown", True):
            with self.subTest(invalid=invalid), self.assertRaises(SettingsError):
                app.set_build_settings(ProjectBuildSettings(configuration=invalid))
        raw = json.loads(app.settings.path.read_text())
        raw["schema_version"] = 999
        app.settings.path.write_text(json.dumps(raw))
        with self.assertRaises(SettingsError):
            app.settings.reload()

    def test_failed_add_leaves_membership_unchanged_and_can_retry(self):
        original = storage.atomic_write

        def fail(path, content):
            if path == self.solution.settings.path:
                raise OSError("simulated failure")
            return original(path, content)

        with patch("cppbuild.storage.atomic_write", side_effect=fail):
            with self.assertRaises(OSError):
                self.app()
        self.assertEqual(self.solution.projects(), [])
        self.assertEqual(Solution.open(self.root / ".cppbuild").projects(), [])
        self.app()

    def test_solution_save_does_not_overwrite_child(self):
        app = self.app()
        before = app.settings.path.read_bytes()
        values = self.solution.settings.get()
        values.name = "Renamed"
        self.solution.settings.save(values)
        self.assertEqual(app.settings.path.read_bytes(), before)

    def test_type_file_external_changes_detected(self):
        app = self.app()
        type_path = next((app.root / ".cppbuild/types").glob("*.json"))
        raw = json.loads(type_path.read_text())
        raw["data"]["compile_definitions"] = ["EXTERNAL"]
        type_path.write_text(json.dumps(raw))
        with self.assertRaises(SettingsConflictError):
            app.settings.save(app.settings.get())
        app.settings.reload()
        self.assertEqual(app.settings.get().types[T.EXECUTABLE].compile_definitions, ["EXTERNAL"])

    def test_duplicate_create_does_not_overwrite(self):
        self.app()
        with self.assertRaises(FileExistsError):
            Solution.create(self.root, "Other")
        self.assertEqual(Solution.open(self.root / ".cppbuild").settings.get().name, "Demo")


if __name__ == "__main__":
    unittest.main()

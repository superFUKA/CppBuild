from copy import deepcopy
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from cppbuild import (
    Environment, EnvironmentOptions, EventCallbackError, ProjectBuildSettings, ProjectType as T,
    SettingsError, Solution, SolutionBuildSettings, TemplateTools, ToolSettings,
)
from cppbuild import engine, storage, tooling


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="cppbuild-workflow-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.solution = Solution.create(self.root / "Original", "Original")
        self.app = self.solution.add_project("App", "App", T.EXECUTABLE)
        self.app.add_file("src/main.cpp", content="int main() { return 0; }", auto_update=False)

    def test_material_create_expand_register_reopen_remove(self):
        original = self.root / "class.hpp"
        original.write_text("struct Example { Example() = default; };", encoding="utf-8")
        self.solution.create_file_template("class_header", original, replacements={"class_name": "Example"})
        material = self.solution.root / self.solution.settings.file_templates()["class_header"]
        self.assertIn("{{class_name}}", material.read_text())
        self.assertIn("Example", original.read_text())
        reopened = Solution.open(self.solution.root / ".cppbuild")
        reopened.get_project("App").add_file("include/player.hpp", template_name="class_header", replacements={"class_name": "Player"}, auto_update=False)
        self.assertEqual((self.app.root / "include/player.hpp").read_text(), "struct Player { Player() = default; };")
        reopened.settings.set_file_template("alias", material)
        copied = reopened.settings.file_templates()
        copied.clear()
        self.assertEqual(len(reopened.settings.file_templates()), 2)
        reopened.settings.remove_file_template("class_header")
        self.assertTrue(material.is_file())
        with self.assertRaises(SettingsError):
            reopened.get_project("App").add_file("bad", template_name="alias", replacements={}, auto_update=False)
        self.assertFalse((self.app.root / "bad").exists())

    def test_binary_material_and_creation_collision_rollback(self):
        original = self.root / "asset.bin"
        original.write_bytes(b"\x00\xff\x80")
        self.solution.create_file_template("asset", original)
        self.app.add_file("src/asset.bin", template_name="asset", auto_update=False)
        self.assertEqual((self.app.root / "src/asset.bin").read_bytes(), original.read_bytes())
        with self.assertRaises(SettingsError):
            self.solution.create_file_template("asset", original)
        with patch.object(self.solution.settings, "_publish", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.solution.create_file_template("failed", original)
        self.assertFalse((self.solution.root / ".cppbuild/templates/failed.bin").exists())
        self.assertNotIn("failed", self.solution.settings.file_templates())
        with self.assertRaises(SettingsError):
            self.solution.settings.set_file_template("outside", original)

    def test_template_roundtrip_no_outputs_or_ephemeral_settings(self):
        library = self.solution.add_project("Library", "Library", T.STATIC_LIBRARY)
        library.add_file("src/library.cpp", content="int answer() { return 42; }", auto_update=False)
        self.app.settings.link_project(library, T.STATIC_LIBRARY)
        self.app.settings.set_pch(system_headers=["vector"])
        self.solution.set_build_settings(SolutionBuildSettings(configuration="Release", run_projects=["App"]))
        (self.solution.root / "asset.dat").write_bytes(b"asset")
        material = self.root / "source.txt"
        material.write_text("material")
        self.solution.create_file_template("text", material)
        for directory in (self.app.root / ".cppbuild/build", self.solution.root / "build", self.solution.root / ".git"):
            directory.mkdir(parents=True)
            (directory / "excluded").write_text("generated")
        template = TemplateTools.create_solution_template(self.solution, self.root / "Template")
        restored = Solution.create(self.root / "Restored", "NewName", template=template)
        self.assertEqual(restored.settings.get().name, "NewName")
        self.assertEqual(restored.settings.get().main_project, "App")
        self.assertEqual(restored._build_settings.configuration, "Debug")
        self.assertEqual(restored.get_project("App").settings.get().system_headers, ["vector"])
        self.assertEqual((restored.root / "asset.dat").read_bytes(), b"asset")
        self.assertFalse(any(p.name == "excluded" for p in restored.root.rglob("*")))
        self.assertTrue((restored.root / ".cppbuild/templates/text.txt").is_file())
        self.assertEqual(len(restored.get_project("App").settings.get().dependencies), 1)
        self.assertFalse((restored.root / ".cppbuild/template.json").exists())
        self.assertEqual(restored.info().projects[0].generation_state, "unknown")

    def test_template_rejects_overlap_existing_destination_and_copy_failure(self):
        with self.assertRaises(SettingsError):
            TemplateTools.create_solution_template(self.solution, self.solution.root / "nested")
        with self.assertRaises(FileExistsError):
            TemplateTools.create_solution_template(self.solution, self.solution.root)
        with patch("cppbuild.templates.shutil.copyfile", side_effect=OSError("copy failed")):
            with self.assertRaises(OSError):
                TemplateTools.create_solution_template(self.solution, self.root / "Failure")
        self.assertFalse((self.root / "Failure").exists())
        self.assertEqual(list(self.root.glob(".cppbuild-template-*")), [])

    def test_legacy_solution_settings_read_without_templates(self):
        path = self.solution.settings.path
        data = storage.read_json(path)
        del data["data"]["file_templates"]
        storage.atomic_write(path, storage.encoded(data))
        self.assertEqual(Solution.open(path.parent).settings.file_templates(), {})

    def update_report(self, project):
        return engine.UpdateReport(engine.ProcessReport(("cmake",), 0, ""), (), project.root / "build", None, None, None)

    def test_events_batch_updates_and_recursive_callback_suppression(self):
        other = self.solution.add_project("Other", "Other", T.EXECUTABLE)
        notifications, updates = [], []
        def linked(event):
            notifications.append(event.project.name)
            if event.project is self.app:
                other.add_file("src/one.cpp", content="int one;", auto_update=True)
                other.add_file("src/two.cpp", content="int two;", auto_update=True)
        key = self.solution.on("file_changed", linked)
        def updated(project):
            updates.append(project.name)
            return self.update_report(project)
        with patch.object(engine, "update", side_effect=updated):
            report = self.app.add_file("src/more.cpp", content="int more;")
        self.assertTrue(report.success)
        self.assertFalse(report.pending_update)
        self.assertEqual(updates, ["App", "Other"])
        self.assertEqual(notifications, ["App"])
        self.assertEqual(len(report.changed_paths), 3)
        self.assertTrue(self.solution.off(key))
        self.assertFalse(self.solution.off(key))
        self.assertEqual(Solution.open(self.solution.root / ".cppbuild")._events.callbacks, {})

    def test_event_failure_preserves_file_and_reports_partial_update(self):
        def failed(event):
            raise RuntimeError("callback failed")
        self.solution.on("file_changed", failed)
        with patch.object(engine, "update", side_effect=OSError("configure unavailable")):
            result = self.app.add_file("src/retained.cpp", content="int retained;")
        self.assertTrue((self.app.root / "src/retained.cpp").exists())
        self.assertFalse(result.success)
        self.assertTrue(result.pending_update)
        self.assertIn("callback failed", result.event_errors[0])
        self.assertIn("configure unavailable", result.update_error)

    def test_lifecycle_events_reentry_before_failure_and_after_result(self):
        seen = []
        key = self.solution.on("before_build", lambda e: self.app.build())
        with patch.object(engine, "operate", side_effect=AssertionError("Must not run")):
            with self.assertRaises(EventCallbackError):
                self.app.build()
        self.solution.off(key)
        self.solution.on("before_build", lambda e: seen.append((e.name, e.project)))
        self.solution.on("after_build", lambda e: seen.append((e.name, e.result)))
        result = engine.OperationReport((engine.ProcessReport(("cmake",), 1, "compile failed"),))
        with patch.object(engine, "operate", return_value=result):
            self.assertIs(self.app.build(), result)
        self.assertEqual(seen, [("before_build", self.app), ("after_build", result)])
        self.solution.on("after_build", lambda e: (_ for _ in ()).throw(RuntimeError("after")))
        with patch.object(engine, "operate", return_value=result):
            with self.assertRaises(EventCallbackError) as caught:
                self.app.build()
        self.assertIs(caught.exception.result, result)

    def test_info_observation_copy_stale_and_no_scan(self):
        self.assertEqual(self.solution.info().projects[0].generation_state, "unknown")
        with patch.object(engine, "process", return_value=engine.ProcessReport(("cmake",), 0, "")):
            self.app.update()
        with patch.object(engine, "_scan", side_effect=AssertionError("scan")), patch.object(engine, "process", side_effect=AssertionError("tool")), patch.object(self.solution.settings, "reload", side_effect=AssertionError("reload")):
            info = self.solution.info()
            self.assertEqual(info.projects[0].generation_state, "current")
            self.assertTrue(info.projects[0].files)
            self.assertFalse(info.external_changes_checked)
            info.settings.name = "Detached"
            self.assertEqual(self.solution.settings.get().name, "Original")
        self.app.add_file("src/new.cpp", content="int n;", auto_update=False)
        self.assertEqual(self.solution.info().projects[0].generation_state, "stale")
        reopened = Solution.open(self.solution.root / ".cppbuild")
        self.assertEqual(reopened.info().projects[0].files, ())
        self.assertEqual(reopened.info().projects[0].build_state, "unknown")

    def test_tool_settings_inheritance_non_persistence_and_shared_selection(self):
        tools = ToolSettings(cmake="custom-cmake", ctest="custom-ctest", environment={"CPPBUILD_TEST_VALUE": "value"})
        self.solution.set_build_settings(SolutionBuildSettings(tools=tools))
        resolved = self.app._resolved_build_settings()
        self.assertEqual(resolved.tools, tools)
        with patch("cppbuild.tooling.shutil.which", return_value="custom-cmake"), patch.object(engine, "process", return_value=engine.ProcessReport(("custom-cmake",), 0, "")) as run:
            self.app.update()
        self.assertEqual(run.call_args.args[0][0], "custom-cmake")
        self.assertEqual(run.call_args.kwargs["env"]["CPPBUILD_TEST_VALUE"], "value")
        self.assertNotIn("CPPBUILD_TEST_VALUE", os.environ)
        self.assertEqual(Solution.open(self.solution.root / ".cppbuild")._build_settings.tools, ToolSettings())
        for bad in (ToolSettings(environment={"Path": "a", "PATH": "b"}), ToolSettings(cmake="relative/cmake.exe")):
            with self.assertRaises(SettingsError):
                self.solution.set_build_settings(SolutionBuildSettings(tools=bad))

    def test_environment_missing_tool_and_configuration_conflict(self):
        with patch("cppbuild.environment.shutil.which", return_value=None), patch.object(engine, "process", side_effect=AssertionError("Unexpected process")):
            report = Environment.check(EnvironmentOptions(require_ctest=True))
        self.assertFalse(report.success)
        self.assertEqual([item.name for item in report.items], ["cmake", "ctest"])
        self.assertTrue(all(item.action for item in report.items))
        self.app.set_build_settings(ProjectBuildSettings(configuration="Release"))
        with patch.object(Environment, "check", side_effect=AssertionError("Unexpected tool")):
            with self.assertRaises(SettingsError):
                self.solution.check_environment()


@unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2022") == "1", "Real VS2022 required")
class WorkflowIntegrationTests(unittest.TestCase):
    def test_environment_template_build_and_info(self):
        with tempfile.TemporaryDirectory(prefix="cppbuild-workflow-vs-") as temp:
            root = Path(temp)
            solution = Solution.create(root / "Original", "Original")
            app = solution.add_project("App", "App", T.EXECUTABLE)
            app.add_file("src/main.cpp", content="int main() { return 0; }", auto_update=False)
            solution.set_build_settings(SolutionBuildSettings(tools=ToolSettings(cmake=shutil.which("cmake"), ctest=shutil.which("ctest"))))
            environment = app.check_environment()
            self.assertTrue(environment.success, str(environment))
            report = app.build()
            self.assertTrue(report.success, "\n".join(p.output for p in report.processes))
            self.assertEqual(solution.info().projects[0].build_state, "current")
            self.assertTrue(solution.info().projects[0].artifacts)
            template = TemplateTools.create_solution_template(solution, root / "Template")
            clone = Solution.create(root / "Cloned", "Cloned", template=template)
            self.assertFalse((clone.get_project("App").root / ".cppbuild/build").exists())
            result = clone.get_project("App").run()
            self.assertTrue(result.success, "\n".join(p.output for p in result.processes))
            self.assertTrue(clone.get_project("App").clean().success)
            self.assertEqual(clone.info().projects[0].build_state, "cleaned")
            app.add_file("src/bad.cpp", content="invalid C++", auto_update=False)
            self.assertEqual(solution.info().projects[0].build_state, "stale")
            self.assertFalse(app.build().success)
            self.assertEqual(solution.info().projects[0].build_state, "failed")

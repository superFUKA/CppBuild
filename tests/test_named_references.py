import os
from pathlib import Path
import shutil
import tempfile
import unittest
import uuid
from unittest.mock import patch

from cppbuild import (Dependency, ProjectType as T, SettingsConflictError, SettingsError,
                      Solution, SolutionBuildSettings, SolutionFolderSettings, TemplateTools)
from cppbuild import storage
from cppbuild.graph import resolve


class NamedReferenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cppbuild-refs-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.provider = Solution.create(self.root / "Provider", "Provider")
        self.lib = self.provider.add_project("Lib", "Lib", T.STATIC_LIBRARY)
        self.solution = Solution.create(self.root / "Consumer", "Consumer")
        self.app = self.solution.add_project("App", "App", T.EXECUTABLE)
        self.tool = self.solution.add_project("Tool", "Tool", T.EXECUTABLE)
        self.config = self.provider.root / ".cppbuild"

    def link(self, project=None, **kwargs):
        return (project or self.app).settings.link_solution(self.config, T.STATIC_LIBRARY, **kwargs)

    def test_automatic_registry_shared_guid_and_last_unlink(self):
        first, second = self.link(), self.link(self.tool)
        guid = self.lib.settings.get().guid
        self.assertEqual(set(self.solution.settings.get().references), {guid})
        dependency = self.app.settings.get().dependencies[first.dependency_id]
        self.assertEqual(dependency.reference, guid)
        self.assertIsNone(dependency.solution_directory)
        self.assertIsNone(dependency.project_guid)
        self.assertEqual(len(resolve(self.solution.projects())[1]), 3)
        reopened = Solution.open(self.solution.root / ".cppbuild")
        reopened.get_project("App").settings.unlink(first.dependency_id)
        self.assertEqual(len(reopened.settings.get().references), 1)
        reopened.get_project("Tool").settings.unlink(second.dependency_id)
        self.assertEqual(Solution.open(self.solution.root / ".cppbuild").settings.get().references, {})
        self.assertTrue(self.lib.settings.path.exists())

    def test_aliases_deduplicate_targets_and_prune_independently(self):
        first = self.link(name="共有ライブラリ")
        second = self.link(name="Another")
        self.link(self.tool, name="共有ライブラリ")
        roots, nodes = resolve(self.solution.projects())
        self.assertEqual(len(nodes), 3)
        self.assertEqual(len(roots[0].dependencies), 1)
        self.app.settings.unlink(first.dependency_id)
        self.assertEqual(len(self.solution.settings.get().references), 2)
        self.solution.remove_project("Tool")
        self.assertEqual(set(self.solution.settings.get().references), {"Another"})
        self.app.settings.unlink(second.dependency_id)
        self.assertEqual(self.solution.settings.get().references, {})

    def test_conflicting_name_location_duplicate_and_invalid_names(self):
        self.link(name="Common")
        other = Solution.create(self.root / "Other", "Other")
        other.add_project("Lib", "Lib", T.STATIC_LIBRARY)
        with self.assertRaises(SettingsError):
            self.tool.settings.link_solution(other.root / ".cppbuild", T.STATIC_LIBRARY, name="Common")
        with self.assertRaises(SettingsError):
            self.link(name="Common")
        copied = self.root / "Copied"
        shutil.copytree(self.provider.root, copied)
        with self.assertRaises(SettingsError):
            self.tool.settings.link_solution(copied / ".cppbuild", T.STATIC_LIBRARY, name="Copy")
        for name in ("", " spaced", "line\nbreak", 12, []):
            with self.subTest(name=name), self.assertRaises(SettingsError):
                self.link(name=name)
        self.assertEqual(set(self.solution.settings.get().references), {"Common"})
        self.assertEqual(self.tool.settings.get().dependencies, {})

    def test_guid_immutable_and_preserved_by_move(self):
        guid = self.lib.settings.get().guid
        self.link()
        data = self.lib.settings.get()
        data.guid = str(uuid.uuid4())
        with self.assertRaises(SettingsError):
            self.lib.settings.save(data)
        self.provider.move_project("Lib", "Moved/Lib", auto_update=False)
        self.assertEqual(self.lib.settings.get().guid, guid)
        node = resolve([self.app])[1][0]
        self.assertEqual(node.project.root, self.lib.root)
        self.solution.move_project("App", "Moved/App", auto_update=False)
        self.assertEqual(resolve([self.app])[1][0].key[0], guid)

    def test_main_project_change_does_not_retarget_existing_links(self):
        self.link()
        other = self.provider.add_project("Other", "Other", T.STATIC_LIBRARY)
        values = self.provider.settings.get()
        values.main_project = "Other"
        self.provider.settings.save(values)
        self.assertEqual(resolve([self.app])[1][0].project.name, "Lib")
        self.link(self.tool)
        self.assertEqual(set(self.solution.settings.get().references),
                         {self.lib.settings.get().guid, other.settings.get().guid})

    def test_save_dependencies_prunes_and_registry_cannot_be_edited(self):
        self.link()
        values = self.solution.settings.get()
        values.references.clear()
        with self.assertRaises(SettingsError):
            self.solution.settings.save(values)
        values = self.app.settings.get()
        values.dependencies.clear()
        self.app.settings.save(values)
        self.assertEqual(self.solution.settings.get().references, {})

    def test_failed_link_and_unlink_restore_both_manifests(self):
        original = storage.atomic_write
        def fail(path, content):
            if path == self.solution.settings.path:
                raise OSError("injected Solution publish failure")
            original(path, content)
        before = {p: p.read_bytes() for p in (self.solution.settings.path, self.app.settings.path)}
        with patch("cppbuild.storage.atomic_write", side_effect=fail), self.assertRaises(OSError):
            self.link()
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.assertEqual(self.app.settings.get().dependencies, {})
        linked = self.link()
        before = {p: p.read_bytes() for p in before}
        with patch("cppbuild.storage.atomic_write", side_effect=fail), self.assertRaises(OSError):
            self.app.settings.unlink(linked.dependency_id)
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.assertIn(linked.dependency_id, self.app.settings.get().dependencies)
        self.assertEqual(len(self.solution.settings.get().references), 1)

    def test_stale_other_consumer_cannot_lose_reference(self):
        first = self.link()
        other = Solution.open(self.solution.root / ".cppbuild")
        second = other.get_project("Tool").settings.link_solution(self.config, T.STATIC_LIBRARY)
        with self.assertRaises(SettingsConflictError):
            self.app.settings.unlink(first.dependency_id)
        self.solution.settings.reload()
        self.app.settings.unlink(first.dependency_id)
        self.assertIn(second.dependency_id, self.tool.settings.get().dependencies)
        self.assertEqual(len(self.solution.settings.get().references), 1)

    def test_template_new_guids_internal_remap_external_preserved(self):
        local = self.solution.add_project("Local", "Local", T.STATIC_LIBRARY)
        self.app.settings.link_project(local, T.STATIC_LIBRARY)
        self.link(name="External")
        self.link(self.tool, name="External")
        template = TemplateTools.create_solution_template(self.solution, self.root / "Template")
        restored = Solution.create(self.root / "Restored", "Restored", template=template)
        self.assertTrue({p.settings.get().guid for p in self.solution.projects()}.isdisjoint(
            {p.settings.get().guid for p in restored.projects()}))
        self.assertEqual(restored.settings.get().references, self.solution.settings.get().references)
        nodes = resolve(restored.projects())[1]
        self.assertEqual(len(nodes), 4)
        self.assertEqual(next(n for n in nodes if n.project.name == "Local").project.root,
                         restored.get_project("Local").root)

    def test_legacy_guid_and_links_migrate_once_with_dependency_ids(self):
        guid = self.lib.settings.get().guid
        raw = storage.read_json(self.lib.settings.path)
        del raw["data"]["guid"]
        storage.atomic_write(self.lib.settings.path, storage.encoded(raw))
        ids = []
        for project in (self.app, self.tool):
            raw = storage.read_json(project.settings.path)
            del raw["data"]["guid"]
            key = uuid.uuid4().hex
            ids.append(key)
            raw["data"]["dependencies"][key] = {"project": "Lib", "project_type": "static_library",
                                               "solution_directory": str(self.config)}
            storage.atomic_write(project.settings.path, storage.encoded(raw))
        self.solution.settings.reload()
        self.assertEqual(len(self.solution.settings.get().references), 1)
        self.assertIn(ids[0], self.app.settings.get().dependencies)
        self.assertIn(ids[1], self.tool.settings.get().dependencies)
        new_guid = next(iter(self.solution.settings.get().references))
        self.assertNotEqual(guid, new_guid)
        before = {p: p.read_bytes() for p in self.root.rglob("project.json")}
        Solution.open(self.solution.root / ".cppbuild")
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_failed_legacy_migration_restores_consumer(self):
        raw = storage.read_json(self.app.settings.path)
        del raw["data"]["guid"]
        raw["data"]["dependencies"][uuid.uuid4().hex] = {
            "project": "Lib", "project_type": "static_library", "solution_directory": str(self.config)}
        storage.atomic_write(self.app.settings.path, storage.encoded(raw))
        before = self.app.settings.path.read_bytes()
        original = storage.atomic_write
        def fail(path, content):
            if path == self.solution.settings.path:
                raise OSError("migration failure")
            original(path, content)
        with patch("cppbuild.storage.atomic_write", side_effect=fail), self.assertRaises(OSError):
            self.solution.settings.reload()
        self.assertEqual(before, self.app.settings.path.read_bytes())
        self.assertEqual(self.solution.settings.get().references, {})

    def test_duplicate_guid_graph_and_missing_target_rejected(self):
        self.link()
        raw = storage.read_json(self.lib.settings.path)
        raw["data"]["guid"] = str(uuid.uuid4())
        storage.atomic_write(self.lib.settings.path, storage.encoded(raw))
        with self.assertRaises(SettingsError):
            resolve([self.app])
        raw = storage.read_json(self.tool.settings.path)
        raw["data"]["guid"] = self.app.settings.get().guid
        storage.atomic_write(self.tool.settings.path, storage.encoded(raw))
        with self.assertRaises(SettingsError):
            self.solution.settings.reload()

    def test_individual_resolution_ignores_invalid_unrelated_member(self):
        self.link()
        self.tool.settings.path.write_text("invalid JSON", encoding="utf-8")
        self.assertEqual({n.project.name for n in resolve([self.app])[1]}, {"App", "Lib"})
        with self.assertRaises(SettingsError):
            self.solution.settings.reload()

    def test_new_project_settings_reuse_and_read_only_reload(self):
        copied = self.provider.add_project("Copy", "LibCopy", T.STATIC_LIBRARY)
        settings = self.lib.settings.get()
        settings.name = "Created"
        created = self.provider.add_project("Created", "Created", T.STATIC_LIBRARY, settings)
        self.assertNotEqual(created.settings.get().guid, settings.guid)
        self.assertNotEqual(copied.settings.get().guid, settings.guid)
        self.link()
        with patch("cppbuild.storage.atomic_write", side_effect=AssertionError("No write on current reload")), \
             patch("cppbuild.storage.write_lock", side_effect=AssertionError("No writer lock on current reload")):
            reopened = Solution.open(self.solution.root / ".cppbuild")
            self.assertEqual(len(reopened.settings.get().references), 1)
            self.assertEqual(len(resolve([self.app])[1]), 2)

    def test_legacy_external_cycle_migrates_then_reports_cycle(self):
        for project, other in ((self.lib, self.app), (self.app, self.lib)):
            raw = storage.read_json(project.settings.path)
            del raw["data"]["guid"]
            raw["data"]["types"] = {"static_library": next(iter(raw["data"]["types"].values()))}
            # Keep the existing kind documents valid while making both ends linkable.
            type_path = project.settings.path.parent / "types" / next(iter(raw["data"]["types"].values()))
            type_data = storage.read_json(type_path)
            type_data["kind"] = "static_library"
            storage.atomic_write(type_path, storage.encoded(type_data))
            raw["data"]["dependencies"] = {uuid.uuid4().hex: {
                "project": other.name, "project_type": "static_library",
                "solution_directory": str(other.solution.root / ".cppbuild")}}
            storage.atomic_write(project.settings.path, storage.encoded(raw))
        reopened = Solution.open(self.solution.root / ".cppbuild")
        with self.assertRaisesRegex(SettingsError, "cycle"):
            resolve([reopened.get_project("App")])

    @unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2022") == "1", "Real VS2022 required")
    def test_real_shared_target_aliases_move_and_unlink(self):
        self.lib.add_file("src/lib.cpp", content="int value() { return 42; }", auto_update=False)
        for project in (self.app, self.tool):
            project.add_file("src/main.cpp", content="int value(); int main() { return value() == 42 ? 0 : 1; }", auto_update=False)
        a = self.link(name="Common")
        alias = self.link(name="Alias")
        self.link(self.tool, name="Common")
        values = self.solution.settings.get()
        values.solution_folders = SolutionFolderSettings()
        self.solution.settings.save(values)
        for config in ("Debug", "Release"):
            self.solution.set_build_settings(SolutionBuildSettings(configuration=config, run_projects=["App", "Tool"],
                external_build_settings={str(self.config): SolutionBuildSettings(configuration=config)}))
            report = self.solution.run()
            self.assertTrue(report.success, str(report))
            sln = self.solution._last_update.artifacts[0].read_text(encoding="utf-8-sig")
            self.assertEqual(sln.count('Lib.vcxproj"'), 1)
        self.provider.move_project("Lib", "Moved/Lib", auto_update=False)
        report = self.solution.run()
        self.assertTrue(report.success, str(report))
        self.app.settings.unlink(a.dependency_id)
        self.app.settings.unlink(alias.dependency_id)
        (self.app.root / "src/main.cpp").write_text("int main() { return 0; }", encoding="utf-8")
        self.solution.remove_project("Tool")
        self.assertEqual(self.solution.settings.get().references, {})
        self.solution.set_build_settings(SolutionBuildSettings(configuration="Release", run_projects=["App"]))
        report = self.solution.run()
        self.assertTrue(report.success, str(report))
        self.assertNotIn('Lib.vcxproj"', self.solution._last_update.artifacts[0].read_text(encoding="utf-8-sig"))

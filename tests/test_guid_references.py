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


class GuidReferenceTests(unittest.TestCase):
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

    def link(self, project=None):
        return (project or self.app).settings.link_solution(self.config, T.STATIC_LIBRARY)

    def legacy(self, project):
        data = storage.manifest(project.settings.path, "project")
        data.pop("initial_type", None)
        return storage.envelope("project", data)

    def named_legacy(self):
        first, other = self.link(), self.link(self.tool)
        alias_id = uuid.uuid4().hex
        raw = storage.read_json(self.solution.settings.path)
        reference = raw["data"]["references"][self.lib.settings.get().guid]
        raw["schema_version"] = 2
        raw["data"]["references"] = {"共有ライブラリ": reference, "Another": reference}
        storage.atomic_write(self.solution.settings.path, storage.encoded(raw))
        for project, links in ((self.app, {first.dependency_id: "共有ライブラリ", alias_id: "Another"}),
                               (self.tool, {other.dependency_id: "共有ライブラリ"})):
            raw = storage.read_json(project.settings.path)
            raw["schema_version"] = 2
            raw["data"]["dependencies"] = {key: {"kind": "project", "values": {
                "project": "Lib", "project_type": "static_library", "reference": name,
                "project_guid": None, "solution_directory": None}} for key, name in links.items()}
            storage.atomic_write(project.settings.path, storage.encoded(raw))
            for path in project.settings._revision:
                if path != project.settings.path:
                    kind = storage.read_json(path)
                    kind["schema_version"] = 2
                    storage.atomic_write(path, storage.encoded(kind))
        return first.dependency_id, alias_id, other.dependency_id

    def test_automatic_registry_shared_guid_and_last_unlink(self):
        first, second = self.link(), self.link(self.tool)
        guid = self.lib.settings.get().guid
        self.assertEqual(set(self.solution.settings.get().references), {guid})
        dependency = self.app.settings.get().dependencies[first.dependency_id]
        self.assertEqual(dependency, Dependency(guid, T.STATIC_LIBRARY))
        self.assertEqual(len(resolve(self.solution.projects())[1]), 3)
        reopened = Solution.open(self.solution.root / ".cppbuild")
        reopened.get_project("App").settings.unlink(first.dependency_id)
        self.assertEqual(len(reopened.settings.get().references), 1)
        reopened.get_project("Tool").settings.unlink(second.dependency_id)
        self.assertEqual(Solution.open(self.solution.root / ".cppbuild").settings.get().references, {})
        self.assertTrue(self.lib.settings.path.exists())

    def test_legacy_aliases_merge_and_preserve_unlink_ids(self):
        first, second, other = self.named_legacy()
        self.solution.settings.reload()
        guid = self.lib.settings.get().guid
        self.assertEqual(set(self.solution.settings.get().references), {guid})
        self.assertEqual(set(self.app.settings.get().dependencies), {first, second})
        self.assertIn(other, self.tool.settings.get().dependencies)
        for project in (self.app, self.tool):
            raw = storage.read_json(project.settings.path)
            self.assertEqual(raw["schema_version"], storage.CURRENT_SCHEMA)
            for entry in raw["data"]["dependencies"].values():
                self.assertEqual(entry["values"], {"project_guid": guid, "project_type": "static_library"})
        with patch("cppbuild.storage.atomic_write", side_effect=AssertionError("Already migrated")):
            Solution.open(self.solution.root / ".cppbuild")
        roots, nodes = resolve(self.solution.projects())
        self.assertEqual(len(nodes), 3)
        self.assertEqual(len(roots[0].dependencies), 1)
        self.app.settings.unlink(first)
        self.assertEqual(len(self.solution.settings.get().references), 1)
        self.solution.remove_project("Tool")
        self.assertEqual(set(self.solution.settings.get().references), {guid})
        self.app.settings.unlink(second)
        self.assertEqual(self.solution.settings.get().references, {})

    def test_shared_names_distinct_guids_and_conflicting_locations(self):
        self.link()
        other = Solution.create(self.root / "Other", "Other")
        other_lib = other.add_project("Lib", "Lib", T.STATIC_LIBRARY)
        self.tool.settings.link_solution(other.root / ".cppbuild", T.STATIC_LIBRARY)
        with self.assertRaises(SettingsError):
            self.link()
        copied = self.root / "Copied"
        shutil.copytree(self.provider.root, copied)
        with self.assertRaises(SettingsError):
            self.tool.settings.link_solution(copied / ".cppbuild", T.STATIC_LIBRARY)
        self.assertEqual(set(self.solution.settings.get().references),
                         {self.lib.settings.get().guid, other_lib.settings.get().guid})
        self.assertEqual(len(self.tool.settings.get().dependencies), 1)

    def test_name_argument_is_removed_without_writing_settings(self):
        before = {p: p.read_bytes() for p in self.root.rglob("*.json")}
        with self.assertRaises(TypeError):
            self.app.settings.link_solution(self.config, T.STATIC_LIBRARY, name="Common")
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*.json")})

    def test_internal_and_external_saved_targets_are_guid_only(self):
        local = self.solution.add_project("Local", "Local", T.STATIC_LIBRARY)
        internal = self.app.settings.link_project(local, T.STATIC_LIBRARY)
        external = self.link()
        saved = storage.read_json(self.app.settings.path)["data"]["dependencies"]
        for link, target in ((internal, local), (external, self.lib)):
            self.assertEqual(saved[link.dependency_id]["values"],
                             {"project_guid": target.settings.get().guid, "project_type": "static_library"})

    def test_failed_alias_migration_restores_files_and_live_state(self):
        self.named_legacy()
        expected = self.app.settings.get(), self.solution.settings.get()
        before = {p: p.read_bytes() for p in self.solution.root.rglob("*.json")}
        original = storage.atomic_write
        def fail(path, content):
            if path == self.solution.settings.path:
                raise OSError("alias migration failure")
            original(path, content)
        with patch("cppbuild.storage.atomic_write", side_effect=fail), self.assertRaises(OSError):
            self.solution.settings.reload()
        self.assertEqual(before, {p: p.read_bytes() for p in self.solution.root.rglob("*.json")})
        self.assertEqual((self.app.settings.get(), self.solution.settings.get()), expected)

    def test_conflicting_legacy_aliases_do_not_modify_files(self):
        self.named_legacy()
        raw = storage.read_json(self.solution.settings.path)
        raw["data"]["references"]["Another"]["solution_directory"] = "../../Copied/.cppbuild"
        storage.atomic_write(self.solution.settings.path, storage.encoded(raw))
        before = {p: p.read_bytes() for p in self.solution.root.rglob("*.json")}
        with self.assertRaises(SettingsError):
            Solution.open(self.solution.root / ".cppbuild")
        self.assertEqual(before, {p: p.read_bytes() for p in self.solution.root.rglob("*.json")})

    def test_current_schema_rejects_aliases_and_named_dependency_fields(self):
        linked = self.link()
        expected = self.app.settings.get(), self.solution.settings.get()
        for path in (self.solution.settings.path, self.app.settings.path):
            with self.subTest(document=path):
                original = path.read_bytes()
                raw = storage.read_json(path)
                if path == self.solution.settings.path:
                    entry = raw["data"]["references"].pop(self.lib.settings.get().guid)
                    raw["data"]["references"]["Alias"] = entry
                else:
                    raw["data"]["dependencies"][linked.dependency_id]["values"]["project"] = "Lib"
                storage.atomic_write(path, storage.encoded(raw))
                before = {p: p.read_bytes() for p in self.solution.root.rglob("*.json")}
                with self.assertRaises(SettingsError):
                    self.solution.settings.reload()
                self.assertEqual(before, {p: p.read_bytes() for p in self.solution.root.rglob("*.json")})
                self.assertEqual((self.app.settings.get(), self.solution.settings.get()), expected)
                storage.atomic_write(path, original)

    def test_named_migration_uses_saved_guid_when_provider_is_offline(self):
        self.named_legacy()
        self.provider.root.rename(self.root / "Unavailable")
        loaded = Solution.open(self.solution.root / ".cppbuild")
        self.assertEqual(set(loaded.settings.get().references), {self.lib.settings.get().guid})
        for dependency in loaded.get_project("App").settings.get().dependencies.values():
            self.assertEqual(dependency.project_guid, self.lib.settings.get().guid)

    def test_local_and_external_guid_collision_is_rejected(self):
        self.link()
        raw = storage.read_json(self.tool.settings.path)
        raw["data"]["guid"] = self.lib.settings.get().guid
        storage.atomic_write(self.tool.settings.path, storage.encoded(raw))
        before = {p: p.read_bytes() for p in self.solution.root.rglob("*.json")}
        with self.assertRaises(SettingsError):
            Solution.open(self.solution.root / ".cppbuild")
        self.assertEqual(before, {p: p.read_bytes() for p in self.solution.root.rglob("*.json")})

    def test_individual_resolution_migrates_a_legacy_solution_registry(self):
        self.link()
        raw = storage.read_json(self.solution.settings.path)
        raw["schema_version"] = 2
        entry = raw["data"]["references"].pop(self.lib.settings.get().guid)
        raw["data"]["references"]["Alias"] = entry
        storage.atomic_write(self.solution.settings.path, storage.encoded(raw))
        self.assertEqual({n.project.name for n in resolve([self.app])[1]}, {"Lib", "App"})
        self.assertEqual(storage.read_json(self.solution.settings.path)["schema_version"], storage.CURRENT_SCHEMA)

    def test_guid_dependency_propagates_observation_changes(self):
        from cppbuild import information
        local = self.solution.add_project("Local", "Local", T.STATIC_LIBRARY)
        self.app.settings.link_project(local, T.STATIC_LIBRARY)
        information.built(self.app, self.app._resolved_build_settings(), ())
        self.assertEqual(next(p for p in self.solution.info().projects if p.name == "App").build_state, "current")
        local.add_file("src/changed.cpp", content="int changed;", auto_update=False)
        self.assertEqual(next(p for p in self.solution.info().projects if p.name == "App").build_state, "stale")

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
        self.link()
        self.link(self.tool)
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
        raw = self.legacy(self.lib)
        del raw["data"]["guid"]
        storage.atomic_write(self.lib.settings.path, storage.encoded(raw))
        ids = []
        for project in (self.app, self.tool):
            raw = self.legacy(project)
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
        raw = self.legacy(self.app)
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
            raw = self.legacy(project)
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
    def test_real_shared_target_move_and_unlink(self):
        self.lib.add_file("src/lib.cpp", content="int value() { return 42; }", auto_update=False)
        for project in (self.app, self.tool):
            project.add_file("src/main.cpp", content="int value(); int main() { return value() == 42 ? 0 : 1; }", auto_update=False)
        a = self.link()
        self.link(self.tool)
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
        (self.app.root / "src/main.cpp").write_text("int main() { return 0; }", encoding="utf-8")
        self.solution.remove_project("Tool")
        self.assertEqual(self.solution.settings.get().references, {})
        self.solution.set_build_settings(SolutionBuildSettings(configuration="Release", run_projects=["App"]))
        report = self.solution.run()
        self.assertTrue(report.success, str(report))
        self.assertNotIn('Lib.vcxproj"', self.solution._last_update.artifacts[0].read_text(encoding="utf-8-sig"))


class TopSolutionPriorityTests(unittest.TestCase):
    """ECS links deps/STL and deps/A; A registers its own copy at A/deps/STL."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cppbuild-top-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ecs = Solution.create(self.root / "ECS", "ECS")
        self.app = self.ecs.add_project("ECS", "ECS", T.EXECUTABLE)
        self.stl = Solution.create(self.root / "ECS/deps/STL", "STL")
        self.stl_lib = self.stl.add_project("STL", "STL", T.STATIC_LIBRARY)
        self.stl_lib.add_file("src/stl.cpp", content="int stl_value() { return 1; }", auto_update=False)
        self.a = Solution.create(self.root / "ECS/deps/A", "A")
        self.a_lib = self.a.add_project("A", "A", T.STATIC_LIBRARY)
        self.a_lib.add_file("src/a.cpp", content="int stl_value(); int a_value() { return stl_value(); }", auto_update=False)
        self.copy = self.root / "ECS/deps/A/deps/STL"
        shutil.copytree(self.stl.root, self.copy)
        (self.copy / "STL/src/stl.cpp").write_text("int stl_value() { return 2; }", encoding="utf-8")
        self.a_lib.settings.link_solution(self.copy / ".cppbuild", T.STATIC_LIBRARY)

    def roots(self, nodes):
        return {n.project.name: n.project.root for n in nodes}

    def test_top_solution_reference_location_wins(self):
        self.assertEqual(self.roots(resolve([self.a_lib])[1])["STL"], self.copy / "STL")
        # Without its own registration, ECS uses A's copy through A.
        self.app.settings.link_solution(self.a.root / ".cppbuild", T.STATIC_LIBRARY)
        self.assertEqual(self.roots(resolve([self.app])[1])["STL"], self.copy / "STL")
        self.app.settings.link_solution(self.stl.root / ".cppbuild", T.STATIC_LIBRARY)
        nodes = resolve([self.app])[1]
        self.assertEqual([n.project.name for n in nodes].count("STL"), 1)
        self.assertEqual(self.roots(nodes)["STL"], self.stl_lib.root)
        a_node = next(n for n in nodes if n.project.name == "A")
        self.assertEqual([d.project.root for d in a_node.dependencies], [self.stl_lib.root])
        values = self.ecs.settings.get()
        values.solution_folders = SolutionFolderSettings()
        self.ecs.settings.save(values)
        # Displayed external members never open the unused nested copy.
        with patch("cppbuild.core.Solution.open", wraps=Solution.open) as opened:
            nodes = resolve(self.ecs.projects(), include_external_members=True)[1]
        self.assertTrue(opened.called)
        self.assertNotIn(self.copy / ".cppbuild", [Path(c.args[0]).resolve() for c in opened.call_args_list])
        self.assertNotIn(self.copy / "STL", [n.project.root for n in nodes])
        self.assertEqual(self.roots(resolve([self.a_lib])[1])["STL"], self.copy / "STL")

    def test_unregistered_copies_below_the_top_are_still_rejected(self):
        b = Solution.create(self.root / "ECS/deps/B", "B")
        b_lib = b.add_project("B", "B", T.STATIC_LIBRARY)
        shutil.copytree(self.stl.root, b.root / "deps/STL")
        b_lib.settings.link_solution(b.root / "deps/STL/.cppbuild", T.STATIC_LIBRARY)
        self.app.settings.link_solution(self.a.root / ".cppbuild", T.STATIC_LIBRARY)
        self.app.settings.link_solution(b.root / ".cppbuild", T.STATIC_LIBRARY)
        with self.assertRaisesRegex(SettingsError, "multiple locations"):
            resolve([self.app])
        self.app.settings.link_solution(self.stl.root / ".cppbuild", T.STATIC_LIBRARY)
        self.assertEqual(self.roots(resolve([self.app])[1])["STL"], self.stl_lib.root)

    @unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2022") == "1", "Real VS2022 required")
    def test_real_top_solution_location_is_built_and_linked(self):
        self.app.add_file("src/main.cpp", content="int a_value(); int main() { return a_value() == 1 ? 0 : 1; }",
                          auto_update=False)
        self.app.settings.link_solution(self.stl.root / ".cppbuild", T.STATIC_LIBRARY)
        self.app.settings.link_solution(self.a.root / ".cppbuild", T.STATIC_LIBRARY)
        for _ in range(2):
            report = self.app.run()
            self.assertTrue(report.success, str(report))
            # A alone switches back to its own copy and still builds.
            report = self.a_lib.build()
            self.assertTrue(report.success, str(report))
        self.ecs.set_build_settings(SolutionBuildSettings(run_projects=["ECS"]))
        report = self.ecs.run()
        self.assertTrue(report.success, str(report))
        sln = self.ecs._last_update.artifacts[0].read_text(encoding="utf-8-sig")
        self.assertEqual(sln.count('STL.vcxproj"'), 1)
        self.assertNotIn(str(self.copy), sln)

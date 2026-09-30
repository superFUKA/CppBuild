"""Library kinds can switch; executable and TEST Projects keep their purpose."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cppbuild import (ImportedLibrary, ProjectBuildSettings, ProjectSettingsData, ProjectType as T,
                      SettingsError, Solution, SolutionBuildSettings, SolutionFolderSettings,
                      TemplateTools, TypeSettingsData)
from cppbuild import storage
from cppbuild.graph import resolve


LIBRARIES = (T.STATIC_LIBRARY, T.SHARED_LIBRARY, T.INTERFACE_LIBRARY)


class ProjectTypeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cppbuild-types-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.solution = Solution.create(self.root / "Kinds", "Kinds")
        self.project = self.solution.add_project("Switch", "Switch", T.STATIC_LIBRARY)
        self.outputs = []
        self.addCleanup(self.write_log)

    def write_log(self):
        log = os.environ.get("CPPBUILD_TYPE_TEST_LOG")
        if log and self.outputs:
            with Path(log).open("a", encoding="utf-8") as stream:
                stream.write(self.id() + "\n" + "\n".join(self.outputs) + "\n")

    def success(self, report):
        self.outputs.extend(p.output for p in report.processes)
        self.assertTrue(report.success, "\n".join(p.output for p in report.processes))

    def legacy(self, solution, *, version=3):
        """Make an old single-kind manifest, including old dependency names."""
        for project in solution.projects():
            raw = storage.read_json(project.settings.path)
            default = raw["data"].pop("initial_type")
            path = project.settings.path.parent / raw["data"]["types"][default]
            kind = storage.read_json(path)
            old = "header_only" if default == "interface_library" else default
            kind["kind"], kind["schema_version"] = old, version
            storage.atomic_write(path, storage.encoded(kind))
            raw["data"]["types"] = {old: raw["data"]["types"][default]}
            raw["schema_version"] = version
            for dependency in raw["data"]["dependencies"].values():
                values = dependency["values"]
                if values.get("project_type") == "interface_library":
                    values["project_type"] = "header_only"
            storage.atomic_write(project.settings.path, storage.encoded(raw))
        raw = storage.read_json(solution.settings.path)
        raw["schema_version"] = version
        storage.atomic_write(solution.settings.path, storage.encoded(raw))

    def test_new_name_and_all_library_selections_without_registration(self):
        self.assertFalse(hasattr(T, "HEADER_ONLY"))
        self.assertEqual(T.INTERFACE_LIBRARY.value, "interface_library")
        for initial in LIBRARIES:
            with self.subTest(initial=initial):
                project = self.solution.add_project(initial.name, initial.name, initial)
                self.assertEqual(set(project.settings.get().types), set(LIBRARIES))
                self.assertEqual(project._resolved_build_settings().project_type, initial)
                for selected in LIBRARIES:
                    project.set_build_settings(ProjectBuildSettings(project_type=selected))
                    self.assertEqual(project._resolved_build_settings().project_type, selected)
                reopened = Solution.open(self.solution.root / ".cppbuild").get_project(initial.name)
                self.assertEqual(reopened._resolved_build_settings().project_type, initial)
                self.assertEqual(reopened.settings.get().guid, project.settings.get().guid)

    def test_other_families_rejected_without_mutating_settings(self):
        for initial in T:
            project = self.solution.add_project(initial.name, initial.name, initial)
            allowed = set(LIBRARIES) if initial in LIBRARIES else {initial}
            for other in set(T) - allowed:
                with self.subTest(initial=initial, other=other):
                    before = project.settings.path.read_bytes()
                    with self.assertRaises(SettingsError):
                        project.set_build_settings(ProjectBuildSettings(project_type=other))
                    self.assertEqual(project._resolved_build_settings().project_type, initial)
                    data = project.settings.get()
                    data.types = {kind: TypeSettingsData() for kind in (LIBRARIES if other in LIBRARIES else (other,))}
                    data.initial_type = other
                    with self.assertRaises(SettingsError):
                        project.settings.save(data)
                    self.assertEqual(project.settings.path.read_bytes(), before)

    def test_mixed_creation_and_partial_library_save_rejected(self):
        data = ProjectSettingsData("Mixed", {T.EXECUTABLE: TypeSettingsData(), T.STATIC_LIBRARY: TypeSettingsData()})
        with self.assertRaises(SettingsError):
            self.solution.add_project("Mixed", "Mixed", T.EXECUTABLE, data)
        self.assertFalse((self.solution.root / "Mixed").exists())
        before = self.project.settings.get()
        data = self.project.settings.get()
        del data.types[T.STATIC_LIBRARY]
        with self.assertRaises(SettingsError):
            self.project.settings.save(data)
        self.assertEqual(self.project.settings.get(), before)

    def test_external_reload_cannot_change_family_on_live_project(self):
        before = self.project.settings.get()
        raw = storage.read_json(self.project.settings.path)
        raw["data"]["initial_type"] = "executable"
        storage.atomic_write(self.project.settings.path, storage.encoded(raw))
        with self.assertRaises(SettingsError):
            self.project.settings.reload()
        self.assertEqual(self.project.settings.get(), before)

    def test_link_kind_is_fixed_when_provider_selection_changes(self):
        app = self.solution.add_project("App", "App", T.EXECUTABLE)
        link = app.settings.link_project(self.project, T.STATIC_LIBRARY)
        for kind in LIBRARIES:
            self.project.set_build_settings(ProjectBuildSettings(project_type=kind))
            _, nodes = resolve([app])
            self.assertEqual(nodes[0].settings.project_type, T.STATIC_LIBRARY)
            self.assertEqual(app.settings.get().dependencies[link.dependency_id].project_type, T.STATIC_LIBRARY)

    def test_legacy_header_settings_and_dependencies_migrate_once(self):
        provider = Solution.create(self.root / "Provider", "Provider")
        library = provider.add_project("Api", "Api", T.INTERFACE_LIBRARY)
        app = self.solution.add_project("App", "App", T.EXECUTABLE)
        external = app.settings.link_solution(provider.root / ".cppbuild", T.INTERFACE_LIBRARY)
        internal = app.settings.link_project(self.project, T.INTERFACE_LIBRARY)
        imported = app.settings.link_imported_library(ImportedLibrary(T.INTERFACE_LIBRARY, include_directories=["include"]))
        original_guid = library.settings.get().guid
        self.legacy(provider)
        self.legacy(self.solution)
        loaded = Solution.open(self.solution.root / ".cppbuild")
        _, nodes = resolve([loaded.get_project("App")])
        migrated = next(n.project for n in nodes if n.project.name == "Api")
        self.assertEqual(migrated.settings.get().guid, original_guid)
        self.assertEqual(migrated.settings.get().initial_type, T.INTERFACE_LIBRARY)
        dependencies = loaded.get_project("App").settings.get().dependencies
        self.assertEqual(set(dependencies), {external.dependency_id, internal.dependency_id, imported.dependency_id})
        self.assertTrue(all(d.project_type == T.INTERFACE_LIBRARY for d in dependencies.values()))
        for owner in (loaded, migrated.solution):
            for project in owner.projects():
                raw = storage.read_json(project.settings.path)
                self.assertEqual(raw["schema_version"], 4)
                self.assertNotIn('"header_only"', project.settings.path.read_text(encoding="utf-8"))
                for reference in raw["data"]["types"].values():
                    typed = storage.read_json(project.settings.path.parent / reference)
                    self.assertNotEqual(typed["kind"], "header_only")
        with patch("cppbuild.storage.atomic_write", side_effect=AssertionError("Unexpected migration")):
            loaded.settings.reload()
            resolve([loaded.get_project("App")])

    def test_v2_header_migration_and_subfolder_paths(self):
        library = self.solution.add_project("Api", "Api", T.INTERFACE_LIBRARY)
        self.legacy(self.solution, version=2)
        raw = storage.read_json(library.settings.path)
        old_path = library.settings.path.parent / raw["data"]["types"]["header_only"]
        nested = old_path.parent / "nested" / old_path.name
        typed = storage.read_json(old_path)
        typed["data"]["include_directories"] = ["../../../include"]
        storage.atomic_write(nested, storage.encoded(typed))
        raw["data"]["types"]["header_only"] = nested.relative_to(library.settings.path.parent).as_posix()
        storage.atomic_write(library.settings.path, storage.encoded(raw))
        loaded = Solution.open(self.solution.root / ".cppbuild").get_project("Api")
        self.assertEqual(loaded.settings.get().types[T.INTERFACE_LIBRARY].include_directories, ["include"])
        self.assertEqual(loaded.settings.get().initial_type, T.INTERFACE_LIBRARY)

    def test_migration_failure_restores_saved_files_and_state(self):
        library = self.solution.add_project("Api", "Api", T.INTERFACE_LIBRARY)
        before_data = library.settings.get()
        self.legacy(self.solution)
        before = {p: p.read_bytes() for p in self.solution.root.rglob("*.json")}
        original = storage.atomic_write

        def fail(path, content):
            if path == library.settings.path:
                raise OSError("migration failed")
            return original(path, content)

        with patch("cppbuild.storage.atomic_write", side_effect=fail), self.assertRaises(OSError):
            self.solution.settings.reload()
        self.assertEqual(before, {p: p.read_bytes() for p in self.solution.root.rglob("*.json")})
        self.assertEqual(library.settings.get(), before_data)

    def test_current_schema_rejects_old_name_and_duplicate_legacy_kinds(self):
        library = self.solution.add_project("Api", "Api", T.INTERFACE_LIBRARY)
        original = library.settings.path.read_bytes()
        raw = storage.read_json(library.settings.path)
        raw["data"]["types"]["header_only"] = raw["data"]["types"].pop("interface_library")
        storage.atomic_write(library.settings.path, storage.encoded(raw))
        with self.assertRaises(SettingsError):
            Solution.open(self.solution.root / ".cppbuild")
        library.settings.path.write_bytes(original)
        self.legacy(self.solution)
        raw = storage.read_json(library.settings.path)
        raw["data"]["types"]["interface_library"] = raw["data"]["types"]["header_only"]
        storage.atomic_write(library.settings.path, storage.encoded(raw))
        with self.assertRaises(SettingsError):
            Solution.open(self.solution.root / ".cppbuild")

    def test_old_mixed_families_fail_without_rewriting(self):
        self.legacy(self.solution)
        raw = storage.read_json(self.project.settings.path)
        source = self.project.settings.path.parent / raw["data"]["types"]["static_library"]
        typed = storage.read_json(source)
        typed["kind"] = "executable"
        target = source.parent / "old-executable.json"
        storage.atomic_write(target, storage.encoded(typed))
        raw["data"]["types"]["executable"] = target.relative_to(self.project.settings.path.parent).as_posix()
        storage.atomic_write(self.project.settings.path, storage.encoded(raw))
        before = {p: p.read_bytes() for p in self.solution.root.rglob("*.json")}
        with self.assertRaises(SettingsError):
            Solution.open(self.solution.root / ".cppbuild")
        self.assertEqual(before, {p: p.read_bytes() for p in self.solution.root.rglob("*.json")})

    def test_initial_type_survives_move_and_template(self):
        project = self.solution.add_project("Api", "Api", T.INTERFACE_LIBRARY)
        guid = project.settings.get().guid
        self.solution.move_project("Api", "Moved/Api", auto_update=False)
        self.assertEqual(project.settings.get().guid, guid)
        template = TemplateTools.create_solution_template(self.solution, self.root / "Template")
        restored = Solution.create(self.root / "Restored", "Restored", template=template).get_project("Api")
        self.assertEqual(restored._resolved_build_settings().project_type, T.INTERFACE_LIBRARY)
        self.assertNotEqual(restored.settings.get().guid, guid)

    @unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2022") == "1", "Real VS2022 required")
    def test_real_switch_libraries_and_link_each_kind(self):
        project = self.project
        data = project.settings.get()
        data.types[T.INTERFACE_LIBRARY].public_definitions = ["USE_INLINE"]
        project.settings.save(data)
        project.add_file("include/api.hpp", content='''#pragma once
#ifdef USE_INLINE
inline int value() { return 42; }
#else
int value();
#endif
''', auto_update=False)
        project.add_file("src/value.cpp", content="__declspec(dllexport) int value() { return 42; }", auto_update=False)
        guid = project.settings.get().guid
        for kind in (*LIBRARIES, T.STATIC_LIBRARY):
            project.set_build_settings(ProjectBuildSettings(project_type=kind))
            report = project.build()
            self.success(report)
            if kind == T.INTERFACE_LIBRARY:
                self.assertEqual(report.artifacts, ())
                self.assertFalse(list((project.root / ".cppbuild/build/vs2022-x64-interface_library").rglob("value.obj")))
            else:
                suffix = ".lib" if kind == T.STATIC_LIBRARY else ".dll"
                self.assertTrue(any(p.suffix == suffix and p.is_file() for p in report.artifacts))
            self.success(self.solution.build())
            self.assertEqual(project.settings.get().guid, guid)
        names = []
        for i, kind in enumerate(LIBRARIES):
            name = f"App{i}"
            app = self.solution.add_project(name, name, T.EXECUTABLE)
            app.settings.link_project(project, kind)
            app.add_file("src/main.cpp", content='#include "api.hpp"\nint main() { return value() == 42 ? 0 : 1; }', auto_update=False)
            names.append(name)
        self.solution.set_build_settings(SolutionBuildSettings(build_projects=names, run_projects=names))
        self.success(self.solution.run())
        self.success(project.clean())
        self.assertFalse(list((project.root / ".cppbuild/build/vs2022-x64-static_library").rglob("Switch.lib")))

    @unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2022") == "1", "Real VS2022 required")
    def test_real_interface_with_invalid_cpp_migration_and_display(self):
        provider = Solution.create(self.root / "Provider", "Provider")
        interface = provider.add_project("Api", "Api", T.INTERFACE_LIBRARY)
        interface.add_file("src/unused.cpp", content="#error must_not_compile", auto_update=False)
        interface.add_file("include/api.hpp", content="#pragma once\nstruct Value { int number; };", auto_update=False)
        extra = provider.add_project("Extra", "Extra", T.INTERFACE_LIBRARY)
        extra.add_file("include/extra.hpp", content="#pragma once", auto_update=False)
        consumer = Solution.create(self.root / "Consumer", "Consumer")
        app = consumer.add_project("App", "App", T.EXECUTABLE)
        app.add_file("src/main.cpp", content='#include "api.hpp"\nint main() { Value v{42}; return v.number == 42 ? 0 : 1; }', auto_update=False)
        app.settings.link_solution(provider.root / ".cppbuild", T.INTERFACE_LIBRARY)
        self.legacy(provider)
        self.legacy(consumer)
        consumer = Solution.open(consumer.root / ".cppbuild")
        settings = consumer.settings.get()
        settings.solution_folders = SolutionFolderSettings()
        consumer.settings.save(settings)
        consumer.set_build_settings(SolutionBuildSettings(run_projects=["App"]))
        self.success(consumer.run())
        sln = consumer._last_update.artifacts[0].read_text(encoding="utf-8-sig")
        self.assertIn("interface_library", sln)
        self.assertNotIn("header_only", sln)
        self.assertFalse(list(provider.root.rglob("unused.obj")))
        loaded = Solution.open(provider.root / ".cppbuild").get_project("Api")
        self.success(loaded.clean())
        self.assertTrue((loaded.root / "src/unused.cpp").is_file())

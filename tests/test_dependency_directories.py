"""Dependency directories resolve nested GUIDs at the top level; link types can be switched per operation."""
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from cppbuild import (MissingDependenciesError, ProjectBuildSettings, ProjectType as T, SettingsError, Solution,
                      SolutionBuildSettings)
from cppbuild import storage
from cppbuild.graph import resolve

EXPORT = ('#pragma once\n#ifdef _WIN32\n#ifdef STL_EXPORTS\n#define STL_API __declspec(dllexport)\n#elif defined(STL_SHARED)\n'
          '#define STL_API __declspec(dllimport)\n#else\n#define STL_API\n#endif\n#else\n#define STL_API\n#endif\n'
          'STL_API int stl_value();\n')


class DependencyDirectoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cppbuild-deps-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        # ECS -> A -> STL, ECS -> B -> STL; ECS does not use STL directly.
        self.ecs = Solution.create(self.root / "ECS", "ECS")
        self.stl = self.library(self.ecs.root / "deps/STL", "STL", "int stl_value() { return 40; }\n")
        self.a = self.consumer("A", "int a_value() { return stl_value() + 1; }\n")
        self.b = self.consumer("B", "int b_value() { return stl_value() + 1; }\n")
        self.app = self.ecs.add_project("App", "App", T.EXECUTABLE)
        self.app.add_file("src/main.cpp", content="#include <cstdio>\nint a_value(); int b_value();\n"
                          "int main() { std::printf(\"%d\", a_value() + b_value()); return a_value() + b_value() == 82 ? 0 : 1; }\n",
                          auto_update=False)
        for name in ("A", "B"):
            self.app.settings.link_solution(self.ecs.root / "deps" / name / ".cppbuild", T.STATIC_LIBRARY)

    def library(self, directory, name, source):
        solution = Solution.create(directory, name)
        project = solution.add_project(name, name, T.STATIC_LIBRARY)
        project.add_file("include/stl.hpp", content=EXPORT, auto_update=False)
        project.add_file("src/value.cpp", content='#include "stl.hpp"\n' + source, auto_update=False)
        data = project.settings.get()
        data.types[T.SHARED_LIBRARY].compile_definitions = ["STL_EXPORTS"]
        data.types[T.SHARED_LIBRARY].public_definitions = ["STL_SHARED"]
        project.settings.save(data)
        return project

    def consumer(self, name, source):
        directory = self.ecs.root / "deps" / name
        solution = Solution.create(directory, name)
        project = solution.add_project(name, name, T.STATIC_LIBRARY)
        project.add_file("src/value.cpp", content='#include "stl.hpp"\n' + source, auto_update=False)
        # Link a private copy, then drop it as a clone without its ignored deps/ would.
        shutil.copytree(self.stl.solution.root, directory / "deps/STL")
        project.settings.link_solution(directory / "deps/STL/.cppbuild", T.STATIC_LIBRARY)
        shutil.rmtree(directory / "deps")
        return project

    def use_dependency_directory(self):
        data = self.ecs.settings.get()
        data.dependency_directories = ["deps"]
        self.ecs.settings.save(data)

    def nodes(self, owner=None):
        _, nodes = resolve([owner or self.app])
        return {n.project.name: n for n in nodes}

    def test_missing_nested_copies_are_reported_together(self):
        with self.assertRaises(MissingDependenciesError) as caught:
            resolve([self.app])
        [missing] = caught.exception.missing
        self.assertEqual(caught.exception.project_guids, (self.stl.settings.get().guid,))
        self.assertEqual(missing.project_type, T.STATIC_LIBRARY)
        self.assertEqual(len(missing.required_by), 2)
        self.assertTrue(any(user.endswith(":A") for user in missing.required_by))
        self.assertEqual(set(missing.registered_locations), {(p.solution.root / "deps/STL/.cppbuild").resolve() for p in (self.a, self.b)})
        self.assertIsInstance(caught.exception, SettingsError)

    def test_dependency_directory_unifies_diamond(self):
        before = self.ecs.settings.path.read_bytes()
        self.use_dependency_directory()
        self.assertIn(b'"dependency_directories"', self.ecs.settings.path.read_bytes())
        self.assertNotEqual(before, self.ecs.settings.path.read_bytes())
        nodes = self.nodes()
        self.assertEqual(nodes["STL"].project.root, self.stl.root.resolve())
        self.assertEqual(nodes["A"].dependencies, [nodes["STL"]])
        self.assertIs(nodes["B"].dependencies[0], nodes["STL"])
        # ECS itself gained no dependency and no reference to STL.
        self.assertNotIn(self.stl.settings.get().guid, self.ecs.settings.get().references)
        reopened = Solution.open(self.ecs.root / ".cppbuild")
        self.assertEqual(reopened.settings.get().dependency_directories, ["deps"])
        _, nodes = resolve([reopened.get_project("App")])
        self.assertIn(self.stl.root.resolve(), [n.project.root for n in nodes])

    def test_all_unavailable_guids_are_listed(self):
        self.use_dependency_directory()
        shutil.rmtree(self.ecs.root / "deps/STL")
        shutil.rmtree(self.ecs.root / "deps/B")
        with self.assertRaises(MissingDependenciesError) as caught:
            resolve([self.app])
        self.assertEqual(set(caught.exception.project_guids),
                         {self.stl.settings.get().guid, self.b.settings.get().guid})

    def test_duplicate_guid_in_dependency_directory_is_an_error(self):
        self.use_dependency_directory()
        shutil.copytree(self.stl.solution.root, self.ecs.root / "deps/STL-copy")
        with self.assertRaises(SettingsError) as caught:
            resolve([self.app])
        self.assertIn("dependency directories", str(caught.exception))
        self.assertNotIsInstance(caught.exception, MissingDependenciesError)

    def test_top_level_link_wins_over_dependency_directory(self):
        self.use_dependency_directory()
        # A working copy of the same GUID outside deps/, linked explicitly by the top-level Solution.
        shutil.copytree(self.stl.solution.root, self.root / "work/STL")
        tool = self.ecs.add_project("Tool", "Tool", T.EXECUTABLE)
        tool.settings.link_solution(self.root / "work/STL/.cppbuild", T.STATIC_LIBRARY)
        self.assertEqual(self.nodes()["STL"].project.root, (self.root / "work/STL/STL").resolve())

    def test_dependency_directory_validation(self):
        for directories in (["."], [".cppbuild"], ["App"], ["deps", "deps"], ["../outside"], "deps"):
            with self.subTest(directories=directories):
                data = self.ecs.settings.get()
                data.dependency_directories = directories
                with self.assertRaises(SettingsError):
                    self.ecs.settings.save(data)
        # A missing directory resolves like an empty one.
        data = self.ecs.settings.get()
        data.dependency_directories = ["not-cloned-yet"]
        self.ecs.settings.save(data)
        with self.assertRaises(MissingDependenciesError):
            resolve([self.app])

    def test_template_keeps_setting_but_not_clones(self):
        from cppbuild import TemplateTools
        self.use_dependency_directory()
        template = TemplateTools.create_solution_template(self.ecs, self.root / "Template")
        self.assertFalse((template / "deps").exists())
        restored = Solution.create(self.root / "Restored", "Restored", template=template)
        self.assertEqual(restored.settings.get().dependency_directories, ["deps"])
        # References into the source's deps/ follow the layout into the restored Solution.
        self.assertEqual({r.solution_directory for r in restored.settings.get().references.values()},
                         {"deps/A/.cppbuild", "deps/B/.cppbuild"})
        with self.assertRaises(MissingDependenciesError) as caught:
            resolve([restored.get_project("App")])
        self.assertEqual(set(caught.exception.project_guids), {self.a.settings.get().guid, self.b.settings.get().guid})
        # Cloning the dependencies into the restored deps/ resolves the whole diamond there.
        for name in ("A", "B", "STL"):
            shutil.copytree(self.ecs.root / "deps" / name, restored.root / "deps" / name)
        _, nodes = resolve([restored.get_project("App")])
        self.assertTrue(all(n.project.root.is_relative_to(restored.root.resolve()) for n in nodes))

    def test_empty_directories_are_not_written(self):
        data = self.ecs.settings.get()
        data.main_project = "App"
        self.ecs.settings.save(data)
        self.assertNotIn("dependency_directories", storage.read_json(self.ecs.settings.path)["data"])

    def test_operation_project_types_switch_external_links(self):
        self.use_dependency_directory()
        stl = self.stl.settings.get().guid
        self.ecs.set_build_settings(SolutionBuildSettings(project_types={stl: T.SHARED_LIBRARY}))
        nodes = self.nodes()
        self.assertEqual(nodes["STL"].settings.project_type, T.SHARED_LIBRARY)
        self.assertEqual(nodes["A"].settings.project_type, T.STATIC_LIBRARY)
        # Saved dependencies are unchanged.
        self.assertEqual(next(iter(self.a.settings.get().dependencies.values())).project_type, T.STATIC_LIBRARY)
        for invalid in ({stl: T.INTERFACE_LIBRARY}, {"not-a-guid": T.STATIC_LIBRARY}, {stl: "static_library"}):
            with self.subTest(invalid=invalid), self.assertRaises(SettingsError):
                self.ecs.set_build_settings(SolutionBuildSettings(project_types=invalid))
        self.ecs.set_build_settings(SolutionBuildSettings(project_types={self.app.settings.get().guid: T.STATIC_LIBRARY}))
        with self.assertRaises(SettingsError):
            resolve([self.app])

    @unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2022") == "1", "Real VS2022 required")
    def test_real_diamond_build_with_shared_override(self):
        self.use_dependency_directory()
        for settings in (SolutionBuildSettings(),
                         SolutionBuildSettings(project_types={self.stl.settings.get().guid: T.SHARED_LIBRARY})):
            with self.subTest(project_types=settings.project_types):
                self.ecs.set_build_settings(settings)
                report = self.app.run()
                self.assertTrue(report.success, "\n".join(p.output[-3000:] for p in report.processes))
                self.assertEqual(report.processes[-1].output.strip(), "82")
                whole = self.ecs.build()
                self.assertTrue(whole.success, "\n".join(p.output[-3000:] for p in whole.processes))
        self.assertTrue(list((self.ecs.root / ".cppbuild/output").rglob("STL.dll")))
        self.assertFalse((self.a.solution.root / "deps").exists())


if __name__ == "__main__":
    unittest.main()

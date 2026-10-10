import json
import os
from pathlib import Path
import re
import tempfile
import unittest

from cppbuild import (Solution, SolutionFolderSettings, SolutionBuildSettings,
                      ProjectType as T, SettingsError, TemplateTools, TypeSettingsData)
from cppbuild.graph import resolve
from cppbuild import workspace


class SolutionFolderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cppbuild-folders-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.solution = Solution.create(self.root / "Main", "Main")
        self.app = self.solution.add_project("App", "App", T.EXECUTABLE)
        self.app.add_file("src/main.cpp", content="int value(); int main() { return value() == 42 ? 0 : 1; }", auto_update=False)
        self.external = Solution.create(self.root / "External", "External")
        self.lib = self.external.add_project("Lib", "Lib", T.STATIC_LIBRARY)
        self.lib.add_file("src/lib.cpp", content="int value() { return 42; }", auto_update=False)
        self.unused = self.external.add_project("Unused", "Unused", T.EXECUTABLE)
        # Deliberately uncompilable: displaying this Project must not build it.
        self.unused.add_file("src/main.cpp", content="#error display_only\n", auto_update=False)
        self.link = self.app.settings.link_solution(self.external.root / ".cppbuild", T.STATIC_LIBRARY)

    def configure(self):
        values = self.solution.settings.get()
        values.solution_folders = SolutionFolderSettings("My Projects", "Linked Projects", {"App": "Apps/Tools"})
        self.solution.settings.save(values)

    def test_roundtrip_legacy_and_copy_isolation(self):
        self.configure()
        reopened = Solution.open(self.solution.root / ".cppbuild")
        self.assertEqual(reopened.settings.get().solution_folders.project_folders, {"App": "Apps/Tools"})
        values = reopened.settings.get()
        values.solution_folders.project_folders.clear()
        self.assertEqual(reopened.settings.get().solution_folders.project_folders, {"App": "Apps/Tools"})
        path = self.solution.settings.path
        data = json.loads(path.read_text())
        del data["data"]["solution_folders"]
        path.write_text(json.dumps(data))
        self.assertIsNone(Solution.open(path.parent).settings.get().solution_folders)

    def test_external_folder_saved_only_when_renamed(self):
        self.configure()
        path = self.solution.settings.path
        self.assertNotIn("external", json.loads(path.read_text())["data"]["solution_folders"])
        self.assertEqual(Solution.open(path.parent).settings.get().solution_folders.external, "External")
        values = self.solution.settings.get()
        values.solution_folders.external = "Third Party"
        self.solution.settings.save(values)
        self.assertEqual(json.loads(path.read_text())["data"]["solution_folders"]["external"], "Third Party")
        self.assertEqual(Solution.open(path.parent).settings.get().solution_folders.external, "Third Party")
        for value in ["My Projects", "linked projects", "A/B", ""]:
            with self.subTest(value=value), self.assertRaises(SettingsError):
                values.solution_folders.external = value
                self.solution.settings.save(values)

    def test_external_folder_generation(self):
        tests = self.external.add_project("Tests", "Tests", T.TEST)
        plan = workspace.plan(self.solution)
        top = plan.documents()[self.solution.root / "CppBuildTopLevel.cmake"]
        self.assertNotIn("CPPBUILD_EXTERNAL_FOLDER", top)
        self.assertNotIn("PREDEFINED_TARGETS_FOLDER", top)
        self.configure()
        plan = workspace.plan(self.solution)
        documents = plan.documents()
        top = documents[self.solution.root / "CppBuildTopLevel.cmake"]
        # Set before the linked Solutions are added, so their GoogleTest goes there too.
        self.assertLess(top.index('set(CPPBUILD_EXTERNAL_FOLDER "External")'), top.index("add_subdirectory("))
        text = documents[tests.root / "CMakeLists.txt"]
        self.assertIn('set_property(GLOBAL PROPERTY PREDEFINED_TARGETS_FOLDER "External/CMake")', top)
        self.assertLess(text.index('set(CMAKE_FOLDER "${CPPBUILD_EXTERNAL_FOLDER}/GoogleTest")'), text.index("FetchContent_MakeAvailable"))
        self.assertLess(text.index("FetchContent_MakeAvailable"), text.index('set(CMAKE_FOLDER "${_cppbuild_folder}")'))

    def test_invalid_paths_members_and_root_collisions(self):
        for value in ["../Outside", "/Root", "C:/Root", "A//B", "A\\B", "A/..", "A;B", "A\nB"]:
            with self.subTest(value=value), self.assertRaises(SettingsError):
                values = self.solution.settings.get()
                values.solution_folders = SolutionFolderSettings(project_folders={"App": value})
                self.solution.settings.save(values)
        for setting in [SolutionFolderSettings(project_folders={"Lib": "Libs"}),
                        SolutionFolderSettings("Same", "same"),
                        SolutionFolderSettings("Nested/Root", "Linked")]:
            with self.assertRaises(SettingsError):
                values.solution_folders = setting
                self.solution.settings.save(values)

    def test_display_expansion_does_not_change_dependency_closure(self):
        self.configure()
        roots, nodes = resolve(self.solution.projects(), include_external_members=True)
        self.assertEqual({n.project.name for n in nodes}, {"App", "Lib", "Unused"})
        plan = workspace.plan(self.solution)
        display = {plan.projects[g].name for g in plan.listed}
        self.assertEqual(display, {"Unused"})
        self.assertEqual([(p.name, kinds) for p, kinds in plan.external_requests()], [("Lib", ["STATIC"]), ("Unused", ["DISPLAY"])])
        self.assertEqual([folder for _, _, _, folder in plan.linked_solutions()], ["Linked Projects/External"])
        entry = plan.documents()[self.solution.root / "CMakeLists.txt"]
        self.assertIn('set(CMAKE_FOLDER "My Projects/Apps/Tools")', entry)
        self.assertEqual([p.name for p in self.solution.projects()], ["App"])
        self.assertEqual({n.project.name for n in resolve(self.solution.projects())[1]}, {"App", "Lib"})
        self.app.settings.unlink(self.link.dependency_id)
        self.assertEqual([n.project.name for n in resolve(self.solution.projects(), include_external_members=True)[1]], ["App"])

    def test_transitive_solutions_and_duplicate_names(self):
        self.configure()
        other = Solution.create(self.root / "Other", "External")
        header = other.add_project("Headers", "Headers", T.INTERFACE_LIBRARY)
        header.add_file("include/api.hpp", content="#pragma once", auto_update=False)
        self.lib.settings.link_solution(other.root / ".cppbuild", T.INTERFACE_LIBRARY)
        roots, nodes = resolve(self.solution.projects(), include_external_members=True)
        self.assertEqual(len(nodes), 4)
        linked = workspace.plan(self.solution).linked_solutions()
        folders = [folder for _, _, _, folder in linked]
        self.assertEqual(len(set(folders)), 2)
        self.assertTrue(all(f.startswith("Linked Projects/External (") for f in folders))
        self.assertEqual(sorted(binary for _, _, binary, _ in linked), ["External", "External_2"])

    def test_template_preserves_layout(self):
        self.configure()
        template = TemplateTools.create_solution_template(self.solution, self.root / "Template")
        restored = Solution.create(self.root / "Restored", "Restored", template=template)
        self.assertEqual(restored.settings.get().solution_folders, self.solution.settings.get().solution_folders)

    def test_unreferenced_multiple_kinds_and_move(self):
        self.configure()
        library = self.external.add_project("Multi", "Multi", T.SHARED_LIBRARY)
        nodes = resolve(self.solution.projects(), include_external_members=True)[1]
        self.assertEqual({n.settings.project_type for n in nodes if n.project.name == "Multi"},
                         {T.SHARED_LIBRARY})
        self.assertEqual(len(library.settings.get().types), 3)
        self.solution.move_project("App", "Moved/App", auto_update=False)
        self.assertEqual(self.solution.settings.get().solution_folders.project_folders, {"App": "Apps/Tools"})

    def test_remove_clears_placement(self):
        self.configure()
        self.solution.add_project("Tool", "Tool", T.EXECUTABLE)
        values = self.solution.settings.get()
        values.solution_folders.project_folders["Tool"] = "Tools"
        self.solution.settings.save(values)
        self.solution.remove_project("Tool")
        self.assertNotIn("Tool", Solution.open(self.solution.root / ".cppbuild").settings.get().solution_folders.project_folders)

    @unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2022") == "1", "Real VS2022 required")
    def test_real_hierarchy_build_and_flat_restore(self):
        self.configure()
        tool = self.solution.add_project("Tool", "Tool", T.EXECUTABLE)
        tool.add_file("src/main.cpp", content="int main() { return 0; }", auto_update=False)
        values = self.solution.settings.get()
        values.solution_folders.projects = "My.Projects (local)"
        values.solution_folders.project_folders["App"] = "Apps/Tools_\u65e5\u672c\u8a9e"
        self.solution.settings.save(values)
        for configuration in ["Debug", "Release"]:
            self.solution.set_build_settings(SolutionBuildSettings(configuration=configuration, run_projects=["App", "Tool"],
                external_build_settings={str(self.external.root / ".cppbuild"): SolutionBuildSettings(configuration=configuration)}))
            report = self.solution.build()
            self.assertTrue(report.success, "\n".join(p.output for p in report.processes))
            report = self.solution.run()
            self.assertTrue(report.success, str(report))
            sln = next((self.solution.root / ".cppbuild/output").rglob("Main.sln"))
            text = sln.read_text(encoding="utf-8-sig")
            entries = re.findall(r'Project\("\{[^}]+\}"\) = "([^"]+)", "([^"]+)", "\{([^}]+)\}"', text)
            folders = {name: guid for name, path, guid in entries if not path.endswith(".vcxproj")}
            parents = dict(re.findall(r'\{([^}]+)\} = \{([^}]+)\}', text))
            self.assertTrue(any(path.endswith("Main_App.vcxproj") for _, path, _ in entries))
            self.assertTrue(any(path.endswith("External_Unused.vcxproj") for _, path, _ in entries))
            self.assertEqual(parents[folders["Apps"]], folders["My.Projects (local)"])
            self.assertEqual(parents[folders["Tools_\u65e5\u672c\u8a9e"]], folders["Apps"])
            self.assertEqual(parents[folders["External"]], folders["Linked Projects"])
            for name, path, guid in entries:
                if path.endswith("Main_App.vcxproj"):
                    self.assertEqual(parents[guid], folders["Tools_\u65e5\u672c\u8a9e"])
                if path.endswith("Main_Tool.vcxproj"):
                    self.assertEqual(parents[guid], folders["My.Projects (local)"])
                if path.endswith("External_Unused.vcxproj"):
                    self.assertEqual(parents[guid], folders["External"])
                    self.assertNotRegex(text, re.escape("{" + guid + "}") + r"[^\n]*Build\.0")
            from cppbuild import tooling
            default = tooling.process(self.solution._build_settings,
                ["cmake", "--build", sln.parent, "--config", configuration], self.solution.root)
            self.assertTrue(default.success, default.output)
            self.assertFalse(list(sln.parent.rglob("Unused.exe")))
        values = self.solution.settings.get()
        values.solution_folders = None
        self.solution.settings.save(values)
        self.assertTrue(self.solution.update().success)
        text = sln.read_text(encoding="utf-8-sig")
        self.assertNotIn("NestedProjects", text)
        self.assertNotIn("Unused.vcxproj", text)

    @unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2022") == "1", "Real VS2022 required")
    def test_real_googletest_of_linked_solution_in_external_folder(self):
        # Only the linked Solution has a TEST Project: its GoogleTest still goes in this Solution's folder.
        tests = self.external.add_project("Tests", "Tests", T.TEST)
        tests.add_file("src/test.cpp", content="#include <gtest/gtest.h>\nTEST(A, B) { EXPECT_TRUE(true); }\n", auto_update=False)
        self.configure()
        values = self.solution.settings.get()
        values.solution_folders.external = "Third Party"
        self.solution.settings.save(values)
        report = self.solution.update()
        self.assertTrue(report.success, str(report))
        sln = next((self.solution.root / ".cppbuild/output").rglob("Main.sln"))
        text = sln.read_text(encoding="utf-8-sig")
        entries = re.findall(r'Project\("\{[^}]+\}"\) = "([^"]+)", "([^"]+)", "\{([^}]+)\}"', text)
        folders = {name: guid for name, path, guid in entries if not path.endswith(".vcxproj")}
        parents = dict(re.findall(r'\{([^}]+)\} = \{([^}]+)\}', text))
        self.assertNotIn(folders["Third Party"], parents)
        placed = {Path(path).stem: parents.get(guid) for _, path, guid in entries if path.endswith(".vcxproj")}
        self.assertEqual(parents[folders["GoogleTest"]], folders["Third Party"])
        self.assertEqual(parents[folders["CMake"]], folders["Third Party"])
        self.assertEqual(placed["gtest"], folders["GoogleTest"])
        self.assertEqual(placed["gtest_main"], folders["GoogleTest"])
        self.assertEqual(placed["ALL_BUILD"], folders["CMake"])
        self.assertEqual(placed["ZERO_CHECK"], folders["CMake"])
        self.assertNotIn("CMakePredefinedTargets", folders)
        self.assertEqual(placed["External_Tests"], folders["External"])
        self.assertEqual(placed["Main_App"], folders["Tools"])

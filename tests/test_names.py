"""Names that must stay unique in one CMake project: targets, ALIASes, runtime files and test names."""
from pathlib import Path
import tempfile
import unittest

from cppbuild import ProjectType as T, SettingsError, Solution, SolutionFolderSettings
from cppbuild import cmake_files, testing, workspace


class NameTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cppbuild-names-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def add(self, solution, name, kind, source="int f() { return 0; }\n"):
        project = solution.add_project(name, name, kind)
        project.add_file("src/main.cpp", content=source, auto_update=False)
        return project

    def check(self, solution):
        workspace.plan(solution).check(lambda node: node.settings.configuration)

    def test_type_suffixed_targets_and_aliases(self):
        solution = Solution.create(self.root / "S", "S")
        self.add(solution, "Lib", T.SHARED_LIBRARY)
        self.check(solution)
        # S_Lib_static is Lib's static target (switchable without CppBuild) and this Project's own target.
        self.add(solution, "Lib_static", T.STATIC_LIBRARY)
        with self.assertRaisesRegex(SettingsError, "CMake target S_Lib_static"):
            self.check(solution)

    def test_reserved_names(self):
        solution = Solution.create(self.root / "gtest", "gtest")
        self.add(solution, "main", T.EXECUTABLE, "int main() { return 0; }\n")
        with self.assertRaisesRegex(SettingsError, "reserved CMake target gtest_main"):
            self.check(solution)

    def test_program_and_shared_library_of_the_same_name(self):
        main = Solution.create(self.root / "Main", "Main")
        other = Solution.create(self.root / "Other", "Other")
        self.add(other, "App", T.SHARED_LIBRARY)
        tool = self.add(other, "Tool", T.STATIC_LIBRARY)
        values = other.settings.get()
        values.main_project = "Tool"
        other.settings.save(values)
        app = self.add(main, "App", T.EXECUTABLE, "int main() { return 0; }\n")
        app.settings.link_solution(other.root / ".cppbuild", T.STATIC_LIBRARY)
        # Other::App is only listed for the IDE, with its own runtime directory.
        values = main.settings.get()
        values.solution_folders = SolutionFolderSettings()
        main.settings.save(values)
        self.check(main)
        # Once Tool (used by Main::App) links it, Other::App is built next to Main::App.
        tool.settings.link_project(other.get_project("App"), T.SHARED_LIBRARY)
        with self.assertRaisesRegex(SettingsError, "program App and a shared library"):
            self.check(main)

    def test_test_names_are_prefixed_per_project(self):
        solution = Solution.create(self.root / "S", "S")
        tests = self.add(solution, "Tests", T.TEST, "#include <gtest/gtest.h>\nTEST(A, B) {}\n")
        text = workspace.plan(solution).documents()[tests.root / "CMakeLists.txt"]
        self.assertIn('TEST_PREFIX "Tests." PROPERTIES LABELS S_Tests', text)
        junit = self.root / "results.xml"
        junit.write_text('<testsuite><testcase name="Tests.A.B"/><testcase name="Other"/></testsuite>')
        cases, diagnostics = testing.read_results(junit, cmake_files.test_prefix(tests))
        self.assertEqual([c.name for c in cases], ["A.B", "Other"])
        self.assertEqual(diagnostics, ())


if __name__ == "__main__":
    unittest.main()

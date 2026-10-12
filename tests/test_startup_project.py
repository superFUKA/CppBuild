"""The program Visual Studio starts with F5 (VS_STARTUP_PROJECT), instead of ALL_BUILD."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from cppbuild import CMakeSettings, ProjectBuildSettings, ProjectType as T, Solution, SolutionBuildSettings
from cppbuild import generators, workspace


class _Demo:
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cppbuild-startup-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.solution = Solution.create(self.root / "Demo", "Demo")
        self.lib = self.solution.add_project("Lib", "Lib", T.STATIC_LIBRARY)
        self.lib.add_file("src/lib.cpp", content="int value() { return 42; }\n", auto_update=False)

    def entry(self):
        return workspace.plan(self.solution).documents()[self.solution.root / "CMakeLists.txt"]


class StartupProjectTests(_Demo, unittest.TestCase):
    def candidates(self):
        found = re.search(r"foreach\(_cppbuild_startup IN ITEMS ([^)]*)\)", self.entry())
        return found.group(1).split() if found else []

    def test_order_main_project_executables_then_tests(self):
        self.assertNotIn("VS_STARTUP_PROJECT", self.entry())  # nothing to start
        self.solution.add_project("Tests", "Tests", T.TEST)
        self.solution.add_project("Zeta", "Zeta", T.EXECUTABLE)
        self.solution.add_project("Alpha", "Alpha", T.EXECUTABLE)
        self.assertEqual(self.candidates(), ["Demo_Alpha", "Demo_Zeta", "Demo_Tests"])
        values = self.solution.settings.get()
        self.assertEqual(values.main_project, "Lib")  # a library main_project is skipped
        values.main_project = "Zeta"
        self.solution.settings.save(values)
        self.assertEqual(self.candidates(), ["Demo_Zeta", "Demo_Alpha", "Demo_Tests"])
        values.main_project = "Tests"
        self.solution.settings.save(values)
        # With BUILD_TESTING=OFF the TEST target is missing and the next candidate is started.
        self.assertEqual(self.candidates(), ["Demo_Tests", "Demo_Alpha", "Demo_Zeta"])
        entry = self.entry()
        # Only for the Solution's own .sln; a Solution linked by another one adds no such property.
        self.assertLess(entry.rindex("add_subdirectory("), entry.index("if(CPPBUILD_WHOLE_SOLUTION)\n  foreach(_cppbuild_startup"))
        self.assertIn("    if(TARGET ${_cppbuild_startup})\n      set_property(DIRECTORY PROPERTY VS_STARTUP_PROJECT ${_cppbuild_startup})", entry)

    def test_linked_solution_programs_are_not_candidates(self):
        other = Solution.create(self.root / "Other", "Other")
        other.add_project("Tool", "Tool", T.EXECUTABLE)
        other.add_project("Base", "Base", T.STATIC_LIBRARY)
        values = other.settings.get()
        values.main_project = "Base"
        other.settings.save(values)
        self.lib.settings.link_solution(other.root / ".cppbuild", T.STATIC_LIBRARY)
        self.assertNotIn("VS_STARTUP_PROJECT", self.entry())


def startup_of(build):
    """The project Visual Studio starts: the first project of a .sln, DefaultStartup in a .slnx."""
    sln = next(build.glob("*.sln"), None)
    if sln is not None:
        found = re.search(r'Project\("\{[^}]+\}"\) = "([^"]+)"', sln.read_text(encoding="utf-8-sig"))
        return found.group(1)
    text = next(build.glob("*.slnx")).read_text(encoding="utf-8-sig")
    found = re.search(r'<Project Path="([^"]+)"[^>]*DefaultStartup="true"', text)
    return Path(found.group(1)).stem if found else None


class _RealStartup(_Demo):
    generator = None

    def test_startup_follows_targets(self):
        app = self.solution.add_project("App", "App", T.EXECUTABLE)
        app.add_file("src/main.cpp", content="int main() { return 0; }\n", auto_update=False)
        tests = self.solution.add_project("Tests", "Tests", T.TEST)
        tests.add_file("src/test.cpp", content="#include <gtest/gtest.h>\nTEST(A, B) { EXPECT_TRUE(true); }\n", auto_update=False)
        archive = os.environ.get("CPPBUILD_TEST_GTEST_ARCHIVE")
        tests.set_build_settings(ProjectBuildSettings(googletest_archive=archive))
        values = self.solution.settings.get()
        values.main_project = "Tests"
        self.solution.settings.save(values)
        settings = SolutionBuildSettings(cmake=CMakeSettings(generator=self.generator))
        self.solution.set_build_settings(settings)
        report = app.update()  # the Solution's one build tree
        self.assertTrue(report.success, str(report))
        self.assertEqual(startup_of(report.build_directory), "Demo_Tests")
        # Plain CMake without tests: the TEST target is missing, so the next program is started.
        toolchain = generators.resolve(settings)
        build = self.root / "no-tests"
        result = subprocess.run([str(c) for c in (*toolchain.configure(self.solution.root, build), "-DBUILD_TESTING=OFF")],
                                env=toolchain.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        self.assertEqual(result.returncode, 0, result.stdout[-4000:])
        self.assertEqual(startup_of(build), "Demo_App")


@unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2022") == "1", "Real VS2022 required")
class VS2022StartupTests(_RealStartup, unittest.TestCase):
    generator = "Visual Studio 17 2022"


@unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2026") == "1", "Real VS2026 required")
class VS2026StartupTests(_RealStartup, unittest.TestCase):
    generator = "Visual Studio 18 2026"

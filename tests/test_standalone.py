"""Generated CMake files: stable, relative, and usable by plain CMake without CppBuild."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from cppbuild import (CMakeSettings, ProjectBuildSettings, ProjectType as T, Solution, SolutionBuildSettings,
                      SolutionFolderSettings)
from cppbuild import generators, workspace


def workspace_pair(root):
    """ECS links STL from its dependency directory; STL's Util links back to ECS's Core."""
    ecs = Solution.create(root / "ECS", "ECS")
    data = ecs.settings.get()
    data.dependency_directories = ["deps"]
    ecs.settings.save(data)
    stl = Solution.create(root / "ECS/deps/STL", "STL")
    containers = stl.add_project("Containers", "Containers", T.STATIC_LIBRARY)
    containers.add_file("include/containers.hpp", content="#pragma once\nint containers();\n", auto_update=False)
    containers.add_file("src/containers.cpp", content='#include "containers.hpp"\nint containers() { return 30; }\n', auto_update=False)
    util = stl.add_project("Util", "Util", T.STATIC_LIBRARY)
    util.add_file("src/util.cpp", content="int core(); int util() { return core() + 2; }\n", auto_update=False)
    tests = stl.add_project("Tests", "Tests", T.TEST)
    tests.add_file("src/test.cpp", content='#include <gtest/gtest.h>\n#include "containers.hpp"\nTEST(STL, Value) { EXPECT_EQ(containers(), 30); }\n', auto_update=False)
    tests.settings.link_project(containers, T.STATIC_LIBRARY)
    core = ecs.add_project("Core", "Core", T.STATIC_LIBRARY)
    core.add_file("src/core.cpp", content='#include "containers.hpp"\nint core() { return containers() + 10; }\n', auto_update=False)
    core.settings.link_solution(stl.root / ".cppbuild", T.STATIC_LIBRARY)
    # Back reference across Solutions: the request for Core comes from ECS and from STL.
    util.settings.link_solution(ecs.root / ".cppbuild", T.STATIC_LIBRARY)
    app = ecs.add_project("App", "App", T.EXECUTABLE)
    app.add_file("src/main.cpp", content="#include <cstdio>\nint util();\nint main() { std::printf(\"%d\", util()); return util() == 42 ? 0 : 1; }\n", auto_update=False)
    app.settings.link_project(core, T.STATIC_LIBRARY)
    main = stl.settings.get()
    main.main_project = "Util"
    stl.settings.save(main)
    app.settings.link_solution(stl.root / ".cppbuild", T.STATIC_LIBRARY)
    return ecs, stl


class GeneratedFileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cppbuild-standalone-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ecs, self.stl = workspace_pair(self.root)

    def test_requests_back_references_and_independence(self):
        plan = workspace.plan(self.ecs)
        documents = plan.documents()
        self.assertEqual(workspace.plan(self.ecs).documents(), documents)
        top = documents[self.ecs.root / "CppBuildTopLevel.cmake"]
        self.assertIn("# STL::Containers", top)
        self.assertIn("# STL::Util", top)
        self.assertIn("STATIC)  # ECS::Core", top)
        self.assertIn('set(CPPBUILD_LINKED_OUTPUT "_linked/STL")\nadd_subdirectory("deps/STL" "${CMAKE_BINARY_DIR}/${CPPBUILD_LINKED_OUTPUT}")', top)
        self.assertNotIn("Tests", top)
        # The linked Solution's own files are written too, with its own top-level view.
        self.assertIn(self.stl.root / "CppBuildTopLevel.cmake", documents)
        self.assertIn(self.stl.root / "Tests/CMakeLists.txt", documents)
        stl_top = documents[self.stl.root / "CppBuildTopLevel.cmake"]
        self.assertIn('set(CPPBUILD_LINKED_OUTPUT "_linked/ECS")\nadd_subdirectory("../.."', stl_top)
        stl_entry = documents[self.stl.root / "CMakeLists.txt"]
        self.assertIn("if(BUILD_TESTING AND (CPPBUILD_WHOLE_SOLUTION OR DEFINED", stl_entry)
        self.assertIn("set(CPPBUILD_LINKED ON)", documents[self.ecs.root / "CppBuildTopLevel.cmake"])
        self.assertNotIn("::static", "".join(documents.values()))
        tests = documents[self.stl.root / "Tests/CMakeLists.txt"]
        self.assertIn("set(gtest_force_shared_crt ON)", tests)
        # Only a local ZIP path is converted; TO_CMAKE_PATH would turn https:// into https:/.
        self.assertIn('if(NOT _cppbuild_googletest_url MATCHES "^[A-Za-z][A-Za-z0-9+.-]*://")', tests)
        self.assertNotIn("FORCE", tests)
        self.assertIn("target_link_libraries(STL_Tests PRIVATE GTest::gtest_main", tests)
        self.assertIn('option(BUILD_TESTING', documents[self.ecs.root / "CppBuildTopLevel.cmake"])
        text = "".join(documents.values()).replace("\\", "/")
        self.assertNotIn(str(self.root).replace("\\", "/"), text)
        self.ecs.set_build_settings(SolutionBuildSettings(cpp_standard=17, configuration="Release"))
        self.assertEqual(workspace.plan(self.ecs).documents(), documents)
        values = self.ecs.settings.get()
        values.solution_folders = SolutionFolderSettings()
        self.ecs.settings.save(values)
        displayed = workspace.plan(self.ecs).documents()[self.ecs.root / "CppBuildTopLevel.cmake"]
        self.assertIn("DISPLAY)  # STL::Tests", displayed)
        self.assertIn('set(CMAKE_FOLDER "LinkedProjects/STL")', displayed)


class StandaloneScenario:
    generator = None

    def test_cppbuild_then_plain_cmake_in_another_place(self):
        archive = os.environ.get("CPPBUILD_TEST_GTEST_ARCHIVE")
        work = Path(__file__).resolve().parents[1] / ".test-work"
        work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="standalone-", dir=work) as temporary:
            root = Path(temporary)
            ecs, stl = workspace_pair(root / "ws")
            settings = SolutionBuildSettings(cmake=CMakeSettings(generator=self.generator))
            ecs.set_build_settings(settings)
            stl.get_project("Tests").set_build_settings(ProjectBuildSettings(googletest_archive=archive))
            report = ecs.get_project("App").run()
            self.assertTrue(report.success, "\n".join(p.output[-3000:] for p in report.processes))
            self.assertEqual(report.processes[-1].output.strip(), "42")
            copy = root / "copy"
            shutil.copytree(root / "ws", copy, ignore=lambda directory, names: [n for n in names if n == ".cppbuild"])
            toolchain = generators.resolve(settings)
            extra = [f"-DCPPBUILD_GOOGLETEST_URL={archive}"] if archive else []

            def run(command):
                result = subprocess.run([str(c) for c in command], env=toolchain.env, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, errors="replace")
                self.assertEqual(result.returncode, 0, f"{command}\n{result.stdout[-4000:]}")
                return result.stdout

            for source, build, configuration, tests in ((copy / "ECS", root / "plain-ecs", "Release", False),
                                                        (copy / "ECS/deps/STL", root / "plain-stl", "Debug", True)):
                run([*toolchain.configure(source, build), *extra])
                run(["cmake", "--build", build, "--config", configuration])
                output = run(["ctest", "--test-dir", build, "-C", configuration, "--no-tests=ignore"])
                self.assertEqual("STL.Value" in output, tests)
            executable = root / "plain-ecs/bin/Release" / ("App.exe" if os.name == "nt" else "App")
            self.assertEqual(run([executable]).strip(), "42")
            # Without tests nothing is downloaded, as with CTest's BUILD_TESTING switch.
            run([*toolchain.configure(copy / "ECS/deps/STL", root / "no-tests"), "-DBUILD_TESTING=OFF",
                 "-DCPPBUILD_GOOGLETEST_URL=missing.zip"])
            run(["cmake", "--build", root / "no-tests", "--config", "Debug"])
            self.assertFalse((root / "no-tests/_deps").exists())
            # CMake's standard BUILD_SHARED_LIBS makes the libraries shared.
            run([*toolchain.configure(copy / "ECS/deps/STL", root / "shared"), "-DBUILD_TESTING=OFF", "-DBUILD_SHARED_LIBS=ON"])
            # Target names follow the type; STL::Containers is the alias of the selected one.
            run(["cmake", "--build", root / "shared", "--config", "Debug", "--target", "STL_Containers_shared"])
            self.assertTrue([p for p in (root / "shared").rglob("*Containers*")
                             if p.suffix in {".dll", ".so", ".dylib"}])
            # A project that does not use CppBuild adds the whole Solution and its linked Solutions.
            foreign = root / "foreign"
            foreign.mkdir()
            (foreign / "CMakeLists.txt").write_text(
                'cmake_minimum_required(VERSION 3.24)\nproject(Foreign LANGUAGES CXX)\nadd_subdirectory("../copy/ECS" ecs)\n')
            run(toolchain.configure(foreign, root / "plain-foreign"))
            run(["cmake", "--build", root / "plain-foreign", "--config", "Debug", "--target", "ECS_App"])
            built = [p for p in (root / "plain-foreign").rglob("App*") if p.name in {"App.exe", "App"} and p.is_file()]
            self.assertEqual(len(built), 1)
            self.assertEqual(run(built).strip(), "42")


@unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2022") == "1", "Real VS2022 required")
class StandaloneVS2022Tests(StandaloneScenario, unittest.TestCase):
    generator = "Visual Studio 17 2022"


@unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2026") == "1", "Real VS2026 required")
class StandaloneVS2026Tests(StandaloneScenario, unittest.TestCase):
    generator = "Visual Studio 18 2026"


@unittest.skipUnless(os.environ.get("CPPBUILD_TEST_NINJA") == "1", "Real Ninja required")
class StandaloneNinjaTests(StandaloneScenario, unittest.TestCase):
    generator = "Ninja Multi-Config"


if __name__ == "__main__":
    unittest.main()

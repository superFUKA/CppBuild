"""Generator/compiler selection without running CMake."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cppbuild import (CMakeSettings, ProjectBuildSettings, ProjectType as T, SettingsError, Solution,
                      SolutionBuildSettings)
from cppbuild import engine, generators, storage


class GeneratorSettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cppbuild-generators-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.solution = Solution.create(self.root / "Demo", "Demo")
        self.app = self.solution.add_project("App", "App", T.EXECUTABLE)

    def test_invalid_combinations_are_rejected_before_tools(self):
        cases = [CMakeSettings(generator="Unix Makefiles"),
                 CMakeSettings(generator="Visual Studio 17 2022", cxx_compiler="clang++"),
                 CMakeSettings(generator="Ninja Multi-Config", toolchain_file="relative.cmake"),
                 CMakeSettings(generator="Ninja Multi-Config", cxx_compiler="g++", toolchain_file=str(self.root / "t.cmake")),
                 CMakeSettings(generator="Ninja Multi-Config", cxx_compiler="bin/g++"),
                 CMakeSettings(generator="")]
        for cmake in cases:
            with self.subTest(cmake=cmake), patch("cppbuild.engine.process") as process:
                with self.assertRaises(SettingsError):
                    self.solution.set_build_settings(SolutionBuildSettings(cmake=cmake))
                with self.assertRaises(SettingsError):
                    self.app.set_build_settings(ProjectBuildSettings(cmake=cmake))
                process.assert_not_called()
        with self.assertRaises(SettingsError) as caught:
            self.solution.set_build_settings(SolutionBuildSettings(cmake=CMakeSettings(generator="Xcode")))
        self.assertIn("CMake may still provide it", str(caught.exception))
        with self.assertRaises(SettingsError):
            self.solution.set_build_settings(SolutionBuildSettings(architecture="x86"))

    def test_project_inherits_whole_cmake_settings(self):
        cmake = CMakeSettings(generator="Ninja Multi-Config", cxx_compiler="clang++")
        self.solution.set_build_settings(SolutionBuildSettings(cmake=cmake, architecture="x64"))
        resolved = self.app._resolved_build_settings()
        self.assertEqual(resolved.cmake, cmake)
        self.assertEqual(resolved.architecture, "x64")
        own = CMakeSettings(generator="Visual Studio 17 2022")
        self.app.set_build_settings(ProjectBuildSettings(cmake=own))
        self.assertEqual(self.app._resolved_build_settings().cmake, own)
        # Build settings stay out of management JSON.
        self.assertNotIn("Ninja", self.solution.settings.path.read_text(encoding="utf-8"))

    def test_default_generator_is_fixed_per_host(self):
        expected = "Visual Studio 17 2022" if os.name == "nt" else "Ninja Multi-Config"
        self.assertEqual(generators.default_generator(), expected)

    @unittest.skipUnless(os.name == "nt", "Visual Studio contexts are Windows-only")
    def test_visual_studio_environments_have_separate_output_leaves(self):
        settings = self.app._resolved_build_settings()
        toolchain = generators.resolve(settings)
        self.assertEqual(toolchain.architecture, generators.host_architecture())
        original = engine.tree(self.solution, settings)[0]
        self.assertRegex(original.name, r"^[0-9a-f]{10}$")
        self.app.set_build_settings(ProjectBuildSettings(architecture="Win32"))
        self.assertNotEqual(engine.tree(self.solution, self.app._resolved_build_settings())[0], original)
        self.app.set_build_settings(ProjectBuildSettings(cmake=CMakeSettings(generator="Visual Studio 18 2026", toolset="v143")))
        changed = engine.tree(self.solution, self.app._resolved_build_settings())[0]
        self.assertNotEqual(changed, original)
        self.assertRegex(changed.name, r"^[0-9a-f]{10}$")
        command = generators.resolve(self.app._resolved_build_settings()).configure("s", "b")
        self.assertEqual(command[command.index("-T") + 1], "v143")

    def test_tree_of_another_environment_is_refused(self):
        self.app.add_file("src/main.cpp", content="int main() {}", auto_update=False)
        build, toolchain = engine.tree(self.solution, self.app._resolved_build_settings())
        from cppbuild import output_paths
        output_paths.claim(self.solution, self.solution._build_settings.intermediate_directory, toolchain.context)
        storage.atomic_write(build / "cppbuild-owner.json", storage.encoded(
            {"solution_root": str(self.solution.root), "environment": {"generator": "other"}}))
        with patch("cppbuild.engine.process") as process:
            with self.assertRaises(SettingsError):
                self.app.update()
        process.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Visual Studio contexts are Windows-only")
    def test_linked_projects_require_one_generation_environment(self):
        library = self.solution.add_project("Lib", "Lib", T.STATIC_LIBRARY)
        self.app.settings.link_project(library, T.STATIC_LIBRARY)
        library.set_build_settings(ProjectBuildSettings(cmake=CMakeSettings(generator="Visual Studio 18 2026")))
        with patch("cppbuild.engine.process") as process:
            with self.assertRaises(SettingsError):
                self.app.build()
            with self.assertRaises(SettingsError):
                self.solution.build()
        process.assert_not_called()
        # Explicit x64 equals the host default on an x64 host.
        library.set_build_settings(ProjectBuildSettings(architecture=generators.host_architecture()))
        from cppbuild.graph import compatible
        self.assertTrue(compatible(library._resolved_build_settings(), self.app._resolved_build_settings()))

    def test_compiler_record_and_architecture_verification(self):
        build = self.root / "tree"
        files = build / "CMakeFiles/4.2.3"
        files.mkdir(parents=True)
        (files / "CMakeCXXCompiler.cmake").write_text(
            'set(CMAKE_CXX_COMPILER "/usr/bin/g++")\nset(CMAKE_CXX_COMPILER_ID "GNU")\n'
            'set(CMAKE_CXX_COMPILER_VERSION "13.2.0")\nset(CMAKE_CXX_COMPILER_ARCHITECTURE_ID "")\n'
            'set(CMAKE_CXX_SIZEOF_DATA_PTR "8")\n')
        (files / "CMakeSystem.cmake").write_text('set(CMAKE_SYSTEM_PROCESSOR "aarch64")\n')
        info = generators.compiler(build)
        self.assertEqual((info.id, info.version, info.architecture), ("GNU", "13.2.0", "ARM64"))
        toolchain = generators.Toolchain("Ninja Multi-Config", "x64")
        self.assertIn("targets ARM64", generators.verify(toolchain, info))
        self.assertIsNone(generators.verify(generators.Toolchain("Ninja Multi-Config", None), info))
        self.assertIsNone(generators.verify(generators.Toolchain("Ninja Multi-Config", "ARM64"), info))
        self.assertEqual(generators._architecture("X86", "4"), "Win32")
        self.assertEqual(generators._architecture("x86_64", "4"), "Win32")
        self.assertEqual(generators._architecture("AMD64", "8"), "x64")

    def test_ninja_command_line(self):
        toolchain = generators.Toolchain("Ninja Multi-Config", None, None, "/opt/cc", "/opt/c++", None,
                                         explicit_compiler=True, make_program="/opt/ninja")
        command = toolchain.configure("src", "out")
        self.assertIn("-DCMAKE_CXX_COMPILER:FILEPATH=/opt/c++", command)
        self.assertIn("-DCMAKE_C_COMPILER:FILEPATH=/opt/cc", command)
        self.assertIn("-DCMAKE_MAKE_PROGRAM:FILEPATH=/opt/ninja", command)
        self.assertNotIn("-A", command)
        self.assertTrue(toolchain.context.startswith("ninja-mc-"))
        other = generators.Toolchain("Ninja Multi-Config", None, None, "/opt/cc", "/opt/clang++", None)
        self.assertNotEqual(toolchain.context, other.context)

    def test_runnable_matrix(self):
        with patch("cppbuild.generators.host_architecture", return_value="x64"):
            self.assertTrue(generators.runnable("x64"))
            self.assertTrue(generators.runnable(None))
            self.assertFalse(generators.runnable("ARM64"))

    def test_generated_files_are_relative_and_independent_of_build_settings(self):
        from cppbuild import workspace
        self.app.add_file("src/main.cpp", content="int main() {}", auto_update=False)
        library = self.solution.add_project("Lib", "Lib", T.SHARED_LIBRARY)
        library.add_file("src/lib.cpp", content="int f() { return 1; }", auto_update=False)
        self.app.settings.link_project(library, T.SHARED_LIBRARY)
        first = workspace.plan(self.solution).documents()
        self.solution.set_build_settings(SolutionBuildSettings(
            configuration="Release", cpp_standard=17, architecture="Win32",
            cmake=CMakeSettings(generator="Ninja Multi-Config"), project_types={library.settings.get().guid: T.STATIC_LIBRARY}))
        library.set_build_settings(ProjectBuildSettings(project_type=T.INTERFACE_LIBRARY, artifact_directory=str(self.root / "out")))
        self.assertEqual(workspace.plan(self.solution).documents(), first)
        text = "".join(first.values())
        self.assertNotIn(str(self.root).replace("\\", "/"), text.replace("\\", "/"))
        self.assertNotIn(str(self.root), text)
        lib = first[library.root / "CMakeLists.txt"]
        self.assertIn("add_library(Demo_Lib SHARED ${_cppbuild_sources})", lib)
        self.assertIn("add_library(Demo_Lib_static STATIC ${_cppbuild_sources})", lib)
        self.assertIn("add_library(Demo::Lib ALIAS", lib)
        app = first[self.app.root / "CMakeLists.txt"]
        self.assertIn("target_link_libraries(Demo_App PRIVATE Demo::Lib_shared)", app)
        self.assertIn('OUTPUT_NAME "App"', app)
        entry = first[self.solution.root / "CMakeLists.txt"]
        self.assertIn('add_subdirectory("App")', entry)
        self.assertIn("list(APPEND CPPBUILD_REQUESTED SHARED)", entry)
        self.assertTrue(all(t.startswith("# Generated by CppBuild") for t in first.values()))


class RenameTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cppbuild-rename-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_existing_destination_is_never_replaced(self):
        source, destination = self.root / "a.txt", self.root / "b.txt"
        source.write_text("new")
        destination.write_text("keep")
        with self.assertRaises(FileExistsError):
            storage.rename_no_replace(source, destination)
        self.assertEqual(destination.read_text(), "keep")
        self.assertTrue(source.exists())
        destination.unlink()
        storage.rename_no_replace(source, destination)
        self.assertEqual(destination.read_text(), "new")
        self.assertFalse(source.exists())

    def test_posix_file_path_uses_link_then_unlink(self):
        source, destination = self.root / "a.txt", self.root / "b.txt"
        source.write_text("new")
        destination.write_text("keep")
        with patch.object(storage.os, "name", "posix"):
            with self.assertRaises(FileExistsError):
                storage.rename_no_replace(source, destination)
            self.assertEqual(destination.read_text(), "keep")
            destination.unlink()
            storage.rename_no_replace(source, destination)
        self.assertEqual(destination.read_text(), "new")
        self.assertFalse(source.exists())

    def test_posix_directory_claims_destination_first(self):
        source, destination = self.root / "src", self.root / "dst"
        source.mkdir()
        destination.mkdir()
        with patch.object(storage.os, "name", "posix"):
            with self.assertRaises(FileExistsError):
                storage.rename_no_replace(source, destination)
        self.assertTrue(source.is_dir())
        self.assertTrue(destination.is_dir())


if __name__ == "__main__":
    unittest.main()

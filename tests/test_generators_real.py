"""Real builds with selectable generators and compilers (opt in).

CPPBUILD_TEST_NINJA=1 uses Ninja Multi-Config (on Windows: MSVC prepared by
vcvarsall, Ninja from PATH or Visual Studio). CPPBUILD_TEST_VS2026=1 uses the
Visual Studio 18 2026 generator (.slnx). No mocks; an opted-in run fails if the
tools are unavailable.
"""
import os
from pathlib import Path
import tempfile
import unittest

from cppbuild import (CMakeSettings, Environment, EnvironmentOptions, ProjectBuildSettings, ProjectType as T,
                      SettingsError, Solution, SolutionBuildSettings)
from cppbuild import engine, generators

EXPORT = ("#pragma once\n#ifdef _WIN32\n#ifdef SHARED_EXPORTS\n#define API __declspec(dllexport)\n#else\n"
          "#define API __declspec(dllimport)\n#endif\n#else\n#define API\n#endif\nAPI int shared_value();\n")


class GeneratorScenario:
    generator = None

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cppbuild-gen-")
        self.addCleanup(temporary.cleanup)
        self.solution = Solution.create(Path(temporary.name) / "日本語 space", "Demo")
        self.cmake = CMakeSettings(generator=self.generator)
        self.settings(configuration="Debug")

    def settings(self, **values):
        self.solution.set_build_settings(SolutionBuildSettings(cmake=self.cmake, **values))

    def assertSuccess(self, report):
        processes = getattr(report, "processes", None) or [report.process]
        self.assertTrue(report.success, "\n".join(f"{p.command}\n{p.output[-4000:]}" for p in processes))
        return report

    def projects(self):
        static = self.solution.add_project("Static", "Static", T.STATIC_LIBRARY)
        static.add_file("include/static.hpp", content="#pragma once\nint static_value();\n", auto_update=False)
        static.add_file("src/static.cpp", content="int static_value() { return 20; }\n", auto_update=False)
        shared = self.solution.add_project("Shared", "Shared", T.SHARED_LIBRARY)
        shared.add_file("include/shared.hpp", content=EXPORT, auto_update=False)
        shared.add_file("src/shared.cpp", content='#include "shared.hpp"\nint shared_value() { return 20; }\n', auto_update=False)
        data = shared.settings.get()
        data.types[T.SHARED_LIBRARY].compile_definitions = ["SHARED_EXPORTS"]
        shared.settings.save(data)
        header = self.solution.add_project("Header", "Header", T.INTERFACE_LIBRARY)
        header.add_file("include/header.hpp", content="#pragma once\ninline int header_value() { return 2; }\n", auto_update=False)
        app = self.solution.add_project("App", "App", T.EXECUTABLE)
        app.add_file("src/main.cpp", content=(
            '#include <cstdio>\n#include "static.hpp"\n#include "shared.hpp"\n#include "header.hpp"\n'
            'int main(int argc, char**) { int v = static_value() + shared_value() + header_value();\n'
            ' std::printf("value=%d", v); return argc == 2 ? 7 : (v == 42 ? 0 : 1); }\n'), auto_update=False)
        for project, kind in ((static, T.STATIC_LIBRARY), (shared, T.SHARED_LIBRARY), (header, T.INTERFACE_LIBRARY)):
            app.settings.link_project(project, kind)
        return static, shared, header, app

    def test_individual_and_whole_operations(self):
        static, shared, header, app = self.projects()
        toolchain = generators.resolve(app._resolved_build_settings())
        self.assertEqual(toolchain.generator, self.generator)
        environment = Environment.check(EnvironmentOptions(cmake=self.cmake))
        self.assertTrue(environment.success, "\n".join(i.detail for i in environment.items))

        updated = self.assertSuccess(shared.update())
        self.assertEqual(updated.generator, self.generator)
        self.assertIsNotNone(updated.compiler)
        self.assertEqual(updated.compiler.architecture, toolchain.architecture or generators.host_architecture())
        if toolchain.visual_studio:
            self.assertTrue(updated.solution_file.is_file())
            self.assertEqual(updated.solution_file.suffix, toolchain.solution_suffix)
            self.assertTrue(updated.project_file.is_file())
        else:
            self.assertIsNone(updated.solution_file)
            self.assertTrue((updated.build_directory / "build.ninja").is_file())

        debug = self.assertSuccess(app.build())
        run = self.assertSuccess(app.run())
        self.assertIn("value=42", run.processes[-1].output)
        # Linked trees share one configuration, so switch the parent.
        self.settings(configuration="Release")
        app.set_build_settings(ProjectBuildSettings(run_arguments=["x"]))
        release = self.assertSuccess(app.build())
        self.assertEqual(app.run().processes[-1].returncode, 7)
        _, build = engine._locations(app, app._resolved_build_settings())
        debug_exe = engine._outputs(app, build, "Debug")["file"]
        release_exe = engine._outputs(app, build, "Release")["file"]
        self.assertIn(debug_exe, debug.artifacts)
        self.assertIn(release_exe, release.artifacts)
        shared_debug = engine._outputs(shared, engine._locations(shared, shared._resolved_build_settings())[1], "Debug")["file"]
        self.assertTrue(shared_debug.is_file())

        # Clean removes this tree's configuration only; linked Projects keep their outputs.
        self.settings(configuration="Debug")
        app.set_build_settings(ProjectBuildSettings())
        cleaned = self.assertSuccess(app.clean())
        self.assertEqual(len(cleaned.processes), 1)
        self.assertFalse(debug_exe.exists())
        self.assertTrue(release_exe.exists())
        self.assertTrue(shared_debug.exists())
        self.assertSuccess(app.rebuild())
        self.assertTrue(debug_exe.exists())

        # The whole Solution builds the same trees as the individual operations.
        app.set_build_settings(ProjectBuildSettings())
        self.settings(configuration="Debug", run_projects=["App"])
        before = debug_exe.stat().st_mtime_ns
        built = self.assertSuccess(self.solution.build())
        self.assertIn(debug_exe, built.artifacts)
        if toolchain.visual_studio or os.name != "nt":
            # Ninja+MSVC cannot track headers under non-ASCII paths (see test_incremental_ascii_path).
            self.assertEqual(before, debug_exe.stat().st_mtime_ns, "\n".join(p.output[-800:] for p in built.processes))
        whole_run = self.assertSuccess(self.solution.run())
        self.assertIn("value=42", whole_run.processes[-1].output)
        self.settings(configuration="Debug", build_projects=["App"])
        self.assertSuccess(self.solution.rebuild())
        self.assertTrue(shared_debug.exists())
        self.settings(configuration="Debug")
        self.assertSuccess(self.solution.clean())
        self.assertFalse(debug_exe.exists())
        self.assertFalse(shared_debug.exists())
        self.assertTrue(release_exe.exists())
        updated = self.assertSuccess(self.solution.update())
        if toolchain.visual_studio:
            self.assertEqual(updated.artifacts[0].suffix, toolchain.solution_suffix)
            self.assertTrue(updated.artifacts[0].is_file())
        else:
            self.assertEqual(updated.artifacts, ())

    def test_googletest_project(self):
        tests = self.solution.add_project("Tests", "Tests", T.TEST)
        tests.add_file("src/test.cpp", content="#include <gtest/gtest.h>\nTEST(Suite, Works) { EXPECT_EQ(1 + 1, 2); }\n", auto_update=False)
        tests.set_build_settings(ProjectBuildSettings(googletest_archive=os.environ.get("CPPBUILD_TEST_GTEST_ARCHIVE")))
        report = tests.test()
        self.assertTrue(report.success, "\n".join([*report.diagnostics, *(p.output[-4000:] for p in report.processes)]))
        self.assertEqual([c.name for c in report.cases], ["Suite.Works"])
        self.assertSuccess(tests.clean())


@unittest.skipUnless(os.environ.get("CPPBUILD_TEST_NINJA") == "1", "Set CPPBUILD_TEST_NINJA=1 for real Ninja builds")
class NinjaTests(GeneratorScenario, unittest.TestCase):
    generator = "Ninja Multi-Config"

    def test_incremental_ascii_path(self):
        # MSVC reports /showIncludes paths in the console code page, which Ninja compares
        # as UTF-8; under ASCII paths individual and whole builds are no-ops once built.
        temporary = tempfile.TemporaryDirectory(prefix="cppbuild-ascii-")
        self.addCleanup(temporary.cleanup)
        self.assertTrue(temporary.name.isascii())
        self.solution = Solution.create(Path(temporary.name) / "ascii", "Demo")
        self.settings(configuration="Debug")
        *_, app = self.projects()
        built = self.assertSuccess(app.build())
        executable = next(p for p in built.artifacts if p.name.startswith("App") and p.suffix in {"", ".exe"})
        before = executable.stat().st_mtime_ns
        self.assertSuccess(app.build())
        whole = self.assertSuccess(self.solution.build())
        self.assertEqual(before, executable.stat().st_mtime_ns, "\n".join(p.output[-800:] for p in whole.processes))
        self.assertTrue(all("no work to do" in p.output for p in whole.processes if "--build" in p.command))

    @unittest.skipUnless(os.name == "nt", "MSVC versions and architectures are selected with vcvarsall")
    def test_msvc_version_and_architectures(self):
        app = self.solution.add_project("App", "App", T.EXECUTABLE)
        app.add_file("src/main.cpp", content='#include <cstdio>\nint main() { std::printf("%d", int(sizeof(void*))); }\n', auto_update=False)
        versions = sorted({p.name[:5] for i in generators._vs_installations()
                           for p in (Path(i) / "VC/Tools/MSVC").glob("*") if p.is_dir()})
        for version in versions:
            with self.subTest(version=version):
                self.solution.set_build_settings(SolutionBuildSettings(architecture="Win32", cmake=CMakeSettings(
                    generator=self.generator, toolset=version)))
                updated = self.assertSuccess(app.update())
                self.assertEqual(updated.compiler.architecture, "Win32")
                self.assertTrue(updated.compiler.version.startswith("19." + version.split(".")[1]), updated.compiler)
                run = self.assertSuccess(app.run())
                self.assertEqual(run.processes[-1].output.strip(), "4")
        # Explicit cl is the same MSVC selection.
        self.solution.set_build_settings(SolutionBuildSettings(cmake=CMakeSettings(generator=self.generator, cxx_compiler="cl")))
        self.assertEqual(self.assertSuccess(app.run()).processes[-1].output.strip(), "8")
        # A foreign target builds but refuses to run on this host.
        if generators.host_architecture() == "x64":
            self.solution.set_build_settings(SolutionBuildSettings(architecture="ARM64", cmake=self.cmake))
            built = self.assertSuccess(app.build())
            self.assertTrue(built.artifacts)
            with self.assertRaises(SettingsError):
                app.run()
        self.solution.set_build_settings(SolutionBuildSettings(cmake=CMakeSettings(generator=self.generator, cxx_compiler="no-such-compiler")))
        with self.assertRaises(SettingsError):
            app.update()


@unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2026") == "1", "Set CPPBUILD_TEST_VS2026=1 for real Visual Studio 2026 builds")
class VisualStudio2026Tests(GeneratorScenario, unittest.TestCase):
    generator = "Visual Studio 18 2026"

    def test_toolset_selection(self):
        app = self.solution.add_project("App", "App", T.EXECUTABLE)
        app.add_file("src/main.cpp", content="int main() { return 0; }\n", auto_update=False)
        self.solution.set_build_settings(SolutionBuildSettings(architecture="Win32", cmake=CMakeSettings(
            generator=self.generator, toolset="v143")))
        updated = self.assertSuccess(app.update())
        self.assertTrue(updated.compiler.version.startswith("19.4"), updated.compiler)
        self.assertEqual(updated.compiler.architecture, "Win32")
        self.assertSuccess(app.run())


if __name__ == "__main__":
    unittest.main()

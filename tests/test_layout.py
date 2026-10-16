"""One .cppbuild per Solution, anywhere: the base directory, Projects outside it, moving .cppbuild,
recursive dependency discovery and the rules that keep each file in one Project."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from cppbuild import (CMakeSettings, FileKind, MissingDependenciesError, ProjectType as T, SettingsError, Solution,
                      SolutionBuildSettings, TemplateTools)
from cppbuild import cmake_files, generators, storage, workspace
from cppbuild.graph import discover, resolve


def source(project, name, text):
    project.add_file(name, content=text, auto_update=False)


class BaseDirectoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cppbuild-layout-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        # repo/sln/My/.cppbuild with the repository as its base; the sources are elsewhere in it.
        self.solution = Solution.create(self.root / "repo/sln/My", "My", base_directory="../..")

    def test_base_directory_is_recorded_relative_to_cppbuild(self):
        self.assertEqual(self.solution.config_directory, self.root / "repo/sln/My/.cppbuild")
        self.assertEqual(self.solution.root, self.root / "repo")
        raw = storage.read_json(self.solution.settings.path)
        self.assertEqual(raw["data"]["base_directory"], "../../..")
        reopened = Solution.open(self.solution.config_directory)
        self.assertEqual(reopened.root, self.root / "repo")
        self.assertEqual(reopened.info().config_directory, self.solution.config_directory)
        # The default base (the parent of .cppbuild) is not written.
        plain = Solution.create(self.root / "plain", "Plain")
        self.assertNotIn("base_directory", storage.read_json(plain.settings.path)["data"])
        self.assertEqual(plain.root, self.root / "plain")

    def test_projects_anywhere_nothing_written_there(self):
        app = self.solution.add_project("src/app", "App", T.EXECUTABLE)
        outside = self.solution.add_project(self.root / "elsewhere/lib", "Lib", T.STATIC_LIBRARY)
        self.assertEqual(app.root, self.root / "repo/src/app")
        self.assertEqual(self.solution.settings.get().projects, {"App": "src/app", "Lib": "../elsewhere/lib"})
        saved = storage.read_json(self.solution.settings.path)["data"]["projects"]
        self.assertEqual(saved, {"App": "../../../src/app", "Lib": "../../../../elsewhere/lib"})
        self.assertEqual(app.settings.path, self.solution.config_directory / "projects/App/project.json")
        self.assertFalse(app.root.exists())
        self.assertFalse(outside.root.exists())
        workspace.plan(self.solution).write()
        self.assertFalse(app.root.exists())
        reopened = Solution.open(self.solution.config_directory)
        self.assertEqual(reopened.get_project("Lib").root, self.root / "elsewhere/lib")
        self.assertEqual(self.solution.file_kind("sln/My/.cppbuild/projects/App/CMakeLists.txt"), FileKind.GENERATED)
        self.assertEqual(self.solution.file_kind("sln/My/.cppbuild/projects/App/project.json"), FileKind.MANAGED)

    def test_project_directory_may_hold_the_cppbuild(self):
        # A single-directory program: the Project is the base directory itself.
        solution = Solution.create(self.root / "single", "Single")
        project = solution.add_project(".", "Single", T.EXECUTABLE)
        data = project.settings.get()
        data.source_directories = ["."]
        project.settings.save(data)
        source(project, "main.cpp", "int main() { return 0; }\n")
        workspace.plan(solution).write()
        from cppbuild import engine
        self.assertEqual([p.name for p in engine._scan(project)], ["main.cpp"])
        text = cmake_files.entry_path(project).read_text(encoding="utf-8")
        self.assertIn('"${CMAKE_CURRENT_SOURCE_DIR}/../../.." ABSOLUTE)', text)
        with self.assertRaises(SettingsError):
            project.add_file(".cppbuild/x.cpp", content="", auto_update=False)

    def test_set_base_directory_moves_nothing(self):
        app = self.solution.add_project("src/app", "App", T.EXECUTABLE)
        before = app.root
        self.solution.set_base_directory("src")
        self.assertEqual(self.solution.root, self.root / "repo/src")
        self.assertEqual(self.solution.settings.get().projects, {"App": "app"})
        self.assertEqual(self.solution.get_project("App").root, before)
        self.assertEqual(Solution.open(self.solution.config_directory).root, self.root / "repo/src")
        with self.assertRaises(SettingsError):
            self.solution.set_base_directory("../sln/My/.cppbuild")

    def test_shared_sources_are_allowed_but_not_in_one_build(self):
        shared = self.root / "repo/shared"
        first = self.solution.add_project("shared", "Core", T.STATIC_LIBRARY)
        source(first, "src/core.cpp", "int core() { return 1; }\n")
        other = Solution.create(self.root / "repo/sln/Other", "Other", base_directory="../..")
        second = other.add_project("shared", "Core", T.STATIC_LIBRARY)
        self.assertEqual(first.root, second.root)
        self.assertEqual(second.root, shared)
        workspace.plan(other).check(lambda node: "Debug")
        app = other.add_project("apps/app", "App", T.EXECUTABLE)
        source(app, "src/main.cpp", "int core(); int main() { return core() - 1; }\n")
        app.settings.link_solution(self.solution.config_directory, T.STATIC_LIBRARY)
        app.settings.link_project(second, T.STATIC_LIBRARY)
        # Both Core Projects would be built in one tree from the same files.
        with self.assertRaisesRegex(SettingsError, "built together"):
            workspace.plan(other).check(lambda node: "Debug")

    def test_cross_drive_paths_are_rejected(self):
        drive = "Z:" if self.root.drive.upper() != "Z:" else "Y:"
        with self.assertRaises(SettingsError):
            self.solution.add_project(drive + "/far/app", "Far", T.EXECUTABLE)
        with self.assertRaises(SettingsError):
            Solution.create(self.root / "x", "X", base_directory=drive + "/base")
        self.assertEqual(self.solution.projects(), [])


class MoveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cppbuild-move-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.solution = Solution.create(self.root / "repo/build/My", "My", base_directory="../..")
        self.lib = self.solution.add_project("src/lib", "Lib", T.STATIC_LIBRARY)
        source(self.lib, "src/lib.cpp", "int value() { return 42; }\n")
        self.app = self.solution.add_project("src/app", "App", T.EXECUTABLE)
        source(self.app, "src/main.cpp", "int value(); int main() { return value() == 42 ? 0 : 1; }\n")
        self.link = self.app.settings.link_project(self.lib, T.STATIC_LIBRARY)
        values = self.solution.settings.get()
        values.dependency_directories = ["deps"]
        self.solution.settings.save(values)

    def test_move_config_keeps_every_place(self):
        workspace.plan(self.solution).write()
        output = self.solution.config_directory / "output/intermediate/keep"
        output.mkdir(parents=True)
        (output / "CMakeCache.txt").write_text("")
        old = self.solution.config_directory
        app = self.app
        self.solution.move_config("tools/cppbuild")
        new = self.root / "repo/tools/cppbuild/.cppbuild"
        self.assertEqual(self.solution.config_directory, new)
        self.assertEqual(self.solution.root, self.root / "repo")
        self.assertIs(self.solution.get_project("App"), app)
        self.assertEqual(app.root, self.root / "repo/src/app")
        self.assertEqual(app.settings.path, new / "projects/App/project.json")
        self.assertIn(self.link.dependency_id, app.settings.get().dependencies)
        saved = storage.read_json(new / "project.json")["data"]
        self.assertEqual(saved["projects"]["App"], "../../../src/app")
        self.assertEqual(saved["dependency_directories"], ["../../../deps"])
        self.assertEqual(saved["base_directory"], "../../..")
        # Management files moved; generated files are written again from the new place.
        self.assertFalse((old / "project.json").exists())
        self.assertFalse((old / "projects").exists())
        self.assertFalse((old / "CMakeLists.txt").exists())
        self.assertFalse((new / "CMakeLists.txt").exists())
        # Build trees stay behind for removal by hand.
        self.assertTrue(output.is_dir())
        reopened = Solution.open(new)
        self.assertEqual(reopened.get_project("Lib").root, self.root / "repo/src/lib")
        self.assertEqual(len(resolve([reopened.get_project("App")])[1]), 2)
        app.settings.save(app.settings.get())

    def test_move_config_with_its_base(self):
        self.solution.move_config(self.root / "elsewhere/My", keep_base=False)
        self.assertEqual(self.solution.root, self.root)
        self.assertEqual(self.solution.get_project("App").root, self.root / "repo/src/app")
        self.assertEqual(self.solution.settings.get().projects["App"], "repo/src/app")
        with self.assertRaises(FileExistsError):
            other = Solution.create(self.root / "busy", "Busy")
            self.solution.move_config(other.config_directory.parent)

    def test_move_project_record_only(self):
        (self.root / "repo/src/lib").rename(self.root / "repo/libs")
        report = self.solution.move_project("Lib", "libs", move_files=False, auto_update=False)
        self.assertTrue(report.success)
        self.assertEqual(self.lib.root, self.root / "repo/libs")
        self.assertEqual(Solution.open(self.solution.config_directory).get_project("Lib").root, self.root / "repo/libs")

    def test_move_project_outside_the_base(self):
        self.solution.move_project("Lib", self.root / "outside/lib", auto_update=False)
        self.assertTrue((self.root / "outside/lib/src/lib.cpp").is_file())
        self.assertEqual(self.solution.settings.get().projects["Lib"], "../outside/lib")


class TemplateLayoutTests(unittest.TestCase):
    def test_template_keeps_the_place_of_cppbuild(self):
        with tempfile.TemporaryDirectory(prefix="cppbuild-tpl-") as temporary:
            root = Path(temporary)
            solution = Solution.create(root / "repo/sln/My", "My", base_directory="../..")
            app = solution.add_project("src/app", "App", T.EXECUTABLE)
            source(app, "src/main.cpp", "int main() { return 0; }\n")
            template = TemplateTools.create_solution_template(solution, root / "Template")
            self.assertEqual(template, root / "Template/sln/My/.cppbuild")
            self.assertTrue((root / "Template/src/app/src/main.cpp").is_file())
            made = Solution.create(root / "Made", "Made", template=template)
            self.assertEqual(made.config_directory, root / "Made/sln/My/.cppbuild")
            self.assertEqual(made.root, root / "Made")
            self.assertEqual(made.get_project("App").root, root / "Made/src/app")
            # A Project outside the base directory cannot be copied with it.
            solution.add_project(root / "outside", "Outside", T.STATIC_LIBRARY)
            with self.assertRaisesRegex(SettingsError, "Outside"):
                TemplateTools.create_solution_template(solution, root / "Template2")


class DiscoveryTests(unittest.TestCase):
    """Solutions found at any depth below the dependency directories."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cppbuild-find-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.top = Solution.create(self.root / "Top", "Top")
        values = self.top.settings.get()
        values.dependency_directories = ["deps"]
        self.top.settings.save(values)
        self.app = self.top.add_project("App", "App", T.EXECUTABLE)

    def library(self, directory, name, **options):
        solution = Solution.create(directory, name, **options)
        project = solution.add_project(options.get("base_directory") and "src" or name, name, T.STATIC_LIBRARY)
        return solution, project

    def test_deep_cppbuild_is_found(self):
        # A repository whose Solution is not at its root.
        stl, lib = self.library(self.top.root / "deps/STL/build/cppbuild", "STL", base_directory="../..")
        found = discover(self.top.root / "deps", self.top.config_directory)
        self.assertEqual(found, [stl.config_directory])
        # Mid registers STL where it was; the Top's dependency directory finds it after a move.
        mid, mid_lib = self.library(self.root / "Mid", "Mid")
        mid_lib.settings.link_solution(stl.config_directory, T.STATIC_LIBRARY)
        self.app.settings.link_solution(mid.config_directory, T.STATIC_LIBRARY)
        moved = self.top.root / "deps/Moved/very/deep"
        moved.parent.mkdir(parents=True)
        shutil.move(str(self.top.root / "deps/STL"), str(moved))
        nodes = resolve([self.app])[1]
        self.assertEqual(next(n.project for n in nodes if n.project.name == "STL").root, moved / "src")

    def test_a_dependency_copy_of_its_own_is_left_out(self):
        stl, lib = self.library(self.top.root / "deps/STL", "STL")
        a, a_lib = self.library(self.top.root / "deps/A", "A")
        values = a.settings.get()
        values.dependency_directories = ["deps"]
        a.settings.save(values)
        shutil.copytree(stl.root, a.root / "deps/STL")
        a_lib.settings.link_solution(a.root / "deps/STL/.cppbuild", T.STATIC_LIBRARY)
        found = discover(self.top.root / "deps", self.top.config_directory)
        self.assertEqual(sorted(found), sorted([stl.config_directory, a.config_directory]))
        self.app.settings.link_solution(a.config_directory, T.STATIC_LIBRARY)
        nodes = resolve([self.app])[1]
        self.assertEqual(next(n.project for n in nodes if n.project.name == "STL").root, stl.root / "STL")

    def test_outward_dependency_directory_does_not_hide_the_rest(self):
        a, _ = self.library(self.top.root / "deps/A", "A")
        values = a.settings.get()
        values.dependency_directories = ["../Shared"]  # a sibling of A, shared with the Top
        a.settings.save(values)
        b, _ = self.library(self.top.root / "deps/Shared/B", "B")
        found = discover(self.top.root / "deps", self.top.config_directory)
        self.assertEqual(sorted(found), sorted([a.config_directory, b.config_directory]))

    def test_build_trees_are_skipped_but_build_directories_are_not(self):
        tree = self.top.root / "deps/repo/build/out"
        tree.mkdir(parents=True)
        (tree / "CMakeCache.txt").write_text("")
        Solution.create(tree / "Copy", "Copy")
        found, _ = self.library(self.top.root / "deps/repo/build/cppbuild", "Found")
        self.assertEqual(discover(self.top.root / "deps", self.top.config_directory), [found.config_directory])

    def test_excluded_and_output_directories_are_not_entered(self):
        hidden, _ = self.library(self.top.root / "deps/repo/.git/Hidden", "Hidden")
        fetching, _ = self.library(self.top.root / "deps/Tmp.cppbuild-fetch/X", "X")
        self.assertEqual(discover(self.top.root / "deps", self.top.config_directory), [])

    def test_two_copies_of_one_solution_are_rejected(self):
        stl, _ = self.library(self.top.root / "deps/STL", "STL")
        shutil.copytree(stl.root, self.top.root / "deps/Again/nested")
        self.app.settings.link_solution(stl.config_directory, T.STATIC_LIBRARY)
        with self.assertRaisesRegex(SettingsError, "same Project GUID"):
            resolve([self.app])

    def test_missing_dependency_is_reported(self):
        stl, _ = self.library(self.root / "away/STL", "STL")
        self.app.settings.link_solution(stl.config_directory, T.STATIC_LIBRARY)
        shutil.rmtree(stl.root)
        with self.assertRaises(MissingDependenciesError):
            resolve([self.app])


class LayoutBuildScenario:
    generator = None

    def test_build_from_anywhere_and_plain_cmake(self):
        work = Path(__file__).resolve().parents[1] / ".test-work"
        work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="lay-", dir=work) as temporary:
            root = Path(temporary)
            settings = SolutionBuildSettings(cmake=CMakeSettings(generator=self.generator))
            # The library's sources are outside the base directory of the Solution building them.
            solution = Solution.create(root / "repo 日本語/sln/My", "My", base_directory="../..")
            solution.set_build_settings(settings)
            lib = solution.add_project(root / "shared/lib", "Lib", T.STATIC_LIBRARY)
            source(lib, "include/lib.hpp", "#pragma once\nint value();\n")
            source(lib, "src/lib.cpp", '#include "lib.hpp"\nint value() { return 42; }\n')
            app = solution.add_project("src/app", "App", T.EXECUTABLE)
            source(app, "src/main.cpp", '#include <cstdio>\n#include "lib.hpp"\nint main() { std::printf("%d", value()); }\n')
            app.settings.link_project(lib, T.STATIC_LIBRARY)
            report = app.run()
            self.assertTrue(report.success, "\n".join(p.output[-3000:] for p in report.processes))
            self.assertEqual(report.processes[-1].output.strip(), "42")
            self.assertFalse((app.root / ".cppbuild").exists())
            self.assertFalse(list((root / "shared").rglob("CMakeLists.txt")))
            # Plain CMake from .cppbuild alone.
            toolchain = generators.resolve(settings)
            build = root / "plain"
            for command in (toolchain.configure(solution.config_directory, build), ["cmake", "--build", build, "--config", "Debug"]):
                result = subprocess.run([str(c) for c in command], env=toolchain.env, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, errors="replace")
                self.assertEqual(result.returncode, 0, result.stdout[-4000:])
            executable = build / "bin/Debug" / ("App.exe" if os.name == "nt" else "App")
            self.assertEqual(subprocess.run([str(executable)], stdout=subprocess.PIPE, text=True).stdout.strip(), "42")
            # Moving .cppbuild keeps the build working.
            solution.move_config("tools")
            report = app.run()
            self.assertTrue(report.success, "\n".join(p.output[-3000:] for p in report.processes))


@unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2022") == "1", "Real VS2022 required")
class LayoutVS2022Tests(LayoutBuildScenario, unittest.TestCase):
    generator = "Visual Studio 17 2022"


@unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2026") == "1", "Real VS2026 required")
class LayoutVS2026Tests(LayoutBuildScenario, unittest.TestCase):
    generator = "Visual Studio 18 2026"


@unittest.skipUnless(os.environ.get("CPPBUILD_TEST_NINJA") == "1", "Real Ninja required")
class LayoutNinjaTests(LayoutBuildScenario, unittest.TestCase):
    generator = "Ninja Multi-Config"


if __name__ == "__main__":
    unittest.main()

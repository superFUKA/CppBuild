"""Projects listed only for the IDE, linked Solutions used alone, and owned output directories."""
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from cppbuild import (CMakeSettings, ProjectBuildSettings, ProjectType as T, Solution, SolutionBuildSettings,
                      SolutionFolderSettings)
from cppbuild import engine, output_paths, workspace


def listed_workspace(root):
    """Main shows External's Projects; External's App shares a name with Main's App.

    Only Main::App -> External::Lib is used. External's Tool, listed for the IDE only,
    links External::Hidden (which does not compile) and Main::Base as a shared library.
    Main's Iface and Bare are interface libraries over Base, with and without files.
    """
    main = Solution.create(root / "Main", "Main")
    external = Solution.create(root / "External", "External")
    lib = external.add_project("Lib", "Lib", T.STATIC_LIBRARY)
    lib.add_file("src/lib.cpp", content="int lib() { return 40; }\n", auto_update=False)
    other = external.add_project("App", "App", T.EXECUTABLE)
    other.add_file("src/main.cpp", content="int main() { return 3; }\n", auto_update=False)
    hidden = external.add_project("Hidden", "Hidden", T.STATIC_LIBRARY)
    hidden.add_file("src/hidden.cpp", content="#error only listed for the IDE\n", auto_update=False)
    tool = external.add_project("Tool", "Tool", T.EXECUTABLE)
    tool.add_file("src/main.cpp", content="int main() { return 0; }\n", auto_update=False)
    tool.settings.link_project(hidden, T.STATIC_LIBRARY)
    values = external.settings.get()
    values.main_project = "Lib"
    external.settings.save(values)
    base = main.add_project("Base", "Base", T.STATIC_LIBRARY)
    base.add_file("src/base.cpp", content="int base() { return 2; }\n", auto_update=False)
    iface = main.add_project("Iface", "Iface", T.INTERFACE_LIBRARY)
    iface.add_file("include/iface.hpp", content="#pragma once\nint base();\n", auto_update=False)
    iface.settings.link_project(base, T.STATIC_LIBRARY)
    bare = main.add_project("Bare", "Bare", T.INTERFACE_LIBRARY)
    bare.settings.link_project(base, T.STATIC_LIBRARY)
    app = main.add_project("App", "App", T.EXECUTABLE)
    app.add_file("src/main.cpp", content="#include <cstdio>\nint lib(); int base();\n"
                 'int main() { std::printf("%d", lib() + base()); return 0; }\n', auto_update=False)
    app.settings.link_project(base, T.STATIC_LIBRARY)
    app.settings.link_solution(external.root / ".cppbuild", T.STATIC_LIBRARY)
    values = main.settings.get()
    values.main_project = "Base"
    values.solution_folders = SolutionFolderSettings()
    main.settings.save(values)
    tool.settings.link_solution(main.root / ".cppbuild", T.SHARED_LIBRARY)
    return main, external


class GeneratedTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cppbuild-listed-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_listed_projects_request_only_listed_types(self):
        main, external = listed_workspace(self.root)
        plan = workspace.plan(main)
        self.assertEqual([(p.name, kinds) for p, kinds in plan.external_requests()],
                         [("App", ["DISPLAY"]), ("Hidden", ["DISPLAY", "DISPLAY_STATIC"]),
                          ("Lib", ["STATIC"]), ("Tool", ["DISPLAY"])])
        self.assertEqual([(p.name, kinds) for p, kinds in plan.member_requests()], [("Base", ["DISPLAY_SHARED"])])
        documents = plan.documents()
        top = documents[main.root / "CppBuildTopLevel.cmake"]
        self.assertIn("DISPLAY DISPLAY_STATIC)  # External::Hidden", top)
        self.assertIn("DISPLAY_SHARED)  # Main::Base", top)
        self.assertIn('set(CPPBUILD_LINKED_OUTPUT "_linked/External")', top)
        # Listed programs and DLLs go apart from the built ones, which may share their names.
        for name in ("App", "Hidden"):
            entry = documents[external.root / name / "CMakeLists.txt"]
            self.assertIn('RUNTIME_OUTPUT_DIRECTORY "${CMAKE_RUNTIME_OUTPUT_DIRECTORY}/${CPPBUILD_LINKED_OUTPUT}"', entry)
        plan.check(lambda node: node.settings.configuration)

    def test_linked_solution_files_include_its_own_links(self):
        a = Solution.create(self.root / "A", "A")
        b = Solution.create(self.root / "B", "B")
        c = Solution.create(self.root / "C", "C")
        leaf = c.add_project("Leaf", "Leaf", T.STATIC_LIBRARY)
        leaf.add_file("src/leaf.cpp", content="int leaf() { return 1; }\n", auto_update=False)
        part = b.add_project("Part", "Part", T.STATIC_LIBRARY)
        part.add_file("src/part.cpp", content="int part() { return 1; }\n", auto_update=False)
        util = b.add_project("Util", "Util", T.STATIC_LIBRARY)
        util.add_file("src/util.cpp", content="int leaf(); int util() { return leaf(); }\n", auto_update=False)
        util.settings.link_solution(c.root / ".cppbuild", T.STATIC_LIBRARY)
        values = b.settings.get()
        values.main_project = "Part"
        b.settings.save(values)
        app = a.add_project("App", "App", T.EXECUTABLE)
        app.add_file("src/main.cpp", content="int part(); int main() { return part() - 1; }\n", auto_update=False)
        app.settings.link_solution(b.root / ".cppbuild", T.STATIC_LIBRARY)
        plan = workspace.plan(a)
        self.assertEqual(sorted(p.name for p in plan.projects.values()), ["App", "Part"])
        documents = plan.documents()
        # cmake -S B adds C as B's linked Solution, so C's files are written too.
        self.assertIn('add_subdirectory("../C"', documents[b.root / "CppBuildTopLevel.cmake"])
        for path in (c.root / "CMakeLists.txt", c.root / "CppBuildTopLevel.cmake", leaf.root / "CMakeLists.txt",
                     util.root / "CMakeLists.txt"):
            self.assertIn(path, documents)
        self.assertEqual(workspace.plan(a).documents(), documents)

    def test_artifact_directory_is_owned_and_not_scanned(self):
        solution = Solution.create(self.root / "Owned", "Owned")
        project = solution.add_project("App", "App", T.EXECUTABLE)
        project.add_file("main.cpp", content="int main() { return 0; }\n", auto_update=False)
        data = project.settings.get()
        data.source_directories = ["."]
        project.settings.save(data)
        project.set_build_settings(ProjectBuildSettings(artifact_directory="../products"))
        plan = workspace.plan(solution)
        plan.cache_script(SimpleNamespace(context="test"))
        area = output_paths.area(project, "../products", "test")
        self.assertTrue((area / output_paths.MARKER).is_file())
        (area / "Debug").mkdir()
        (area / "Debug/App.exe").write_bytes(b"MZ")
        self.assertEqual([p.name for p in engine._scan(project)], ["main.cpp"])


class RealScenario:
    generator = None

    def test_listed_projects_and_interface_build(self):
        work = Path(__file__).resolve().parents[1] / ".test-work"
        work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="listed-", dir=work) as temporary:
            main, external = listed_workspace(Path(temporary))
            main.set_build_settings(SolutionBuildSettings(cmake=CMakeSettings(generator=self.generator)))
            output = main.root / ".cppbuild/output"

            def built(name):
                return [p for p in output.rglob("*") if p.name in {name + ".lib", "lib" + name + ".a"}]

            # An interface library builds what it links, with and without files of its own.
            for name in ("Bare", "Iface"):
                report = main.get_project(name).build()
                self.assertTrue(report.success, "\n".join(p.output[-3000:] for p in report.processes))
                self.assertTrue(built("Base"), name)
                main.clean()
                self.assertFalse(built("Base"), name)
            # Listed Projects neither collide with Main::App nor build Hidden.
            report = main.build()
            self.assertTrue(report.success, "\n".join(p.output[-3000:] for p in report.processes))
            programs = [p for p in output.rglob("App.exe" if os.name == "nt" else "App") if p.is_file()]
            self.assertEqual(len(programs), 1, programs)
            self.assertNotIn("_linked", programs[0].parts)
            self.assertFalse(built("Hidden"))
            report = main.get_project("App").run()
            self.assertTrue(report.success, str(report))
            self.assertEqual(report.processes[-1].output.strip(), "42")


@unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2022") == "1", "Real VS2022 required")
class RealVS2022Tests(RealScenario, unittest.TestCase):
    generator = "Visual Studio 17 2022"


@unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2026") == "1", "Real VS2026 required")
class RealVS2026Tests(RealScenario, unittest.TestCase):
    generator = "Visual Studio 18 2026"


@unittest.skipUnless(os.environ.get("CPPBUILD_TEST_NINJA") == "1", "Real Ninja required")
class RealNinjaTests(RealScenario, unittest.TestCase):
    generator = "Ninja Multi-Config"


if __name__ == "__main__":
    unittest.main()

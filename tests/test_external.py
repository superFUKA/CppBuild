import os
from pathlib import Path
import tempfile
import unittest

from cppbuild import (Solution, ProjectType as T, ProjectSettingsData, TypeSettingsData,
                      ProjectBuildSettings, SolutionBuildSettings, CMakePackage, CMakeSource,
                      ImportedLibrary)


@unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2022") == "1", "Real VS2022 required")
class ExternalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cppbuild-external-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def success(self, report):
        self.assertTrue(report.success, "\n".join(p.output for p in report.processes))

    def test_external_solution_and_imported_roundtrip_unlink(self):
        provider = Solution.create(self.root / "Provider", "Provider")
        lib = provider.add_project("Lib", "Lib", T.STATIC_LIBRARY)
        lib.add_file("src/lib.cpp", content="int value() { return 42; }", auto_update=False)
        consumer = Solution.create(self.root / "Consumer", "Consumer")
        app = consumer.add_project("App", "App", T.EXECUTABLE)
        app.add_file("src/main.cpp", content="int value(); int main() { return value() == 42 ? 0 : 1; }", auto_update=False)
        linked = app.settings.link_solution(provider.root / ".cppbuild", T.STATIC_LIBRARY)
        self.success(app.run())
        consumer.set_build_settings(SolutionBuildSettings(configuration="Release", external_build_settings={
            str(provider.root / ".cppbuild"): SolutionBuildSettings(configuration="Release")}))
        self.success(app.run())
        binary = next((lib.root / ".cppbuild/build").rglob("Debug/Lib.lib"))
        original = binary.read_bytes()
        reopened = Solution.open(consumer.root / ".cppbuild").get_project("App")
        reopened.settings.unlink(linked.dependency_id)
        linked = reopened.settings.link_imported_library(ImportedLibrary(T.STATIC_LIBRARY, {"Debug": str(binary)}))
        self.success(reopened.run())
        self.success(reopened.clean())
        self.assertEqual(binary.read_bytes(), original)
        reopened.settings.unlink(linked.dependency_id)
        (reopened.root / "src/main.cpp").write_text("int main() { return 0; }")
        self.success(reopened.run())
        self.assertEqual(binary.read_bytes(), original)

    def test_source_package_pch_and_clean(self):
        source = self.root / "External"
        source.mkdir()
        (source / "CMakeLists.txt").write_text("add_library(External STATIC external.cpp)\n")
        (source / "external.cpp").write_text("int value() { return 42; }")
        package = self.root / "Package"
        package.mkdir()
        (package / "DemoPackageConfig.cmake").write_text("add_library(DemoPackage::Headers INTERFACE IMPORTED)\nset_property(TARGET DemoPackage::Headers PROPERTY INTERFACE_COMPILE_DEFINITIONS PACKAGE_VALUE=42)\n")
        solution = Solution.create(self.root / "Solution", "Demo")
        app = solution.add_project("App", "App", T.EXECUTABLE)
        app.add_file("include/pch.hpp", content="#pragma once\n#include <vector>\n", auto_update=False)
        app.add_file("src/main.cpp", content="int value(); int main() { return value() == PACKAGE_VALUE ? 0 : 1; }", auto_update=False)
        a = app.settings.link_cmake_source(CMakeSource(str(source), "External"))
        b = app.settings.link_package(CMakePackage("DemoPackage", "DemoPackage::Headers", str(package)))
        app.settings.set_pch(project_headers=["include/pch.hpp"], system_headers=["string"])
        self.success(app.run())
        pch = list((app.root / ".cppbuild/build").rglob("*.pch"))
        self.assertTrue(pch)
        binary = next((app.root / ".cppbuild/build").rglob("External.lib"))
        original = binary.read_bytes()
        self.success(app.clean())
        self.assertEqual(binary.read_bytes(), original)
        app.settings.clear_pch()
        self.success(app.rebuild())
        self.assertTrue((app.root / "include/pch.hpp").is_file())
        app = Solution.open(solution.root / ".cppbuild").get_project("App")
        app.settings.unlink(a.dependency_id)
        app.settings.unlink(b.dependency_id)
        (source / "CMakeLists.txt").write_text("message(FATAL_ERROR must_not_be_loaded)\n")
        (package / "DemoPackageConfig.cmake").write_text("message(FATAL_ERROR must_not_be_loaded)\n")
        (app.root / "src/main.cpp").write_text("int main() { return 0; }")
        self.success(app.run())

    def test_static_and_shared_same_project_simultaneous_consumers(self):
        solution = Solution.create(self.root / "Solution", "Demo")
        data = ProjectSettingsData("Lib", {T.STATIC_LIBRARY: TypeSettingsData(), T.SHARED_LIBRARY: TypeSettingsData()})
        lib = solution.add_project("Lib", "Lib", T.STATIC_LIBRARY, data)
        lib.set_build_settings(ProjectBuildSettings(project_type=T.STATIC_LIBRARY))
        lib.add_file("src/lib.cpp", content="__declspec(dllexport) int value() { return 42; }", auto_update=False)
        apps = []
        for name, kind in (("StaticApp", T.STATIC_LIBRARY), ("SharedApp", T.SHARED_LIBRARY)):
            app = solution.add_project(name, name, T.EXECUTABLE)
            app.add_file("src/main.cpp", content="int value(); int main() { return value() == 42 ? 0 : 1; }", auto_update=False)
            app.settings.link_project(lib, kind)
            apps.append(name)
        solution.set_build_settings(SolutionBuildSettings(build_projects=apps, run_projects=apps))
        self.success(solution.run())
        self.assertTrue(list((lib.root / ".cppbuild/build/vs2022-x64-static_library").rglob("Lib.lib")))
        self.assertTrue(list((lib.root / ".cppbuild/build/vs2022-x64-shared_library").rglob("Lib.dll")))

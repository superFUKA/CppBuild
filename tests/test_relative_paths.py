from contextlib import chdir
import os
from pathlib import Path, PureWindowsPath
import shutil
import tempfile
import unittest
from unittest.mock import patch

from cppbuild import (CMakePackage, CMakeSource, ImportedLibrary, ProjectType as T,
                      SettingsError, Solution, SolutionBuildSettings, TemplateTools)
from cppbuild import storage
from cppbuild.graph import resolve


class RelativePathTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cppbuild-relative-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bundle = self.root / "Original"
        self.provider = Solution.create(self.bundle / "Provider", "Provider")
        self.lib = self.provider.add_project("Lib", "Lib", T.STATIC_LIBRARY)
        self.consumer = Solution.create(self.bundle / "Consumer", "Consumer")
        self.app = self.consumer.add_project("App", "App", T.EXECUTABLE)
        self.app.settings.link_solution(self.provider.root / ".cppbuild", T.STATIC_LIBRARY)

    def add_paths(self):
        data = self.app.settings.get()
        data.source_directories = [str(self.app.root / "src"), str(self.app.root / "include")]
        data.project_headers = [str(self.app.root / "include/pch.hpp")]
        data.types[T.EXECUTABLE].include_directories = [str(self.app.root / "include")]
        self.app.settings.save(data)
        self.app.settings.link_cmake_source(CMakeSource(str(self.bundle / "vendor/source"), "Vendor"))
        self.app.settings.link_package(CMakePackage("Package", "Package::Headers", str(self.bundle / "vendor/package")))
        self.app.settings.link_imported_library(ImportedLibrary(T.SHARED_LIBRARY,
            {"Debug": str(self.bundle / "vendor/bin/lib.dll")}, [str(self.bundle / "vendor/include")],
            {"Debug": str(self.bundle / "vendor/lib/lib.lib")}))
        (self.bundle / "material.hpp").write_text("#pragma once", encoding="utf-8")
        self.consumer.create_file_template("Header", self.bundle / "material.hpp")

    def assert_document(self, path, kind):
        raw = storage.read_json(path)
        self.assertEqual(raw["schema_version"], storage.CURRENT_SCHEMA)
        self.assertEqual(raw["kind"], kind)
        self.assertNotIn(str(self.root), path.read_text(encoding="utf-8"))
        return raw["data"]

    def assert_targets(self, solution, bundle):
        app = solution.get_project("App")
        self.assertEqual(resolve([app])[1][0].project.root, bundle / "Provider/Lib")
        values = app.settings.get()
        self.assertEqual(values.source_directories, ["src", "include"])
        for dependency in values.dependencies.values():
            if isinstance(dependency, CMakeSource):
                self.assertEqual((app.root / dependency.directory).resolve(), bundle / "vendor/source")
            elif isinstance(dependency, CMakePackage):
                self.assertEqual((app.root / dependency.directory).resolve(), bundle / "vendor/package")
            elif isinstance(dependency, ImportedLibrary):
                self.assertEqual((app.root / dependency.locations["Debug"]).resolve(), bundle / "vendor/bin/lib.dll")
                self.assertEqual((app.root / dependency.import_libraries["Debug"]).resolve(), bundle / "vendor/lib/lib.lib")
                self.assertEqual((app.root / dependency.include_directories[0]).resolve(), bundle / "vendor/include")
        material = solution.settings.get().file_templates["Header"]
        self.assertEqual((solution.config_directory / "templates" / material).read_text(encoding="utf-8"), "#pragma once")

    def test_every_persisted_path_is_relative_to_its_base(self):
        """The Solution's paths are relative to .cppbuild, a Project's to the Project's directory."""
        self.add_paths()
        solution = self.assert_document(self.consumer.settings.path, "solution")
        self.assertEqual(solution["projects"]["App"], "../App")
        self.assertEqual(solution["file_templates"]["Header"], "Header.hpp")
        self.assertNotIn("base_directory", solution)
        reference = solution["references"][self.lib.settings.get().guid]["solution_directory"]
        self.assertFalse(PureWindowsPath(reference).is_absolute())
        self.assertEqual((self.consumer.config_directory / reference).resolve(), self.provider.config_directory)
        self.assertEqual(self.app.settings.path, self.consumer.config_directory / "projects/App/project.json")
        project = self.assert_document(self.app.settings.path, "project")
        self.assertEqual(project["source_directories"], ["src", "include"])
        self.assertEqual(project["project_headers"], ["include/pch.hpp"])
        filename = project["types"][T.EXECUTABLE.value]
        type_file = self.app.settings.path.parent / "types" / filename
        kind = self.assert_document(type_file, T.EXECUTABLE.value)
        self.assertEqual(kind["include_directories"], ["include"])
        for dependency in project["dependencies"].values():
            values = dependency["values"]
            if dependency["kind"] in {"source", "package"}:
                self.assertFalse(PureWindowsPath(values["directory"]).is_absolute())
                self.assertTrue((self.app.root / values["directory"]).resolve().is_relative_to(self.bundle))
            elif dependency["kind"] == "imported":
                self.assertEqual((self.app.root / values["locations"]["Debug"]).resolve(),
                                 self.bundle / "vendor/bin/lib.dll")

    def test_transplant_and_unrelated_cwd_keep_all_targets(self):
        self.add_paths()
        moved = self.root / "Different/deeper/移動 space"
        shutil.copytree(self.bundle, moved)
        self.bundle.rename(self.root / "Unavailable")
        unrelated = self.root / "Working"
        unrelated.mkdir()
        with chdir(unrelated):
            loaded = Solution.open(moved / "Consumer/.cppbuild")
            self.assert_targets(loaded, moved)
            self.assertEqual(loaded.get_project("App").settings.get().guid, self.app.settings.get().guid)
            # Registering another consumer still reuses the relocated reference.
            tool = loaded.add_project("Tools/Tool", "Tool", T.EXECUTABLE)
            tool.settings.link_solution(moved / "Provider/.cppbuild", T.STATIC_LIBRARY)
            self.assertEqual(len(loaded.settings.get().references), 1)

    def test_template_at_different_depth_keeps_external_targets_after_transplant(self):
        self.add_paths()
        template = TemplateTools.create_solution_template(self.consumer, self.bundle / "Templates/deep/Template")
        clone = Solution.create(self.bundle / "Exports/deeper/Clone", "Clone", template=template)
        self.assert_targets(clone, self.bundle)
        moved = self.root / "Elsewhere/Bundle"
        shutil.copytree(self.bundle, moved)
        self.bundle.rename(self.root / "Unavailable")
        loaded = Solution.open(moved / "Exports/deeper/Clone/.cppbuild")
        self.assert_targets(loaded, moved)
        self.assertNotEqual(loaded.get_project("App").settings.get().guid, self.app.settings.get().guid)

    def test_invalid_dependency_kind_raises_settings_error_without_changing_state(self):
        expected = self.app.settings.get()
        for invalid in ([], {}, None, 17, "unknown"):
            with self.subTest(kind=invalid):
                raw = storage.read_json(self.app.settings.path)
                next(iter(raw["data"]["dependencies"].values()))["kind"] = invalid
                storage.atomic_write(self.app.settings.path, storage.encoded(raw))
                before = {p: p.read_bytes() for p in self.consumer.root.rglob("*.json")}
                with self.assertRaises(SettingsError):
                    self.app.settings.reload()
                self.assertEqual(self.app.settings.get(), expected)
                self.assertEqual(before, {p: p.read_bytes() for p in self.consumer.root.rglob("*.json")})

    def test_stored_absolute_paths_and_other_drive_are_rejected(self):
        raw = storage.read_json(self.consumer.settings.path)
        raw["data"]["references"][self.lib.settings.get().guid]["solution_directory"] = str(self.provider.root / ".cppbuild")
        storage.atomic_write(self.consumer.settings.path, storage.encoded(raw))
        with self.assertRaises(SettingsError):
            Solution.open(self.consumer.root / ".cppbuild")
        # Use a fresh valid Solution for the input-path check.
        other = Solution.create(self.root / "Other", "Other")
        app = other.add_project("App", "App", T.EXECUTABLE)
        before = app.settings.path.read_bytes()
        drive = "Z:" if self.root.drive.upper() != "Z:" else "Y:"
        with self.assertRaises(SettingsError):
            app.settings.link_cmake_source(CMakeSource(drive + "/vendor", "Vendor"))
        self.assertEqual(app.settings.path.read_bytes(), before)

    @unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2022") == "1", "Real VS2022 required")
    def test_real_build_after_transplant_without_original_paths(self):
        self.lib.add_file("src/lib.cpp", content="int value() { return 42; }", auto_update=False)
        vendor = self.bundle / "Vendor"
        vendor.mkdir()
        (vendor / "CMakeLists.txt").write_text("add_library(Vendor STATIC vendor.cpp)\n", encoding="utf-8")
        (vendor / "vendor.cpp").write_text("int vendor() { return 7; }", encoding="utf-8")
        self.app.settings.link_cmake_source(CMakeSource(str(vendor), "Vendor"))
        self.app.add_file("src/main.cpp", content="int value(); int vendor(); int main() { return value()+vendor()==49 ? 0 : 1; }", auto_update=False)
        moved = self.root / "Different/日本語 space"
        shutil.copytree(self.bundle, moved)
        self.bundle.rename(self.root / "Unavailable")
        with chdir(self.root):
            loaded = Solution.open(moved / "Consumer/.cppbuild")
            for configuration in ("Debug", "Release"):
                loaded.set_build_settings(SolutionBuildSettings(configuration=configuration, run_projects=["App"],
                    external_build_settings={str(moved / "Provider/.cppbuild"): SolutionBuildSettings(configuration=configuration)}))
                report = loaded.run()
                self.assertTrue(report.success, str(report))
                report = loaded.get_project("App").run()
                self.assertTrue(report.success, str(report))

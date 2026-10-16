"""What a program embedding CppBuild relies on without copying its internal rules:
typed errors, accepted values, file kinds, transactions and skipped configures."""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest

import cppbuild
from cppbuild import (AlreadyRegisteredError, CMakePackage, FileKind, InvalidValueError, NoMainProjectError,
                      NotRegisteredError, ProjectBuildSettings, ProjectType as T, SettingsError, Solution,
                      SolutionBuildSettings)

REAL = os.environ.get("CPPBUILD_TEST_VS2022") == "1"


class IntegrationApiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cppbuild-api-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.solution = Solution.create(self.root / "Sol", "Sol")
        self.app = self.solution.add_project("App", "App", T.EXECUTABLE)
        self.app.add_file("src/main.cpp", content="int main() { return 0; }\n", auto_update=False)
        self.lib = self.solution.add_project("Lib", "Lib", T.STATIC_LIBRARY)
        self.lib.add_file("src/lib.cpp", content="int lib() { return 1; }\n", auto_update=False)

    def managed_and_generated(self):
        """Digest of every management and generated file, as a caller would back them up."""
        return {f.relative_to(self.solution.root).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest()
                for f in self.solution.root.rglob("*")
                if f.is_file() and self.solution.file_kind(f) in (FileKind.MANAGED, FileKind.GENERATED)}

    # Typed errors -------------------------------------------------------------------

    def test_errors_have_types_and_stay_settings_errors(self):
        link = self.app.settings.link_project(self.lib, T.STATIC_LIBRARY)
        with self.assertRaises(AlreadyRegisteredError) as caught:
            self.app.settings.link_project(self.lib, T.STATIC_LIBRARY)
        self.assertEqual((caught.exception.kind, caught.exception.name), ("dependency", link.dependency_id))
        self.assertIsInstance(caught.exception, SettingsError)
        with self.assertRaises(AlreadyRegisteredError) as caught:
            self.solution.add_project("Other", "Lib", T.STATIC_LIBRARY)
        self.assertEqual((caught.exception.kind, caught.exception.name), ("project", "Lib"))

        for call, kind in ((lambda: self.solution.get_project("Missing"), "project"),
                           (lambda: self.solution.remove_project("Missing"), "project"),
                           (lambda: self.app.settings.unlink("0" * 32), "dependency"),
                           (lambda: self.solution.settings.remove_file_template("Missing"), "file_template"),
                           (lambda: self.app.add_file("src/x.cpp", template_name="Missing"), "file_template"),
                           (lambda: self.solution.remove_git_source("Missing"), "git_source"),
                           (lambda: self.solution.set_build_settings(SolutionBuildSettings(run_projects=["Missing"])), "project")):
            with self.subTest(kind=kind), self.assertRaises(NotRegisteredError) as caught:
                call()
            self.assertEqual(caught.exception.kind, kind)
            # The lookups raised KeyError before; both still catch it.
            self.assertIsInstance(caught.exception, KeyError)
            self.assertIsInstance(caught.exception, SettingsError)
            self.assertNotIn("'", str(caught.exception)[:1])

    def test_invalid_values_name_the_field_and_the_accepted_values(self):
        cases = ((SolutionBuildSettings(cpp_standard=14), "cpp_standard", cppbuild.CPP_STANDARDS),
                 (SolutionBuildSettings(configuration="Fast"), "configuration", cppbuild.CONFIGURATIONS),
                 (SolutionBuildSettings(architecture="x86"), "architecture", (None, *cppbuild.ARCHITECTURES)))
        for settings, field, allowed in cases:
            with self.subTest(field=field), self.assertRaises(InvalidValueError) as caught:
                self.solution.set_build_settings(settings)
            self.assertEqual((caught.exception.field, caught.exception.allowed), (field, tuple(allowed)))
        with self.assertRaises(InvalidValueError) as caught:
            self.solution.set_build_settings(SolutionBuildSettings(cmake=cppbuild.CMakeSettings(generator="Unix Makefiles")))
        self.assertEqual((caught.exception.field, caught.exception.allowed), ("generator", cppbuild.GENERATORS))
        with self.assertRaises(InvalidValueError) as caught:
            self.solution.settings.set_msvc_runtime("shared")
        self.assertEqual(caught.exception.allowed, cppbuild.MSVC_RUNTIMES)
        with self.assertRaises(InvalidValueError) as caught:
            self.app.settings.set_system_libraries(["ws2_32.lib;evil"])
        self.assertEqual(caught.exception.field, "system_libraries")
        with self.assertRaises(InvalidValueError) as caught:
            self.app.settings.link_package(CMakePackage("Bad Name", "Bad::Bad", "pkg"))
        self.assertEqual(caught.exception.field, "name")
        with self.assertRaises(InvalidValueError) as caught:
            self.solution.add_project("Bad", "bad name", T.STATIC_LIBRARY)
        self.assertEqual(caught.exception.field, "name")
        with self.assertRaises(InvalidValueError) as caught:
            self.app.settings.link_project(self.lib, T.EXECUTABLE)
        self.assertEqual(caught.exception.field, "link_type")
        self.assertEqual(cppbuild.SWITCHABLE_TYPES, {T.STATIC_LIBRARY, T.SHARED_LIBRARY})

    def test_linking_a_solution_without_a_main_project(self):
        empty = Solution.create(self.root / "Empty", "Empty")
        with self.assertRaises(NoMainProjectError) as caught:
            self.app.settings.link_solution(empty.root / ".cppbuild", T.STATIC_LIBRARY)
        self.assertEqual((caught.exception.solution, caught.exception.project_type), (empty.root, None))
        tool = Solution.create(self.root / "Tool", "Tool")
        tool.add_project("Tool", "Tool", T.EXECUTABLE)
        with self.assertRaises(NoMainProjectError) as caught:
            self.app.settings.link_solution(tool.root / ".cppbuild", T.STATIC_LIBRARY)
        self.assertEqual(caught.exception.project_type, T.STATIC_LIBRARY)
        # A path without a Solution stays a FileNotFoundError.
        with self.assertRaises(FileNotFoundError):
            self.app.settings.link_solution(self.root / "Nowhere/.cppbuild", T.STATIC_LIBRARY)

    # File kinds -----------------------------------------------------------------------

    def test_file_kinds(self):
        from cppbuild import workspace
        workspace.plan(self.solution).write()
        output = self.solution.root / ".cppbuild/output/intermediate/0123456789"
        output.mkdir(parents=True)
        (output / ".cppbuild-output.json").write_text("{}")
        kinds = {
            "CMakeLists.txt": FileKind.GENERATED, "CppBuildTopLevel.cmake": FileKind.GENERATED,
            "App/CMakeLists.txt": FileKind.GENERATED, ".cppbuild/project.json": FileKind.MANAGED,
            "Lib/.cppbuild/project.json": FileKind.MANAGED, ".cppbuild/output": FileKind.OUTPUT,
            ".cppbuild/output/intermediate/0123456789/CMakeCache.txt": FileKind.OUTPUT,
            ".cppbuild/operations/.write.lock": FileKind.OUTPUT, ".cppbuild/.write.lock": FileKind.OUTPUT,
            "App/src/main.cpp": None, "App/.cppbuild/types": FileKind.MANAGED,
        }
        for path, expected in kinds.items():
            with self.subTest(path=path):
                self.assertEqual(self.solution.file_kind(path), expected)
        self.assertEqual(self.solution.file_kind(self.solution.root / "App/CMakeLists.txt"), FileKind.GENERATED)
        # A CMakeLists.txt someone wrote is not CppBuild's.
        (self.solution.root / "notes").mkdir()
        (self.solution.root / "notes/CMakeLists.txt").write_text("project(Mine)\n")
        self.assertIsNone(self.solution.file_kind("notes/CMakeLists.txt"))
        # Every generated file of the plan is GENERATED, every settings file MANAGED.
        for path in workspace.plan(self.solution).documents():
            self.assertEqual(self.solution.file_kind(path), FileKind.GENERATED)
        for project in self.solution.projects():
            for path in project.settings._revision:
                self.assertEqual(self.solution.file_kind(path), FileKind.MANAGED)

    # Templates ------------------------------------------------------------------------

    def test_file_template_from_outside_the_solution(self):
        outside = self.root / "outside/Widget.hpp"
        outside.parent.mkdir()
        outside.write_text("class Widget {};\n")
        self.solution.create_file_template("widget", outside, replacements={"name": "Widget"})
        self.app.add_file("include/Gadget.hpp", template_name="widget", replacements={"name": "Gadget"}, auto_update=False)
        self.assertEqual((self.app.root / "include/Gadget.hpp").read_text(), "class Gadget {};\n")
        self.assertEqual(outside.read_text(), "class Widget {};\n")

    # Transactions ---------------------------------------------------------------------

    def test_transaction_restores_files_when_the_block_raises(self):
        before = self.managed_and_generated()
        with self.assertRaises(RuntimeError):
            with self.solution.transaction() as change:
                self.app.settings.link_project(self.lib, T.STATIC_LIBRARY)
                self.solution.add_project("New", "New", T.STATIC_LIBRARY)
                self.solution.create_file_template("note", self.app.root / "src/main.cpp")
                raise RuntimeError("stop")
        self.assertTrue(change.restored)
        self.assertIsNone(change.update)
        self.assertEqual(self.managed_and_generated(), before)
        self.assertFalse((self.solution.root / "New").exists())
        self.assertFalse((self.solution.root / ".cppbuild/templates").exists())
        self.assertEqual({p.name for p in self.solution.projects()}, {"App", "Lib"})
        self.assertEqual(self.app.settings.get().dependencies, {})
        # Settings objects read the restored files: further changes need no reload.
        self.app.settings.link_project(self.lib, T.STATIC_LIBRARY)

    def test_transaction_restores_files_when_the_update_raises(self):
        before = self.managed_and_generated()
        with self.assertRaisesRegex(SettingsError, "No C\\+\\+ sources"):
            with self.solution.transaction() as change:
                self.solution.add_project("Empty", "Empty", T.STATIC_LIBRARY)
        self.assertTrue(change.restored)
        self.assertEqual(self.managed_and_generated(), before)
        self.assertFalse((self.solution.root / "Empty").exists())

    def test_transaction_leaves_files_changed_by_someone_else(self):
        with self.assertRaises(RuntimeError):
            with self.solution.transaction() as change:
                self.app.settings.set_system_libraries(["ws2_32"])
                # Another writer replaces the file after this transaction wrote it.
                self.app.settings.path.write_bytes(self.app.settings.path.read_bytes() + b"\n")
                raise RuntimeError("stop")
        self.assertEqual(change.conflicts, (self.app.settings.path,))

    def test_transactions_do_not_nest_or_move_projects(self):
        with self.assertRaises(RuntimeError):
            with self.solution.transaction():
                with self.assertRaisesRegex(SettingsError, "nested"):
                    with self.solution.transaction():
                        pass
                with self.assertRaisesRegex(SettingsError, "cannot undo"):
                    self.solution.move_project("Lib", "Moved")
                raise RuntimeError("stop")

    @unittest.skipUnless(REAL, "Real VS2022 required")
    def test_real_transaction_restores_files_when_cmake_fails(self):
        self.assertTrue(self.solution.update().success)
        before = self.managed_and_generated()
        (self.root / "nopkg").mkdir()
        with self.solution.transaction() as change:
            self.app.settings.link_package(CMakePackage("NoSuchPackage", "NoSuchPackage::NoSuchPackage", "../../nopkg"))
        self.assertFalse(change.success)
        self.assertTrue(change.restored)
        self.assertIn("NoSuchPackage", change.update.processes[0].output)
        self.assertIn(self.app.settings.path, change.restored_paths)
        self.assertEqual(self.managed_and_generated(), before)
        self.assertEqual(self.app.settings.get().dependencies, {})
        # The next update configures the restored files again.
        report = self.solution.update()
        self.assertTrue(report.success)
        self.assertFalse(report.processes[0].skipped)
        with self.solution.transaction() as change:
            self.app.settings.link_project(self.lib, T.STATIC_LIBRARY)
        self.assertTrue(change.success, change.update.processes[0].output[-3000:])
        self.assertFalse(change.restored)
        self.assertTrue(self.app.settings.get().dependencies)

    # Skipped configures -----------------------------------------------------------------

    @unittest.skipUnless(REAL, "Real VS2022 required")
    def test_real_update_skips_cmake_when_nothing_changed(self):
        first = self.solution.update()
        self.assertTrue(first.success, first.processes[0].output[-3000:])
        self.assertFalse(first.processes[0].skipped)
        again = self.solution.update()
        self.assertTrue(again.processes[0].skipped)
        self.assertEqual(again.artifacts, first.artifacts)
        project = self.app.update()
        self.assertTrue(project.process.skipped)
        self.assertIsNotNone(project.compiler)
        # Whatever the configure is given or reads configures again.
        changes = (
            ("source added", lambda: self.lib.add_file("src/more.cpp", content="int more() { return 2; }\n", auto_update=False)),
            ("setting saved", lambda: self.app.settings.set_warnings(4)),
            ("build setting", lambda: self.solution.set_build_settings(SolutionBuildSettings(cpp_standard=17))),
            ("generated file edited", lambda: (self.app.root / "CMakeLists.txt").write_bytes(
                (self.app.root / "CMakeLists.txt").read_bytes() + b"# edited\n")),
            ("build tree removed", lambda: next((self.solution.root / ".cppbuild/output").rglob("CMakeCache.txt")).unlink()),
        )
        for name, change in changes:
            with self.subTest(name):
                change()
                report = self.solution.update()
                self.assertTrue(report.success, report.processes[0].output[-3000:])
                self.assertFalse(report.processes[0].skipped)
                self.assertTrue(self.solution.update().processes[0].skipped)
        built = self.solution.build()
        self.assertTrue(built.success)
        self.assertTrue(built.processes[0].skipped)
        self.assertTrue(self.solution.update().processes[0].skipped)
        # A failed configure is never taken as current.
        (self.root / "nopkg").mkdir()
        self.app.settings.link_package(CMakePackage("NoSuchPackage", "NoSuchPackage::NoSuchPackage", "../../nopkg"))
        self.assertFalse(self.solution.update().success)
        self.assertFalse(self.solution.update().processes[0].skipped)


if __name__ == "__main__":
    unittest.main()

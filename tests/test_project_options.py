"""Options a C++ Project sets in Visual Studio's property pages: compiler and linker options,
conditional definitions, warnings, build events, output files, C sources, .def files, the MSVC
runtime, the Windows SDK and C++20 modules."""
import json
import os
from pathlib import Path
import re
import struct
import tempfile
import unittest

from cppbuild import (BuildOption, CMakeSettings, ProjectBuildSettings, ProjectType as T, SettingsError, Solution,
                      SolutionBuildSettings)
from cppbuild import engine, generators, workspace


class SettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cppbuild-options-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.solution = Solution.create(self.root / "Demo", "Demo")
        self.app = self.solution.add_project("App", "App", T.EXECUTABLE)
        self.lib = self.solution.add_project("Lib", "Lib", T.STATIC_LIBRARY)

    def saved(self, project):
        return json.loads(project.settings.path.read_text())["data"]

    def documents(self):
        return workspace.plan(self.solution).documents()

    def test_saved_only_when_set_and_reloaded(self):
        settings = self.app.settings
        for key in ("compile_options", "link_options", "conditional_definitions", "warning_level",
                    "warnings_as_errors", "build_events", "output_files"):
            self.assertNotIn(key, self.saved(self.app))
        settings.set_compile_options(["/permissive-", BuildOption("/arch:AVX2", compiler="MSVC", configuration="Release")])
        settings.set_link_options([BuildOption("/STACK:4194304", compiler="MSVC")])
        settings.set_conditional_definitions([BuildOption("APP_DEBUG", configuration="Debug"), "ALWAYS=1"])
        settings.set_warnings(4, as_errors=True)
        settings.set_build_events(pre_build=[["cmake", "-E", "echo", "before"]], post_build=[["tool", "a b"]])
        settings.set_output_files(["data/config.json", "assets"])
        saved = self.saved(self.app)
        self.assertEqual(saved["compile_options"], [{"value": "/permissive-"},
                                                    {"value": "/arch:AVX2", "compiler": "MSVC", "configuration": "Release"}])
        self.assertEqual(saved["build_events"], {"pre_build": [["cmake", "-E", "echo", "before"]], "post_build": [["tool", "a b"]]})
        self.assertEqual((saved["warning_level"], saved["warnings_as_errors"]), (4, True))
        # Paths are stored relative to the settings file, like every other Project path.
        self.assertEqual(saved["output_files"], ["../data/config.json", "../assets"])
        data = Solution.open(self.solution.root / ".cppbuild").get_project("App").settings.get()
        self.assertEqual(data.compile_options[1], BuildOption("/arch:AVX2", "MSVC", "Release"))
        self.assertEqual(data.conditional_definitions, [BuildOption("APP_DEBUG", None, "Debug"), BuildOption("ALWAYS=1")])
        self.assertEqual(data.output_files, ["data/config.json", "assets"])
        self.assertEqual(data.build_events.post_build, [["tool", "a b"]])
        for reset in (settings.set_compile_options, settings.set_link_options, settings.set_conditional_definitions,
                      settings.set_warnings, settings.set_build_events, settings.set_output_files):
            reset()
        self.assertEqual(set(self.saved(self.app)) & {"compile_options", "warning_level", "build_events", "output_files"}, set())

    def test_invalid_values(self):
        settings = self.app.settings
        for call in (lambda: settings.set_compile_options("/W4"),
                     lambda: settings.set_compile_options(["a;b"]),
                     lambda: settings.set_compile_options(["$<CONFIG:Debug>"]),
                     lambda: settings.set_compile_options(['/D"X"']),
                     lambda: settings.set_compile_options([BuildOption("/O2", configuration="Fast")]),
                     lambda: settings.set_compile_options([BuildOption("/O2", compiler="M S")]),
                     lambda: settings.set_conditional_definitions(["1BAD"]),
                     lambda: settings.set_warnings(5),
                     lambda: settings.set_warnings(True),
                     lambda: settings.set_build_events(post_build=[[]]),
                     lambda: settings.set_build_events(post_build=[["a\nb"]]),
                     lambda: settings.set_output_files(["../outside"]),
                     lambda: settings.set_output_files(["."]),
                     lambda: settings.set_output_files(["a", "a"]),
                     lambda: self.solution.settings.set_msvc_runtime("mt"),
                     lambda: generators.validate(CMakeSettings(windows_sdk="latest"))):
            with self.subTest(), self.assertRaises(SettingsError):
                call()

    def test_generated_content(self):
        settings = self.app.settings
        settings.set_compile_options([BuildOption("/DGT=(2>1)", compiler="MSVC"), BuildOption("-O2", configuration="Release"), "/MP"])
        settings.set_link_options([BuildOption("/STACK:4194304", compiler="MSVC", configuration="Debug")])
        settings.set_conditional_definitions([BuildOption("PAIR=1,2", compiler="MSVC")])
        settings.set_warnings(3)
        settings.set_build_events(pre_link=[["cmake", "-E", "echo", "${NOT_EXPANDED}"]])
        settings.set_output_files(["data"])
        self.lib.settings.set_link_options(["/IGNORED"])
        app = self.documents()[self.app.root / "CMakeLists.txt"]
        self.assertIn('target_compile_options(Demo_App PRIVATE '
                      '"$<$<OR:$<COMPILE_LANG_AND_ID:C,MSVC>,$<COMPILE_LANG_AND_ID:CXX,MSVC>>:/DGT=(2$<ANGLE-R>1)>" '
                      '"$<$<AND:$<COMPILE_LANGUAGE:C,CXX>,$<CONFIG:Release>>:-O2>" "$<$<COMPILE_LANGUAGE:C,CXX>:/MP>")', app)
        self.assertIn('target_link_options(Demo_App PRIVATE "$<$<AND:$<LINK_LANG_AND_ID:CXX,MSVC>,$<CONFIG:Debug>>:/STACK:4194304>")', app)
        self.assertIn('target_compile_definitions(Demo_App PRIVATE "$<$<CXX_COMPILER_ID:MSVC>:PAIR=1$<COMMA>2>")', app)
        self.assertIn('target_compile_options(Demo_App PRIVATE "$<$<COMPILE_LANGUAGE:C,CXX>:/W3>")', app)
        self.assertIn('"$<$<COMPILE_LANGUAGE:C,CXX>:-Wall;-Wextra>"', app)
        self.assertIn('add_custom_command(TARGET Demo_App PRE_LINK\n  COMMAND "cmake" "-E" "echo" [=[${NOT_EXPANDED}]=]', app)
        self.assertIn("add_custom_target(Demo_App_files ${_cppbuild_copy} VERBATIM)\nadd_dependencies(Demo_App Demo_App_files)", app)
        lib = self.documents()[self.lib.root / "CMakeLists.txt"]
        self.assertNotIn("target_link_options(Demo_Lib ", lib)  # a static library has no link step
        self.assertIn("target_link_options(Demo_Lib_shared PRIVATE", lib)
        top = self.documents()[self.solution.root / "CppBuildTopLevel.cmake"]
        self.assertNotIn("CMAKE_MSVC_RUNTIME_LIBRARY", top)
        self.solution.settings.set_msvc_runtime("static")
        self.assertEqual(json.loads(self.solution.settings.path.read_text())["data"]["msvc_runtime"], "static")
        top = self.documents()[self.solution.root / "CppBuildTopLevel.cmake"]
        self.assertIn('set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")', top)
        entry = self.documents()[self.solution.root / "CMakeLists.txt"]
        self.assertIn("project(Demo LANGUAGES C CXX)", entry)
        self.assertIn("_cppbuild_module_scanning()", entry)

    def test_output_files_target_name_is_checked(self):
        self.app.settings.set_output_files(["data"])
        self.solution.add_project("App_files", "App_files", T.EXECUTABLE)
        (self.app.root / "src").mkdir(parents=True)
        (self.app.root / "src/main.cpp").write_text("int main() { return 0; }\n")
        other = self.solution.get_project("App_files")
        (other.root / "src").mkdir(parents=True)
        (other.root / "src/main.cpp").write_text("int main() { return 0; }\n")
        with self.assertRaisesRegex(SettingsError, "Demo_App_files"):
            workspace.plan(self.solution).check(lambda node: "Debug")

    def test_windows_sdk_selection(self):
        vs = generators.Toolchain("Visual Studio 17 2022", "x64", windows_sdk="10.0.22621.0")
        self.assertIn("-DCMAKE_SYSTEM_VERSION=10.0.22621.0", vs.configure("s", "b"))
        plain = generators.Toolchain("Visual Studio 17 2022", "x64")
        self.assertEqual(plain.context, "vs2022-x64")  # existing trees keep their names
        self.assertNotIn("windows_sdk", plain.identity())
        self.assertNotEqual(vs.context, plain.context)

    def test_template_keeps_runtime_and_output_files(self):
        from cppbuild import TemplateTools
        self.solution.settings.set_msvc_runtime("static")
        self.app.settings.set_output_files(["data/config.json"])
        template = TemplateTools.create_solution_template(self.solution, self.root / "Template")
        created = Solution.create(self.root / "New", "New", template=template)
        self.assertEqual(created.settings.get().msvc_runtime, "static")
        self.assertEqual(created.get_project("App").settings.get().output_files, ["data/config.json"])


def pe_header(executable):
    data = Path(executable).read_bytes()
    return data, struct.unpack_from("<I", data, 0x3C)[0] + 4 + 20  # the optional header


def stack_reserve(executable):
    data, optional = pe_header(executable)
    pe32_plus = struct.unpack_from("<H", data, optional)[0] == 0x20B
    return struct.unpack_from("<Q" if pe32_plus else "<I", data, optional + 72)[0]


class _RealOptions:
    generator = None

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cppbuild-options-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def solution(self, name="Demo", **values):
        solution = Solution.create(self.root / name, name)
        solution.set_build_settings(SolutionBuildSettings(cmake=CMakeSettings(generator=self.generator, **values)))
        return solution

    def built(self, project, **values):
        if values:
            project.set_build_settings(ProjectBuildSettings(**values))
        report = project.build()
        self.assertTrue(report.success, "\n".join(p.output[-4000:] for p in report.processes))
        return report

    def executable(self, project, configuration="Debug"):
        build = engine.tree(project.solution, project._resolved_build_settings())[0]
        return engine.main_artifact(build, f"{project.solution.settings.get().name}_{project.name}", configuration)

    def test_options_definitions_events_and_files(self):
        solution = self.solution()
        app = solution.add_project("App", "App", T.EXECUTABLE)
        app.add_file("src/main.cpp", content=(
            "static_assert(GT, \"> survives inside a generator expression\");\n"
            "constexpr int pair[] = {PAIR};\n"
            "static_assert(sizeof(pair) == 2 * sizeof(int), \", survives inside a generator expression\");\n"
            "int main() {\n"
            "#if defined(APP_DEBUG) && !defined(APP_RELEASE)\n  return 10;\n"
            "#elif defined(APP_RELEASE) && !defined(APP_DEBUG)\n  return 20;\n"
            "#else\n  return 1;\n#endif\n}\n"), auto_update=False)
        app.add_file("data/config.txt", content="first\n", auto_update=False)
        app.add_file("assets/a.txt", content="asset\n", auto_update=False)
        app.settings.set_compile_options([BuildOption("/DGT=(2>1)", compiler="MSVC")])
        app.settings.set_conditional_definitions([BuildOption("APP_DEBUG", configuration="Debug"),
                                                  BuildOption("APP_RELEASE", configuration="Release"),
                                                  BuildOption("PAIR=1,2", compiler="MSVC")])
        app.settings.set_link_options([BuildOption("/STACK:4194304", compiler="MSVC")])
        app.settings.set_build_events(pre_build=[["cmake", "-E", "touch", "pre_build.txt"]],
                                      pre_link=[["cmake", "-E", "touch", "pre_link.txt"]],
                                      post_build=[["cmake", "-E", "touch", "post_build.txt"]])
        app.settings.set_output_files(["data/config.txt", "assets"])

        self.built(app)
        self.assertEqual(app.run().processes[-1].returncode, 10)
        executable = self.executable(app)
        self.assertEqual(stack_reserve(executable), 4194304)
        for name in ("pre_build.txt", "pre_link.txt", "post_build.txt"):
            self.assertTrue((app.root / name).is_file(), name)
        self.assertEqual((executable.parent / "data/config.txt").read_text(), "first\n")
        self.assertEqual((executable.parent / "assets/a.txt").read_text(), "asset\n")
        # A changed data file is copied again although no source changed.
        (app.root / "data/config.txt").write_text("second\n")
        self.built(app)
        self.assertEqual((executable.parent / "data/config.txt").read_text(), "second\n")
        self.built(app, configuration="Release")
        self.assertEqual(app.run().processes[-1].returncode, 20)

    def test_warnings(self):
        solution = self.solution()
        strict = solution.add_project("Strict", "Strict", T.EXECUTABLE)
        # C4100 (unreferenced parameter) is a level 4 warning.
        strict.add_file("src/main.cpp", content="int unused(int value) { return 0; }\nint main() { return unused(1); }\n",
                        auto_update=False)
        strict.settings.set_warnings(4, as_errors=True)
        report = strict.build()
        self.assertFalse(report.success)
        self.assertIn("C4100", "\n".join(p.output for p in report.processes))
        strict.settings.set_warnings(3, as_errors=True)
        self.built(strict)
        # Without a level, MSVC warns at Visual Studio's /W3 (C4101 is a level 3 warning), not at /W1.
        plain = solution.add_project("Plain", "Plain", T.EXECUTABLE)
        plain.add_file("src/main.cpp", content="int main() { int unused; return 0; }\n", auto_update=False)
        report = self.built(plain)
        self.assertIn("C4101", "\n".join(p.output for p in report.processes))

    def test_c_sources_pch_and_def_exports(self):
        solution = self.solution()
        mixed = solution.add_project("Mixed", "Mixed", T.STATIC_LIBRARY)
        # "new" is a C++ keyword: this file only compiles as C, and the C++ PCH must not reach it.
        mixed.add_file("src/part.c", content="int c_value(void) { int new = 3; return new; }\n", auto_update=False)
        mixed.add_file("src/part.cpp", content="int cpp_value() { return static_cast<int>(std::vector<int>(4).size()); }\n",
                       auto_update=False)
        mixed.settings.set_pch(system_headers=["vector"])
        exports = solution.add_project("Exports", "Exports", T.SHARED_LIBRARY)
        # No dllexport: the .def file alone makes the DLL export the function and produce an import library.
        exports.add_file("src/exports.cpp", content='extern "C" int def_value() { return 9; }\n', auto_update=False)
        exports.add_file("src/exports.def", content="EXPORTS\n  def_value\n", auto_update=False)
        app = solution.add_project("App", "App", T.EXECUTABLE)
        app.add_file("src/main.cpp", content=(
            'extern "C" int c_value(); int cpp_value(); extern "C" int def_value();\n'
            "int main() { return c_value() + cpp_value() + def_value() == 16 ? 0 : 1; }\n"), auto_update=False)
        app.settings.link_project(mixed, T.STATIC_LIBRARY)
        app.settings.link_project(exports, T.SHARED_LIBRARY)
        self.built(app)
        self.assertEqual(app.run().processes[-1].returncode, 0)

    def test_static_runtime(self):
        solution = self.solution()
        solution.settings.set_msvc_runtime("static")
        app = solution.add_project("App", "App", T.EXECUTABLE)
        app.add_file("src/main.cpp", content="#include <cstdio>\nint main() { std::printf(\"ok\"); return 0; }\n", auto_update=False)
        raw = solution.add_project("Raw", "Raw", T.EXECUTABLE)
        raw.add_file("src/main.cpp", content='extern "C" int AppMain() { return 7; }\n', auto_update=False)
        raw.settings.set_entry_point("AppMain")
        tests = solution.add_project("Tests", "Tests", T.TEST)
        tests.set_build_settings(ProjectBuildSettings(googletest_archive=os.environ.get("CPPBUILD_TEST_GTEST_ARCHIVE")))
        tests.add_file("src/test.cpp", content="#include <gtest/gtest.h>\nTEST(A, B) { EXPECT_EQ(1, 1); }\n", auto_update=False)
        for configuration in ("Debug", "Release"):
            self.built(app, configuration=configuration)
            self.built(raw, configuration=configuration)
            self.assertEqual(raw.run().processes[-1].returncode, 7)
            # The static runtime is linked in: no runtime DLL is imported.
            self.assertNotRegex(self.executable(app, configuration).read_bytes(), re.compile(rb"(?i)vcruntime140"))
        tests.set_build_settings(ProjectBuildSettings(googletest_archive=os.environ.get("CPPBUILD_TEST_GTEST_ARCHIVE")))
        report = tests.test()
        self.assertTrue(report.success, str(report))

    def test_windows_sdk(self):
        installed = sorted(p.name for p in Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                                                  "Windows Kits/10/Include").glob("10.*"))
        if len(installed) < 2:
            self.skipTest("Selecting a Windows SDK needs two installed versions")
        chosen = installed[0]  # not the newest, which is the default
        solution = self.solution(windows_sdk=chosen)
        app = solution.add_project("App", "App", T.EXECUTABLE)
        app.add_file("src/main.cpp", content="#include <windows.h>\nint main() { return 0; }\n", auto_update=False)
        report = self.built(app)
        toolchain = generators.resolve(app._resolved_build_settings())
        if toolchain.visual_studio:
            build = engine.tree(solution, app._resolved_build_settings())[0]
            text = (build / "App/Demo_App.vcxproj").read_text(encoding="utf-8-sig")
            self.assertIn(f"<WindowsTargetPlatformVersion>{chosen}</WindowsTargetPlatformVersion>", text)
        else:
            self.assertIn(chosen, toolchain.env["INCLUDE"])

    def test_imported_dll_is_placed_beside_the_program(self):
        from cppbuild import ImportedLibrary
        provider = self.solution("Provider")
        lib = provider.add_project("Lib", "Lib", T.SHARED_LIBRARY)
        lib.add_file("src/lib.cpp", content='extern "C" __declspec(dllexport) int external_value() { return 5; }\n',
                     auto_update=False)
        self.built(lib)
        build = engine.tree(provider, lib._resolved_build_settings())[0]
        dll = engine.main_artifact(build, "Provider_Lib", "Debug")
        implib = next(p for p in engine.artifacts(build, "Provider_Lib", "Debug") if p.suffix == ".lib")
        solution = self.solution()
        app = solution.add_project("App", "App", T.EXECUTABLE)
        app.add_file("src/main.cpp", content='extern "C" __declspec(dllimport) int external_value();\n'
                     "int main() { return external_value(); }\n", auto_update=False)
        app.settings.link_imported_library(ImportedLibrary(T.SHARED_LIBRARY, {"Debug": str(dll)},
                                                           import_libraries={"Debug": str(implib)}))
        self.built(app)
        # Previously the program failed to start (0xC0000135): nothing put the DLL on its search path.
        self.assertTrue((self.executable(app).parent / "Lib.dll").is_file())
        self.assertEqual(app.run().processes[-1].returncode, 5)

    def test_modules(self):
        solution = self.solution()
        math = solution.add_project("Math", "Math", T.STATIC_LIBRARY)
        math.add_file("src/math.ixx", content="export module math;\nexport int square(int x) { return x * x; }\n", auto_update=False)
        plain = solution.add_project("Plain", "Plain", T.STATIC_LIBRARY)
        plain.add_file("src/plain.cpp", content="int plain() { return 0; }\n", auto_update=False)
        app = solution.add_project("App", "App", T.EXECUTABLE)
        app.add_file("src/main.cpp", content="import math;\nint plain();\nint main() { return square(3) + plain(); }\n",
                     auto_update=False)
        app.settings.link_project(math, T.STATIC_LIBRARY)
        app.settings.link_project(plain, T.STATIC_LIBRARY)
        self.built(app)
        self.assertEqual(app.run().processes[-1].returncode, 9)
        # Only the module and the program importing it are scanned; scanning costs a step per source.
        build = engine.tree(solution, app._resolved_build_settings())[0]
        if generators.resolve(app._resolved_build_settings()).visual_studio:
            def scanned(name):
                text = (build / name / f"Demo_{name}.vcxproj").read_text(encoding="utf-8-sig")
                return "<ScanSourceForModuleDependencies>true<" in text
        else:
            rules = "".join(f.read_text(errors="replace") for f in build.rglob("*.ninja"))
            def scanned(name):
                return re.search(rf"Demo_{name}\.dir[\\/]Debug[\\/]CXX\.dd", rules) is not None
        self.assertEqual({n: scanned(n) for n in ("Math", "App", "Plain")}, {"Math": True, "App": True, "Plain": False})


@unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2022") == "1", "Real VS2022 required")
class VS2022OptionsTests(_RealOptions, unittest.TestCase):
    generator = "Visual Studio 17 2022"


@unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2026") == "1", "Real VS2026 required")
class VS2026OptionsTests(_RealOptions, unittest.TestCase):
    generator = "Visual Studio 18 2026"


@unittest.skipUnless(os.environ.get("CPPBUILD_TEST_NINJA") == "1", "Real Ninja required")
class NinjaOptionsTests(_RealOptions, unittest.TestCase):
    generator = "Ninja Multi-Config"


if __name__ == "__main__":
    unittest.main()

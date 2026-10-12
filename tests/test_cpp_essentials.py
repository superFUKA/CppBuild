"""Settings every C++ program needs: UTF-8 sources, entry point and subsystem, OS libraries,
resources, gMock."""
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest

from cppbuild import (CMakeSettings, ProjectBuildSettings, ProjectType as T, SettingsError, Solution,
                      SolutionBuildSettings)
from cppbuild import engine, workspace


class SettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cppbuild-essentials-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.solution = Solution.create(self.root / "Demo", "Demo")
        self.app = self.solution.add_project("App", "App", T.EXECUTABLE)
        self.lib = self.solution.add_project("Lib", "Lib", T.STATIC_LIBRARY)

    def entry(self, project):
        return workspace.plan(self.solution).documents()[project.root / "CMakeLists.txt"]

    def saved(self, project):
        return json.loads(project.settings.path.read_text())["data"]

    def test_entry_point_saved_only_when_set_and_checked(self):
        settings = self.app.settings
        self.assertNotIn("entry_point", self.saved(self.app))
        settings.set_entry_point("wWinMain")
        self.assertEqual((settings.get().entry_point, settings.get().gui), ("wWinMain", True))
        self.assertEqual((self.saved(self.app)["entry_point"], self.saved(self.app)["gui"]), ("wWinMain", True))
        reopened = Solution.open(self.solution.root / ".cppbuild").get_project("App").settings.get()
        self.assertEqual((reopened.entry_point, reopened.gui), ("wWinMain", True))
        settings.set_entry_point("AppMain", gui=True)
        self.assertEqual((settings.get().entry_point, settings.get().gui), ("AppMain", True))
        settings.set_entry_point("AppMain")
        self.assertFalse(settings.get().gui)  # a custom name is a console program unless gui is given
        settings.set_entry_point()  # main
        self.assertEqual((settings.get().entry_point, settings.get().gui), (None, False))
        self.assertNotIn("entry_point", self.saved(self.app))
        self.assertNotIn("gui", self.saved(self.app))
        for call in (lambda: settings.set_entry_point("WinMain", gui=False),
                     lambda: settings.set_entry_point("main", gui=True),
                     lambda: settings.set_entry_point("2nd"),
                     lambda: settings.set_entry_point("App Main"),
                     lambda: self.lib.settings.set_entry_point("AppMain"),
                     lambda: self.solution.add_project("Tests", "Tests", T.TEST).settings.set_entry_point("wWinMain")):
            with self.subTest(), self.assertRaises(SettingsError):
                call()

    def test_system_libraries_saved_and_checked(self):
        self.assertNotIn("system_libraries", self.saved(self.lib))
        self.lib.settings.set_system_libraries(["ws2_32", "d3d11", "stdc++fs"])
        self.assertEqual(self.saved(self.lib)["system_libraries"], ["ws2_32", "d3d11", "stdc++fs"])
        for libraries in (["ws2_32", "ws2_32"], ["-lfoo"], ["C:/x.lib"], ["a b"], [""], ["$<0:x>"]):
            with self.subTest(libraries=libraries), self.assertRaises(SettingsError):
                self.lib.settings.set_system_libraries(libraries)
        self.lib.settings.set_system_libraries()
        self.assertNotIn("system_libraries", self.saved(self.lib))

    def test_generated_content(self):
        self.app.settings.link_project(self.lib, T.STATIC_LIBRARY)
        self.lib.settings.set_system_libraries(["ws2_32"])
        option = '"$<$<COMPILE_LANG_AND_ID:CXX,MSVC>:/utf-8;/Zc:__cplusplus>"'
        lib = self.entry(self.lib)
        # Consumers read the library's headers too, so the options are part of its usage requirements.
        self.assertIn(f"target_compile_options(Demo_Lib PUBLIC {option})", lib)
        self.assertIn("target_link_libraries(Demo_Lib PUBLIC ws2_32)", lib)
        app = self.entry(self.app)
        self.assertIn(f"target_compile_options(Demo_App PRIVATE {option})", app)
        self.assertNotIn("WIN32_EXECUTABLE", app)
        self.assertNotIn("/ENTRY", app)
        self.app.settings.set_entry_point("wWinMain")
        app = self.entry(self.app)
        self.assertIn("set_target_properties(Demo_App PROPERTIES WIN32_EXECUTABLE ON)", app)
        self.assertNotIn("/ENTRY", app)  # the toolchain finds the standard entry functions itself
        self.app.settings.set_entry_point("AppMain")
        app = self.entry(self.app)
        self.assertIn('target_link_options(Demo_App PRIVATE "/ENTRY:AppMain" "/SUBSYSTEM:CONSOLE")', app)
        self.assertIn("only MSVC-compatible linkers support", app)
        self.assertNotIn("WIN32_EXECUTABLE", app)
        tests = self.solution.add_project("Tests", "Tests", T.TEST)
        text = self.entry(tests)
        self.assertIn("set(BUILD_GMOCK ON)", text)
        self.assertIn("target_link_libraries(Demo_Tests PRIVATE GTest::gtest_main GTest::gmock)", text)


def subsystem(executable):
    """IMAGE_SUBSYSTEM of a PE file: 2 is a GUI program, 3 a console one."""
    data = Path(executable).read_bytes()
    header = struct.unpack_from("<I", data, 0x3C)[0]
    return struct.unpack_from("<H", data, header + 4 + 20 + 68)[0]


class _RealEssentials:
    generator = None
    architecture = None

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cppbuild-essentials-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def solution(self):
        solution = Solution.create(self.root / "Demo", "Demo")
        solution.set_build_settings(SolutionBuildSettings(
            architecture=self.architecture, cmake=CMakeSettings(generator=self.generator)))
        return solution

    def executable(self, project):
        build = engine.tree(project.solution, project._resolved_build_settings())[0]
        return engine.main_artifact(build, f"Demo_{project.name}", "Debug")

    def built(self, project):
        report = project.build()
        self.assertTrue(report.success, "\n".join(p.output[-3000:] for p in report.processes))

    def test_program_essentials(self):
        solution = self.solution()
        # Japanese text in a UTF-8 source, the real __cplusplus, a resource and an OS library.
        net = solution.add_project("Net", "Net", T.STATIC_LIBRARY)
        net.add_file("include/net.hpp", content="#pragma once\n// 日本語のコメント\nconst char* label();\nint start();\n", auto_update=False)
        net.add_file("src/net.cpp", content=(
            '#include <winsock2.h>\n#include "net.hpp"\nconst char* label() { return "表示"; }\n'
            "int start() { WSADATA data; int result = WSAStartup(MAKEWORD(2, 2), &data); WSACleanup(); return result; }\n"),
            auto_update=False)
        net.settings.set_system_libraries(["ws2_32"])
        app = solution.add_project("App", "App", T.EXECUTABLE)
        app.add_file("src/main.cpp", content=(
            "#include <windows.h>\n#include <cstring>\n#include <cwchar>\n#include \"net.hpp\"\n"
            "static_assert(__cplusplus == 202002L, \"__cplusplus reports the selected standard\");\n"
            "int main() {\n"
            "  wchar_t text[32] = {};\n"
            "  LoadStringW(GetModuleHandleW(nullptr), 1, text, 32);\n"
            "  if (std::strlen(label()) != 6) return 1;  // UTF-8 bytes of the two characters\n"
            "  if (std::wcscmp(text, L\"resource\") != 0) return 2;\n"
            "  return start() == 0 ? 0 : 3;\n"
            "}\n"), auto_update=False)
        app.add_file("src/app.rc", content='STRINGTABLE\nBEGIN\n  1 "resource"\nEND\n', auto_update=False)
        app.settings.link_project(net, T.STATIC_LIBRARY)
        # Entry points: a GUI program, and named functions linked with /ENTRY.
        gui = solution.add_project("Gui", "Gui", T.EXECUTABLE)
        gui.add_file("src/main.cpp", content="#include <windows.h>\nint WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) { return 0; }\n",
                     auto_update=False)
        gui.settings.set_entry_point("wWinMain")
        raw = solution.add_project("Raw", "Raw", T.EXECUTABLE)
        raw.add_file("src/main.cpp", content='extern "C" int AppMain() { return 7; }\n', auto_update=False)
        raw.settings.set_entry_point("AppMain")
        raw_gui = solution.add_project("RawGui", "RawGui", T.EXECUTABLE)
        raw_gui.add_file("src/main.cpp", content='extern "C" int AppMain() { return 0; }\n', auto_update=False)
        raw_gui.settings.set_entry_point("AppMain", gui=True)

        for project in (app, gui, raw, raw_gui):
            self.built(project)
        self.assertEqual(app.run().processes[-1].returncode, 0)
        self.assertEqual(raw.run().processes[-1].returncode, 7)
        self.assertEqual({p.name: subsystem(self.executable(p)) for p in (app, gui, raw, raw_gui)},
                         {"App": 3, "Gui": 2, "Raw": 3, "RawGui": 2})
        # /ENTRY links the release runtime libraries in Release.
        raw.set_build_settings(ProjectBuildSettings(configuration="Release"))
        self.built(raw)
        self.assertEqual(raw.run().processes[-1].returncode, 7)

    def test_gmock(self):
        solution = self.solution()
        tests = solution.add_project("Tests", "Tests", T.TEST)
        tests.set_build_settings(ProjectBuildSettings(googletest_archive=os.environ.get("CPPBUILD_TEST_GTEST_ARCHIVE")))
        tests.add_file("src/test.cpp", content=(
            "#include <gmock/gmock.h>\n#include <gtest/gtest.h>\n"
            "struct Clock { virtual ~Clock() = default; virtual int now() = 0; };\n"
            "struct MockClock : Clock { MOCK_METHOD(int, now, (), (override)); };\n"
            "TEST(Mock, Returns) { MockClock clock; EXPECT_CALL(clock, now()).WillOnce(testing::Return(5)); EXPECT_EQ(clock.now(), 5); }\n"),
            auto_update=False)
        report = tests.test()
        self.assertTrue(report.success, str(report))


@unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2022") == "1", "Real VS2022 required")
class VS2022EssentialsTests(_RealEssentials, unittest.TestCase):
    generator = "Visual Studio 17 2022"


@unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2022") == "1", "Real VS2022 required")
class VS2022Win32EssentialsTests(_RealEssentials, unittest.TestCase):
    # 32-bit names are decorated; /ENTRY must still find the entry function.
    generator = "Visual Studio 17 2022"
    architecture = "Win32"


@unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2026") == "1", "Real VS2026 required")
class VS2026EssentialsTests(_RealEssentials, unittest.TestCase):
    generator = "Visual Studio 18 2026"


@unittest.skipUnless(os.environ.get("CPPBUILD_TEST_NINJA") == "1", "Real Ninja required")
class NinjaEssentialsTests(_RealEssentials, unittest.TestCase):
    generator = "Ninja Multi-Config"


if __name__ == "__main__":
    unittest.main()

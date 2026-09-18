"""M3a feasibility probe only; does not implement or select a product integration policy."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET


@unittest.skipUnless(os.environ.get("CPPBUILD_TEST_VS2022") == "1", "Real VS2022 probe requires opt in")
class IntegrationCandidateTests(unittest.TestCase):
    def test_external_projects_dependency_and_ownership(self):
        with tempfile.TemporaryDirectory(prefix="cppbuild-integration-") as directory:
            root = Path(directory)

            def write(relative, text):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
                return path

            def execute(*arguments):
                result = subprocess.run([str(a) for a in arguments], cwd=root,
                                        text=True, errors="replace", stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, env=dict(os.environ))
                self.assertEqual(result.returncode, 0, result.stdout)
                return result

            def configure(name):
                execute("cmake", "-S", root / "source" / name, "-B", root / "build" / name,
                        "-G", "Visual Studio 17 2022", "-A", "x64")

            vswhere = Path(os.environ["ProgramFiles(x86)"]) / "Microsoft Visual Studio/Installer/vswhere.exe"
            installation = execute(vswhere, "-latest", "-products", "*", "-version", "[17.0,18.0)",
                                   "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                                   "-property", "installationPath").stdout.strip()
            msbuild = Path(installation) / "MSBuild/Current/Bin/MSBuild.exe"

            def build_solution(config):
                execute("cmake", "--build", root / "build/Whole", "--config", config,
                        "--target", "cppbuild_App")

            common = 'cmake_minimum_required(VERSION 3.24)\nset(CMAKE_SUPPRESS_REGENERATION ON)\n'
            write("source/Math/math.cpp", "int value() { return 42; }\n")
            write("source/Math/CMakeLists.txt", common + 'project(Math LANGUAGES CXX)\nadd_library(Math STATIC math.cpp)\n')
            write("source/Tool/main.cpp", "int main() { return 0; }\n")
            write("source/Tool/CMakeLists.txt", common + 'project(Tool LANGUAGES CXX)\nadd_executable(Tool main.cpp)\n')
            write("source/App/main.cpp", "int value(); int main() { return value() == 42 ? 0 : 1; }\n")
            # A fixed fixture output layout is intentional here. Product code must
            # derive and validate the dependency artifacts from generated metadata.
            app_cmake = common + 'project(App LANGUAGES CXX)\nadd_executable(App main.cpp)\nadd_library(MathImported STATIC IMPORTED)\n'
            for config in ("Debug", "Release"):
                path = (root / "build/Math" / config / "Math.lib").as_posix()
                app_cmake += f'set_property(TARGET MathImported PROPERTY IMPORTED_LOCATION_{config.upper()} "{path}")\n'
            app_cmake += 'set_property(TARGET MathImported PROPERTY IMPORTED_CONFIGURATIONS "Debug;Release")\n'
            app_cmake += 'set_property(TARGET MathImported PROPERTY MAP_IMPORTED_CONFIG_RELWITHDEBINFO Release)\n'
            app_cmake += 'set_property(TARGET MathImported PROPERTY MAP_IMPORTED_CONFIG_MINSIZEREL Release)\n'
            app_cmake += 'target_link_libraries(App PRIVATE MathImported)\n'
            write("source/App/CMakeLists.txt", app_cmake)
            for name in ("Math", "App", "Tool"):
                configure(name)

            solution_cmake = common + 'project(Whole LANGUAGES NONE)\n'
            project_paths = {}
            for name in ("Math", "App", "Tool"):
                path = root / "build" / name / f"{name}.vcxproj"
                project_paths[name] = path
                tree = ET.parse(path)
                guid = tree.find(".//{*}ProjectGuid").text.strip("{}")
                solution_cmake += f'include_external_msproject({name} "{path.as_posix()}" GUID "{guid}")\n'
            solution_cmake += 'add_dependencies(App Math)\n'
            # A generated CMake orchestration target can preserve the public
            # cmake --build entry while invoking solution-aware MSBuild inside it.
            solution_path = (root / "build/Whole/Whole.sln").as_posix()
            solution_cmake += (f'add_custom_target(cppbuild_App COMMAND "{msbuild.as_posix()}" '
                               f'"{solution_path}" /t:App "/p:Configuration=$<CONFIG>" '
                               '/p:Platform=x64 /m /verbosity:minimal VERBATIM)\n')
            write("source/Whole/CMakeLists.txt", solution_cmake)
            configure("Whole")
            sln = root / "build/Whole/Whole.sln"
            before_sln = sln.read_bytes()
            text = sln.read_text(encoding="utf-8-sig")
            for name in ("Math", "App", "Tool"):
                self.assertIn(f"{name}.vcxproj", text)
            # CMake --target App invokes the external vcxproj directly. The
            # dependency recorded only in Whole.sln is not honored that way.
            direct = subprocess.run(["cmake", "--build", str(root / "build/Whole"),
                                     "--config", "Debug", "--target", "App"],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            self.assertNotEqual(direct.returncode, 0)
            self.assertFalse((root / "build/Math/Debug/Math.lib").exists())
            for config in ("Debug", "Release"):
                build_solution(config)
                execute(root / "build/App" / config / "App.exe")
                self.assertTrue((root / "build/Math" / config / "Math.lib").is_file())
                self.assertFalse((root / "build/Tool" / config / "Tool.exe").exists())
                self.assertFalse(list((root / "build/Whole").rglob("*.exe")))

            execute("cmake", "--build", root / "build/Tool", "--config", "Debug", "--target", "Tool")
            protected = [root / "build/Tool/Debug/Tool.exe", root / "build/Math/Debug/Math.lib",
                         root / "build/App/Release/App.exe"]
            bytes_before = {p: p.read_bytes() for p in protected}
            tool_project_before = project_paths["Tool"].read_bytes()
            write("source/App/helper.cpp", "int helper() { return 17; }\n")
            write("source/App/main.cpp", "int value(); int helper(); int main() { return value() + helper() == 59 ? 0 : 1; }\n")
            write("source/App/CMakeLists.txt", app_cmake + 'target_sources(App PRIVATE helper.cpp)\n')
            configure("App")
            self.assertEqual(sln.read_bytes(), before_sln)
            self.assertEqual(project_paths["Tool"].read_bytes(), tool_project_before)
            build_solution("Debug")
            execute(root / "build/App/Debug/App.exe")
            execute("cmake", "--build", root / "build/App", "--config", "Debug", "--target", "clean")
            # This is also the artifact built through Whole: ownership is shared.
            self.assertFalse((root / "build/App/Debug/App.exe").exists())
            for path, content in bytes_before.items():
                self.assertEqual(path.read_bytes(), content)
            build_solution("Debug")
            execute(root / "build/App/Debug/App.exe")

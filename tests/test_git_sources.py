"""Linking Solutions by git URL: recorded commits, indirect sources, and fetching without CppBuild."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from cppbuild import (CMakeSettings, GitFetchError, GitSourceConflictError, ProjectType as T, SettingsError, Solution,
                      SolutionBuildSettings, TemplateTools)
from cppbuild import generators, git_sources, workspace

GIT = shutil.which("git")
IGNORE = "deps/\n.cppbuild/output/\n.cppbuild/operations/\n"


def git(*args, cwd=None):
    result = subprocess.run(["git", "-c", "user.name=CppBuild", "-c", "user.email=cppbuild@example.invalid",
                             "-c", "init.defaultBranch=main", "-c", "core.autocrlf=false", *args],
                            cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if result.returncode != 0:
        raise AssertionError(f"git {args}: {result.stdout}")
    return result.stdout.strip()


def library(root, name, value, *, solution_dir=None, deps=False):
    """A repository holding a CppBuild Solution whose main Project is a static library <name>Lib."""
    solution_root = root / (solution_dir or "")
    solution = Solution.create(solution_root, name)
    if deps:
        values = solution.settings.get()
        values.dependency_directories = ["deps"]
        solution.settings.save(values)
    lib = solution.add_project(f"{name}Lib", f"{name}Lib", T.STATIC_LIBRARY)
    lib.add_file(f"src/{name}.cpp", content=f"int {name.lower()}() {{ return {value}; }}\n", auto_update=False)
    return solution, lib


def commit(root, message="update"):
    (root / ".gitignore").write_text(IGNORE)
    if not (root / ".git").exists():
        git("init", "-q", str(root))
    git("add", "-A", cwd=root)
    git("commit", "-q", "-m", message, cwd=root)
    return git("rev-parse", "HEAD", cwd=root)


def publish(solution, root, message="update"):
    """Commit the Solution with its generated CMake files, as a repository using CppBuild does."""
    workspace.plan(solution).write()
    return commit(root, message)


def url(root):
    return root.as_uri()


@unittest.skipUnless(GIT, "git required")
class GitSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cppbuild-git-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.app_solution = Solution.create(self.root / "App", "App")
        values = self.app_solution.settings.get()
        values.dependency_directories = ["deps"]
        self.app_solution.settings.save(values)
        self.app = self.app_solution.add_project("App", "App", T.EXECUTABLE)
        self.app.add_file("src/main.cpp", content="int main() { return 0; }\n", auto_update=False)

    def test_link_records_the_commit_and_resolves_the_clone(self):
        stl, _ = library(self.root / "repos/STL", "STL", 1)
        first = publish(stl, self.root / "repos/STL")
        report = self.app.settings.link_git(url(self.root / "repos/STL"), link_type=T.STATIC_LIBRARY)
        self.assertEqual(report.target, "STLLib")
        record = self.app_solution.settings.get().git_sources["STL"]
        self.assertEqual((record.revision, record.path), (first, ".cppbuild"))
        clone = self.app_solution.root / "deps/STL"
        self.assertEqual(git("rev-parse", "HEAD", cwd=clone), first)
        plan = workspace.plan(self.app_solution)
        self.assertEqual({p.name for p in plan.projects.values()}, {"App", "STLLib"})
        top = plan.documents()[self.app_solution.root / "CppBuildTopLevel.cmake"]
        self.assertIn(f'_cppbuild_git_source("deps/STL" "{url(self.root / "repos/STL")}" {first} ".cppbuild")', top)
        self.assertIn('option(CPPBUILD_FETCH', top)
        # A newer commit upstream: the record and the clone stay until set_git_source is asked.
        (stl.get_project("STLLib").root / "src/extra.cpp").write_text("int extra() { return 2; }\n")
        second = commit(self.root / "repos/STL")
        self.assertEqual(self.app_solution.git_sources()[0].revision, first)
        status = self.app_solution.set_git_source(url(self.root / "repos/STL"))
        self.assertEqual(status.revision, second)
        self.assertEqual(git("rev-parse", "HEAD", cwd=clone), first)
        warnings = self.app_solution.fetch_git_sources().warnings
        self.assertEqual(len(warnings), 1)
        self.assertIn(f"deps/STL is at {first}, but {second} is recorded", warnings[0])
        # A tag or branch is recorded as its commit.
        git("tag", "v1", first, cwd=self.root / "repos/STL")
        self.assertEqual(self.app_solution.set_git_source(url(self.root / "repos/STL"), "v1").revision, first)
        self.assertEqual(self.app_solution.set_git_source(url(self.root / "repos/STL"), "main").revision, second)
        # A Solution made from a template keeps the record and clones on its first operation.
        template = TemplateTools.create_solution_template(self.app_solution, self.root / "Template")
        made = Solution.create(self.root / "Made", "Made", template=template)
        self.assertEqual(made.settings.get().git_sources, self.app_solution.settings.get().git_sources)
        self.assertFalse((made.root / "deps/STL").exists())
        self.assertEqual(made.fetch_git_sources().fetched, ("STL",))
        self.assertEqual(git("rev-parse", "HEAD", cwd=made.root / "deps/STL"), second)

    def test_indirect_sources_subdirectories_and_conflicts(self):
        core, _ = library(self.root / "repos/Mono", "Core", 1, solution_dir="libs/Core")
        core_first = publish(core, self.root / "repos/Mono")
        (core.get_project("CoreLib").root / "src/more.cpp").write_text("int more() { return 3; }\n")
        core_second = commit(self.root / "repos/Mono")
        # STL and Net both link Core from the same repository, at different commits.
        stl, stl_lib = library(self.root / "repos/STL", "STL", 2, deps=True)
        stl_lib.settings.link_git(url(self.root / "repos/Mono"), core_first, "libs/Core/.cppbuild", link_type=T.STATIC_LIBRARY)
        publish(stl, self.root / "repos/STL")
        net, net_lib = library(self.root / "repos/Net", "Net", 3, deps=True)
        net_lib.settings.link_git(url(self.root / "repos/Mono"), core_second, "libs/Core/.cppbuild", link_type=T.STATIC_LIBRARY)
        publish(net, self.root / "repos/Net")

        self.app.settings.link_git(url(self.root / "repos/STL"), link_type=T.STATIC_LIBRARY)
        sources = {s.name: s for s in self.app_solution.git_sources()}
        self.assertEqual(set(sources), {"STL", "Mono"})
        self.assertEqual(sources["Mono"].recorded_by, (self.app_solution.root / "deps/STL").resolve())
        self.assertTrue((self.app_solution.root / "deps/Mono/libs/Core/.cppbuild/project.json").is_file())
        plan = workspace.plan(self.app_solution)
        self.assertEqual({p.name for p in plan.projects.values()}, {"App", "STLLib", "CoreLib"})
        core_root = next(p for p in plan.projects.values() if p.name == "CoreLib").solution.root
        self.assertEqual(core_root, (self.app_solution.root / "deps/Mono/libs/Core").resolve())
        top = plan.documents()[self.app_solution.root / "CppBuildTopLevel.cmake"]
        self.assertIn(f'"deps/Mono" "{url(self.root / "repos/Mono")}" {core_first} "libs/Core/.cppbuild"', top)

        # Net records Core differently, and App records nothing for it: an error before saving.
        before = self.app_solution.settings.get()
        with self.assertRaises(GitSourceConflictError) as raised:
            self.app.settings.link_git(url(self.root / "repos/Net"), link_type=T.STATIC_LIBRARY)
        self.assertIn(core_first, str(raised.exception))
        self.assertIn(core_second, str(raised.exception))
        self.assertEqual(self.app_solution.settings.get(), before)
        # Recording it at the top level chooses; the clone already there is kept as it is.
        self.app_solution.set_git_source(url(self.root / "repos/Mono"), core_second, "libs/Core/.cppbuild")
        self.app.settings.link_git(url(self.root / "repos/Net"), link_type=T.STATIC_LIBRARY)
        mono = next(s for s in self.app_solution.git_sources() if s.name == "Mono")
        self.assertEqual((mono.revision, mono.recorded_by), (core_second, None))
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.app_solution.root / "deps/Mono"), core_first)
        with self.assertRaisesRegex(SettingsError, "unlink"):
            self.app_solution.remove_git_source("STL")

    def test_failures_and_existing_places(self):
        with self.assertRaisesRegex(SettingsError, "credentials"):
            self.app.settings.link_git("https://user:secret@example.invalid/STL.git", link_type=T.STATIC_LIBRARY)
        with self.assertRaises(GitFetchError) as raised:
            self.app.settings.link_git(url(self.root / "missing/STL"), link_type=T.STATIC_LIBRARY)
        message = str(raised.exception)
        self.assertIn("deps/STL", message)
        self.assertIn("git clone", message)
        self.assertEqual(self.app_solution.settings.get().git_sources, {})
        # Someone's own copy in the place is used and never switched.
        stl, _ = library(self.root / "repos/STL", "STL", 1)
        first = publish(stl, self.root / "repos/STL")
        shutil.copytree(self.root / "repos/STL", self.app_solution.root / "deps/STL")
        (stl.get_project("STLLib").root / "src/extra.cpp").write_text("int extra() { return 2; }\n")
        second = commit(self.root / "repos/STL")
        self.app.settings.link_git(url(self.root / "repos/STL"), first, link_type=T.STATIC_LIBRARY)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.app_solution.root / "deps/STL"), first)
        self.assertNotEqual(first, second)
        # A record whose place is missing is cloned on the next operation, or reported when fetching is off.
        shutil.rmtree(self.app_solution.root / "deps/STL", onerror=lambda f, p, _: (os.chmod(p, 0o700), f(p)))
        self.app_solution.set_build_settings(SolutionBuildSettings(fetch_git=False))
        self.assertFalse(self.app_solution.git_sources()[0].present)
        self.app_solution.set_build_settings(SolutionBuildSettings())
        report = self.app_solution.fetch_git_sources()
        self.assertEqual(report.fetched, ("STL",))
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.app_solution.root / "deps/STL"), first)


class PlainCMakeScenario:
    """Clone only the top repository and run plain cmake: linked repositories are cloned while configuring."""
    generator = None

    def test_clone_and_configure_without_cppbuild(self):
        work = Path(__file__).resolve().parents[1] / ".test-work"
        work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="git-plain-", dir=work) as temporary:
            root = Path(temporary)
            core, _ = library(root / "repos/Core", "Core", 40)
            publish(core, root / "repos/Core")
            stl, stl_lib = library(root / "repos/STL", "STL", 2, deps=True)
            stl_lib.settings.link_git(url(root / "repos/Core"), link_type=T.STATIC_LIBRARY)
            (stl_lib.root / "src/STL.cpp").write_text("int core(); int stl() { return core() + 2; }\n")
            publish(stl, root / "repos/STL")
            app_solution = Solution.create(root / "repos/App", "App")
            values = app_solution.settings.get()
            values.dependency_directories = ["deps"]
            app_solution.settings.save(values)
            app = app_solution.add_project("App", "App", T.EXECUTABLE)
            app.add_file("src/main.cpp", content='#include <cstdio>\nint stl();\nint main() { std::printf("%d", stl()); }\n',
                         auto_update=False)
            app.settings.link_git(url(root / "repos/STL"), link_type=T.STATIC_LIBRARY)
            settings = SolutionBuildSettings(cmake=CMakeSettings(generator=self.generator))
            app_solution.set_build_settings(settings)
            report = app.run()
            self.assertTrue(report.success, "\n".join(p.output[-3000:] for p in report.processes))
            self.assertEqual(report.processes[-1].output.strip(), "42")
            publish(app_solution, root / "repos/App")

            toolchain = generators.resolve(settings)

            def cmake(*args, ok=True):
                result = subprocess.run(["cmake", *map(str, args)], env=toolchain.env, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, errors="replace")
                self.assertEqual(result.returncode == 0, ok, result.stdout[-4000:])
                return result.stdout

            checkout = root / "checkout"
            git("clone", "-q", url(root / "repos/App"), str(checkout))
            self.assertFalse((checkout / "deps").exists())
            # Without fetching, the missing places are listed with how to place them by hand.
            output = cmake(*toolchain.configure(checkout, root / "off")[1:], "-DCPPBUILD_FETCH=OFF", ok=False)
            self.assertIn("deps/STL", output)
            self.assertIn("deps/Core", output)
            self.assertIn("git clone", output)
            self.assertFalse((checkout / "deps").exists())
            cmake(*toolchain.configure(checkout, root / "build")[1:])
            self.assertTrue((checkout / "deps/Core/.cppbuild/project.json").is_file())
            cmake("--build", root / "build", "--config", "Debug")
            executable = root / "build/bin/Debug" / ("App.exe" if os.name == "nt" else "App")
            self.assertEqual(subprocess.run([str(executable)], stdout=subprocess.PIPE, text=True).stdout.strip(), "42")
            # A newer record than the clone in place: a warning, and the clone is left as it is.
            head = git("rev-parse", "HEAD", cwd=checkout / "deps/STL")
            text = (checkout / "CppBuildTopLevel.cmake").read_text()
            (checkout / "CppBuildTopLevel.cmake").write_text(text.replace(head, "0" * 40))
            output = " ".join(cmake(*toolchain.configure(checkout, root / "build")[1:]).split())  # CMake wraps warnings
            self.assertIn(f"deps/STL is at {head}, but {'0' * 40} is recorded", output)
            self.assertIn("it is left unchanged", output)
            self.assertEqual(git("rev-parse", "HEAD", cwd=checkout / "deps/STL"), head)
            # Files from an incompatible CppBuild are reported.
            entry = checkout / "deps/STL/CMakeLists.txt"
            entry.write_text(entry.read_text().replace("PROPERTY CPPBUILD_FORMAT 1", "PROPERTY CPPBUILD_FORMAT 0"))
            self.assertIn("incompatible CppBuild", cmake(*toolchain.configure(checkout, root / "build")[1:], ok=False))


@unittest.skipUnless(GIT and os.environ.get("CPPBUILD_TEST_VS2022") == "1", "Real VS2022 and git required")
class PlainCMakeVS2022Tests(PlainCMakeScenario, unittest.TestCase):
    generator = "Visual Studio 17 2022"


@unittest.skipUnless(GIT and os.environ.get("CPPBUILD_TEST_VS2026") == "1", "Real VS2026 and git required")
class PlainCMakeVS2026Tests(PlainCMakeScenario, unittest.TestCase):
    generator = "Visual Studio 18 2026"


@unittest.skipUnless(GIT and os.environ.get("CPPBUILD_TEST_NINJA") == "1", "Real Ninja and git required")
class PlainCMakeNinjaTests(PlainCMakeScenario, unittest.TestCase):
    generator = "Ninja Multi-Config"


if __name__ == "__main__":
    unittest.main()

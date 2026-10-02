"""One shared build tree per Solution: one operation lock, and state that follows the tree."""
from pathlib import Path
import tempfile
import unittest

from cppbuild import (CMakeSettings, ProjectBuildSettings, ProjectType as T, SettingsConflictError, Solution,
                      SolutionBuildSettings)
from cppbuild import engine, information, storage, workspace


class TreeStateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cppbuild-tree-")
        self.addCleanup(temporary.cleanup)
        self.solution = Solution.create(Path(temporary.name) / "S", "S")
        self.lib = self.solution.add_project("Lib", "Lib", T.STATIC_LIBRARY)
        self.lib.add_file("src/lib.cpp", content="int lib() { return 1; }\n", auto_update=False)
        self.app = self.solution.add_project("App", "App", T.EXECUTABLE)
        self.app.add_file("src/main.cpp", content="int lib(); int main() { return lib() - 1; }\n", auto_update=False)
        self.app.settings.link_project(self.lib, T.STATIC_LIBRARY)

    def states(self):
        return {p.name: (p.generation_state, p.build_state) for p in self.solution.info().projects}

    def test_individual_and_whole_clean_share_the_solution_lock(self):
        with storage.write_lock(self.solution.root / ".cppbuild/operations"):
            with self.assertRaises(SettingsConflictError):
                self.app.clean()
        with storage.write_lock(self.app.root / ".cppbuild/operations"):
            with self.assertRaises(SettingsConflictError):
                self.solution.clean()
        self.assertTrue(self.app.clean().success)

    def test_another_build_tree_makes_the_state_stale(self):
        for project in (self.lib, self.app):
            settings = project._resolved_build_settings()
            information.generated(project, settings, (), True)
            information.built(project, settings, (), True)
        self.assertEqual(self.states(), {"Lib": ("current", "current"), "App": ("current", "current")})
        self.solution.set_build_settings(SolutionBuildSettings(intermediate_directory="output/elsewhere"))
        self.assertEqual(self.states(), {"Lib": ("stale", "stale"), "App": ("stale", "stale")})

    def test_individual_generation_records_only_projects_of_its_tree(self):
        tool = self.solution.add_project("Tool", "Tool", T.EXECUTABLE)
        tool.add_file("src/main.cpp", content="int main() { return 0; }\n", auto_update=False)
        tool.set_build_settings(ProjectBuildSettings(cmake=CMakeSettings(generator="Ninja Multi-Config")))
        plan = workspace.plan(self.solution)
        # Tool's operation configures the whole Solution in Tool's tree, not in the others' own tree.
        engine.generated(plan, True, tool._resolved_build_settings())
        self.assertEqual({name: state[0] for name, state in self.states().items()},
                         {"Lib": "unknown", "App": "unknown", "Tool": "current"})
        # A whole-Solution operation requires matching settings and records every member.
        engine.generated(plan, True)
        self.assertEqual(self.states()["Lib"], ("current", "unknown"))


if __name__ == "__main__":
    unittest.main()

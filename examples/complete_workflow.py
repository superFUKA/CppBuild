"""python -m examples.complete_workflow NEW_DIRECTORY [GOOGLETEST_1_14_ZIP]."""
from pathlib import Path
import sys

from cppbuild import ProjectBuildSettings, ProjectType, Solution, SolutionBuildSettings, TemplateTools


def require(report):
    if not report.success:
        raise RuntimeError(str(report))


def main():
    destination = Path(sys.argv[1]).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    archive = str(Path(sys.argv[2]).resolve()) if len(sys.argv) > 2 else None
    solution = Solution.create(destination / "Original", "Workflow")
    library = solution.add_project("Library", "Library", ProjectType.INTERFACE_LIBRARY)
    tests = solution.add_project("Tests", "Tests", ProjectType.TEST)
    tests.settings.link_project(library, ProjectType.INTERFACE_LIBRARY)
    tests.set_build_settings(ProjectBuildSettings(googletest_archive=archive))
    solution.set_build_settings(SolutionBuildSettings(test_projects=["Tests"]))
    library.add_file("include/material.hpp", content="#pragma once\ninline int Example() { return 42; }\n", auto_update=False)
    solution.create_file_template("function", library.root / "include/material.hpp", replacements={"function_name": "Example"})

    def add_test(event):
        if event.project is library and any(path.name == "answer.hpp" for path in event.changed_paths):
            tests.add_file("src/answer_test.cpp", content='#include <gtest/gtest.h>\n#include <answer.hpp>\nTEST(Answer, Value) { EXPECT_EQ(answer(), 42); }\n')

    registration = solution.on("file_changed", add_test)
    require(library.add_file("include/answer.hpp", template_name="function", replacements={"function_name": "answer"}))
    solution.off(registration)
    require(solution.check_environment())
    require(solution.build())
    require(solution.test())
    template = TemplateTools.create_solution_template(solution, destination / "Template")
    clone = Solution.create(destination / "Restored", "Restored", template=template)
    clone.get_project("Tests").set_build_settings(ProjectBuildSettings(googletest_archive=archive))
    report = clone.test()
    require(report)
    for case in report.cases:
        print(case.name, case.status)
    for project in clone.info().projects:
        print(project.name, project.generation_state, project.build_state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Run with: python -m examples.google_test DESTINATION [GOOGLETEST_1_14_ZIP]."""
from pathlib import Path
import sys

from cppbuild import ProjectBuildSettings, ProjectType, Solution, SolutionBuildSettings


def main():
    solution = Solution.create(sys.argv[1], "TestExample")
    tests = solution.add_project("Tests", "Tests", ProjectType.TEST)
    archive = str(Path(sys.argv[2]).resolve()) if len(sys.argv) > 2 else None
    tests.set_build_settings(ProjectBuildSettings(googletest_archive=archive))
    tests.add_file("src/smoke.cpp", content='''#include <gtest/gtest.h>
TEST(Smoke, Arithmetic) { EXPECT_EQ(2 + 2, 4); }
''', auto_update=False)
    solution.set_build_settings(SolutionBuildSettings(test_projects=["Tests"]))
    report = solution.test()
    for process in report.processes:
        if not process.success:
            print(process.output)
    for project in report.projects:
        for diagnostic in project.diagnostics:
            print(diagnostic)
    for case in report.cases:
        print(case.name, case.status)
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())

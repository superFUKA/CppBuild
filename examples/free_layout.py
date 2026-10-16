"""Run from the repository root: python -m examples.free_layout <new-directory>.

One .cppbuild per Solution, placed apart from the sources it builds:

    <new-directory>/
      src/greeting/...        a library's sources (nothing else is written here)
      src/app/...             the program's sources
      build/cppbuild/.cppbuild   the Solution: settings and generated CMake files
"""
from pathlib import Path
import sys

from cppbuild import ProjectType, Solution


def main():
    root = Path(sys.argv[1]).resolve()
    root.mkdir(parents=True, exist_ok=False)
    # The base directory is the repository root, so Projects are given from there.
    solution = Solution.create(root / "build/cppbuild", "Demo", base_directory="../..")
    greeting = solution.add_project("src/greeting", "Greeting", ProjectType.STATIC_LIBRARY)
    greeting.add_file("include/greeting.hpp", content="#pragma once\nconst char* greeting();\n", auto_update=False)
    greeting.add_file("src/greeting.cpp", content='#include "greeting.hpp"\nconst char* greeting() { return "Hello CppBuild"; }\n',
                      auto_update=False)
    app = solution.add_project("src/app", "App", ProjectType.EXECUTABLE)
    app.add_file("src/main.cpp", content='#include <iostream>\n#include "greeting.hpp"\nint main() { std::cout << greeting() << "\\n"; }\n',
                 auto_update=False)
    app.settings.link_project(greeting, ProjectType.STATIC_LIBRARY)
    report = app.run()
    for process in report.processes:
        print(process.output)
    print("Solution settings and CMake files:", solution.config_directory)
    print("Without CppBuild: cmake -S", solution.config_directory, "-B <build directory>")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())

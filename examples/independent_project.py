"""Run from the repository root: python -m examples.independent_project <new-directory>."""
from pathlib import Path
import sys

from cppbuild import ProjectType, Solution


def main():
    root = Path(sys.argv[1]).resolve()
    solution = Solution.create(root, "Demo")
    app = solution.add_project("App", "App", ProjectType.EXECUTABLE)
    app.add_file("src/main.cpp", content='#include <iostream>\nint main() { std::cout << "Hello CppBuild\\n"; }\n', auto_update=False)
    report = app.run()
    for process in report.processes:
        print(process.output)
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())

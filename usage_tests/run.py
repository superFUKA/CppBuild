"""Run from the repository root: python -m usage_tests.run --help."""
import argparse
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
import json
import os
from pathlib import Path
import time
import traceback

from cppbuild import (
    CMakePackage, CMakeSettings, CMakeSource, ImportedLibrary, ProjectBuildSettings,
    ProjectSettingsData, ProjectType as T, SettingsConflictError, SettingsError,
    Solution, SolutionBuildSettings, TemplateTools, ToolSettings, TypeSettingsData,
)


# Generation environment for every scenario; set from --generator/--architecture.
ENVIRONMENT = {}


def settings(**values):
    return SolutionBuildSettings(**values, **ENVIRONMENT)


def serial(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, (Path, Enum)):
        return str(value.value if isinstance(value, Enum) else value)
    raise TypeError(type(value).__name__)


class Session:
    def __init__(self, root, archive):
        self.root, self.archive = root, archive
        self.records = []

    def record(self, name, detail):
        self.records.append({'name': name, 'detail': detail})
        (self.root / 'steps.json').write_text(
            json.dumps(self.records, default=serial, ensure_ascii=False, indent=2), encoding='utf-8')

    def check(self, name, condition):
        self.record(name, {'passed': bool(condition)})
        if not condition:
            raise AssertionError(name)

    def report(self, name, report, success=True):
        self.record(name, report)
        if report.success != success:
            raise AssertionError(f'{name}: expected success={success}; see steps.json')
        return report

    def rejects(self, name, exception, action):
        try:
            action()
        except exception as error:
            self.record(name, {'expected_exception': type(error).__name__, 'message': str(error)})
        else:
            raise AssertionError(f'{name}: expected {exception.__name__}')


def add(project, name, content):
    result = project.add_file(name, content=content, auto_update=False)
    if not result.success:
        raise RuntimeError(str(result))


def app(solution, name, body='return 0;'):
    project = solution.add_project(name, name, T.EXECUTABLE)
    add(project, 'src/main.cpp', '#include <iostream>\nint main(int argc, char** argv) { ' + body + ' }\n')
    return project


def output(report):
    return '\n'.join(p.output for p in report.processes)


def lifecycle(s):
    solution = Solution.create(s.root / '日本語 空白 Solution', 'Lifecycle')
    project = app(solution, 'Console', 'std::cout << VALUE << ":" << __cplusplus << "\\n"; return VALUE == 42 ? 0 : 1;')
    data = project.settings.get()
    data.types[T.EXECUTABLE].compile_definitions = ['VALUE=42']
    project.settings.save(data)
    s.report('environment', project.check_environment())
    for configuration, standard in [('Debug', 17), ('Release', 20)]:
        solution.set_build_settings(settings(configuration=configuration, cpp_standard=standard))
        s.report(configuration + ' update', project.update())
        result = s.report(configuration + ' run', project.run())
        s.check(configuration + ' output', '42:' in output(result))
        s.report(configuration + ' incremental build', project.build())
    reopened = Solution.open(solution.root / '.cppbuild')
    s.check('ephemeral configuration reset', reopened.info().build_settings.configuration == 'Debug')
    s.check('saved definitions', reopened.get_project('Console').settings.get() == project.settings.get())
    s.report('move source with auto update', project.move_file('src/main.cpp', 'src/nested/main.cpp'))
    s.report('run moved source', project.run())
    s.report('add invalid source', project.add_file('src/broken.cpp', content='this is not C++;\n', auto_update=False))
    s.report('expected compiler failure', project.build(), success=False)
    s.check('failed state', solution.info().projects[0].build_state == 'failed')
    s.report('remove invalid source', project.remove_file('src/broken.cpp'))
    s.report('recovery rebuild', project.rebuild())
    s.report('clean', project.clean())
    s.check('sources survive clean', (project.root / 'src/nested/main.cpp').is_file())
    s.check('cleaned state', solution.info().projects[0].build_state == 'cleaned')
    s.report('run after clean', project.run())
    stale = Solution.open(solution.root / '.cppbuild')
    data = solution.settings.get()
    data.name = 'RenamedSolution'
    solution.settings.save(data)
    s.rejects('concurrent settings conflict', SettingsConflictError, lambda: stale.settings.save(stale.settings.get()))
    stale.settings.reload()
    s.check('reload', stale.settings.get().name == 'RenamedSolution')
    unused = app(solution, 'Unused')
    solution.remove_project('Unused')
    s.check('unregister preserves files', (unused.root / 'src/main.cpp').is_file())
    s.rejects('duplicate project', SettingsError, lambda: app(solution, 'Console'))
    s.rejects('path escape', SettingsError, lambda: project.add_file('../escape.cpp', content='', auto_update=False))


def libraries(s):
    solution = Solution.create(s.root / 'Solution', 'Libraries')
    headers = solution.add_project('Headers', 'Headers', T.INTERFACE_LIBRARY)
    add(headers, 'include/number.hpp', '#pragma once\ninline int number() { return 40; }\n')
    data = headers.settings.get()
    data.types[T.INTERFACE_LIBRARY].public_definitions = ['HEADER_BONUS=2']
    headers.settings.save(data)
    dual = solution.add_project('Math', 'Math', T.STATIC_LIBRARY, ProjectSettingsData('Math', {
        T.STATIC_LIBRARY: TypeSettingsData(), T.SHARED_LIBRARY: TypeSettingsData()}))
    # No explicit selection: StaticApp and SharedApp keep their saved link types.
    add(dual, 'src/math.cpp', '#include <number.hpp>\n__declspec(dllexport) int answer() { return number() + HEADER_BONUS; }\n')
    dual.settings.link_project(headers, T.INTERFACE_LIBRARY)
    consumers = []
    for name, kind in [('StaticApp', T.STATIC_LIBRARY), ('SharedApp', T.SHARED_LIBRARY)]:
        consumer = app(solution, name, 'std::cout << answer() << "\\n"; return answer() == 42 ? 0 : 1;')
        path = consumer.root / 'src/main.cpp'
        path.write_text('int answer();\n' + path.read_text(encoding='utf-8'), encoding='utf-8')
        consumer.settings.link_project(dual, kind)
        consumers.append(name)
    for configuration in ['Debug', 'Release']:
        solution.set_build_settings(settings(configuration=configuration, build_projects=consumers,
                                                         run_projects=consumers, parallel=2, run_parallel=2))
        s.report(configuration + ' whole solution build', solution.build())
        result = s.report(configuration + ' static and shared consumers', solution.run())
        s.check(configuration + ' both answers', sum(p.output.strip() == '42' for p in result.processes) == 2)
    # Both kinds coexist in one tree; the DLL beside the static library is named Math-shared.
    binary = next((solution.root / '.cppbuild/output').rglob('Release/Math*.dll'))
    before = binary.read_bytes()
    s.report('consumer only clean', solution.get_project('SharedApp').clean())
    s.check('dependency DLL survives', binary.read_bytes() == before)
    s.report('whole solution rebuild', solution.rebuild())
    s.report('whole solution clean', solution.clean())
    s.report('whole solution recovery', solution.run())
    s.rejects('remove linked project', SettingsError, lambda: solution.remove_project('Math'))


def external(s):
    provider = Solution.create(s.root / 'Provider', 'Provider')
    library = provider.add_project('Vendor', 'Vendor', T.STATIC_LIBRARY)
    add(library, 'src/vendor.cpp', 'int vendor() { return 42; }\n')
    solution = Solution.create(s.root / 'Consumer', 'Consumer')
    consumer = app(solution, 'Client', 'return vendor() == 42 ? 0 : 1;')
    source = consumer.root / 'src/main.cpp'
    source.write_text('int vendor();\n' + source.read_text(encoding='utf-8'), encoding='utf-8')
    link = consumer.settings.link_solution(provider.root / '.cppbuild', T.STATIC_LIBRARY)
    for configuration in ['Debug', 'Release']:
        solution.set_build_settings(settings(configuration=configuration, external_build_settings={
            str(provider.root / '.cppbuild'): settings(configuration=configuration)}))
        s.report(configuration + ' external solution', consumer.run())
    consumer.settings.unlink(link.dependency_id)
    imported = consumer.settings.link_imported_library(ImportedLibrary(T.STATIC_LIBRARY, {
        config: str(next((solution.root / '.cppbuild/output').rglob(config + '/Vendor.lib')))
        for config in ['Debug', 'Release']}))
    s.report('imported Release library', consumer.run())
    consumer = Solution.open(solution.root / '.cppbuild').get_project('Client')
    s.report('imported Debug after reopen', consumer.run())
    binary = next((solution.root / '.cppbuild/output').rglob('Debug/Vendor.lib'))
    before = binary.read_bytes()
    s.report('clean imported consumer', consumer.clean())
    s.check('imported binary untouched', before == binary.read_bytes())
    consumer.settings.unlink(imported.dependency_id)
    vendor = s.root / 'CMakeSource'
    vendor.mkdir()
    (vendor / 'CMakeLists.txt').write_text('add_library(VendorSource STATIC vendor.cpp)\n', encoding='utf-8')
    (vendor / 'vendor.cpp').write_text('int vendor() { return 42; }\n', encoding='utf-8')
    package = s.root / 'Package'
    package.mkdir()
    (package / 'NumbersConfig.cmake').write_text('add_library(Numbers::Headers INTERFACE IMPORTED)\nset_property(TARGET Numbers::Headers PROPERTY INTERFACE_COMPILE_DEFINITIONS PACKAGE_VALUE=42)\n', encoding='utf-8')
    a = consumer.settings.link_cmake_source(CMakeSource(str(vendor), 'VendorSource'))
    b = consumer.settings.link_package(CMakePackage('Numbers', 'Numbers::Headers', str(package)))
    source.write_text('int vendor(); int main() { return vendor() == PACKAGE_VALUE ? 0 : 1; }\n', encoding='utf-8')
    add(consumer, 'include/pch.hpp', '#pragma once\n#include <vector>\n')
    consumer.settings.set_pch(project_headers=['include/pch.hpp'], system_headers=['string'])
    s.report('CMake source package and PCH', consumer.run())
    s.check('PCH produced', bool(list((solution.root / '.cppbuild/output').rglob('*.pch'))))
    consumer.settings.clear_pch()
    s.report('without PCH', consumer.rebuild())
    for dependency in [a, b]:
        consumer.settings.unlink(dependency.dependency_id)
    (vendor / 'CMakeLists.txt').write_text('message(FATAL_ERROR unlinked_source_loaded)\n', encoding='utf-8')
    (package / 'NumbersConfig.cmake').write_text('message(FATAL_ERROR unlinked_package_loaded)\n', encoding='utf-8')
    source.write_text('int main() { return 0; }\n', encoding='utf-8')
    s.report('unlinked dependencies not loaded', consumer.run())


def execution(s):
    solution = Solution.create(s.root / 'Solution', 'Execution')
    for name, code in [('First', 0), ('Failure', 7), ('Last', 0)]:
        project = app(solution, name, f'std::cout << "RUN:{name}:" << (argc > 1 ? argv[1] : "none") << "\\n"; return {code};')
        project.set_build_settings(ProjectBuildSettings(run_arguments=['argument with spaces']))
    def ran(report):
        return [p.output.strip() for p in report.processes if p.output.startswith('RUN:')]
    solution.set_build_settings(settings(run_projects=['First', 'Failure', 'Last']))
    result = s.report('stop at nonzero exit', solution.run(), success=False)
    s.check('stopped before Last', ran(result) == ['RUN:First:argument with spaces', 'RUN:Failure:argument with spaces'])
    solution.set_build_settings(settings(run_projects=['Failure', 'Last', 'First'], run_continue_on_failure=True))
    result = s.report('continue after nonzero exit', solution.run(), success=False)
    s.check('requested execution order', ran(result) == [f'RUN:{n}:argument with spaces' for n in ['Failure', 'Last', 'First']])
    solution.set_build_settings(settings(run_projects=['Last', 'First'], run_parallel=2, run_wait=False))
    pending = solution.run()
    result = s.report('asynchronous parallel completion', pending.wait(timeout=60))
    s.check('done after wait', pending.done and pending.success)
    s.check('both applications ran', len(ran(result)) == 2)
    first = solution.get_project('First')
    first.set_build_settings(ProjectBuildSettings(run_wait=False, run_arguments=['individual']))
    s.check('individual async arguments', 'RUN:First:individual' in output(s.report('individual async', first.run().wait(timeout=60))))
    first.set_build_settings(ProjectBuildSettings(tools=ToolSettings(environment={'CPPBUILD_USAGE_TEST': 'visible'})))
    (first.root / 'src/main.cpp').write_text('#include <cstdlib>\n#include <string>\nint main() { auto p = std::getenv("CPPBUILD_USAGE_TEST"); return p && std::string(p) == "visible" ? 0 : 1; }\n', encoding='utf-8')
    s.report('custom tool environment reaches executable', first.run())


def templates(s):
    solution = Solution.create(s.root / 'Original', 'Templates')
    project = app(solution, 'App', 'return answer() == 42 ? 0 : 1;')
    source = project.root / 'src/main.cpp'
    source.write_text('#include <answer.hpp>\n' + source.read_text(encoding='utf-8'), encoding='utf-8')
    material = s.root / 'function.hpp'
    material.write_text('#pragma once\ninline int Example() { return 42; }\n', encoding='utf-8')
    solution.create_file_template('function', material, replacements={'name': 'Example'})
    events = []
    def changed(event):
        events.append(event.name)
        if any(p.name == 'answer.hpp' for p in event.changed_paths):
            project.add_file('include/event.hpp', content='#pragma once\n', auto_update=True)
    key = solution.on('file_changed', changed)
    s.report('material expansion and event file creation', project.add_file('include/answer.hpp', template_name='function', replacements={'name': 'answer'}))
    s.check('event recursion suppressed', events == ['file_changed'])
    s.check('callback created file', (project.root / 'include/event.hpp').is_file())
    s.check('unsubscribe', solution.off(key))
    before = solution.on('before_build', lambda event: events.append(event.name))
    after = solution.on('after_build', lambda event: events.append(event.name))
    s.report('lifecycle callbacks', solution.build())
    s.check('before and after order', events[-2:] == ['before_build', 'after_build'])
    solution.off(before)
    solution.off(after)
    solution.set_build_settings(settings(configuration='Release', run_projects=['App']))
    s.report('original run', solution.run())
    template = TemplateTools.create_solution_template(solution, s.root / 'Template')
    clone = Solution.create(s.root / 'Restored', 'Restored', template=template)
    s.check('template omits generated build trees', not list(clone.root.rglob('CMakeCache.txt')))
    s.check('template restores source', (clone.get_project('App').root / 'include/event.hpp').is_file())
    s.check('template omits ephemeral settings', clone.info().build_settings.configuration == 'Debug')
    clone.set_build_settings(settings())  # Build settings are not restored; select the environment again.
    s.report('restored consumer runs', clone.get_project('App').run())
    material_path = clone.root / clone.settings.file_templates()['function']
    clone.settings.set_file_template('alias', material_path)
    clone.settings.remove_file_template('function')
    s.report('registered material alias', clone.get_project('App').add_file('include/another.hpp', template_name='alias', replacements={'name': 'another'}))
    s.report('restored consumer after addition', clone.get_project('App').run())


def testing(s):
    solution = Solution.create(s.root / '日本語 Tests', 'Testing')
    library = solution.add_project('Library', 'Library', T.SHARED_LIBRARY)
    add(library, 'src/value.cpp', '__declspec(dllexport) int value() { return 42; }\n')
    for name in ['Passing', 'Failing', 'Empty']:
        project = solution.add_project(name, name, T.TEST)
        project.set_build_settings(ProjectBuildSettings(googletest_archive=s.archive, test_parallel=2))
        project.settings.link_project(library, T.SHARED_LIBRARY)
        text = '#include <gtest/gtest.h>\nint value();\n'
        if name == 'Passing':
            text += 'TEST(Value, Answer) { EXPECT_EQ(value(), 42); }\nTEST(Value, Skip) { GTEST_SKIP(); }\n'
        elif name == 'Failing':
            text += 'TEST(Value, IntentionalFailure) { EXPECT_EQ(value(), 99); }\n'
        add(project, 'src/test.cpp', text)
    for configuration in ['Debug', 'Release']:
        solution.set_build_settings(settings(configuration=configuration, test_projects=['Passing']))
        result = s.report(configuration + ' passing and skipped cases', solution.test())
        s.check(configuration + ' case statuses', {c.status for c in result.cases} == {'passed', 'skipped'})
    solution.set_build_settings(settings(test_projects=['Failing', 'Passing'], test_continue_on_failure=False))
    result = s.report('test stop on failure', solution.test(), success=False)
    s.check('test stopped', len(result.projects) == 1 and any(c.status == 'failed' for c in result.cases))
    solution.set_build_settings(settings(test_projects=['Failing', 'Passing'], test_continue_on_failure=True))
    result = s.report('test continue after failure', solution.test(), success=False)
    s.check('both test projects executed', len(result.projects) == 2)
    s.report('individual ignores solution selection', solution.get_project('Passing').test())
    s.report('zero tests is failure', solution.get_project('Empty').test(), success=False)
    failing = solution.get_project('Failing')
    (failing.root / 'src/test.cpp').write_text('#include <gtest/gtest.h>\nint value();\nTEST(Value, Fixed) { EXPECT_EQ(value(), 42); }\n', encoding='utf-8')
    s.report('repaired test', failing.test())
    s.report('whole solution selected test build', solution.build())


def standalone(s):
    """The generated CMake files alone: copied elsewhere without .cppbuild, configured by plain cmake."""
    import shutil
    import subprocess
    from cppbuild import generators
    stl = Solution.create(s.root / 'Work/STL', 'STL')
    containers = stl.add_project('Containers', 'Containers', T.STATIC_LIBRARY)
    add(containers, 'include/containers.hpp', '#pragma once\nint containers();\n')
    add(containers, 'src/containers.cpp', '#include "containers.hpp"\nint containers() { return 40; }\n')
    tests = stl.add_project('Tests', 'Tests', T.TEST)
    add(tests, 'src/test.cpp', '#include <gtest/gtest.h>\n#include "containers.hpp"\nTEST(STL, Containers) { EXPECT_EQ(containers(), 40); }\n')
    tests.settings.link_project(containers, T.STATIC_LIBRARY)
    ecs = Solution.create(s.root / 'Work/ECS', 'ECS')
    data = ecs.settings.get()
    data.dependency_directories = ['deps']
    ecs.settings.save(data)
    shutil.move(str(stl.root), str(ecs.root / 'deps/STL'))
    stl = Solution.open(ecs.root / 'deps/STL/.cppbuild')
    core = ecs.add_project('Core', 'Core', T.STATIC_LIBRARY)
    add(core, 'src/core.cpp', '#include "containers.hpp"\nint core() { return containers() + 2; }\n')
    core.settings.link_solution(stl.root / '.cppbuild', T.STATIC_LIBRARY)
    application = app(ecs, 'App', 'std::cout << core() << "\\n"; return core() == 42 ? 0 : 1;')
    source = application.root / 'src/main.cpp'
    source.write_text('int core();\n' + source.read_text(encoding='utf-8'), encoding='utf-8')
    application.settings.link_project(core, T.STATIC_LIBRARY)
    checks = ecs.add_project('Tests', 'Tests', T.TEST)
    add(checks, 'src/test.cpp', '#include <gtest/gtest.h>\nint core();\nTEST(ECS, Core) { EXPECT_EQ(core(), 42); }\n')
    checks.settings.link_project(core, T.STATIC_LIBRARY)
    checks.set_build_settings(ProjectBuildSettings(googletest_archive=s.archive))
    ecs.set_build_settings(settings(test_projects=['Tests']))
    s.report('CppBuild whole test', ecs.test())
    from cppbuild.cmake_files import generated_file
    def generated():
        # The build tree below .cppbuild holds third-party CMake files (GoogleTest); only CppBuild's count.
        return {p: p.read_bytes() for p in ecs.root.rglob('*') if '.cppbuild' not in p.parts and generated_file(p)}
    first = generated()
    expected = [ecs.root / 'CMakeLists.txt', ecs.root / 'CppBuildTopLevel.cmake', stl.root / 'CMakeLists.txt',
                stl.root / 'CppBuildTopLevel.cmake', *(p.root / 'CMakeLists.txt' for p in [*ecs.projects(), *stl.projects()])]
    s.check('generated files written', set(first) == set(expected))
    s.report('CppBuild update again', ecs.update())
    s.check('generated files are stable', first == generated())
    s.check('generated files have no absolute paths',
            not any(str(s.root).replace('\\', '/') in p.read_text(encoding='utf-8').replace('\\', '/') for p in first))
    copy = s.root / 'Copy'
    shutil.copytree(ecs.root, copy, ignore=lambda directory, names: [n for n in names if n == '.cppbuild'])
    toolchain = generators.resolve(settings())
    url = ['-DCPPBUILD_GOOGLETEST_URL=' + s.archive] if s.archive else []
    for name, root, build, configuration in [('ECS', copy, s.root / 'plain', 'Release'),
                                              ('STL', copy / 'deps/STL', s.root / 'plain-stl', 'Debug')]:
        for step, command in [('configure', [*toolchain.configure(root, build), *url]),
                              ('build', ['cmake', '--build', build, '--config', configuration]),
                              ('ctest', ['ctest', '--test-dir', build, '-C', configuration, '--output-on-failure'])]:
            result = subprocess.run([str(c) for c in command], env=toolchain.env, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, errors='replace')
            s.record(f'plain {name} {step}', {'command': [str(c) for c in command], 'returncode': result.returncode,
                                              'output': result.stdout[-4000:]})
            s.check(f'plain {name} {step} succeeded', result.returncode == 0)
    executable = s.root / ('plain/bin/Release/App.exe' if os.name == 'nt' else 'plain/bin/Release/App')
    result = subprocess.run([str(executable)], stdout=subprocess.PIPE, text=True)
    s.check('plain executable runs', result.returncode == 0 and result.stdout.strip() == '42')


SCENARIOS = {f.__name__: f for f in [lifecycle, libraries, external, execution, templates, testing, standalone]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='New directory; existing directories are never overwritten')
    parser.add_argument('--scenario', action='append', choices=SCENARIOS, help='Repeat to select scenarios; default: all')
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--gtest-archive', type=Path, help='GoogleTest 1.14.0 ZIP (fixed hash verified by CMake)')
    source.add_argument('--online', action='store_true', help='Allow the testing scenario to fetch GoogleTest')
    parser.add_argument('--generator', help='CMake generator; default: fixed per host OS')
    parser.add_argument('--architecture', choices=['x64', 'Win32', 'ARM64'], help='Default: host/compiler default')
    parser.add_argument('--toolset', help='VS toolset or MSVC version for Ninja')
    args = parser.parse_args()
    ENVIRONMENT.update(cmake=CMakeSettings(generator=args.generator, toolset=args.toolset), architecture=args.architecture)
    selected = args.scenario or list(SCENARIOS)
    if {'testing', 'standalone'} & set(selected) and not (args.gtest_archive or args.online):
        parser.error('testing and standalone require --gtest-archive ZIP or --online; no silent skips')
    archive = str(args.gtest_archive.resolve()) if args.gtest_archive else None
    if archive and not Path(archive).is_file():
        parser.error('GoogleTest archive does not exist')
    root = (args.output or Path('.test-work') / ('usage-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))).resolve()
    root.mkdir(parents=True, exist_ok=False)
    summary = {'output': str(root), 'generator': args.generator, 'architecture': args.architecture,
               'toolset': args.toolset, 'scenarios': [], 'success': False}
    print(f'Output: {root} generator={args.generator or "default"} architecture={args.architecture or "default"}', flush=True)
    for name in selected:
        folder = root / name
        folder.mkdir()
        session = Session(folder, archive)
        start = time.monotonic()
        print(f'RUN {name}', flush=True)
        result = {'name': name, 'success': False}
        try:
            SCENARIOS[name](session)
            result['success'] = True
        except Exception:
            result['error'] = traceback.format_exc()
            (folder / 'failure.txt').write_text(result['error'], encoding='utf-8')
            print(result['error'], flush=True)
        result['seconds'] = round(time.monotonic() - start, 2)
        result['checks_and_operations'] = len(session.records)
        summary['scenarios'].append(result)
        summary['success'] = all(r['success'] for r in summary['scenarios']) and len(summary['scenarios']) == len(selected)
        (root / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
        print(f'{"PASS" if result["success"] else "FAIL"} {name} ({result["seconds"]}s)', flush=True)
    return 0 if summary['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

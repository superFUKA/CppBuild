"""Generator, compiler and architecture resolution for independent CMake trees.

Visual Studio generators select MSVC themselves. Other generators use CMake's
normal compiler search; on Windows without an explicit compiler the library
prepares the MSVC environment with vcvarsall, so no Developer Prompt is needed.
Nothing here installs or downloads tools.
"""
from dataclasses import astuple, dataclass, field
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

from .models import CMakeSettings, SettingsError
from . import tooling

VISUAL_STUDIO = {"Visual Studio 17 2022": ("vs2022", ".sln"), "Visual Studio 18 2026": ("vs2026", ".slnx")}
NINJA_MULTI_CONFIG = "Ninja Multi-Config"
SUPPORTED = (*VISUAL_STUDIO, NINJA_MULTI_CONFIG)
ARCHITECTURES = ("x64", "Win32", "ARM64")
_VCVARS = {"x64": "x64", "Win32": "x86", "ARM64": "arm64"}
_OSX = {"x64": "x86_64", "ARM64": "arm64"}
_VSCMD = {"x64": "x64", "x86": "Win32", "arm64": "ARM64"}


def default_generator():
    return "Visual Studio 17 2022" if os.name == "nt" else NINJA_MULTI_CONFIG


def host_architecture():
    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64", "x64"}:
        return "x64"
    if machine in {"arm64", "aarch64"}:
        return "ARM64"
    if re.fullmatch(r"i[3-6]86|x86", machine):
        return "Win32"
    return None


def runnable(architecture):
    """Whether this host can execute binaries built for architecture."""
    host = host_architecture()
    if architecture is None or host is None or architecture == host:
        return True
    if os.name == "nt":
        return (host, architecture) in {("x64", "Win32"), ("ARM64", "x64"), ("ARM64", "Win32")}
    if sys.platform == "darwin":
        return (host, architecture) == ("ARM64", "x64")
    return False


def _tool_name(value, label):
    if value is None:
        return
    if not isinstance(value, str) or not value or "\x00" in value:
        raise SettingsError(f"{label} must be a nonempty string")
    if ("/" in value or "\\" in value) and not Path(value).is_absolute():
        raise SettingsError(f"Explicit {label} paths must be absolute")


def validate(value):
    if not isinstance(value, CMakeSettings):
        raise SettingsError("Expected CMakeSettings")
    for key in ("generator", "toolset", "toolchain_file"):
        item = getattr(value, key)
        if item is not None and (not isinstance(item, str) or not item or "\x00" in item):
            raise SettingsError(f"{key} must be a nonempty string or None")
    _tool_name(value.c_compiler, "c_compiler")
    _tool_name(value.cxx_compiler, "cxx_compiler")
    generator = value.generator or default_generator()
    if generator not in SUPPORTED:
        raise SettingsError(f"Generator {generator!r} is not supported by CppBuild "
                            f"(supported: {', '.join(SUPPORTED)}); CMake may still provide it")
    if value.toolchain_file is not None and not Path(value.toolchain_file).is_absolute():
        raise SettingsError("toolchain_file must be an absolute path")
    if generator in VISUAL_STUDIO and (value.c_compiler or value.cxx_compiler):
        raise SettingsError("Visual Studio generators select the compiler with toolset")
    if value.toolchain_file is not None and (value.c_compiler or value.cxx_compiler):
        raise SettingsError("toolchain_file and explicit compilers are mutually exclusive")


def _digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class CompilerInfo:
    id: str | None
    version: str | None
    path: str | None
    architecture: str | None


@dataclass(frozen=True)
class Toolchain:
    """A resolved generation environment. Equality ignores the process environment."""
    generator: str
    architecture: str | None
    toolset: str | None = None
    c_compiler: str | None = None
    cxx_compiler: str | None = None
    toolchain_file: str | None = None
    explicit_compiler: bool = field(default=False, compare=False)
    make_program: str | None = field(default=None, compare=False)
    env: dict = field(default_factory=dict, compare=False, repr=False)

    @property
    def visual_studio(self):
        return self.generator in VISUAL_STUDIO

    @property
    def solution_suffix(self):
        return VISUAL_STUDIO[self.generator][1] if self.visual_studio else None

    def identity(self):
        return {"generator": self.generator, "architecture": self.architecture, "toolset": self.toolset,
                "c_compiler": self.c_compiler, "cxx_compiler": self.cxx_compiler,
                "toolchain_file": self.toolchain_file}

    @property
    def context(self):
        if self.visual_studio:
            # The plain VS2022 name predates generator selection; existing caches stay valid.
            name = f"{VISUAL_STUDIO[self.generator][0]}-{self.architecture}"
            if self.toolset or self.toolchain_file:
                name += "-" + _digest(self.identity())[:8]
            return name
        return "ninja-mc-" + _digest(self.identity())[:12]

    def configure(self, source, build):
        command = ["cmake", "-S", source, "-B", build, "-G", self.generator]
        if self.visual_studio:
            command += ["-A", self.architecture]
            if self.toolset:
                command += ["-T", self.toolset]
        else:
            if self.make_program:
                command.append("-DCMAKE_MAKE_PROGRAM:FILEPATH=" + Path(self.make_program).as_posix())
            if self.explicit_compiler:
                if self.c_compiler:
                    command.append("-DCMAKE_C_COMPILER:FILEPATH=" + Path(self.c_compiler).as_posix())
                command.append("-DCMAKE_CXX_COMPILER:FILEPATH=" + Path(self.cxx_compiler).as_posix())
            if sys.platform == "darwin" and self.architecture is not None:
                command.append("-DCMAKE_OSX_ARCHITECTURES=" + _OSX[self.architecture])
        if self.toolchain_file:
            command.append("-DCMAKE_TOOLCHAIN_FILE:FILEPATH=" + Path(self.toolchain_file).as_posix())
        return command


_resolved = {}
_vcvars = {}


def resolve(settings):
    """Resolve generator, compiler and architecture for build settings (cached per process)."""
    cmake = settings.cmake
    validate(cmake)
    base = tooling.environment(settings.tools)
    key = (settings.architecture, astuple(cmake), tuple(sorted(base.items())))
    if key not in _resolved:
        _resolved[key] = _resolve(settings.architecture, cmake, base)
    return _resolved[key]


def _resolve(architecture, cmake, base):
    generator = cmake.generator or default_generator()
    if architecture is not None and architecture not in ARCHITECTURES:
        raise SettingsError(f"Unsupported architecture: {architecture!r}")
    if generator in VISUAL_STUDIO:
        if os.name != "nt":
            raise SettingsError("Visual Studio generators require Windows")
        return Toolchain(generator, architecture or host_architecture() or "x64", cmake.toolset,
                         toolchain_file=cmake.toolchain_file, env=dict(base))
    env = dict(base)
    msvc = (os.name == "nt" and cmake.toolchain_file is None
            and all(v is None or Path(v).name.lower() in {"cl", "cl.exe"} for v in (cmake.c_compiler, cmake.cxx_compiler)))
    if msvc:
        env, architecture = _msvc_environment(env, architecture, cmake.toolset)
    elif cmake.toolset:
        raise SettingsError("toolset with a non-Visual Studio generator selects an MSVC version and requires MSVC on Windows")
    if sys.platform == "darwin" and architecture is not None and architecture not in _OSX:
        raise SettingsError(f"Architecture {architecture} is not available on macOS")
    path = env.get("PATH", "")

    def which(name, required):
        if name is None:
            return None
        found = shutil.which(name, path=path)
        if found is None and required:
            raise SettingsError(f"Compiler not found: {name}")
        return str(Path(found).resolve()) if found else None

    explicit = msvc or cmake.cxx_compiler is not None
    if msvc:
        cxx = c = which("cl", True)
    else:
        c = which(cmake.c_compiler, True)
        cxx = which(cmake.cxx_compiler, True)
        if cxx is None and cmake.toolchain_file is None:
            # Mirrors CMake's search closely enough to separate caches when CXX or PATH changes.
            candidates = [env["CXX"]] if env.get("CXX") else ["c++", "g++", "clang++"]
            cxx = next((found for found in (which(n, False) for n in candidates) if found), None)
    make = shutil.which("ninja", path=path)
    if make is None and os.name == "nt":
        make = next((str(p) for p in (Path(i) / "Common7/IDE/CommonExtensions/Microsoft/CMake/Ninja/ninja.exe"
                                      for i in _vs_installations()) if p.is_file()), None)
    if make is None:
        raise SettingsError("Ninja not found; install ninja or add it to PATH with ToolSettings.environment")
    return Toolchain(generator, architecture, cmake.toolset, c, cxx, cmake.toolchain_file,
                     explicit_compiler=explicit, make_program=make, env=env)


def _vs_installations():
    root = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    vswhere = Path(root) / "Microsoft Visual Studio/Installer/vswhere.exe"
    if not vswhere.is_file():
        return []
    try:
        result = subprocess.run([str(vswhere), "-products", "*", "-format", "json", "-utf8"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
        data = json.loads(result.stdout.decode("utf-8") or "[]")
    except (OSError, ValueError):
        return []
    def version(item):
        return tuple(int(x) for x in re.findall(r"\d+", item.get("installationVersion", "0")))
    return [item["installationPath"] for item in sorted(data, key=version, reverse=True) if "installationPath" in item]


def _msvc_environment(env, architecture, toolset):
    existing = env.get("VSCMD_ARG_TGT_ARCH")
    if existing:
        # A Developer Prompt environment was supplied; use it rather than a second vcvarsall.
        target = _VSCMD.get(existing.lower())
        if architecture is not None and architecture != target:
            raise SettingsError(f"The supplied MSVC environment targets {target}, not {architecture}")
        if toolset and not env.get("VCTOOLSVERSION", "").startswith(toolset):
            raise SettingsError(f"The supplied MSVC environment is not version {toolset}")
        return env, target
    host = host_architecture()
    target = architecture or host
    if host not in _VCVARS or target not in _VCVARS:
        raise SettingsError("Cannot determine the MSVC host/target architecture")
    argument = _VCVARS[target] if host == target else f"{_VCVARS[host]}_{_VCVARS[target]}"
    for installation in _vs_installations():
        vcvars = Path(installation) / "VC/Auxiliary/Build/vcvarsall.bat"
        tools = Path(installation) / "VC/Tools/MSVC"
        if not vcvars.is_file() or not tools.is_dir():
            continue
        if toolset and not any(p.name.startswith(toolset) for p in tools.iterdir()):
            continue
        key = (str(vcvars), argument, toolset, tuple(sorted(env.items())))
        if key not in _vcvars:
            _vcvars[key] = _run_vcvars(vcvars, argument, toolset, env)
        return dict(_vcvars[key]), target
    raise SettingsError("No Visual Studio installation with MSVC" + (f" {toolset}" if toolset else "")
                        + " was found for the Ninja generator")


def _run_vcvars(vcvars, argument, toolset, env):
    version = f" -vcvars_ver={toolset}" if toolset else ""
    # cmd /u writes built-in command output (set) as UTF-16, independent of code pages.
    command = f'cmd.exe /d /u /s /c ""{vcvars}" {argument}{version} >nul 2>&1 && set"'
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, check=False)
    output = result.stdout.decode("utf-16-le", errors="replace")
    values = {}
    for line in output.splitlines():
        name, separator, value = line.partition("=")
        if separator and name:
            values[name.upper()] = value
    if result.returncode != 0 or "VSCMD_ARG_TGT_ARCH" not in values:
        raise SettingsError(f"vcvarsall failed for {argument}{version}")
    return values


def compiler(build):
    """Read the compiler CMake detected for a configured tree, if any."""
    files = sorted((Path(build) / "CMakeFiles").glob("*/CMakeCXXCompiler.cmake"), key=lambda p: p.stat().st_mtime_ns)
    if not files:
        return None
    data = files[-1].read_text(encoding="utf-8", errors="replace")
    def value(name, text=data):
        match = re.search(r'^set\(' + name + r' "([^"]*)"\)', text, re.M)
        return match.group(1) if match else None
    system = files[-1].parent / "CMakeSystem.cmake"
    processor = value("CMAKE_SYSTEM_PROCESSOR", system.read_text(encoding="utf-8", errors="replace")) if system.is_file() else None
    return CompilerInfo(value("CMAKE_CXX_COMPILER_ID"), value("CMAKE_CXX_COMPILER_VERSION"), value("CMAKE_CXX_COMPILER"),
                        _architecture(value("CMAKE_CXX_COMPILER_ARCHITECTURE_ID") or processor, value("CMAKE_CXX_SIZEOF_DATA_PTR")))


def _architecture(name, pointer):
    name = (name or "").lower()
    if name in {"x64", "amd64", "x86_64"}:
        return "Win32" if pointer == "4" else "x64"
    if name == "x86" or re.fullmatch(r"i[3-6]86", name):
        return "Win32"
    if name in {"arm64", "aarch64"} and pointer != "4":
        return "ARM64"
    return None


def verify(toolchain, info):
    """Return an error message when the configured tree does not match the request."""
    if info is None:
        return None  # Nothing recorded to compare; CMake itself reports missing compilers.
    if toolchain.architecture is not None and info.architecture is not None and info.architecture != toolchain.architecture:
        return (f"Compiler {info.path} targets {info.architecture}, not {toolchain.architecture}; "
                "select a matching compiler or a toolchain_file")
    if toolchain.explicit_compiler and info.path and toolchain.cxx_compiler:
        if os.path.normcase(str(Path(info.path).resolve())) != os.path.normcase(str(Path(toolchain.cxx_compiler).resolve())):
            return f"The build tree uses {info.path}, not the requested {toolchain.cxx_compiler}"
    return None


def capabilities(tools):
    """CMake's generator list, marked with CppBuild support."""
    from .engine import process
    env = tooling.environment(tools)
    executable = shutil.which(tools.cmake, path=env.get("PATH", ""))
    if executable is None:
        raise FileNotFoundError(f"Tool executable not found: {tools.cmake}")
    result = process([executable, "-E", "capabilities"], Path.cwd(), env)
    if not result.success:
        raise SettingsError("cmake -E capabilities failed:\n" + result.output)
    return json.loads(result.output).get("generators", [])

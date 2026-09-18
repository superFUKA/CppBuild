"""Shared tool selection for operations and environment diagnostics."""
import os
import shutil
from pathlib import Path

from .models import SettingsError, ToolSettings


def validate(tools):
    if not isinstance(tools, ToolSettings):
        raise SettingsError("Expected ToolSettings")
    for name in (tools.cmake, tools.ctest):
        if not isinstance(name, str) or not name or "\x00" in name:
            raise SettingsError("Tool must be an executable name or absolute path")
        if ("/" in name or "\\" in name) and not Path(name).is_absolute():
            raise SettingsError("Explicit tool paths must be absolute")
    if not isinstance(tools.environment, dict) or any(not isinstance(k, str) or not k or "=" in k or "\x00" in k
        or not isinstance(v, str) or "\x00" in v for k, v in tools.environment.items()):
        raise SettingsError("Tool environment must map valid names to strings")
    if os.name == "nt" and len({k.upper() for k in tools.environment}) != len(tools.environment):
        raise SettingsError("Duplicate case-insensitive environment variable")


def environment(tools):
    validate(tools)
    env = dict(os.environ)
    for key, value in tools.environment.items():
        env[key.upper() if os.name == "nt" else key] = value
    return env


def process(settings, command, cwd, env=None):
    from .engine import process as execute
    tools = settings.tools
    validate(tools)
    command = list(command)
    command[0] = tools.cmake if command[0] == "cmake" else tools.ctest
    env = environment(tools) if env is None else env
    executable = shutil.which(command[0], path=env.get("PATH", ""))
    if executable is None:
        raise FileNotFoundError(f"Tool executable not found: {command[0]}")
    command[0] = executable
    return execute(command, cwd, env=env)

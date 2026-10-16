"""Detailed diagnostic log of what CppBuild does (off by default, never saved).

Everything goes to the standard logging logger "cppbuild" and its children, so an
application may also attach its own handlers. configure_logging() adds CppBuild's
own handler (a file and/or stderr) and sets the level:

INFO   public API calls made by the caller, commands run with their exit codes,
       files written or removed, transactions.
DEBUG  plus arguments and results of every public call (also nested ones), the
       full output of every command, environment differences, decisions such
       as skipping a configure, locks and event notifications.
TRACE  plus the full environment of every command and the content of every file
       written or read as JSON.

Values of environment variables whose names look secret, and passwords in URLs,
are replaced with "***".
"""
from contextlib import contextmanager
import contextvars
from dataclasses import dataclass, replace
from datetime import datetime
import functools
import itertools
import json
import logging
import os
from pathlib import Path
import platform
import re
import sys
import threading
import time

from .models import InvalidValueError

TRACE = 5
logging.addLevelName(TRACE, "TRACE")
LEVELS = {"ERROR": logging.ERROR, "WARNING": logging.WARNING, "INFO": logging.INFO,
          "DEBUG": logging.DEBUG, "TRACE": TRACE}
FORMATS = ("text", "json")

ROOT = logging.getLogger("cppbuild")
ROOT.addHandler(logging.NullHandler())


@dataclass(frozen=True)
class LogSettings:
    """enabled=False removes CppBuild's handler. console=None writes to stderr only when no file is given.
    A relative file is resolved against the current directory when configured."""
    enabled: bool = True
    level: str = "DEBUG"
    file: str | os.PathLike | None = None
    console: bool | None = None
    format: str = "text"
    append: bool = True


_settings = LogSettings(enabled=False)
_handlers = []
_lock = threading.Lock()


def logger(name):
    return logging.getLogger(f"cppbuild.{name}")


def _validate(settings):
    if not isinstance(settings, LogSettings):
        raise InvalidValueError("Expected LogSettings", field="settings", value=settings)
    if not isinstance(settings.enabled, bool):
        raise InvalidValueError("enabled must be a bool", field="enabled", value=settings.enabled)
    level = settings.level.upper() if isinstance(settings.level, str) else settings.level
    if level not in LEVELS:
        raise InvalidValueError(f"Unsupported log level: {settings.level!r}", field="level",
                                value=settings.level, allowed=tuple(LEVELS))
    if settings.format not in FORMATS:
        raise InvalidValueError(f"Unsupported log format: {settings.format!r}", field="format",
                                value=settings.format, allowed=FORMATS)
    if settings.console not in (None, True, False):
        raise InvalidValueError("console must be a bool or None", field="console", value=settings.console)
    if not isinstance(settings.append, bool):
        raise InvalidValueError("append must be a bool", field="append", value=settings.append)
    file = settings.file
    if file is not None:
        if not isinstance(file, (str, os.PathLike)) or not str(file) or "\x00" in str(file):
            raise InvalidValueError("file must be a path", field="file", value=file)
        file = Path(file).resolve()
    return replace(settings, level=level, file=file)


def configure_logging(settings=None):
    """Apply LogSettings (None: off) and return the applied settings with an absolute file."""
    global _settings
    settings = _validate(LogSettings(enabled=False) if settings is None else settings)
    with _lock:
        for handler in _handlers:
            ROOT.removeHandler(handler)
            handler.close()
        _handlers.clear()
        if settings.enabled:
            formatter = _JsonFormatter() if settings.format == "json" else _TextFormatter()
            if settings.file is not None:
                settings.file.parent.mkdir(parents=True, exist_ok=True)
                _handlers.append(logging.FileHandler(settings.file, "a" if settings.append else "w", encoding="utf-8"))
            if settings.console or (settings.console is None and settings.file is None):
                _handlers.append(_ConsoleHandler())
            for handler in _handlers:
                handler.setFormatter(formatter)
                handler.addFilter(_context)
                ROOT.addHandler(handler)
            ROOT.setLevel(LEVELS[settings.level])
        else:
            ROOT.setLevel(logging.NOTSET)
        _settings = settings
    if settings.enabled:
        _header()
    return settings


def log_settings():
    return _settings


def _from_environment():
    """CPPBUILD_LOG=<level>|1|0, CPPBUILD_LOG_FILE=<path>, CPPBUILD_LOG_FORMAT=text|json."""
    value = os.environ.get("CPPBUILD_LOG", "").strip()
    if not value or value.lower() in ("0", "false", "off", "no"):
        return
    level = "DEBUG" if value.lower() in ("1", "true", "on", "yes") else value
    configure_logging(LogSettings(level=level, file=os.environ.get("CPPBUILD_LOG_FILE") or None,
                                  format=os.environ.get("CPPBUILD_LOG_FORMAT", "text").strip().lower() or "text"))


def _header():
    from importlib import metadata
    try:
        version = metadata.version("cppbuild")
    except metadata.PackageNotFoundError:
        version = "unknown"
    ROOT.info("logging started: level=%s file=%s format=%s", _settings.level, _settings.file, _settings.format,
              extra={"data": {"cppbuild": version, "python": sys.version, "executable": sys.executable,
                              "platform": platform.platform(), "pid": os.getpid(), "cwd": os.getcwd(),
                              "argv": sys.argv, "package": str(Path(__file__).parent)}})
    ROOT.debug("environment: cppbuild=%s python=%s platform=%s pid=%d cwd=%s package=%s",
               version, sys.version.replace("\n", " "), platform.platform(), os.getpid(), os.getcwd(),
               Path(__file__).parent)


# Formatting --------------------------------------------------------------------------

_call = contextvars.ContextVar("cppbuild_log_call", default=())
_ids = itertools.count(1)


def _context(record):
    stack = _call.get()
    record.call = f"#{stack[-1]}" if stack else "-"
    record.depth = len(stack)
    return True


class _TextFormatter(logging.Formatter):
    def __init__(self):
        super().__init__("%(asctime)s %(levelname)-7s %(name)s [%(threadName)s] %(call)s %(message)s")

    def formatTime(self, record, datefmt=None):
        return datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds")


class _JsonFormatter(logging.Formatter):
    def format(self, record):
        entry = {"time": datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
                 "level": record.levelname, "logger": record.name, "thread": record.threadName,
                 "call": getattr(record, "call", "-"), "depth": getattr(record, "depth", 0),
                 "message": record.getMessage()}
        if getattr(record, "data", None) is not None:
            entry["data"] = record.data
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


class _ConsoleHandler(logging.StreamHandler):
    """stderr looked up per record, so redirected/captured streams are honoured."""

    def __init__(self):
        super().__init__(sys.stderr)

    def emit(self, record):
        self.stream = sys.stderr
        try:
            super().emit(record)
        except UnicodeEncodeError:
            self.stream.write(self.format(record).encode("ascii", "backslashreplace").decode("ascii") + "\n")


# Values ------------------------------------------------------------------------------

_SECRET_NAME = re.compile(r"TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|API_?KEY|PRIVATE_?KEY|AUTH", re.IGNORECASE)
_URL_PASSWORD = re.compile(r"(://[^/\s:@]*:)[^/\s@]+@")


def redact(text):
    return _URL_PASSWORD.sub(r"\1***@", str(text))


def secret(name):
    return bool(_SECRET_NAME.search(name))


def masked_environment(env):
    return {key: ("***" if secret(key) else value) for key, value in sorted(env.items())}


def environment_changes(env):
    """Variables a child process gets differently from this process."""
    if env is None:
        return {}
    base = {key.upper() if os.name == "nt" else key: value for key, value in os.environ.items()}
    target = {key.upper() if os.name == "nt" else key: value for key, value in env.items()}
    changes = {}
    for key in sorted(set(base) | set(target)):
        if base.get(key) != target.get(key):
            changes[key] = "***" if secret(key) else target.get(key, "<removed>")
    return changes


def describe(value):
    """A short identity for CppBuild objects; repr() for everything else."""
    from .core import Project, Solution, ProjectSettings, SolutionSettings
    if isinstance(value, Solution):
        return f"Solution({str(value.root)!r})"
    if isinstance(value, Project):
        return f"Project({value.name!r}, {str(value.root)!r})"
    if isinstance(value, (ProjectSettings, SolutionSettings)):
        return f"{type(value).__name__}({describe(value.owner)})"
    if type(value) in (list, tuple, set, frozenset):
        items = ", ".join(describe(item) for item in (sorted(value, key=repr) if isinstance(value, (set, frozenset)) else value))
        return {list: f"[{items}]", tuple: f"({items}{',' if len(value) == 1 else ''})"}.get(type(value), f"{{{items}}}")
    if type(value) is dict:
        return "{" + ", ".join(f"{describe(k)}: {describe(v)}" for k, v in value.items()) + "}"
    try:
        return redact(repr(value))
    except Exception as exc:  # a repr must never break the operation being logged
        return f"<{type(value).__name__}: repr failed: {exc}>"


def enabled(level):
    return ROOT.isEnabledFor(level)


# Public API calls --------------------------------------------------------------------

def api(cls):
    """Log every public method of cls: the caller's calls at INFO, nested ones at DEBUG."""
    for name, member in list(vars(cls).items()):
        if name.startswith("_"):
            continue
        if isinstance(member, classmethod):
            setattr(cls, name, classmethod(_traced(cls.__name__, name, member.__func__, bound=False, skip=1)))
        elif isinstance(member, staticmethod):
            setattr(cls, name, staticmethod(_traced(cls.__name__, name, member.__func__, bound=False)))
        elif callable(member) and not isinstance(member, type):
            setattr(cls, name, _traced(cls.__name__, name, member, bound=True))
    return cls


_api_log = logger("api")


def _traced(owner, name, function, *, bound, skip=0):
    @functools.wraps(function)
    def invoke(*args, **kwargs):
        if not _api_log.isEnabledFor(logging.DEBUG if _call.get() else logging.INFO):
            return function(*args, **kwargs)
        # The instance's own class, so inherited methods name the subclass.
        label = f"{type(args[0]).__name__ if bound and args else owner}.{name}"
        stack = _call.get()
        level = logging.DEBUG if stack else logging.INFO
        call_id = next(_ids)
        token = _call.set(stack + (call_id,))
        parent = f" (in #{stack[-1]})" if stack else ""
        try:
            if _api_log.isEnabledFor(logging.DEBUG):
                shown = [describe(a) for a in args[skip:]]
                target = (shown.pop(0) + " ") if bound and shown else ""
                arguments = ", ".join(shown + [f"{k}={describe(v)}" for k, v in kwargs.items()])
                _api_log.log(level, "call %s%s(%s)%s", target, label, arguments, parent)
            else:
                _api_log.log(level, "call %s%s", label, parent)
            started = time.perf_counter()
            try:
                result = function(*args, **kwargs)
            except BaseException as exc:
                elapsed = time.perf_counter() - started
                _api_log.log(logging.ERROR if not stack else logging.DEBUG,
                             "raise %s after %.3fs: %s: %s", label, elapsed, type(exc).__name__, exc,
                             exc_info=not stack)
                raise
            elapsed = time.perf_counter() - started
            if _api_log.isEnabledFor(logging.DEBUG):
                _api_log.log(level, "return %s after %.3fs: %s", label, elapsed, describe(result))
            else:
                success = getattr(result, "success", None)
                _api_log.log(level, "return %s after %.3fs%s", label, elapsed,
                             "" if success is None else f" success={success}")
            return result
        finally:
            _call.reset(token)
    return invoke


# Commands ----------------------------------------------------------------------------

_process_log = logger("process")


@contextmanager
def command(arguments, cwd=None, env=None):
    """Log a child process around its run; call the yielded function with (returncode, output)."""
    shown = " ".join(redact(_quote(str(a))) for a in arguments)
    _process_log.info("run: %s", shown, extra={"data": {"command": [redact(a) for a in map(str, arguments)],
                                                        "cwd": None if cwd is None else str(cwd)}})
    _process_log.debug("cwd: %s", cwd)
    if _process_log.isEnabledFor(logging.DEBUG):
        _process_log.debug("environment changes: %s", json.dumps(environment_changes(env), ensure_ascii=False))
    if _process_log.isEnabledFor(TRACE):
        _process_log.log(TRACE, "environment: %s",
                         json.dumps(masked_environment(os.environ if env is None else env), ensure_ascii=False))
    started = time.perf_counter()

    def finished(returncode, output=None):
        elapsed = time.perf_counter() - started
        _process_log.log(logging.INFO if returncode == 0 else logging.WARNING,
                         "exit %s after %.3fs: %s", returncode, elapsed, shown,
                         extra={"data": {"returncode": returncode, "seconds": round(elapsed, 3)}})
        if output is not None and _process_log.isEnabledFor(logging.DEBUG):
            _process_log.debug("output (%d chars) of %s:\n%s", len(output), shown, redact(output))
    try:
        yield finished
    except BaseException as exc:
        _process_log.error("failed to run %s after %.3fs: %s: %s", shown, time.perf_counter() - started,
                           type(exc).__name__, exc)
        raise


def _quote(text):
    return f'"{text}"' if (not text or any(c in text for c in " \t\"")) else text


# Files -------------------------------------------------------------------------------

_file_log = logger("files")


def wrote(path, data):
    if not _file_log.isEnabledFor(logging.INFO):
        return
    _file_log.info("write %s (%d bytes)", path, len(data))
    if _file_log.isEnabledFor(TRACE):
        _file_log.log(TRACE, "content of %s:\n%s", path, _text(data))


def removed(path, what="file"):
    _file_log.info("remove %s %s", what, path)


def read(path, text):
    if _file_log.isEnabledFor(TRACE):
        _file_log.log(TRACE, "read %s:\n%s", path, text)
    else:
        _file_log.debug("read %s", path)


def _text(data):
    try:
        return data.decode("utf-8") if isinstance(data, bytes) else str(data)
    except UnicodeDecodeError:
        return f"<{len(data)} bytes, not UTF-8>"

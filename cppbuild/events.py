"""Instance-local ordered notifications and file-change update batching."""
from dataclasses import dataclass
from functools import wraps
import uuid

from .models import SettingsError


OPERATIONS = {"update", "build", "rebuild", "clean", "run", "test"}
EVENTS = {"file_changed"} | {f"{phase}_{operation}" for phase in ("before", "after") for operation in OPERATIONS}


@dataclass(frozen=True)
class Event:
    name: str
    solution: object
    project: object | None = None
    changed_paths: tuple = ()
    result: object | None = None
    error: Exception | None = None


class EventCallbackError(RuntimeError):
    def __init__(self, errors, result=None):
        self.errors, self.result = tuple(errors), result
        super().__init__("; ".join(self.errors))


class Dispatcher:
    def __init__(self, solution):
        self.solution = solution
        self.callbacks = {}
        self.active = set()
        self.stack = []
        self.batch = None

    def on(self, event, callback):
        if not isinstance(event, str) or event not in EVENTS or not callable(callback):
            raise SettingsError("Expected a supported event and callable")
        key = uuid.uuid4().hex
        self.callbacks[key] = (event, callback)
        return key

    def off(self, key):
        return self.callbacks.pop(key, None) is not None

    def emit(self, event):
        if len(self.stack) >= 32:
            return ["Event nesting limit exceeded; remaining notifications skipped"]
        errors = []
        self.stack.append(event.name)
        try:
            for key, (name, callback) in tuple(self.callbacks.items()):
                if name != event.name or key in self.active or key not in self.callbacks:
                    continue
                self.active.add(key)
                try:
                    callback(event)
                except Exception as exc:
                    errors.append(f"{event.name}/{key}: {type(exc).__name__}: {exc}")
                finally:
                    self.active.remove(key)
        finally:
            self.stack.pop()
        return errors

    def check_file_change(self):
        if self.stack and self.stack[-1] != "file_changed":
            raise SettingsError("File mutation in callbacks is supported only for file_changed")

    def file_changed(self, project, paths, auto_update):
        from .engine import FileOperationReport
        outer = self.batch is None
        if outer:
            self.batch = {"paths": [], "updates": {}, "errors": []}
        batch = self.batch
        batch["paths"].extend(paths)
        batch["updates"][project] = batch["updates"].get(project, False) or auto_update
        event = Event("file_changed", self.solution, project, tuple(paths))
        batch["errors"].extend(self.emit(event))
        if not outer:
            return FileOperationReport(tuple(paths))
        # End notification batching before update lifecycle notifications.
        self.batch = None
        updates, own_update, own_error = [], None, None
        pending = False
        for changed, enabled in batch["updates"].items():
            if not enabled:
                pending = True
                continue
            result, error = None, None
            try:
                result = changed.update()
            except (OSError, ValueError, NotImplementedError, EventCallbackError) as exc:
                error = f"{type(exc).__name__}: {exc}"
            updates.append((changed.name, result, error))
            pending |= error is not None or result is None or not result.success
            if changed is project:
                own_update, own_error = result, error
        return FileOperationReport(tuple(batch["paths"]), own_update, own_error,
                                   pending, tuple(batch["errors"]), tuple(updates))


def operation(function):
    @wraps(function)
    def invoke(owner, *args, **kwargs):
        solution = getattr(owner, "solution", owner)
        project = owner if owner is not solution else None
        dispatcher = solution._events
        if dispatcher.stack:
            raise SettingsError("Build/update/run/test/clean cannot be re-entered from a callback")
        name = function.__name__
        errors = dispatcher.emit(Event("before_" + name, solution, project))
        if errors:
            owner._last_operation = (name, None)
            raise EventCallbackError(errors)
        try:
            result = function(owner, *args, **kwargs)
        except Exception as exc:
            owner._last_operation = (name, None)
            if project is not None:
                if name == "update":
                    project._generation_state = "failed"
                else:
                    project._build_state = "failed"
            failures = dispatcher.emit(Event("after_" + name, solution, project, error=exc))
            if failures:
                exc.add_note("; ".join(failures))
            raise
        owner._last_operation = (name, result)
        errors = dispatcher.emit(Event("after_" + name, solution, project, result=result))
        if errors:
            raise EventCallbackError(errors, result)
        return result
    return invoke

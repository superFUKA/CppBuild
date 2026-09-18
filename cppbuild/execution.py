"""Ordered batches of applications with optional asynchronous completion."""
from concurrent.futures import ThreadPoolExecutor

from . import engine


class RunReport:
    """A pending run is not a success. wait() returns the final OperationReport."""

    def __init__(self, future, processes, artifacts):
        self._future = future
        self._processes = tuple(processes)
        self.artifacts = tuple(artifacts)

    @property
    def done(self):
        return self._future.done()

    @property
    def processes(self):
        return self.wait().processes if self.done else self._processes

    @property
    def success(self):
        return self.wait().success if self.done else None

    def wait(self, timeout=None):
        return self._future.result(timeout)


def execute(commands, processes=(), artifacts=(), *, parallel=1, wait=True, continue_on_failure=False):
    # Freeze commands/settings before handing work to the background thread.
    commands = [(tuple(command), cwd, dict(env)) for command, cwd, env in commands]
    processes, artifacts = tuple(processes), tuple(artifacts)

    def run():
        results = list(processes)
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            for start in range(0, len(commands), parallel):
                batch = commands[start:start + parallel]
                pending = [pool.submit(engine.process, command, cwd, env) for command, cwd, env in batch]
                completed = [future.result() for future in pending]
                results.extend(completed)
                if not continue_on_failure and any(not result.success for result in completed):
                    break
        return engine.OperationReport(tuple(results), artifacts)

    if wait:
        return run()
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(run)
    executor.shutdown(wait=False)
    return RunReport(future, processes, artifacts)

"""Solution.transaction(): setting changes and the update that applies them, kept or undone together."""
from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path

from . import storage
from .models import SettingsError


@dataclass
class TransactionReport:
    """The update run when the block ends, and what was undone.

    restored: the files were put back because the block raised or the update failed.
    conflicts: files changed by someone else meanwhile, left as they are.
    """
    update: object = None
    restored: bool = False
    restored_paths: tuple = ()
    removed_clones: tuple = ()
    conflicts: tuple = ()

    @property
    def success(self):
        return self.update is not None and self.update.success and not self.restored


@contextmanager
def transaction(solution):
    if storage.journal() is not None:
        raise SettingsError("Transactions cannot be nested")
    report = TransactionReport()
    journal = storage.Journal()
    token = storage.begin(journal)
    active = True
    try:
        try:
            yield report
            report.update = solution.update()
        except BaseException:
            storage.end(token)
            active = False
            _restore(solution, journal, report)
            raise
        storage.end(token)
        active = False
        if not report.update.success:
            _restore(solution, journal, report)
    finally:
        if active:
            storage.end(token)


def _restore(solution, journal, report):
    restored, conflicts = [], []
    for path, previous in journal.previous.items():
        current = path.read_bytes() if path.is_file() else None
        if current == previous:
            continue
        if path in journal.latest and current != journal.latest[path]:
            conflicts.append(path)
            continue
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            storage.atomic_write(path, previous)
        restored.append(path)
    for directory in journal.directories:
        _remove_empty(directory)
    from .git_sources import discard
    discard(journal.clones)
    report.restored = True
    report.restored_paths = tuple(sorted(restored, key=str))
    report.removed_clones = tuple(journal.clones)
    report.conflicts = tuple(sorted(conflicts, key=str))
    solution.settings.reload()


def _remove_empty(directory):
    """Remove the directories a transaction created, where nothing else was put since."""
    if not directory.is_dir():
        return
    for current, _, _ in sorted(os.walk(directory), key=lambda item: len(item[0]), reverse=True):
        try:
            Path(current).rmdir()
        except OSError:
            pass

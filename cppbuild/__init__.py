"""CppBuild: see deliverables/MILESTONES.md for implemented scope."""
from .core import Project, Solution
from .engine import FileOperationReport, OperationReport, ProcessReport, UpdateReport
from .execution import RunReport
from .testing import TestReport, TestCaseResult
from .templates import TemplateTools
from .events import Event, EventCallbackError
from .environment import Environment, EnvironmentOptions, EnvironmentReport, EnvironmentItem, GeneratorInfo
from .generators import CompilerInfo
from .information import ProjectInfo, SolutionInfo
from .models import ToolSettings, SolutionFolderSettings, CMakeSettings
from .models import (
    ChangeReport, INHERIT, ProjectBuildSettings, ProjectSettingsData, ProjectType,
    SettingsConflictError, SettingsError, SolutionBuildSettings, SolutionSettingsData,
    TypeSettingsData, BuildOption, BuildEvents, Dependency, LinkReport, CMakePackage, CMakeSource, ImportedLibrary, ProjectReference,
    MissingDependency, MissingDependenciesError,
    GitSource, FetchFailure, GitFetchError, GitSourceConflictError,
    InvalidValueError, AlreadyRegisteredError, NotRegisteredError, NoMainProjectError,
    CONFIGURATIONS, ARCHITECTURES, CPP_STANDARDS, MSVC_RUNTIMES, LIBRARY_TYPES, SWITCHABLE_TYPES, FileKind,
)
from .git_sources import FetchReport, GitSourceStatus, UnlinkReport
from .transactions import TransactionReport
from .generators import SUPPORTED as GENERATORS
from .logs import LogSettings, configure_logging, log_settings, TRACE as LOG_TRACE
from . import logs as _logs

LOG_LEVELS = tuple(_logs.LEVELS)
_logs._from_environment()

__all__ = [
    "SolutionFolderSettings", "Project", "Solution", "ProjectType", "ProjectSettingsData", "SolutionSettingsData",
    "TypeSettingsData", "BuildOption", "BuildEvents", "ProjectBuildSettings", "SolutionBuildSettings", "INHERIT",
    "ChangeReport", "SettingsError", "SettingsConflictError",
    "FileOperationReport", "OperationReport", "ProcessReport", "UpdateReport",
    "Dependency", "LinkReport", "ProjectReference",
    "CMakePackage", "CMakeSource", "ImportedLibrary",
    "RunReport", "TestReport", "TestCaseResult",
    "TemplateTools", "Event", "EventCallbackError", "Environment", "EnvironmentOptions",
    "EnvironmentReport", "EnvironmentItem", "ToolSettings", "ProjectInfo", "SolutionInfo",
    "CMakeSettings", "GeneratorInfo", "CompilerInfo", "MissingDependency", "MissingDependenciesError",
    "GitSource", "GitSourceStatus", "FetchReport", "FetchFailure", "GitFetchError", "GitSourceConflictError",
    "InvalidValueError", "AlreadyRegisteredError", "NotRegisteredError", "NoMainProjectError",
    "CONFIGURATIONS", "ARCHITECTURES", "CPP_STANDARDS", "MSVC_RUNTIMES", "LIBRARY_TYPES", "SWITCHABLE_TYPES", "GENERATORS",
    "FileKind", "TransactionReport", "UnlinkReport",
    "LogSettings", "configure_logging", "log_settings", "LOG_TRACE", "LOG_LEVELS",
]

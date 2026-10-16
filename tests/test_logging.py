import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import cppbuild
from cppbuild import InvalidValueError, LogSettings, ProjectType as T, SettingsError, Solution
from cppbuild import engine, logs


class LoggingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="cppbuild-logging-")
        self.addCleanup(self.temporary.cleanup)
        # Restore what the run started with (CPPBUILD_LOG may log the whole suite).
        self.addCleanup(cppbuild.configure_logging, cppbuild.log_settings())
        cppbuild.configure_logging(None)
        self.root = Path(self.temporary.name)
        self.log = self.root / "logs" / "cppbuild.log"

    def configure(self, level, **values):
        return cppbuild.configure_logging(LogSettings(level=level, file=self.log, append=False, **values))

    def text(self):
        return self.log.read_text(encoding="utf-8")

    def workflow(self):
        solution = Solution.create(self.root / "S", "S")
        app = solution.add_project("App", "App", T.EXECUTABLE)
        app.add_file("src/main.cpp", content="int main() { return 0; }", auto_update=False)
        return solution, app

    def test_off_by_default(self):
        self.assertFalse(cppbuild.log_settings().enabled)
        self.assertTrue(all(type(h).__name__ == "NullHandler" for h in logs.ROOT.handlers))
        self.workflow()
        self.assertFalse(self.log.exists())

    def test_info_records_caller_calls_and_files_only(self):
        applied = self.configure("info")
        self.assertEqual(applied.level, "INFO")
        self.assertEqual(applied.file, self.log.resolve())
        self.workflow()
        text = self.text()
        self.assertIn("logging started", text)
        self.assertIn("call Solution.create", text)
        self.assertIn("return Solution.add_project after", text)
        self.assertIn("write " + str(self.root / "S" / ".cppbuild" / "project.json"), text)
        self.assertNotIn("SolutionSettings.get", text)  # nested calls are DEBUG
        self.assertNotIn("'S'", text)  # arguments are DEBUG
        self.assertNotIn(" DEBUG ", text)

    def test_debug_records_arguments_nested_calls_events_and_locks(self):
        self.configure("DEBUG")
        self.workflow()
        text = self.text()
        self.assertIn("call Solution.create(", text)
        self.assertIn(", 'S')", text)
        self.assertIn("Project('App'", text)
        self.assertRegex(text, r"call SolutionSettings\(Solution\(.*\)\) SolutionSettings\.get\(\) \(in #\d+\)")
        self.assertIn("event file_changed project=App", text)
        self.assertIn("lock acquired:", text)
        self.assertNotIn("content of", text)  # file content is TRACE

    def test_trace_records_written_content(self):
        self.configure("TRACE")
        self.workflow()
        text = self.text()
        self.assertIn("content of", text)
        self.assertIn('"name": "App"', text)

    def test_json_lines(self):
        self.configure("DEBUG", format="json")
        self.workflow()
        entries = [json.loads(line) for line in self.text().splitlines()]
        self.assertEqual(entries[0]["data"]["pid"], os.getpid())
        call = next(e for e in entries if e["message"].startswith("call ") and "Solution.add_project(" in e["message"])
        self.assertEqual(call["level"], "INFO")
        self.assertEqual(call["logger"], "cppbuild.api")
        self.assertEqual(call["depth"], 1)
        nested = next(e for e in entries if "SolutionSettings.get" in e["message"])
        self.assertEqual(nested["depth"], 2)

    def test_errors_are_logged_with_traceback_and_reraised(self):
        self.configure("INFO")
        solution, _ = self.workflow()
        with self.assertRaises(SettingsError):
            solution.get_project("Missing")
        text = self.text()
        self.assertRegex(text, r"ERROR +cppbuild\.api .*raise Solution\.get_project after")
        self.assertIn("Traceback (most recent call last)", text)

    def test_disable_and_append(self):
        self.configure("INFO")
        Solution.create(self.root / "A", "A")
        cppbuild.configure_logging(LogSettings(enabled=False))
        Solution.create(self.root / "B", "B")
        self.assertIn("A", self.text())
        self.assertNotIn(str(self.root / "B"), self.text())
        cppbuild.configure_logging(LogSettings(level="INFO", file=self.log))
        Solution.create(self.root / "C", "C")
        text = self.text()
        self.assertIn(str(self.root / "A"), text)
        self.assertIn(str(self.root / "C"), text)

    def test_console_when_no_file(self):
        with patch.object(sys, "stderr", new_callable=__import__("io").StringIO) as stderr:
            cppbuild.configure_logging(LogSettings(level="INFO"))
            Solution.create(self.root / "S", "S")
        self.assertIn("call Solution.create", stderr.getvalue())

    def test_invalid_settings(self):
        for values, field in (({"level": "LOUD"}, "level"), ({"format": "xml"}, "format"),
                              ({"console": "yes"}, "console"), ({"file": ""}, "file"), ({"enabled": 1}, "enabled")):
            with self.subTest(values=values), self.assertRaises(InvalidValueError) as raised:
                cppbuild.configure_logging(LogSettings(**values))
            self.assertEqual(raised.exception.field, field)
        with self.assertRaises(InvalidValueError) as raised:
            cppbuild.configure_logging(LogSettings(level="LOUD"))
        self.assertEqual(raised.exception.allowed, cppbuild.LOG_LEVELS)
        self.assertFalse(cppbuild.log_settings().enabled)

    def test_process_command_output_and_redaction(self):
        self.configure("TRACE")
        env = dict(os.environ, CPPBUILD_TEST_TOKEN="hunter2", CPPBUILD_TEST_VALUE="visible")
        result = engine.process([sys.executable, "-c", "print('hello from child https://me:pw@example.com/x')"],
                                self.root, env)
        self.assertTrue(result.success)
        text = self.text()
        self.assertIn("run: ", text)
        self.assertIn("exit 0 after", text)
        self.assertIn("hello from child https://me:***@example.com/x", text)
        self.assertIn('"CPPBUILD_TEST_VALUE": "visible"', text)
        self.assertNotIn("hunter2", text)
        self.assertNotIn(":pw@", text)

    def test_failed_process_is_a_warning(self):
        self.configure("INFO")
        engine.process([sys.executable, "-c", "raise SystemExit(3)"], self.root)
        self.assertRegex(self.text(), r"WARNING +cppbuild\.process .*exit 3 after")

    def test_environment_variables(self):
        values = {"CPPBUILD_LOG": "trace", "CPPBUILD_LOG_FILE": str(self.log), "CPPBUILD_LOG_FORMAT": "json"}
        with patch.dict(os.environ, values):
            logs._from_environment()
        applied = cppbuild.log_settings()
        self.assertEqual((applied.enabled, applied.level, applied.format), (True, "TRACE", "json"))
        with patch.dict(os.environ, {"CPPBUILD_LOG": "1"}), patch.object(sys, "stderr", new_callable=__import__("io").StringIO):
            cppbuild.configure_logging(None)
            logs._from_environment()
        self.assertEqual(cppbuild.log_settings().level, "DEBUG")
        cppbuild.configure_logging(None)
        with patch.dict(os.environ, {"CPPBUILD_LOG": "0"}):
            logs._from_environment()
        self.assertFalse(cppbuild.log_settings().enabled)


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from validate_atopile_source import source_failure_evidence


class AtopileSourceDiagnosticsTests(unittest.TestCase):
    def test_nested_linker_errors_expose_the_missing_resource(self):
        failure = ExceptionGroup("Linker errors", [
            FileNotFoundError("parts/MissingIC/MissingIC.ato"),
            ExceptionGroup("Imports", [ValueError("unknown signal: common_dm")]),
        ])
        self.assertEqual(source_failure_evidence(failure), [
            "FileNotFoundError: parts/MissingIC/MissingIC.ato",
            "ValueError: unknown signal: common_dm",
        ])

    def test_error_count_and_message_size_are_bounded(self):
        failure = ExceptionGroup("Linker errors", [ValueError("x"*4000) for _ in range(30)])
        evidence = source_failure_evidence(failure)
        self.assertEqual(len(evidence), 9)
        self.assertTrue(all(len(line) <= 480 for line in evidence))
        self.assertIn("omitted", evidence[-1])

    def test_explicit_cause_is_retained_without_following_a_cycle(self):
        outer = RuntimeError("resource load failed\nwhile linking")
        inner = FileNotFoundError("parts/IC.ato")
        outer.__cause__ = inner
        inner.__cause__ = outer
        self.assertEqual(source_failure_evidence(outer), [
            "RuntimeError: resource load failed while linking",
            "FileNotFoundError: parts/IC.ato",
        ])


if __name__ == "__main__":
    unittest.main()

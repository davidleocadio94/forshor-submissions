import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('policy_memcap', Path(__file__).parents[1] / 'memcap.py')
memcap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(memcap)


class LinuxMemoryTests(unittest.TestCase):
    def test_limit_classification_comes_from_the_guard_outcome(self):
        for stage, outcome in (("memory", "killed: memory 12.04 GB > cap 12.0 GB"),
                               ("timeout", "killed: timeout 900 s")):
            self.assertEqual(memcap.limit_rejection({"outcome": outcome}), (stage, outcome))
        for record in ({}, {"outcome": "finished", "returncode": 1}, {"outcome": None},
                       {"outcome": "finished", "stderr": "killed: memory 12.04 GB > cap 12.0 GB"}):
            self.assertIsNone(memcap.limit_rejection(record))

    def test_cap_counts_resident_and_swapped_pages(self):
        status = 'Name:\tlean\nVmRSS:\t1024 kB\nVmSwap:\t512 kB\n'
        with patch.object(memcap.sys, 'platform', 'linux'), patch.object(Path, 'read_text', return_value=status):
            self.assertEqual(memcap.mem(123), 1536 * 1024)

    def test_a_process_exiting_between_polls_is_not_an_error(self):
        with patch.object(memcap.sys, 'platform', 'linux'), patch.object(Path, 'read_text', side_effect=FileNotFoundError):
            self.assertEqual(memcap.mem(123), 0)

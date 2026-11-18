"""A guard kill must survive long compiler output and the intake's truncation."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import check_ptp_policy as ptp


class MemoryFeedback(unittest.TestCase):
    def test_capped_stages_preserve_guard_reason_before_compiler_output(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'memory.json'
            process = subprocess.CompletedProcess([], 1, '', 'compiler diagnostic\n' * 500)
            for stage, outcome in [('memory', 'killed: memory 12.04 GB > cap 12.0 GB'),
                                   ('timeout', 'killed: timeout 900 s')]:
                log.write_text(json.dumps({'outcome': outcome, 'seconds': 42}))
                with self.subTest(stage=stage), patch.object(ptp, 'run', return_value=(process, 42)):
                    with self.assertRaises(ptp.Reject) as caught:
                        ptp.capped(['lake', 'build', ptp.CERT_MOD], 12, 900, log)
                    self.assertEqual((caught.exception.stage, caught.exception.reason), (stage, outcome))
                    self.assertEqual(json.loads(caught.exception.detail)['seconds'], 42)

    def test_normal_build_errors_remain_for_the_caller_to_classify(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'memory.json'
            process = subprocess.CompletedProcess([], 1, '', 'type mismatch')
            for contents in (None, 'not json', '{"outcome": "finished"}', '[]'):
                if contents is not None:
                    log.write_text(contents)
                with self.subTest(contents=contents), patch.object(ptp, 'run', return_value=(process, 1)):
                    self.assertEqual(ptp.capped(['lake', 'build', ptp.CERT_MOD], 12, 900, log), (process, 1))

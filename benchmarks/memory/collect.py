#!/usr/bin/env python3
"""Retain small evidence and memory diagnostics, never bulky Lean build trees."""
import json
import os
from pathlib import Path
import shutil
import sys
out = Path(sys.argv[1])
for source in (out / 'work/evidence').glob('*'):
    if source.is_file():
        target = out / 'evidence' / source.name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(source, target)
log = out / 'work/forshor.log'
if log.exists():
    shutil.copyfile(log, out / 'forshor-build.log')
target = out / 'memcap-records'
target.mkdir(exist_ok=True)
for source in Path(os.environ['TMPDIR']).iterdir():
    if not source.is_file() or source.stat().st_size > 1_000_000:
        continue
    try:
        data = json.loads(source.read_text())
        if isinstance(data, dict) and 'peak_gb' in data and 'cmd' in data:
            shutil.copyfile(source, target / (source.name + '.json'))
    except (ValueError, UnicodeError, OSError):
        pass

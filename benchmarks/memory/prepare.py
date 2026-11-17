#!/usr/bin/env python3
"""Prepare one fixed benchmark input with the trusted template converter."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

p = argparse.ArgumentParser()
p.add_argument('--case', required=True)
p.add_argument('--verifier', type=Path, required=True)
p.add_argument('--out', type=Path, required=True)
a = p.parse_args()
base = Path(__file__).resolve().parent
manifest = json.loads((base / 'manifest.json').read_text())
case = manifest['cases'][a.case]
root = a.verifier.resolve()
assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip() == manifest['verifier_commit']
sys.path[:0] = [str(root / 'evaluation'), str(root)]
from export_v2 import engine, epoch, canon
eng = engine.load()
model = epoch.model_of()
assert canon.epoch_of(model) == manifest['expected_epoch']
if 'seed' in case:
    sys.path.insert(0, str(root / 'evaluation/starter/pack/harness'))
    import policy_to_template
    seed = root / 'evaluation/engine/data/forshor_submitted' / case['seed'] / 'site_policy.json'
    source = policy_to_template.template_from_file(seed, lib='ForShor')
else:
    raw = (base / case['source']).read_bytes()
    if 'source_sha256' in case:
        assert hashlib.sha256(raw).hexdigest() == case['source_sha256']
    source = raw.decode()
    if 'bands_override' in case:
        source, count = re.subn(r'(?m)^  bands := .*$', '  bands := ' + case['bands_override'], source)
        assert count == 1
from scripts.ci.policy_source import validate
validate(source)
a.out.mkdir(parents=True, exist_ok=True)
(a.out / 'Policy.lean').write_text(source)
(a.out / 'identity.json').write_text(json.dumps({
    'case': a.case, 'selection': case, 'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
    'verifier_commit': manifest['verifier_commit'], 'forshor_commit': manifest['forshor_commit'],
    'epoch': canon.epoch_of(model), 'model': model,
}, indent=2) + '\n')

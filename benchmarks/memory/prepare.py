#!/usr/bin/env python3
"""Prepare the approved simple/heavy/256-band comparison from accepted tables."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
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
original = json.loads((base / case['base_policy']).read_text())
assert canon.policy_digest(original) == case['base_policy_digest']
assert hashlib.sha256((base / case['base_source']).read_bytes()).hexdigest() == case['base_source_sha256']
tables = {k: next(copy.deepcopy(b) for b in original['bands'] if b['k'] == k) for k in [4, 8]}
low = {**tables[4], 'threshold': 128}
top_k = 4 if case['variant'] == 'simple' else 8
high = {**tables[top_k], 'threshold': 1024}
bands = [high, low]
band_count = case.get('band_count', 256 if case['variant'] == 'heavy-256' else 2)
assert 2 <= band_count <= 256
if band_count > 2:
    # Split the original top interval with the SAME table.
    # Dispatch at every width, all table operations and numeric values stay fixed.
    bands = [{**high, 'threshold': n} for n in range(1024 + band_count - 2, 1023, -1)] + [low]
    def table_at(bs, n):
        b = next((b for b in bs if n >= b['threshold']), None)
        return {k: v for k, v in b.items() if k != 'threshold'} if b else None
    breakpoints = {0} | {max(0, b['threshold'] + d) for b in bands for d in [-1, 0, 1]}
    assert all(table_at(bands, n) == table_at([high, low], n) for n in breakpoints)
assert len(bands) == band_count
policy = {'mode': original['mode'], 'name': a.case, 'bands': bands}
# Preserve the production parser's complete table correctness and numeric checks.
checked = eng.tables.Policy.from_json(policy)
sys.path.insert(0, str(root / 'evaluation/starter/pack/harness'))
import policy_to_template
source = policy_to_template.template_text(checked, lib='ForShor')
if case.get('kernel_sorted'):
    assert source.count('  sorted := by decide\n') == 1
    source = source.replace('  sorted := by decide\n', '  sorted := by decide +kernel\n')
from scripts.ci.policy_source import validate
validate(source)
counts = {
    'bands': len(bands), 'k': sorted({b['k'] for b in bands}),
    'distinct_tables': len({b.table.digest for b in checked.bands}),
    'max_operations_per_band': max(len(b['operations']) for b in bands),
    'total_operations_across_bands': sum(len(b['operations']) for b in bands),
    'source_bytes': len(source.encode()),
}
a.out.mkdir(parents=True, exist_ok=True)
(a.out / 'Policy.lean').write_text(source)
(a.out / 'prepared-policy.json').write_text(json.dumps(policy, indent=2) + '\n')
(a.out / 'identity.json').write_text(json.dumps({
    'case': a.case, 'selection': case, 'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
    'prepared_policy_digest': canon.policy_digest(policy), 'counts': counts,
    'verifier_commit': manifest['verifier_commit'], 'forshor_commit': manifest['forshor_commit'],
    'epoch': canon.epoch_of(model), 'model': model,
}, indent=2) + '\n')
print(json.dumps({'case': a.case, **counts, 'digest': canon.policy_digest(policy)}))

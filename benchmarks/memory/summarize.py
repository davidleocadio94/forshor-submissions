#!/usr/bin/env python3
"""Summarize downloaded benchmark artifacts without importing the resource engine."""
import argparse
import json
from pathlib import Path
import statistics


def stats(values):
    return {'n': len(values), 'min': min(values), 'median': statistics.median(values), 'max': max(values),
            'sample_stdev': statistics.stdev(values) if len(values) > 1 else None} if values else None


def main():
    p = argparse.ArgumentParser()
    p.add_argument('artifacts', type=Path)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    records = []
    for path in sorted(a.artifacts.rglob('observations.json')):
        root = path.parent
        identity = json.loads((root / 'identity.json').read_text())
        host = json.loads((root / 'host.json').read_text())
        obs = json.loads(path.read_text())
        verification = json.loads((root / 'verification.json').read_text()) if (root / 'verification.json').exists() else {}
        outcome = json.loads((root / 'intake-result.json').read_text()) if (root / 'intake-result.json').exists() else {}
        samples = [json.loads(s) for s in (root / 'samples.jsonl').read_text().splitlines()]
        stages = {}
        for s in obs['stages']:
            name = s['stage']
            stages.setdefault(name, []).append({
                'rss_swap_GiB': s['peak_rss_swap_bytes'] / 2**30,
                'rss_GiB': s['peak_rss_bytes'] / 2**30,
                'swap_GiB': s['peak_swap_bytes'] / 2**30,
                'pss_swap_pss_GiB': s['peak_pss_swap_pss_bytes'] / 2**30,
                'seconds': s['observed_duration_s'], 'samples': s['samples'],
                'at_pss_peak': s.get('at_pss_peak'),
            })
        memcaps = []
        for f in (root / 'memcap-records').glob('*.json'):
            d = json.loads(f.read_text())
            if 'peak_gb' in d:
                memcaps.append(d)
        policy_files = list((root / 'evidence').glob('*.policy.json'))
        policy = verification.get('policy') or (json.loads(policy_files[0].read_text()) if policy_files else {})
        if not policy and (root / 'prepared-policy.json').exists():
            policy = json.loads((root / 'prepared-policy.json').read_text())
        digest = verification.get('digest')
        if policy and not digest:
            import hashlib
            digest = hashlib.sha256(json.dumps({k: policy[k] for k in ['mode', 'bands']}, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
        records.append({
            'artifact_directory': str(root), 'case': identity['case'], 'identity': identity,
            'accepted': outcome.get('verified', False), 'outcome': outcome, 'exit_code': obs['exit_code'],
            'benchmark_guard': obs.get('killed'), 'whole_tree': obs.get('whole_tree'),
            'benchmark_deadline_s': obs.get('benchmark_deadline_s'),
            'policy_digest': digest, 'mode': policy.get('mode'),
            'bands': [{'threshold': b['threshold'], 'k': b['k'], 'operations': len(b['operations'])} for b in policy.get('bands', [])],
            'host': host, 'total_seconds': obs['duration_s'], 'stages': stages, 'production_memcaps': memcaps,
            'host_min_available_GiB': min(s['host']['MemAvailable'] for s in samples) / 2**30 if samples else None,
            'host_max_swap_used_GiB': max(s['host']['SwapTotal'] - s['host']['SwapFree'] for s in samples) / 2**30 if samples else None,
        })
    groups = {}
    for r in records:
        groups.setdefault(r['case'], []).append(r)
    cases = {}
    for case, runs in groups.items():
        stages = {}
        for name in sorted({s for r in runs for s in r['stages']}):
            metrics = {}
            for metric in ['rss_swap_GiB', 'pss_swap_pss_GiB', 'seconds']:
                per_run = [max(s[metric] for s in r['stages'][name]) for r in runs if name in r['stages']]
                metrics[metric] = stats(per_run)
            stages[name] = metrics
        cases[case] = {'runs': len(runs), 'accepted': sum(r['accepted'] for r in runs),
                       'policy_digests': sorted({r['policy_digest'] for r in runs if r['policy_digest']}),
                       'total_seconds': stats([r['total_seconds'] for r in runs]), 'stages': stages,
                       'min_host_available_GiB': min(r['host_min_available_GiB'] for r in runs),
                       'max_host_swap_used_GiB': max(r['host_max_swap_used_GiB'] for r in runs)}
    a.out.write_text(json.dumps({'cases': cases, 'runs': records}, indent=2) + '\n')
    for case, c in cases.items():
        print(case, f"{c['accepted']}/{c['runs']} accepted", f"total {c['total_seconds']['min']:.1f}–{c['total_seconds']['max']:.1f}s")
        for stage, metrics in c['stages'].items():
            m, t = metrics['rss_swap_GiB'], metrics['seconds']
            print(f"  {stage:22} {m['min']:.3f}–{m['max']:.3f} GiB; {t['min']:.1f}–{t['max']:.1f}s")


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""External Linux sampler. Runs an unchanged command; never patches verifier code.
RSS/swap every 250 ms; smaps_rollup PSS/SwapPss every second. Lake stage trees
exclude this observer. Timing resolution is the sampling interval. Kernel RSS
itself is approximate; peaks are observations, not mathematical maxima.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import time


def fields(path):
    try:
        return {k: int(v) * 1024 for k, v in re.findall(r'^(\w+):\s+(\d+) kB', path.read_text(), re.M)}
    except (OSError, ProcessLookupError):
        return {}


def processes():
    out = {}
    for d in Path('/proc').iterdir():
        if not d.name.isdigit():
            continue
        try:
            stat = (d / 'stat').read_text().rsplit(')', 1)[1].split()
            args = (d / 'cmdline').read_bytes().decode(errors='replace').strip('\0').split('\0')
            out[int(d.name)] = {'ppid': int(stat[1]), 'start': int(stat[19]), 'args': args}
        except (OSError, ValueError, IndexError):
            continue
    return out


def descendants(pid, procs):
    seen = {pid}
    while True:
        new = {p for p, v in procs.items() if v['ppid'] in seen} - seen
        if not new:
            return seen & procs.keys()
        seen |= new


def stage_name(args):
    if not args or Path(args[0]).name != 'lake':
        return None
    if len(args) > 1 and args[1] == 'build':
        if any('SubmittedCert' in s for s in args):
            return 'mirror_compile_cert'
        if any('Submitted' in s for s in args):
            return 'mirror_compile_data'
        return 'forshor_compile'
    if 'scripts/trusted/ReplayPrefix.lean' in args:
        return 'replay_cert' if 'Cert' in args[-1] else 'replay_data'
    if len(args) > 1 and args[1] == 'exe':
        return 'forshor_print'
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--deadline-seconds', type=float, default=1680)
    ap.add_argument('command', nargs=argparse.REMAINDER)
    a = ap.parse_args()
    command = a.command[1:] if a.command[:1] == ['--'] else a.command
    a.out.mkdir(parents=True, exist_ok=True)
    host = {
        'uname': list(platform.uname()), 'cpu_count': os.cpu_count(),
        'meminfo_before': fields(Path('/proc/meminfo')),
        'os_release': Path('/etc/os-release').read_text(),
        'cpuinfo': Path('/proc/cpuinfo').read_text(),
        'swaps': Path('/proc/swaps').read_text(),
        'runner': {k: os.environ.get(k) for k in ['RUNNER_OS', 'RUNNER_ARCH', 'RUNNER_ENVIRONMENT', 'ImageOS', 'ImageVersion', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'GITHUB_SHA', 'LEAN_NUM_THREADS']},
        'versions': {x: subprocess.check_output([x, '--version'], text=True).strip() for x in ['lean', 'lake', 'python3']},
        'interval_seconds': 0.25, 'pss_interval_seconds': 1.0,
        'command': command,
    }
    (a.out / 'host.json').write_text(json.dumps(host, indent=2))
    t0 = time.monotonic()
    proc = subprocess.Popen(command, start_new_session=True)
    stages = {}
    whole_tree = {'peak_rss_swap_bytes': 0, 'peak_pss_swap_pss_bytes': 0}
    killed = None
    next_pss = 0
    last_sample = t0
    with (a.out / 'samples.jsonl').open('w', buffering=1) as stream:
        while proc.poll() is None:
            now = time.monotonic()
            if now - t0 > a.deadline_seconds:
                killed = 'benchmark outer deadline'
                # Some trusted subprocess wrappers start their own sessions.
                # Kill the entire observed descendant tree, not just one group.
                for pid in descendants(proc.pid, processes()):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                break
            procs = processes()
            tree = descendants(proc.pid, procs)
            for pid in tree:
                v = procs[pid]
                key = f'{pid}:{v["start"]}'
                name = stage_name(v['args'])
                if name and key not in stages:
                    stages[key] = {'stage': name, 'pid': pid, 'start_ticks': v['start'], 'args': v['args'],
                                   'first_seen_s': now - t0, 'last_seen_s': now - t0, 'samples': 0,
                                   'peak_rss_swap_bytes': 0, 'peak_rss_bytes': 0, 'peak_swap_bytes': 0,
                                   'peak_pss_swap_pss_bytes': 0, 'pss_samples': 0}
            sample = {'elapsed_s': now - t0, 'host': fields(Path('/proc/meminfo')), 'stages': []}
            take_pss = now >= next_pss
            if take_pss:
                next_pss = now + 1
            status = {pid: fields(Path(f'/proc/{pid}/status')) for pid in tree}
            smaps_by_pid = {pid: fields(Path(f'/proc/{pid}/smaps_rollup')) for pid in tree} if take_pss else {}
            total = {'rss_bytes': sum(s.get('VmRSS', 0) for s in status.values()),
                     'swap_bytes': sum(s.get('VmSwap', 0) for s in status.values()),
                     'process_count': len(tree)}
            whole_tree['peak_rss_swap_bytes'] = max(whole_tree['peak_rss_swap_bytes'], total['rss_bytes'] + total['swap_bytes'])
            if take_pss:
                total.update(pss_bytes=sum(s.get('Pss', 0) for s in smaps_by_pid.values()),
                             swap_pss_bytes=sum(s.get('SwapPss', 0) for s in smaps_by_pid.values()))
                whole_tree['peak_pss_swap_pss_bytes'] = max(whole_tree['peak_pss_swap_pss_bytes'], total['pss_bytes'] + total['swap_pss_bytes'])
            sample['whole_tree'] = total
            for key, stage in stages.items():
                pid = stage['pid']
                if pid not in procs or procs[pid]['start'] != stage['start_ticks']:
                    continue
                members = descendants(pid, procs)
                rss = swap = pss = swap_pss = pss_read = 0
                for member in members:
                    stat = status.get(member, {})
                    rss += stat.get('VmRSS', 0)
                    swap += stat.get('VmSwap', 0)
                    if take_pss:
                        smaps = smaps_by_pid.get(member, {})
                        pss += smaps.get('Pss', 0)
                        swap_pss += smaps.get('SwapPss', 0)
                        pss_read += bool(smaps)
                measurement = {'key': key, 'stage': stage['stage'], 'rss_bytes': rss, 'swap_bytes': swap, 'process_count': len(members)}
                stage['last_seen_s'] = now - t0
                stage['samples'] += 1
                stage['peak_rss_bytes'] = max(stage['peak_rss_bytes'], rss)
                stage['peak_swap_bytes'] = max(stage['peak_swap_bytes'], swap)
                if rss + swap > stage['peak_rss_swap_bytes']:
                    stage['peak_rss_swap_bytes'] = rss + swap
                    stage['at_peak'] = {**measurement, 'elapsed_s': now - t0, 'host_mem_available_bytes': sample['host'].get('MemAvailable')}
                if take_pss:
                    measurement.update(pss_bytes=pss, swap_pss_bytes=swap_pss, pss_processes_read=pss_read)
                    stage['pss_samples'] += 1
                    if pss + swap_pss > stage['peak_pss_swap_pss_bytes']:
                        stage['peak_pss_swap_pss_bytes'] = pss + swap_pss
                        stage['at_pss_peak'] = measurement.copy()
                sample['stages'].append(measurement)
            stream.write(json.dumps(sample) + '\n')
            last_sample = now
            time.sleep(max(0, .25 - (time.monotonic() - now)))
    rc = proc.wait()
    for stage in stages.values():
        stage['observed_duration_s'] = stage['last_seen_s'] - stage['first_seen_s'] + .25
    result = {'exit_code': rc, 'killed': killed, 'duration_s': time.monotonic() - t0,
              'whole_tree': whole_tree, 'benchmark_deadline_s': a.deadline_seconds,
              'stages': list(stages.values()), 'meminfo_after': fields(Path('/proc/meminfo'))}
    (a.out / 'observations.json').write_text(json.dumps(result, indent=2))
    return rc


if __name__ == '__main__':
    raise SystemExit(main())

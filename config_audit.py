#!/usr/bin/env python3
"""Audit a Strata run config against the repo at CWD (run from repo root on 226, e.g. ~/code/Strata):
  python3 config_audit.py strata-iq3_s.json
Checks: every --flag in args is parsed by src/program/generate.cpp; every env key is referenced in
src/include/serve; lists unset STRATA_* that serve auto-injects (parallel stack etc.) vs truly unset.
Expect: FLAGS missing: none / ENV unused: none. A flag absent from --help can still be live
(check the parser table, not --help) before calling it dead."""
import json, re, subprocess, sys

cfg = json.load(open(sys.argv[1]))
args = cfg.get('args', [])
flags = [a for a in args if a.startswith('--')]
envs = sorted((cfg.get('env') or {}).keys())

def gitgrep(*a):
    return subprocess.run(['git', 'grep', *a], capture_output=True, text=True).stdout

src = gitgrep('-h', '-e', '"--', 'HEAD', '--', 'src/program/generate.cpp')
missing = [f for f in flags if f'"{f}"' not in src]
unused = [e for e in envs if not gitgrep('-l', '-e', e, 'HEAD', '--', 'src', 'include', 'serve').strip()]
print('FLAGS missing:', missing or 'none')
print('ENV unused:', unused or 'none')
print('ENV used:', len(envs) - len(unused))

allnew = set(x for x in gitgrep('-rhoE', 'STRATA_[A-Z0-9_]+', 'HEAD', '--', 'src', 'include').split() if x.startswith('STRATA_'))
autoset = set(re.findall(r'STRATA_[A-Z0-9_]+', gitgrep('-e', 'STRATA_', 'HEAD', '--', 'serve/server.py')))
notset = sorted(allnew - set(envs))
print('unset but serve auto-injects (expected for batch>=2 stacks):', sorted(n for n in notset if n in autoset))
print('unset & not auto (candidates only):', sorted(n for n in notset if n not in autoset)[:40])

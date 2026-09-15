#!/usr/bin/env python3
"""Offline CLI double. Configuration is beside the copied binary, never in env."""
import json
from pathlib import Path
import sys
import time

config_path = Path(__file__).with_suffix('.json')
cfg = json.loads(config_path.read_text())
args = sys.argv[1:]
provider = cfg['provider']
if '--version' in args:
    print('codex-cli 0.154.0' if provider == 'codex' else '2.1.270 (Claude Code)')
elif '--help' in args:
    if cfg.get('slow_help'):
        time.sleep(3)
    print(cfg.get('help', '--print --output-format --verbose --safe-mode --setting-sources --settings '
          '--no-session-persistence --tools --allowedTools --strict-mcp-config --mcp-config --model '
          '--session-id --permission-mode --disable-slash-commands --json --sandbox --ephemeral '
          '--ignore-user-config --config --cd --skip-git-repo-check'))
elif 'status' in args:
    if cfg.get('auth', True):
        print('{"loggedIn":true,"apiProvider":"firstParty"}' if provider == 'claude' else 'Logged in using ChatGPT')
    else:
        sys.exit(1)
else:
    task = sys.stdin.read()
    assert '--resume' not in args and '--continue' not in args
    assert '--model' in args
    if provider == 'claude':
        assert args[args.index('--tools') + 1] == 'Read,Grep,Glob'
        assert '--safe-mode' in args and '--no-session-persistence' in args
        assert args[args.index('--output-format') + 1] == 'stream-json' and '--verbose' in args
        session = args[args.index('--session-id') + 1]
    else:
        assert args[args.index('--sandbox') + 1] == 'read-only'
        assert '--ignore-user-config' in args and '--json' in args and '--ephemeral' in args
        session = 'a7bf11f5-7f39-4806-8903-799b43ea1ff8'
    count = config_path.with_suffix('.count')
    count.write_text(str(int(count.read_text()) + 1) if count.exists() else '1')
    if cfg.get('change_file'):
        Path(cfg['change_file']).write_text('changed by reviewer')
    if cfg.get('tamper_package'):
        Path(cfg['package'], 'SPEC-1-contract.md').write_text('tampered')
    if cfg.get('slow_run'):
        time.sleep(3)
    raw = Path(cfg['fixture']).read_text()
    for key, value in {'SESSION': session, 'REPO': cfg['repo'], 'PACKAGE': cfg['package'],
                       'MODE': cfg.get('mode', 'contract')}.items():
        raw = raw.replace(key, value)
    if cfg.get('malformed'):
        raw += 'not JSON\n'
    if cfg.get('truncate'):
        raw = '\n'.join(raw.splitlines()[:-1]) + '\n'
    print(raw, end='')
    print('offline reviewer diagnostics', file=sys.stderr)
    sys.exit(cfg.get('exit_code', 0))

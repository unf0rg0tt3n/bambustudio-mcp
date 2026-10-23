"""Fixed one-request SSH entry point; no shell or arbitrary-command action."""
import argparse
import json
import sys

from .presets import LIMIT, Presets

ACTIONS = {'list', 'read', 'derive', 'create', 'update', 'validate', 'backups', 'restore'}


def dispatch(api, request):
    request = dict(request)
    action = request.pop('action')
    if action not in ACTIONS:
        raise ValueError('Action not allowlisted')
    target = api._target(request['target'])
    if target.get('transport', 'local') != 'local':
        raise ValueError('SSH agent only permits local targets; no forwarding')
    return getattr(api, action)(**request)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    try:
        with open(args.config) as stream:
            api = Presets(json.load(stream))
        raw = sys.stdin.buffer.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ValueError('Request too large')
        print(json.dumps({'result': dispatch(api, json.loads(raw))}, allow_nan=False))
    except Exception as exc:
        print(json.dumps({'error': str(exc)}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

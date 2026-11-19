"""Compare the application's HTTP schema with the accepted role-admin design contract.

No lifespan/database I/O occurs here. Authorization and transaction tests are separate.
"""
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / 'services/core-api/contracts'


def _shape(value, definitions):
    if '$ref' in value:
        return _shape(definitions[value['$ref'].split('/')[-1]], definitions)
    result = {}
    for key in ('type', 'format', 'enum', 'const', 'minItems', 'maxItems'):
        if key in value:
            result[key] = value[key]
    if 'additionalProperties' in value:
        extra = value['additionalProperties']
        result['additionalProperties'] = _shape(extra, definitions) if isinstance(extra, dict) else extra
    if 'properties' in value:
        result['properties'] = {name: _shape(schema, definitions) for name, schema in value['properties'].items()}
        result['required'] = sorted(value.get('required', []))
    if 'items' in value:
        result['items'] = _shape(value['items'], definitions)
    if 'anyOf' in value:
        result['anyOf'] = sorted((_shape(item, definitions) for item in value['anyOf']), key=lambda item: json.dumps(item, sort_keys=True))
    return result


def test_current_application_preserves_role_admin_http_contract():
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'services/core-api'), PYTHON_DOTENV_DISABLED='1',
        DATABASE_URL='postgresql+psycopg://synthetic:synthetic@127.0.0.1:9/synthetic',
        REDIS_URL='redis://127.0.0.1:9/0', KNOWI_REDIS_PREFIX='role-schema:', KNOWI_LOGIN_RECOVERY_KEY='42' * 32)
    for legacy in ('PYMENTOR_REDIS_PREFIX', 'PYMENTOR_LOGIN_RECOVERY_KEY'):
        env.pop(legacy, None)
    result = subprocess.run([sys.executable, '-c',
        'import json; from pymentor_core.app.factory import create_app; print(json.dumps(create_app().openapi()))'],
        env=env, cwd=ROOT, capture_output=True, text=True, check=True, timeout=30)
    actual = json.loads(result.stdout)
    expected = json.loads((CONTRACT / 'role-admin-v1.openapi.json').read_text(encoding='utf-8'))
    definitions = json.loads((CONTRACT / 'role-admin-v1.schema.json').read_text(encoding='utf-8'))['$defs']
    schemas = actual['components']['schemas']
    failures = []
    for path, methods in expected['paths'].items():
        for method, operation in methods.items():
            live = actual['paths'].get(path, {}).get(method)
            label = method.upper() + ' ' + path
            if live is None:
                failures.append(label + ': missing operation')
                continue
            wanted_params = {(p['in'], p['name']): p for p in operation['parameters']}
            live_params = {(p['in'], p['name']): p for p in live.get('parameters', [])}
            for key, wanted in wanted_params.items():
                current = live_params.get(key)
                if current is None or current.get('required', False) != wanted.get('required', False):
                    failures.append(label + ': missing/changed parameter ' + str(key))
            if 'requestBody' in operation:
                wanted = operation['requestBody']['content']['application/json']['schema']
                current = live.get('requestBody', {}).get('content', {}).get('application/json', {}).get('schema', {})
                if _shape(current, schemas) != _shape(wanted, definitions):
                    failures.append(label + ': request JSON shape differs')
            for status, response in operation['responses'].items():
                if 'content' not in response:
                    continue
                current = live.get('responses', {}).get(status, {}).get('content', {}).get('application/json', {}).get('schema', {})
                wanted = response['content']['application/json']['schema']
                if _shape(current, schemas) != _shape(wanted, definitions):
                    failures.append(label + ': response ' + status + ' JSON shape differs')
    assert not failures, '\n'.join(failures)

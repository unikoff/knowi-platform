"""Read-only inventory for the installed FastAPI version; no lifespan/DB access."""
import ast
import inspect
import json
import textwrap
from pathlib import Path

from multicourse_local import configure
configure('mc460-20261113-2048')
from pymentor_core.app.asgi import create_app

ROOT = Path(__file__).resolve().parents[4]
app = create_app()

def effective(route):
    if type(route).__name__ == '_IncludedRouter':
        for child in route.effective_candidates():
            yield from effective(child)
    else:
        yield route

rows = []
for top in app.routes:
    for route in effective(top):
        fn = getattr(route, 'endpoint', None)
        if fn is None:
            continue
        original = getattr(route, 'original_route', route)
        if not fn.__module__.startswith('pymentor_core'):
            continue
        source = textwrap.dedent(inspect.getsource(fn))
        deps = []
        def dependencies(d):
            for dep in d.dependencies:
                deps.append(getattr(dep.call, '__name__', str(dep.call)))
                dependencies(dep)
        dependant = getattr(route, 'dependant', getattr(original, 'dependant', None))
        if dependant is not None:
            dependencies(dependant)
        rows.append({'path': route.path, 'methods': sorted(route.methods),
                     'handler': fn.__module__ + '.' + fn.__name__,
                     'async': inspect.iscoroutinefunction(fn),
                     'source': str(Path(inspect.getsourcefile(fn)).relative_to(ROOT)),
                     'line': inspect.getsourcelines(fn)[1], 'dependencies': deps,
                     'awaits': sorted({ast.unparse(n.value) for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Await)})})

sync_names = {row['handler'].rsplit('.', 1)[-1] for row in rows if not row['async']}
assert sync_names <= {'health', 'liveness_health', 'startup_health', 'login_capabilities', 'email_change_capabilities'}, sync_names
assert not any('get_db' in row['dependencies'] for row in rows)
baseline = json.loads((ROOT/'services/core-api/tests/fixtures/contracts/multicourse/MC1.2/openapi-baseline.json').read_text(encoding='utf-8'))
assert app.openapi() == baseline
report = {'scope': 'source inventory; representative PG runtime tests are separate',
          'database_connected': False, 'openapi_equal_baseline': True,
          'openapi_paths': len(app.openapi()['paths']), 'route_count': len(rows),
          'unique_handlers': len({r['handler'] for r in rows}),
          'sync_only_sql_free_health_or_capabilities': sorted(sync_names), 'routes': rows}
out = ROOT/'docs/multicourse/Core-196/route-inventory.json'
out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k != 'routes'}))

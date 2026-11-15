"""Verify additive course context against the frozen pre-course OpenAPI."""
from copy import deepcopy
import json
from multicourse_local import ROOT,configure
configure('mc460-20261113-2048')
from pymentor_core.app.asgi import create_app

base=json.loads((ROOT/'services/core-api/tests/fixtures/contracts/multicourse/MC1.2/openapi-baseline.json').read_text(encoding='utf-8'))
current=create_app().openapi()
normalized=deepcopy(current)
added=set(current['paths'])-set(base['paths'])
assert added=={'/learning/products','/api/learning/products'}
for path in added:del normalized['paths'][path]
count=0
for path,methods in base['paths'].items():
    for method,old in methods.items():
        new=normalized['paths'][path][method]
        if path.startswith(('/learning/','/api/learning/')):
            params=new.get('parameters',[])
            course=[p for p in params if p.get('name')=='course_id']
            assert len(course)==1 and course[0]['required'] is False and course[0]['in']=='query'
            params=[p for p in params if p.get('name')!='course_id']
            if params:new['parameters']=params
            else:new.pop('parameters',None)
            # Additional documented failure statuses preserve all former responses.
            new['responses']={k:v for k,v in new['responses'].items() if k in old['responses']}
            count+=1
for schema in ('CodeSubmitRequest','InterpreterRunRequest'):
    obj=normalized['components']['schemas'][schema]
    assert 'course_id' not in obj.get('required',[])
    del obj['properties']['course_id']
assert normalized==base,'Non-additive API difference requires review'
out=ROOT/'docs/multicourse/MC2.3';out.mkdir(parents=True,exist_ok=True)
(out/'openapi-multicourse.json').write_text(json.dumps(current,ensure_ascii=False,indent=2),encoding='utf-8')
report={'result':'PASS','baseline_paths':len(base['paths']),'current_paths':len(current['paths']),
        'added_public_product_paths':sorted(added),'optional_course_query_operations':count,
        'optional_course_body_schemas':['CodeSubmitRequest','InterpreterRunRequest'],
        'existing_fields_errors_methods_paths_preserved':True,
        'intentional_private_result_values_change':'Core205 projection; schema unchanged'}
(out/'contract-check.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report))

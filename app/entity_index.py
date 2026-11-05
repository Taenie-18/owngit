"""先识别实际主体，供后续任务共享，避免把亲属误列为当事人。"""
import json
import re
from .models import ExtractionError, ModelRequest
from .tasks import object_schema
from .businesses.registry import get_business

ENTITY_SCHEMA=object_schema({
 'entities':{'type':'array','items':object_schema({
   'name':{'type':['string','null'],'minLength':1},
   'role':{'enum':['原告','被告','代理人','子女','其他相关人']},
   'sourceIndex':{'type':'integer','minimum':0},'quote':{'type':'string','minLength':1}
 })},
 'conflicts':{'type':'array','items':object_schema({'path':{'type':'string'},'reason':{'type':'string','minLength':1}})}
})

def build_entities(call, document, warnings=None):
    """建立带原文依据的共享主体列表；内部ID不加入下游业务JSON。"""
    business = get_business(document.business_type)
    schema = json.loads(json.dumps(ENTITY_SCHEMA))
    schema['properties']['entities']['items']['properties']['role']['enum'] = list(business.ENTITY_ROLES)
    req=ModelRequest('entities',
        business.ENTITY_SYSTEM,
        json.dumps({'schema':schema,'sources':[{'sourceIndex':i,'text':t} for i,t in enumerate(document.file_context)]},ensure_ascii=False))
    warnings = warnings if warnings is not None else []
    try:
        result = call(req, schema)
    except ExtractionError as exc:
        if exc.code != 'MODEL_SCHEMA':
            raise
        warnings.append({'code': 'ENTITY_CONTEXT_UNAVAILABLE', 'path': '/blocks/parties'})
        return []
    if result['conflicts']:
        warnings.append({'code': 'ENTITY_CONFLICT', 'path': '/blocks/parties'})
    # 同一角色和姓名去重，不把文本中的重复提及当成多个主体。
    entities = []
    seen = set()
    for item in result['entities']:
        idx = item['sourceIndex']
        quote = item['quote']
        name = item['name']
        # 姓名和引文都必须能在对应原文中找到，不能仅相信模型给出的角色。
        if (
            not name
            or idx >= len(document.file_context)
            or quote not in document.file_context[idx]
            or name not in re.sub(r'\s+', '', quote)
        ):
            warnings.append({'code': 'ENTITY_UNGROUNDED', 'path': '/blocks/parties'})
            continue
        key = (item['role'], name)
        if key in seen:
            continue
        seen.add(key)
        entities.append({**item, 'entityId': f'E{len(entities) + 1}'})
    # 姓名或任一方缺失时，仍将原文交给后续任务抽取其他已知信息。
    return entities

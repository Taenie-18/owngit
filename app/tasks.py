"""从统一字段契约划分五个抽取任务，并组装提示词。"""
import copy
import json
from dataclasses import dataclass
from functools import lru_cache
from .config import BASE_DIR, BUSINESS_TYPE
from .businesses.registry import get_business


@lru_cache(maxsize=None)
def load_schema(business_type=BUSINESS_TYPE):
    business = get_business(business_type)
    return json.loads((BASE_DIR / 'config' / business.SCHEMA_FILE).read_text(encoding='utf-8'))


def blank(node):
    """生成提示词中的空结构示例；固定五槽位保留，其余未知值为null。"""
    if 'const' in node:
        return node['const']
    if node.get('type') == 'object':
        return {k: blank(v) for k, v in node['properties'].items()}
    if node.get('type') == 'array' and node.get('minItems') == 5:
        return [None] * 5
    return None


def object_schema(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


EVIDENCE_SCHEMA=object_schema({
    'path':{'type':'string','pattern':'^/blocks/'},
    'sourceIndex':{'type':'integer','minimum':0},
    'quote':{'type':'string','minLength':1}
})
CONFLICT_SCHEMA = object_schema({'path': {'type': 'string'}, 'reason': {'type': 'string', 'minLength': 1}})

@dataclass(frozen=True)
class Task:
    name: str
    schema: dict


def task_specs(schema=None, business_type=None):
    """按路径分配任务，每个字段只有一个负责方，避免并行覆盖。"""
    schema = schema or load_schema(business_type or BUSINESS_TYPE)
    business_type = business_type or schema['properties']['docType']['const']
    business = get_business(business_type)
    b = schema['properties']['blocks']['properties']
    specs = []
    # None表示负责整个模块；字段列表表示只负责指定编号。
    for name, selection in business.SELECTION:
        props = {key: copy.deepcopy(b[key]) if fields is None else object_schema({f: copy.deepcopy(b[key]['properties'][f]) for f in fields}) for key, fields in selection.items()}
        specs.append(Task(name, object_schema({'data': object_schema(props), 'evidence': {'type': 'array', 'items': EVIDENCE_SCHEMA}, 'conflicts': {'type': 'array', 'items': CONFLICT_SCHEMA}})))
    return specs


def prompt(task, document, entities):
    """同时提供原文、主体上下文和本任务契约，不允许模型自行增加字段。"""
    business = get_business(document.business_type)
    hints = []
    # 表格若有独立事实正文，只取该正文作提示，避免把后续分项重复拼入。
    if task.name in business.NARRATIVE_TASKS:
        for index, text in enumerate(document.file_context):
            lines = text.splitlines()
            active = False
            body = []
            for line in lines:
                label = ''.join(line.split())
                if label == '事实与理由':
                    active = True
                    continue
                if active and label.startswith(business.NARRATIVE_BOUNDARY):
                    break
                if active and line.strip():
                    body.append(line)
            if body:
                hints.append({'sourceIndex': index, 'paragraphs': body})
    return json.dumps({'task':task.name,'sources':[{'sourceIndex':i,'text':s} for i,s in enumerate(document.file_context)],
        'entityContext':entities,'requiredOutputSchema':task.schema,
        'freeNarrativeHints':hints,
        'narrativeRule':business.NARRATIVE_RULE,
        'emptyShapeExample':{'data':blank(task.schema['properties']['data']),'evidence':[],'conflicts':[]}},ensure_ascii=False)

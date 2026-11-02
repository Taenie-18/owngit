"""保证下游结构合法；内容问题局部置空，证据问题记录内部警告。"""
import re
import unicodedata
from decimal import Decimal

from jsonschema import Draft202012Validator
from .normalize import normalize
from .tasks import load_schema


def compact(value):
    return ''.join(c for c in unicodedata.normalize('NFKC', value) if c.isalnum())


def leaves(value, path=''):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from leaves(item, path + '/' + key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from leaves(item, path + '/' + str(index))
    else:
        yield path, value


def resolve(value, path):
    for part in path.split('/')[1:]:
        part = part.replace('~1', '/').replace('~0', '~')
        if isinstance(value, list) and not part.isdigit():
            raise ValueError('数组路径必须是非负下标')
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def canonical_path(path):
    """只修正已知的任务包装前缀，不能删除当事人记录内部合法的data层。"""
    if not isinstance(path, str):
        return ''
    for prefix in ('/blocks/data/', '/data/blocks/', '/data/'):
        if path.startswith(prefix):
            return '/blocks/' + path[len(prefix):]
    return path


def selected(text, option):
    return bool(re.search(re.escape(option) + r'\s*[☑√✓]', text) or
                re.search(r'[☑√✓]\s*' + re.escape(option) + r'(?=\s|[□☐☑/；;，,。]|$)', text))


def has_value(value):
    if isinstance(value, dict):
        return any(has_value(v) for k, v in value.items() if k != 'category')
    if isinstance(value, list):
        return any(has_value(v) for v in value)
    return value is not None


def clear_field(output, path, warnings, code):
    """仅清除能准确定位的问题字段，其他模块保持原值。"""
    path = canonical_path(path)
    warnings.append({'code': code, 'path': path})
    if not path.startswith('/blocks/'):
        return
    try:
        parent, key = path.rsplit('/', 1)
        container = resolve(output, parent)
        if isinstance(container, list):
            if not key.isdigit():
                return
            container[int(key)] = None
        else:
            container[key] = None
    except (KeyError, IndexError, TypeError, ValueError):
        pass  # 内部错误路径只记警告，不能据此清空整份结果。


class ResultValidator:
    def __init__(self):
        self.schema = load_schema()
        Draft202012Validator.check_schema(self.schema)
        self.validator = Draft202012Validator(self.schema)

    def repair(self, value, node, warnings, path=''):
        """按契约补齐结构；非法枚举不做相近匹配，只能变为null。"""
        if 'const' in node:
            return node['const']
        if 'oneOf' in node:
            branch = next((n for n in node['oneOf'] if isinstance(value, dict) == (n.get('type') == 'object')), node['oneOf'][0])
            return self.repair(value, branch, warnings, path)
        if node.get('type') == 'object':
            data = value if isinstance(value, dict) else {}
            for key in data.keys() - node['properties'].keys():
                warnings.append({'code': 'UNKNOWN_FIELD', 'path': path + '/' + key})
            return {key: self.repair(data.get(key), child, warnings, path + '/' + key)
                    for key, child in node['properties'].items()}
        if 'items' in node:
            items = value if isinstance(value, list) else []
            if node.get('minItems') == node.get('maxItems') == 5:
                return [self.repair(items[i] if i < len(items) else None, node['items'], warnings, path + '/' + str(i)) for i in range(5)]
            result = []
            for i, item in enumerate(items):
                category = node['items'].get('properties', {}).get('category', {}).get('const')
                if category and isinstance(item, dict) and item.get('category', category) != category:
                    warnings.append({'code': 'UNSUPPORTED_CATEGORY', 'path': path + '/' + str(i)})
                    continue
                item = self.repair(item, node['items'], warnings, path + '/' + str(i))
                if has_value(item) and item not in result:
                    result.append(item)
            return result or None
        if value is None or Draft202012Validator(node).is_valid(value):
            return value
        warnings.append({'code': 'INVALID_VALUE', 'path': path})
        return None

    def validate(self, output, document, entities, evidence, *, warnings=None, conflicts=()):
        warnings = warnings if warnings is not None else []
        for conflict in conflicts:
            if isinstance(conflict, dict):
                clear_field(output, conflict.get('path', ''), warnings, 'SOURCE_CONFLICT')
        # 冲突可能指向整个对象，补齐结构后再检查剩余内容。
        fixed = normalize(self.repair(output, self.schema, warnings), warnings=warnings)
        output.clear()
        output.update(fixed)
        checked = []
        for raw in evidence:
            if not isinstance(raw, dict):
                warnings.append({'code': 'EVIDENCE_INVALID', 'path': ''})
                continue
            path = canonical_path(raw.get('path'))
            index, quote = raw.get('sourceIndex'), raw.get('quote')
            try:
                if not path.startswith('/blocks/') or type(index) is not int or not 0 <= index < len(document.file_context) or not isinstance(quote, str) or not quote:
                    raise ValueError()
                resolve(output, path)
                source = document.file_context[index]
                start = source.find(quote)
                aligned = start < 0
                if aligned:
                    positions = [i for i, c in enumerate(source) if not c.isspace()]
                    needle = re.sub(r'\s+', '', quote)
                    found = ''.join(source[i] for i in positions).find(needle) if len(needle) >= 4 else -1
                    if found < 0:
                        raise ValueError()
                    start = positions[found]
                    quote = source[start:positions[found + len(needle) - 1] + 1]
                checked.append({**raw, 'path': path, 'quote': quote, 'charStart': start,
                                'charEnd': start + len(quote), 'whitespaceAligned': aligned})
            except (KeyError, IndexError, TypeError, ValueError):
                warnings.append({'code': 'EVIDENCE_INVALID', 'path': path})
        source_text = '\n'.join(document.file_context)
        for path, value in list(leaves(output)):
            if value is None or path == '/docType' or path.endswith('/category'):
                continue
            # 不把“说不清”等原话硬塞进具体要素；叙述区保留原话，调解合法选项除外。
            if '/narrative/' not in path and path != '/blocks/mediation/3' and re.search(r'说不清|不清楚|未提供|没想好|不知道|不确定|无法确定|可能|也许|好像', value):
                clear_field(output, path, warnings, 'UNCERTAIN_VALUE')
                continue
            refs = [r for r in checked if path == r['path'] or path.startswith(r['path'] + '/')]
            if not refs:
                warnings.append({'code': 'EVIDENCE_MISSING', 'path': path})
            proof = '\n'.join(r['quote'] for r in refs) if refs else source_text
            # 元数据不完善不阻断；明显无来源的姓名、数字和金额单位仍局部置空。
            numbers = re.findall(r'\d+(?:\.\d+)?', value.replace(',', ''))
            originals = re.findall(r'\d+(?:\.\d+)?', proof.replace(',', ''))
            invalid = any(not any(Decimal(n) == Decimal(m) for m in originals) for n in numbers)
            for amount, unit in re.findall(r'(\d+(?:\.\d+)?)\s*(亿元|万元|元)', value):
                quantities = re.findall(r'(\d+(?:\.\d+)?)\s*(亿元|万元|元)', proof.replace(',', ''))
                invalid |= not any(Decimal(amount) == Decimal(a) and unit == u for a, u in quantities)
            if path.endswith(('/name', '/idNumber', '/phone', '/domicile', '/residence', '/signer')):
                invalid |= compact(value) not in compact(proof)
            if invalid:
                clear_field(output, path, warnings, 'VALUE_UNGROUNDED')
            elif value in ('否', '无', '无财产', '无债务', '无此问题', '不了解'):
                # 明确的“未申请”等口语否定可保留；仅有空框或标题不算否定。
                cleaned = re.sub(r'[\u4e00-\u9fff]+\s*[□☐]', '', proof)
                negative = re.search(r'没有|不存在|未申请|尚未|未办理|未曾|未提起|未委托|不主张|不要求|不涉及|不了解|无共同|无代理|无财产|无债务|无此问题|(?:^|\n)\s*无\s*(?:$|\n)', cleaned)
                literal = re.search(r'(?:^|[\n：:])\s*' + re.escape(value) + r'[。；;]?\s*(?:$|\n)', cleaned)
                if not selected(proof, value) and not negative and not literal:
                    clear_field(output, path, warnings, 'UNCONFIRMED_NEGATIVE')
        self.business(output, entities, checked, document, warnings)
        fixed = self.repair(output, self.schema, warnings)
        output.clear()
        output.update(fixed)
        self.validator.validate(output)  # 最后一关只保证交付结构，不要求案情完整。
        return checked

    def business(self, output, entities, evidence, document, warnings):
        blocks = output['blocks']
        for role in ('原告', '被告'):
            known = {e['name'] for e in entities if e['role'] == role}
            parties = blocks['parties'][role] or []
            for i, party in enumerate(parties):
                data = party['data']
                base = f'/blocks/parties/{role}/{i}/data'
                if data['name'] and known and data['name'] not in known:
                    clear_field(output, base, warnings, 'PARTY_MISMATCH')
                    continue
                names = [p['data']['name'] for r in ('原告', '被告') for p in blocks['parties'][r] or [] if p['data'] and p['data']['name']]
                for field in ('phone', 'idNumber', 'domicile', 'residence'):
                    value = data[field]
                    if not value or not data['name']:
                        continue
                    owners = []
                    for text in document.file_context:
                        start = text.find(value)
                        while start >= 0:
                            positions = [(text.rfind(n, 0, start + 1), n) for n in names]
                            positions = [p for p in positions if p[0] >= 0]
                            if positions:
                                owners.append(max(positions)[1])
                            start = text.find(value, start + 1)
                    if owners and data['name'] not in owners:
                        clear_field(output, base + '/' + field, warnings, 'PARTY_ATTRIBUTE_MISMATCH')
        claims = blocks['claims']
        for key, negative in [('2', '无财产'), ('3', '无债务'), ('4', '无此问题'), ('5', '无此问题'), ('6', '无此问题'), ('7', '无此问题')]:
            item = claims[key]
            ticks = item['tick'] or []
            if negative in ticks and (len(ticks) > 1 or any(has_value(v) for k, v in item.items() if k != 'tick')):
                clear_field(output, f'/blocks/claims/{key}/tick', warnings, 'CHOICE_CONFLICT')
            if key in ('5', '6') and '原告' in ticks and '被告' in ticks:
                clear_field(output, f'/blocks/claims/{key}/tick', warnings, 'AMBIGUOUS_SUBJECT')
        pre = blocks['jurisdiction']['1']
        if len(pre['tick'] or []) > 1 or ('否' in (pre['tick'] or []) and any(has_value(v) for k, v in pre.items() if k != 'tick')):
            clear_field(output, '/blocks/jurisdiction/1/tick', warnings, 'CHOICE_CONFLICT')
        for key in ('2', '3', '4'):
            list_key, anchor = {'2': ('财产项', '明细'), '3': ('债务项', '名称'), '4': ('子女项', '姓名')}[key]
            for i, item in enumerate(claims[key][list_key] or []):
                allocation = '承担主体' if key == '3' else '归属'
                for ref in evidence:
                    for line in ref['quote'].splitlines():
                        if item[anchor] and compact(item[anchor]) in compact(line):
                            marks = [r for r in ('原告', '被告', '其他') if selected(line, r)]
                            if marks and item[allocation] and item[allocation] not in marks:
                                clear_field(output, f'/blocks/claims/{key}/{list_key}/{i}/{allocation}', warnings, 'ASSIGNMENT_MISMATCH')
        # 模板若有五组明确勾选，只处理不一致的槽位，不影响其他调解信息。
        for text in document.file_context:
            text = re.sub(r'\s+', '', text)
            start = text.find('是否了解先行调解')
            end = text.find('是否考虑先行调解', start)
            marks = re.findall(r'了解([□☐☑√✓])不了解([□☐☑√✓])', text[start:end]) if start >= 0 and end > start else []
            if len(marks) == 5:
                for i, (yes, no) in enumerate(marks):
                    actual = None if (yes in '☑√✓') == (no in '☑√✓') else '了解' if yes in '☑√✓' else '不了解'
                    if blocks['mediation']['2'][i] != actual:
                        clear_field(output, f'/blocks/mediation/2/{i}', warnings, 'MEDIATION_POSITION')

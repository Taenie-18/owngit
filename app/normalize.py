"""只调整明确日期的显示格式，不计算金额或补全案件事实。"""
import copy
import re
from datetime import date


def date_display(value, path, warnings=None):
    """只转换完整且有效的日历日期，不推导缺失的月或日。"""
    if not isinstance(value, str):
        return value
    match = re.fullmatch(r'\s*(\d{4})(?:年|-)(\d{1,2})(?:月|-)(\d{1,2})日?\s*', value)
    if not match:
        return value
    y, m, d = map(int, match.groups())
    try:
        date(y, m, d)
    except ValueError:
        if warnings is not None:
            warnings.append({'code': 'INVALID_DATE', 'path': path})
        return None
    return f'{y}年{m}月{d}日'


def normalize(output, warnings=None):
    """复制后调整约定的日期字段；金额、姓名和案件叙述保持原值。"""
    result = copy.deepcopy(output)
    blocks = result.get('blocks', {})
    for role in ('原告', '被告'):
        for i, party in enumerate(blocks.get('parties', {}).get(role) or []):
            data = party.get('data', {})
            data['birth'] = date_display(data.get('birth'), f'/blocks/parties/{role}/{i}/data/birth', warnings)
    facts = blocks.get('facts', {}).get('1', {})
    if '结婚时间' in facts:
        facts['结婚时间'] = date_display(facts['结婚时间'], '/blocks/facts/1/结婚时间', warnings)
    preservation = blocks.get('jurisdiction', {}).get('1', {})
    if '保全时间' in preservation:
        preservation['保全时间'] = date_display(preservation['保全时间'], '/blocks/jurisdiction/1/保全时间', warnings)
    return result

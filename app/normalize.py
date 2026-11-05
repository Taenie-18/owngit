"""只调整明确日期的显示格式，不计算金额或补全案件事实。"""
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
    """按输出所属板块应用日期规则，业务配置不会写入共享状态。"""
    from .businesses.registry import get_business
    return get_business(output.get('docType', 'divorce-dispute')).normalize(output, warnings)

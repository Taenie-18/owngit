"""显式注册业务模块，增加板块时保持入口清晰。"""
from . import divorce, private_lending
from ..models import ExtractionError

BUSINESSES = {'divorce-dispute': divorce, 'private-lending': private_lending}


def get_business(business_type):
    if not isinstance(business_type, str) or business_type not in BUSINESSES:
        raise ExtractionError('INVALID_REQUEST', 'businessType不属于已支持板块',
                              status=400, fields=['/businessType'])
    return BUSINESSES[business_type]

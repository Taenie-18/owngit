"""使用虚构数据验证统一接口的两个板块，按各自Schema检查响应。"""
import argparse
import json
from pathlib import Path
import sys
from urllib import error, request
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from jsonschema import Draft202012Validator
from app.tasks import load_schema


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8011')
    parser.add_argument('--output-dir', default='/tmp/element-extraction-staging')
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for business, filename in (
        ('divorce-dispute', 'smoke-request.json'),
        ('private-lending', 'smoke-private-lending.json'),
    ):
        payload = json.loads((ROOT / 'deploy' / filename).read_text(encoding='utf-8'))
        payload['requestId'] = 'staging-' + business + '-' + uuid4().hex[:12]
        url = args.base_url.rstrip('/') + '/element-extraction/api/v1/divorce/extract'
        req = request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                              headers={'Content-Type': 'application/json'})
        try:
            with request.urlopen(req, timeout=600) as response:
                body = json.loads(response.read())
            Draft202012Validator(load_schema(business)).validate(body)
            parties = body['blocks']['parties']['原告'] or []
            if not any(p['data']['name'] == '测试甲' for p in parties):
                raise ValueError('原告姓名未正确提取')
            if business == 'private-lending' and '10000' not in (body['blocks']['claims']['1']['本金'] or ''):
                raise ValueError('借款本金未正确提取')
        except error.HTTPError as exc:
            failure = json.loads(exc.read())
            print(json.dumps({'requestId': payload['requestId'], 'businessType': business,
                              'status': exc.code, 'errorCode': failure.get('error', {}).get('code')}, ensure_ascii=False))
            return 1
        # 保存的仅为本脚本虚构联调数据，方便测试人员检查完整抽取结果。
        path = output_dir / (payload['requestId'] + '.json')
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'requestId': payload['requestId'], 'businessType': business,
                          'status': 200, 'schemaValid': True, 'resultFile': str(path)}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

"""集中维护契约版本、配置文件位置和输入长度限制。"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
VERSION = '1.0.0'
SCHEMA_PATH = BASE_DIR / 'config/divorce.schema.json'
FIELDS_PATH = BASE_DIR / 'config/divorce.fields.json'
BUSINESS_TYPE = 'divorce-dispute'
# 数量和长度同时限制，避免单次输入过大而挤占模型上下文。
MAX_ITEMS = 20
MAX_ITEM_CHARS = 50000
MAX_TOTAL_CHARS = 100000

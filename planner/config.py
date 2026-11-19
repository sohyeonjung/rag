import os
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')
KST = timezone(timedelta(hours=9))

def today():
    return datetime.now(KST).date()

def redact(value):
    if isinstance(value, dict):
        return {k: ('***' if re.fullmatch(r'.*(?:api_key|_key|token|password|authorization)|key|serviceKey|crtfc_key', k, re.I) else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if not isinstance(value, str):
        return value
    for name in ('GMS_KEY', 'GMS_API_KEY', 'OPENAI_API_KEY', 'DART_API_KEY', 'DATA_GO_KR_KEY'):
        secret = os.getenv(name)
        if secret:
            value = value.replace(secret, '***')
    return re.sub(r'(crtfc_key|serviceKey|api_key)=[^&\s]+', r'\1=***', value, flags=re.I)

def llm():
    from langchain_openai import ChatOpenAI
    key = os.getenv('GMS_KEY') or os.getenv('GMS_API_KEY') or os.getenv('OPENAI_API_KEY')
    if not key:
        raise ValueError('MISSING_LLM_KEY')
    return ChatOpenAI(model=os.getenv('PLANNER_MODEL', 'gpt-4.1-mini'), api_key=key,
                      base_url=os.getenv('PLANNER_BASE_URL', 'https://gms.ssafy.io/gmsapi/api.openai.com/v1'),
                      temperature=0, timeout=40, max_retries=0)

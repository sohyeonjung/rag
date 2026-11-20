"""Typed Tool registry. Public API responses are normalized, not invented."""
import json
import os
import re
import time
from datetime import date, timedelta
from urllib.parse import unquote
import requests
from pydantic import Field, ValidationError
from planner.models import StrictModel, ToolResult
from planner.config import ROOT, today
from planner import fixtures

class ToolFailure(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message

class CandidateArgs(StrictModel):
    keyword: str = Field(default='', max_length=80)
    offset: int = Field(default=0, ge=0, le=100)
    limit: int = Field(default=3, ge=1, le=10)
    as_of: date | None = None
    exclude_codes: list[str] = Field(default_factory=list)

class FinancialArgs(StrictModel):
    stock_code: str = Field(pattern=r'^(\d{6}|D\d{5})$')
    year: int = Field(ge=2015, le=2100)

class BackgroundArgs(StrictModel):
    stock_code: str = Field(pattern=r'^(\d{6}|D\d{5})$')

class RagArgs(FinancialArgs):
    question: str = Field(min_length=1, max_length=3000)

CATALOG = [
    ('candidate_search', CandidateArgs, 'external', '새 기업 후보가 필요할 때 사용. 기존 2회차 기업 목록 범위에서 최신 확인 가능한 시세와 사업 키워드로 후보를 찾는다. offset으로 다음 후보를 탐색한다.'),
    ('financial_details', FinancialArgs, 'external', '후보의 매출 성장과 영업흑자를 판단할 때 사용. 동일 연도 사업보고서의 당기·전기 매출과 영업이익을 조회한다.'),
    ('background_search', BackgroundArgs, 'external', '기업 사업 배경과 확인 가능한 원문 링크가 필요할 때 사용. 재무 판단에는 사용하지 않는다.'),
    ('rag_search', RagArgs, 'local', '2회차에 색인한 공시·경제용어 문서의 근거가 필요한 질문에 사용. 인덱스가 없으면 NO_INDEX를 반환한다.'),
]

def tool_specs():
    return [dict(name=n, type=t, description=d, input_schema=s.model_json_schema(),
                 output_schema=ToolResult.model_json_schema()) for n,s,t,d in CATALOG]

def key(name):
    value = os.getenv(name, '').strip()
    if not value:
        raise ToolFailure('MISSING_KEY', f'{name} 설정이 필요합니다.')
    return value

def get_json(url, params, headers=None):
    try:
        response = requests.get(url, params=params, headers=headers, timeout=(5, 15))
        if response.status_code in (401,403):
            raise ToolFailure('AUTH_ERROR', 'API 인증 또는 접근 권한을 확인하세요.')
        if response.status_code == 429:
            raise ToolFailure('RATE_LIMIT', 'API 호출 한도에 도달했습니다.')
        response.raise_for_status()
        return response.json()
    except requests.Timeout:
        raise ToolFailure('TIMEOUT', '외부 API 응답 시간이 초과되었습니다.') from None
    except (requests.RequestException, ValueError):
        raise ToolFailure('API_ERROR', '외부 API 응답을 확인하지 못했습니다.') from None

def amount(value):
    if value is None or str(value).strip() in ('', '-', 'N/A'):
        return None
    text = str(value).strip().replace(',', '')
    if text.startswith('(') and text.endswith(')'):
        text = '-' + text[1:-1]
    try:
        return int(text)
    except ValueError:
        return None

def parse_financial(data, code, year):
    if data.get('status') != '000':
        status = data.get('status')
        error = 'NO_DATA' if status == '013' else 'AUTH_ERROR' if status in ('010','011','012') else 'RATE_LIMIT' if status == '020' else 'API_ERROR'
        raise ToolFailure(error, f'DART 재무정보를 확인하지 못했습니다. 상태 코드: {status}')
    rows = data.get('list', [])
    # Never mix separate/consolidated statements or unrelated currencies.
    for basis in ('CFS', 'OFS'):
        group = [r for r in rows if r.get('fs_div') == basis and r.get('sj_div') == 'IS']
        revenues = [r for r in group if re.sub(r'\s', '', r.get('account_nm', '')) in ('매출액','수익(매출액)','영업수익')]
        profits = [r for r in group if re.sub(r'\s', '', r.get('account_nm', '')) in ('영업이익','영업이익(손실)','영업손익')]
        if len(revenues) != 1 or len(profits) != 1:
            continue
        rev, profit = revenues[0], profits[0]
        currencies = {r.get('currency') for r in (rev,profit)}
        if len(currencies) != 1 or not next(iter(currencies)):
            continue
        values = [amount(rev.get('thstrm_amount')), amount(rev.get('frmtrm_amount')), amount(profit.get('thstrm_amount'))]
        if any(v is None for v in values):
            continue
        receipt = rev.get('rcept_no', '')
        return dict(stock_code=code, year=year, revenue=values[0], previous_revenue=values[1],
                    operating_profit=values[2], basis=basis, currency=next(iter(currencies)),
                    source='금융감독원 OpenDART', source_url=f'https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}' if receipt else 'https://dart.fss.or.kr',
                    current_period=rev.get('thstrm_dt'), previous_period=rev.get('frmtrm_dt'), demo=False)
    raise ToolFailure('NO_FINANCIALS', '동일 재무제표 기준의 비교 가능한 매출·전기 매출·영업이익을 확인하지 못했습니다.')

class Tools:
    def __init__(self, mode, bus):
        self.mode, self.bus = mode, bus
        self.cache = {}
        self.market_cache = {}
        if mode == 'demo':
            self.items = fixtures.companies()
        else:
            self.items = json.loads((ROOT/'data/items.json').read_text(encoding='utf-8'))['items']
        self.by_code = {i['stock_code']: i for i in self.items}

    def call(self, name, arguments):
        started = time.perf_counter()
        signature = json.dumps([name, arguments], sort_keys=True, default=str)
        if signature in self.cache:
            self.bus.emit('tool_cache', tool=name, arguments=arguments)
            return self.cache[signature]
        self.bus.emit('tool_request', tool=name, arguments=arguments)
        try:
            schema = next((s for n,s,_,_ in CATALOG if n == name), None)
            if not schema:
                raise ToolFailure('UNKNOWN_TOOL', '등록되지 않은 Tool입니다.')
            args = schema.model_validate(arguments)
            data = getattr(self, name)(args)
            result = {'ok': True, 'data': data, 'error': None}
        except ValidationError:
            result = {'ok': False, 'data': None, 'error': {'code': 'INVALID_INPUT', 'message': 'Tool 입력 형식을 확인하세요.'}}
        except ToolFailure as exc:
            result = {'ok': False, 'data': None, 'error': {'code': exc.code, 'message': exc.message}}
        except Exception:
            result = {'ok': False, 'data': None, 'error': {'code': 'TOOL_ERROR', 'message': '자료 처리 중 오류가 발생했습니다. 입력과 설정을 확인하세요.'}}
        self.bus.emit('tool_result', tool=name, result=result, elapsed_ms=round((time.perf_counter()-started)*1000))
        self.cache[signature] = result
        return result

    def candidate_search(self, args):
        requested = args.as_of or today()-timedelta(days=1)
        actual = min(requested, today()-timedelta(days=1))
        available = self.items
        note = '가상 기업·가상 수치입니다. 실제 투자·기업 분석에 사용할 수 없습니다.' if self.mode == 'demo' else '2회차 기업 목록 범위만 탐색하며 전체 시장을 대표하지 않습니다.'
        if self.mode == 'live':
            api_key = unquote(key('DATA_GO_KR_KEY'))
            cache_key = requested.isoformat()
            if cache_key not in self.market_cache:
                market = []
                for back in range(7):
                    day = actual-timedelta(days=back)
                    raw = get_json('https://apis.data.go.kr/1160100/GetStockSecuritiesInfoService_V2/getStockPriceInfo_V2',
                                   dict(serviceKey=api_key, resultType='json', numOfRows=1000, pageNo=1,
                                        basDt=day.strftime('%Y%m%d'), mrktCls='KOSPI'))
                    response = raw.get('response', {})
                    header = response.get('header', {})
                    if str(header.get('resultCode', '00')) not in ('00','0'):
                        raise ToolFailure('API_ERROR', '주식시세 API가 실패 상태를 반환했습니다. 인증·활용 신청 상태를 확인하세요.')
                    body = response.get('body', {})
                    market = (body.get('items') or {}).get('item') or []
                    if isinstance(market, dict):
                        market = [market]
                    if market:
                        actual = day
                        if int(body.get('totalCount', len(market))) > len(market):
                            note += ' API 첫 1,000행 범위로 제한되었습니다.'
                        break
                if not market:
                    raise ToolFailure('NO_RESULT', '조회 기준일 이전 7일에서 시세 자료를 찾지 못했습니다.')
                self.market_cache[cache_key] = (market, actual, note)
            market, actual, note = self.market_cache[cache_key]
            codes = {str(row.get('srtnCd', '')).removeprefix('A').zfill(6) for row in market}
            available = [i for i in available if i['stock_code'] in codes]
        keyword = args.keyword.strip().lower()
        selected = [i for i in available if i['stock_code'] not in args.exclude_codes
                    and (not keyword or keyword in ('전체','기업') or keyword in ' '.join([i['name'], i.get('summary',''), *i.get('aliases',[])]).lower())]
        batch = selected[args.offset:args.offset+args.limit]
        if requested != actual:
            note += f' 요청 기준일 {requested} 대신 확인 가능한 {actual} 자료를 사용합니다. 미래 시세 예측이 아닙니다.'
        return dict(items=[{k: i.get(k) for k in ('stock_code','name','corp_code','wiki_title')} for i in batch],
                    total=len(selected), next_offset=args.offset+len(batch), has_more=args.offset+len(batch)<len(selected),
                    requested_as_of=str(requested), actual_as_of=str(actual), note=note,
                    source_url='https://www.data.go.kr/data/15094808/openapi.do', demo=self.mode=='demo')

    def financial_details(self, args):
        if args.year >= today().year:
            raise ToolFailure('NO_DATA', '아직 완료되지 않은 사업연도입니다. 완료된 연도를 지정하세요.')
        item = self.by_code.get(args.stock_code)
        if not item:
            raise ToolFailure('NOT_FOUND', '후보 목록에 없는 기업입니다.')
        if self.mode == 'demo':
            return fixtures.financial(args.stock_code, args.year)
        if not item.get('corp_code'):
            raise ToolFailure('NO_CORP_CODE', '해당 기업의 DART 고유번호가 없습니다.')
        raw = get_json('https://opendart.fss.or.kr/api/fnlttSinglAcnt.json',
                       dict(crtfc_key=key('DART_API_KEY'), corp_code=item['corp_code'], bsns_year=args.year, reprt_code='11011'))
        return parse_financial(raw, args.stock_code, args.year)

    def background_search(self, args):
        item = self.by_code.get(args.stock_code)
        if not item:
            raise ToolFailure('NOT_FOUND', '기업을 찾지 못했습니다.')
        if self.mode == 'demo':
            return dict(title=item['name'], summary=item['summary'], url='', source='가상 배경 자료', demo=True)
        # Reuse the existing wiki page identity rules, never a guessed search hit.
        from finrag.collect.wiki import fetch_page
        page = fetch_page(item.get('wiki_title') or item['name'], intro_only=True)
        if not page:
            raise ToolFailure('NO_RESULT', '명확히 일치하는 위키백과 기업 문서를 찾지 못했습니다.')
        return dict(title=page['title'], summary=page['text'][:1400], url=page['url'],
                    source='한국어 위키백과', license='CC BY-SA 4.0', demo=False)

    def rag_search(self, args):
        from finrag.config import path
        if self.mode == 'demo' or not path('chroma_dir').exists():
            raise ToolFailure('NO_INDEX', '2회차 Chroma 색인이 없습니다. 기존 색인을 연결하거나 자료를 먼저 색인하세요.')
        from finrag.store.vectorstore import collection
        store = collection('docs')
        if not store._collection.count():
            raise ToolFailure('NO_INDEX', '색인된 기업 문서가 없습니다.')
        docs = store.similarity_search(args.question, k=4, filter={'stock_code': args.stock_code})
        if not docs:
            raise ToolFailure('NO_RESULT', '해당 기업에서 관련 근거를 찾지 못했습니다.')
        return {'sources': [dict(text=d.page_content, metadata=d.metadata) for d in docs]}

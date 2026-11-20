import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from planner.agent import run_chat, judge_financial, llm_error
from planner.models import ChatIn, Conditions, Interpretation, WorkPlan
from planner.storage import Store
from planner.events import EventBus
from planner.tools import Tools, parse_financial, ToolFailure
from planner.config import redact
from planner.api import create_app

class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name)/'test.db')
    def tearDown(self):
        self.temp.cleanup()
    def ask(self,message,**kwargs):
        return run_chat(ChatIn(message=message,**kwargs),self.store)

    def test_replan_and_no_duplicate_financial_calls(self):
        r=self.ask('매출이 10% 이상 성장한 흑자 기업 3개')
        self.assertEqual(len(r['companies']),3)
        self.assertEqual(r['replans'],1)
        calls=[e['arguments']['stock_code'] for e in r['events'] if e['kind']=='tool_request' and e['tool']=='financial_details']
        self.assertEqual(len(calls),len(set(calls)))
        self.assertTrue(all(c['growth_pct']>=10 and c['financial']['operating_profit']>0 for c in r['companies']))
        self.assertEqual(len(self.store.logs(r['session_id'])),len(r['events']))

    def test_first_pass(self):
        r=self.ask('전체 기업 3개')
        self.assertEqual(r['replans'],0)
        self.assertEqual(len(r['companies']),3)

    def test_replan_limit(self):
        r=self.ask('매출 999% 이상 성장 기업 1개')
        self.assertEqual(r['replans'],3)
        self.assertIn('상한',r['answer'])
        self.assertEqual(r['companies'],[])

    def test_memory_only_updates_changed_condition(self):
        first=self.ask('반도체 매출 10% 이상 흑자 기업 2개')
        r=self.ask('같은 조건으로 1개만',session_id=first['session_id'])
        self.assertEqual(r['conditions']['keyword'],'반도체')
        self.assertEqual(r['conditions']['growth_min'],10)
        self.assertTrue(r['conditions']['profitable'])
        self.assertEqual(r['conditions']['count'],1)

    def test_different_candidates_excludes_previous(self):
        first=self.ask('전체 기업 1개')
        r=self.ask('같은 조건으로 다른 기업 1개',session_id=first['session_id'])
        self.assertNotEqual(r['companies'][0]['stock_code'],first['companies'][0]['stock_code'])

    def test_schedule_extraction_and_memory_failure(self):
        r=self.ask('2026-11-20 14시부터 16시 전체 기업 1개')
        self.assertEqual(r['conditions']['research_date'],'2026-11-20')
        self.assertEqual(r['conditions']['start_time'],'14:00:00')
        self.assertEqual(r['conditions']['end_time'],'16:00:00')
        with patch.object(self.store,'remember',side_effect=OSError('disk full')):
            r=self.ask('전체 기업 1개')
        self.assertEqual(len(r['companies']),1)
        self.assertIn('대화 맥락 저장',r['notes'][-1])

    def test_greeting_has_no_tools(self):
        r=self.ask('안녕')
        self.assertFalse(any(e['kind']=='tool_request' for e in r['events']))

    def planned_live(self, intent, names):
        class DemoTools(Tools):
            def __init__(self, mode, bus): super().__init__('demo',bus)
        class Model:
            def with_structured_output(self, schema, **kwargs):
                self.schema=schema
                return self
            def invoke(self, prompt):
                if self.schema is Interpretation:
                    return Interpretation(intent=intent,updates={'count':1},reason='검증용 의도')
                return WorkPlan(tools=names,reason='검증용 선택')
        with patch('planner.agent.llm',return_value=Model()):
            return run_chat(ChatIn(message='검증 요청',mode='live'),self.store,tool_factory=DemoTools)

    def test_executor_uses_selected_plan_without_optional_background(self):
        r=self.planned_live('research',['candidate_search','financial_details'])
        calls=[e['tool'] for e in r['events'] if e['kind']=='tool_request']
        self.assertEqual(calls,['candidate_search','financial_details'])
        self.assertEqual(len(r['companies']),1)
        self.assertIsNone(r['companies'][0]['background'])

    def test_selected_optional_order_controls_execution(self):
        r=self.planned_live('research',['candidate_search','background_search','financial_details'])
        calls=[e['tool'] for e in r['events'] if e['kind']=='tool_request']
        self.assertEqual(calls,['candidate_search','background_search','financial_details'])
        plan=next(e['steps'] for e in r['events'] if e['kind']=='plan')
        self.assertEqual(calls,[s['tool'] for s in plan])

    def test_dependency_guard_adds_only_required_financial_tool(self):
        r=self.planned_live('research',['candidate_search'])
        calls=[e['tool'] for e in r['events'] if e['kind']=='tool_request']
        self.assertEqual(calls,['candidate_search','financial_details'])
        self.assertTrue(any(e['kind']=='plan_guard' for e in r['events']))

    def test_background_request_skips_unnecessary_financials(self):
        r=self.ask('전체 기업 1개 사업 배경만 알려줘')
        calls=[e['tool'] for e in r['events'] if e['kind']=='tool_request']
        self.assertEqual(calls,['candidate_search','background_search'])
        self.assertIsNone(r['companies'][0]['financial'])
        r=self.planned_live('background',['financial_details','background_search'])
        self.assertEqual([e['tool'] for e in r['events'] if e['kind']=='tool_request'],calls)

    def test_failure_is_not_reported_as_successful_background(self):
        with patch.object(Tools,'background_search',side_effect=ToolFailure('NO_RESULT','배경 없음')):
            r=self.ask('전체 기업 1개 사업 배경만 알려줘')
        self.assertEqual(r['companies'],[])
        self.assertTrue(r['errors'])

    def test_session_isolation(self):
        first=self.ask('반도체 기업 1개')
        other=self.ask('전체 기업 3개')
        self.assertNotEqual(first['session_id'],other['session_id'])
        self.assertTrue(all(e['session_id']==first['session_id'] for e in self.store.logs(first['session_id'])))

    def test_logging_failure_does_not_break_agent(self):
        def broken(e): raise OSError('disk failed')
        r=run_chat(ChatIn(message='전체 기업 1개'),self.store,subscribers=[broken])
        self.assertTrue(r['logging_failed'])
        self.assertEqual(len(r['companies']),1)
        r=run_chat(ChatIn(message='전체 기업 1개'),self.store,subscribers=[])
        self.assertEqual(len(r['companies']),1)
        self.assertEqual(self.store.logs(r['session_id']),[])

    def test_one_tool_failure_continues(self):
        original=Tools.financial_details
        def fail_one(self,args):
            if args.stock_code=='D00000': raise ToolFailure('NO_DATA','누락 시험')
            return original(self,args)
        with patch.object(Tools,'financial_details',fail_one): r=self.ask('전체 기업 3개')
        self.assertEqual(len(r['companies']),3)
        self.assertTrue(any(e['code']=='NO_DATA' for e in r['errors']))

    def test_growth_zero_denominator_not_invented(self):
        growth,reasons=judge_financial(dict(previous_revenue=0,revenue=100,operating_profit=2),dict(growth_min=10,profitable=True))
        self.assertIsNone(growth)
        self.assertTrue(reasons)

    def test_tool_validation_cache_and_missing_key(self):
        tools=Tools('demo',EventBus('a','b'))
        self.assertEqual(tools.call('financial_details',{'stock_code':'bad','year':2025})['error']['code'],'INVALID_INPUT')
        self.assertEqual(tools.call('unknown',{})['error']['code'],'UNKNOWN_TOOL')
        a=tools.call('candidate_search',{})
        self.assertEqual(a,tools.call('candidate_search',{}))
        self.assertEqual(tools.bus.events[-1]['kind'],'tool_cache')
        with patch.dict(os.environ,{'DATA_GO_KR_KEY':''}):
            live=Tools('live',EventBus('a','b'))
            self.assertEqual(live.call('candidate_search',{})['error']['code'],'MISSING_KEY')

    def test_future_requested_date_is_disclosed(self):
        r=Tools('demo',EventBus('a','b')).call('candidate_search',{'as_of':'2099-01-01'})
        self.assertNotEqual(r['data']['requested_as_of'],r['data']['actual_as_of'])
        self.assertIn('2099-01-01',r['data']['note'])

    def test_rag_without_index(self):
        r=Tools('demo',EventBus('a','b')).call('rag_search',{'stock_code':'D00000','year':2025,'question':'사업 내용'})
        self.assertEqual(r['error']['code'],'NO_INDEX')

    def test_dart_basis_and_missing_amount(self):
        def row(name,current,previous,basis='CFS'):
            return dict(account_nm=name, thstrm_amount=current, frmtrm_amount=previous, fs_div=basis,sj_div='IS',currency='KRW')
        rows=[row('매출액','120','100'),row('영업이익','5','4'),row('매출액','999','1','OFS')]
        r=parse_financial({'status':'000','list':rows},'005930',2025)
        self.assertEqual(r['revenue'],120)
        self.assertEqual(r['basis'],'CFS')
        rows[0]['thstrm_amount']=''
        with self.assertRaises(ToolFailure):parse_financial({'status':'000','list':rows},'005930',2025)

    def test_redaction_preserves_keyword(self):
        with patch.dict(os.environ,{'GMS_KEY':'not-a-real-secret'}):
            r=redact({'keyword':'반도체','api_key':'not-a-real-secret','text':'not-a-real-secret serviceKey=abc'})
        self.assertEqual(r['keyword'],'반도체')
        self.assertNotIn('not-a-real-secret',json.dumps(r))
        self.assertNotIn('abc',json.dumps(r))

    def test_missing_llm_key_safe(self):
        with patch.dict(os.environ,{'GMS_KEY':'','GMS_API_KEY':'','OPENAI_API_KEY':''}):
            r=self.ask('매출 성장 기업 1개',mode='live')
        self.assertEqual(r['errors'][0]['code'],'LLM_ERROR')
        self.assertFalse(any(e['kind']=='tool_request' for e in r['events']))

    def test_llm_quota_failure_is_actionable_and_redacted(self):
        class QuotaError(Exception):
            status_code=429
        exc=QuotaError('insufficient_quota organization_spend_limit_exceeded private-provider-payload')
        self.assertEqual(llm_error(exc)['code'],'LLM_QUOTA')
        with patch('planner.agent.llm',side_effect=exc):
            r=self.ask('기업 1개',mode='live')
        self.assertEqual(r['errors'][0]['code'],'LLM_QUOTA')
        self.assertIn('한도',r['answer'])
        self.assertNotIn('private-provider-payload',json.dumps(r))
        self.assertFalse(any(e['kind']=='tool_request' for e in r['events']))

    def test_api_crud_overlap_logs_and_validation(self):
        with TestClient(create_app(self.store.path)) as client:
            body=dict(title='테스트 조사',research_date='2026-11-20',start_time='14:00',end_time='16:00',companies=[{'name':'가상기업'}])
            first=client.post('/api/plans',json=body)
            self.assertEqual(first.status_code,201)
            identifier=first.json()['plan']['id']
            second=client.post('/api/plans',json={**body,'start_time':'15:00'})
            self.assertEqual(second.status_code,201)
            self.assertIn(identifier,second.json()['overlapping_plan_ids'])
            changed=client.put('/api/plans/'+identifier,json={**body,'title':'수정','start_time':'10:00','end_time':'11:00'})
            self.assertEqual(changed.json()['plan']['title'],'수정')
            self.assertEqual(changed.json()['overlapping_plan_ids'],[])
            self.assertEqual(client.get('/api/plans/'+identifier).status_code,200)
            self.assertEqual(client.post('/api/plans',json={**body,'end_time':'13:00'}).status_code,422)
            self.assertEqual(client.delete('/api/plans/'+identifier).status_code,204)
            self.assertEqual(client.get('/api/plans/'+identifier).status_code,404)
            r=client.post('/api/chat',json={'message':'안녕'}).json()
            self.assertTrue(client.get('/api/logs').json())
            self.assertEqual(client.put('/api/logs/'+r['session_id'],json={}).status_code,405)
            self.assertEqual(client.delete('/api/logs/'+r['session_id']).status_code,204)
            self.assertEqual(client.get('/api/logs/'+r['session_id']).json(),[])
            self.assertTrue(client.get('/openapi.json').json()['paths'])
            schemas=client.get('/openapi.json').json()['components']['schemas']
            self.assertIn('ChatOut',schemas)
            self.assertIn('PlanSaved',schemas)
            catalog=client.get('/api/tools').json()
            self.assertEqual(catalog[0]['output_schema']['type'],'object')

if __name__=='__main__': unittest.main()

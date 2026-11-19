"""Run after the user fills .env. Live data stays ignored, outside submission ZIP."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import json
from datetime import datetime
from planner.config import ROOT, KST, today, redact
from planner.events import EventBus
from planner.tools import Tools
from planner.agent import run_chat
from planner.models import ChatIn, Conditions
from planner.storage import Store

def main():
    target=ROOT/'data/live-verification'
    target.mkdir(parents=True,exist_ok=True)
    bus=EventBus('live-check','tools')
    tools=Tools('live',bus)
    results={
        'candidate_search':tools.call('candidate_search',{'keyword':'삼성전자','limit':1}),
        'financial_details':tools.call('financial_details',{'stock_code':'005930','year':today().year-1}),
        'background_search':tools.call('background_search',{'stock_code':'005930'}),
    }
    store=Store(target/'verification.sqlite')
    # Fresh session each run: historical memory must not change the smoke-test input.
    chat=run_chat(ChatIn(message='삼성전자 기업 1개를 조사해줘',mode='live'),store)
    followup=run_chat(ChatIn(message='같은 조건에서 2024년 자료로 바꿔줘',mode='live',session_id=chat['session_id']),store)
    replan=run_chat(ChatIn(message='입력한 재무 조건으로 기업을 탐색해주세요',mode='live',
                          conditions=Conditions(count=1,year=today().year-1,growth_min=99999)),store)
    status={name:{'ok':r['ok'],'error':r['error']} for name,r in results.items()}
    llm_events=[e for e in chat['events'] if e['kind']=='decision' and e.get('engine')=='llm']
    status['llm']={'analysis_and_plan_observed':len(llm_events)>=2}
    status['end_to_end']={'ok':bool(chat['companies']) and not chat['errors']}
    status['memory']={'ok':bool(chat['companies']) and followup['conditions']['year']==2024 and
                      followup['conditions']['keyword']==chat['conditions']['keyword'] and
                      bool(followup['companies']) and not followup['errors']}
    status['replan_limit']={'ok':replan['replans']==3 and not replan['companies'] and not replan['errors']}
    verified_at=datetime.now(KST).isoformat()
    payload=redact(dict(verified_at=verified_at,status=status,tools=results,chat=chat,followup=followup,replan=replan))
    (target/'result.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    # Publish only test outcomes and timing, never actual provider responses or credentials.
    public=dict(verified_at=verified_at,status=status,
                elapsed_ms={'initial':chat['elapsed_ms'],'followup':followup['elapsed_ms'],'replan':replan['elapsed_ms']},
                replan_count=replan['replans'],raw_responses_included=False)
    evidence=ROOT/'docs/core3/evidence'
    evidence.mkdir(parents=True,exist_ok=True)
    (evidence/'live_status.json').write_text(json.dumps(public,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(status,ensure_ascii=False,indent=2))
    print('Live details saved locally only:',target/'result.json')
    return 0 if all(r['ok'] for r in results.values()) and len(llm_events)>=2 and all(status[n]['ok'] for n in ('end_to_end','memory','replan_limit')) else 1

if __name__=='__main__':sys.exit(main())

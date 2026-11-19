import os
import threading
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from planner.config import ROOT, redact
from planner.models import ChatIn, PlanIn
from planner.storage import Store
from planner.agent import run_chat
from planner.tools import tool_specs

def create_app(db_path=None):
    app = FastAPI(title='FinScope · 기업 리서치 플래너', version='3.0.0')
    app.add_middleware(CORSMiddleware, allow_origins=['http://127.0.0.1:5500','http://localhost:5500'],
                       allow_methods=['GET','POST','PUT','DELETE'], allow_headers=['Content-Type'])
    store = Store(db_path or os.getenv('PLANNER_DB', str(ROOT/'data/planner.sqlite')))
    app.state.store = store
    # Bound striped locks serialize a session without retaining arbitrary user IDs forever.
    locks = [threading.Lock() for _ in range(64)]

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request, exc):
        return JSONResponse(status_code=422, content={'detail':'입력 형식·범위 또는 시작/종료 시각을 확인해주세요.'})

    @app.exception_handler(Exception)
    async def safe_error(request, exc):
        return JSONResponse(status_code=500, content={'detail':'요청을 처리하지 못했습니다. 서버 설정을 확인해주세요.'})

    @app.get('/api/health')
    def health():
        return {'status':'ok', 'keys':{k:bool(os.getenv(k)) for k in ('GMS_KEY','OPENAI_API_KEY','DART_API_KEY','DATA_GO_KR_KEY')},
                'demo_available':True, 'live_verified':False}

    @app.get('/api/tools')
    def specs():
        return tool_specs()

    @app.post('/api/chat')
    def chat(body:ChatIn):
        with locks[hash(body.session_id or '') % len(locks)]:
            return redact(run_chat(body,store))

    @app.get('/api/plans')
    def plans():
        return store.plans()

    @app.post('/api/plans',status_code=201)
    def save(body:PlanIn):
        return store.save_plan(body.model_dump(mode='json'))

    @app.get('/api/plans/{identifier}')
    def plan(identifier:str):
        value = store.plan(identifier)
        if not value:
            raise HTTPException(404,'계획을 찾지 못했습니다.')
        return value

    @app.put('/api/plans/{identifier}')
    def update(identifier:str, body:PlanIn):
        try:
            return store.save_plan(body.model_dump(mode='json'),identifier)
        except KeyError:
            raise HTTPException(404,'계획을 찾지 못했습니다.') from None

    @app.delete('/api/plans/{identifier}',status_code=204)
    def delete(identifier:str):
        if not store.delete_plan(identifier):
            raise HTTPException(404,'계획을 찾지 못했습니다.')

    @app.get('/api/logs')
    def logs():
        return store.log_sessions()

    @app.get('/api/logs/{session}')
    def detail(session:str):
        return store.logs(session)

    @app.delete('/api/logs/{session}',status_code=204)
    def delete_logs(session:str):
        if not store.delete_logs(session):
            raise HTTPException(404,'실행 로그를 찾지 못했습니다.')

    return app

app = create_app()

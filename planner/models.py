from datetime import date, time
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Conditions(StrictModel):
    keyword: str = Field(default='', max_length=80)
    count: int = Field(default=3, ge=1, le=10)
    year: int | None = Field(default=None, ge=2015, le=2100)
    growth_min: float | None = Field(default=None, ge=-100, le=100000)
    profitable: bool = False
    research_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    exclude_codes: list[str] = Field(default_factory=list, max_length=100)

class Interpretation(StrictModel):
    intent: Literal['research', 'greeting', 'help', 'rag', 'clarify']
    updates: dict = Field(default_factory=dict, description='Only fields explicitly set or changed by this message; null clears optional fields.')
    different_candidates: bool = False
    reason: str = Field(max_length=400)
    question: str = Field(default='', max_length=400)

class WorkPlan(StrictModel):
    tools: list[Literal['candidate_search', 'financial_details', 'background_search', 'rag_search']]
    reason: str = Field(max_length=400)

class ChatIn(StrictModel):
    message: str = Field(min_length=1, max_length=3000)
    session_id: str | None = Field(default=None, pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    mode: Literal['demo', 'live'] = 'demo'
    conditions: Conditions | None = None

class PlanIn(StrictModel):
    title: str = Field(min_length=1, max_length=120)
    research_date: date
    start_time: time
    end_time: time
    companies: list[dict] = Field(min_length=1, max_length=10)
    notes: str = Field(default='', max_length=4000)
    mode: Literal['demo', 'live'] = 'demo'

    @model_validator(mode='after')
    def valid_time(self):
        if self.end_time <= self.start_time:
            raise ValueError('종료 시각은 시작 시각보다 늦어야 합니다. 날짜를 넘기는 일정은 나누어 저장하세요.')
        return self

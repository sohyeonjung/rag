from datetime import datetime
from planner.config import KST, redact

class EventBus:
    def __init__(self, session_id, run_id, subscribers=()):
        self.session_id, self.run_id = session_id, run_id
        self.subscribers = list(subscribers)
        self.events = []
        self.logging_failed = False

    def emit(self, kind, **data):
        event = redact(dict(session_id=self.session_id, run_id=self.run_id,
                            timestamp=datetime.now(KST).isoformat(), kind=kind,
                            order=len(self.events) + 1, **data))
        self.events.append(event)
        for subscriber in self.subscribers:
            try:
                subscriber(event)
            except Exception:
                self.logging_failed = True
        return event

class LoggingAgent:
    """Subscriber only; never calls an LLM, and cannot interrupt the main graph."""
    def __init__(self, store):
        self.store = store

    def __call__(self, event):
        self.store.log(event)

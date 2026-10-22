"""설정 로드. 비밀 값은 .env(환경변수), 튜닝 값은 config.yaml에서 읽는다."""

import os
import re
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

SECRET_ENV_NAMES = ("GMS_KEY", "DART_API_KEY", "DATA_GO_KR_KEY")


class MissingKeyError(RuntimeError):
    """필요한 키가 .env에 없을 때. 화면에는 키 이름만 보여 준다."""

    def __init__(self, name: str):
        super().__init__(f"{name}가 설정되지 않았습니다. .env 파일을 확인하세요.")
        self.name = name


@lru_cache
def settings() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def env(name: str, required: bool = True) -> str:
    value = os.environ.get(name, "").strip()
    if required and not value:
        raise MissingKeyError(name)
    return value


def path(key: str) -> Path:
    """config.yaml의 paths 항목을 저장소 기준 절대 경로로 돌려준다."""
    return ROOT / settings()["paths"][key]


def model_name(role: str) -> str:
    """role: query(P1) · select(P2) · answer(P3)"""
    profile = env("MODEL_PROFILE", required=False) or "dev"
    return settings()["models"][profile][role]


def mask_secrets(text: str) -> str:
    """로그·오류 메시지에서 키 값을 지운다. DART 키는 URL 쿼리(crtfc_key=)에 들어가므로 특히 주의. (NF2004)"""
    for name in SECRET_ENV_NAMES:
        value = os.environ.get(name, "").strip()
        if value:
            text = text.replace(value, "***")
    return re.sub(r"(crtfc_key|serviceKey)=[^&\s]+", r"\1=***", text)

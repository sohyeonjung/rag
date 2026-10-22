"""외부 API 공통 호출. 재시도 · 호출 간격 · 키 마스킹을 한 곳에서 지킨다. (NF2006, NF2004)"""

import time

import requests

from finrag.config import mask_secrets, settings


class ExternalAPIError(RuntimeError):
    """외부 API 호출 실패. 메시지에는 키가 남지 않는다."""


_last_call: dict[str, float] = {}


def _wait_interval(host: str) -> None:
    interval = settings()["collect"]["request_interval_sec"]
    elapsed = time.monotonic() - _last_call.get(host, 0.0)
    if elapsed < interval:
        time.sleep(interval - elapsed)
    _last_call[host] = time.monotonic()


def get(url: str, params: dict, headers: dict | None = None, timeout: int = 30) -> requests.Response:
    """GET 요청. 429·5xx·네트워크 오류는 점점 길게 기다리며 다시 시도한다."""
    host = requests.utils.urlparse(url).netloc
    retries = settings()["collect"]["max_retries"]
    wait = 2.0
    last_error = ""
    for attempt in range(1, retries + 1):
        _wait_interval(host)
        try:
            response = requests.get(url, params=params, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            last_error = type(e).__name__
        else:
            if response.status_code == 200:
                return response
            last_error = f"HTTP {response.status_code}"
            if response.status_code != 429 and response.status_code < 500:
                break
            retry_after = response.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                wait = max(wait, float(retry_after))
        if attempt < retries:
            time.sleep(wait)
            wait *= 2
    raise ExternalAPIError(mask_secrets(f"{host} 호출 실패 ({last_error})"))

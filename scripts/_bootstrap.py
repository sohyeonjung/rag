"""scripts/*.py 를 저장소 루트에서 `python scripts/xxx.py` 로 실행할 수 있게 한다."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Windows 콘솔(cp949)에서도 한글이 깨지지 않게 (트러블슈팅 #1)
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

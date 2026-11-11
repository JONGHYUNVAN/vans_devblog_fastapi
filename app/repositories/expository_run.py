"""설명문 파이프라인 실행 폴더 하나에 대한 읽기·쓰기. 단계 결과·프롬프트·시도 원문·호출 기록을 파일로 남겨 사람이 열어 보고 이어 할 수 있게 한다."""

import json
from pathlib import Path
from typing import Any

import anyio

LOG_FILE = "log.jsonl"


class ExpositoryRunRepository:
    """파일 이름 하나가 결과 하나다. 줄바꿈 변환을 막으려고 바이트로 쓴다(Windows 에서도 LF)."""

    def __init__(self, run_dir: Path) -> None:
        self._run_dir = anyio.Path(run_dir)

    @property
    def run_dir(self) -> Path:
        return Path(self._run_dir)

    async def get_text(self, name: str) -> str | None:
        """없으면 None."""
        path = self._run_dir / name
        if not await path.exists():
            return None
        return (await path.read_bytes()).decode("utf-8")

    async def get_json(self, name: str) -> Any | None:
        """없으면 None. 사람이 고친 파일이 JSON 이 아니면 json.JSONDecodeError 를 그대로 던진다."""
        text = await self.get_text(name)
        return None if text is None else json.loads(text)

    async def put_text(self, name: str, text: str) -> None:
        await self._run_dir.mkdir(parents=True, exist_ok=True)
        await (self._run_dir / name).write_bytes(text.encode("utf-8"))

    async def put_json(self, name: str, value: Any) -> None:
        await self.put_text(name, json.dumps(value, ensure_ascii=False, indent=2) + "\n")

    async def add_log(self, entry: dict[str, Any]) -> None:
        """호출 하나의 기록을 log.jsonl 에 한 줄로 덧붙인다."""
        await self._run_dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        async with await anyio.open_file(self._run_dir / LOG_FILE, "ab") as log_file:
            await log_file.write(line.encode("utf-8"))

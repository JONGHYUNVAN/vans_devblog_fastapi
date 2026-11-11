"""설명문 파이프라인 실행 폴더 하나에 대한 읽기·쓰기. 단계 결과와 입력을 채운 프롬프트를 파일로 남겨 사람이 열어 보고 이어 할 수 있게 한다.

호출 원문·토큰·오류는 여기 두지 않는다. 영수증(agent_calls)에 있다(09 문서 D-99).
"""

import json
from pathlib import Path
from typing import Any

import anyio


class ExpositoryRunRepository:
    """파일 이름 하나가 결과 하나다. 줄바꿈 변환을 막으려고 바이트로 쓴다(Windows 에서도 LF)."""

    def __init__(self, run_dir: Path) -> None:
        self._run_dir = anyio.Path(run_dir)

    @property
    def run_dir(self) -> Path:
        """실행 폴더. 폴더 이름이 영수증의 실행 id 가 된다."""
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
        """파일 하나를 통째로 쓴다. 실행 폴더가 없으면 만든다."""
        await self._run_dir.mkdir(parents=True, exist_ok=True)
        await (self._run_dir / name).write_bytes(text.encode("utf-8"))

    async def put_json(self, name: str, value: Any) -> None:
        """들여쓴 JSON 으로 쓴다. 사람이 열어 고칠 수 있게 한글을 이스케이프하지 않는다."""
        await self.put_text(name, json.dumps(value, ensure_ascii=False, indent=2) + "\n")

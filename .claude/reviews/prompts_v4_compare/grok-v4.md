# 만료 전에는 로그아웃이 아니다: 스테이트리스 액세스 토큰의 무효화 경계

> 서명만 검사하는 스테이트리스 액세스 토큰은 만료 전 개별 폐기가 불가능하다. 무효화 경계는 로그아웃이 아니라 만료 시각이다.

<!-- sec:intro -->

로그아웃 처리가 끝난 뒤에도 같은 액세스 토큰을 붙인 요청은 `exp` 이전이면 검증을 통과한다. 서버가 그 토큰의 사본을 저장하지 않아, 로그아웃 핸들러가 지울 검증 입력이 없기 때문이다. 서명만 검사하는 스테이트리스 액세스 토큰은 만료 시각 이전에는 서버가 개별 폐기할 수 없어, 만료 시각만이 무효화 경계가 된다.

이 글은 그 경계를 검증 순서와 실행 가능한 검증기로 고정한다. 범위는 네 가지다. 스테이트리스가 서버에서 지웠다는 뜻과 다른 이유, 서명 검사 뒤에 만료 시각만 거절 조건으로 남는 순서, 짧은 만료가 탈취 후 남은 창을 줄이는 방식, 만료를 길게 잡는 관행이 그 창을 다시 여는 지점이다.

전제는 JWT 헤더와 페이로드를 디코드해 본 경험이다. 검증기는 토큰 헤더의 `alg`로 알고리즘을 고르지 않고 HS256과 서버 비밀로 고정한다. 쿠키와 웹 스토리지 중 저장 위치, 리프레시 토큰 로테이션, 서명 알고리즘 교체는 구현하지 않는다. 15분과 7일은 측정값이 아니라, 같은 `now`에서 창의 길이가 TTL을 따른다는 것을 보이기 위한 스크립트 입력이다.

<!-- sec:body-1 -->

## 1. 스테이트리스는 서버가 지웠다는 뜻이 아니다

세션 저장소가 있는 검증은 식별자를 서버 행으로 해석한다. 로그아웃은 그 행을 지운다. 다음 요청이 같은 식별자를 가져와도 조회가 실패하므로 거절된다. 삭제가 무효화인 이유는 검증이 매번 그 행을 읽기 때문이다.

인과는 이렇다. `logout`이 행을 삭제한다. 다음 `verify`가 그 행을 읽는다. 키가 없으면 `unknown_session`이다. 로그아웃의 반환과 다음 요청의 거절 사이에 공유 상태가 있다.

<!-- sec:code-1 -->
```python-snippet
class SessionStore:
    def logout(self, session_id: str) -> None:
        self._rows.pop(session_id, None)

    def verify(self, session_id: str) -> str:
        try:
            return self._rows[session_id]
        except KeyError as exc:
            raise Rejected("unknown_session") from exc
```

액세스 토큰을 서명된 클레임으로 두고 서버에 대응 행을 두지 않으면 이 조회가 사라진다. 토큰 문자열이 자격 증명 자체다. 클라이언트가 메모리에서 문자열을 버려도 서버 검증 함수의 입력은 바뀌지 않는다. 이미 복사된 문자열은 그 함수가 모르는 곳에 남는다.

토큰 쪽 인과에는 로그아웃에서 검증으로 가는 화살표가 없다. 로그아웃 핸들러는 클라이언트 쪽 문자열을 버리게 할 뿐이다. `verify`의 인자는 토큰 문자열, 비밀, `now`뿐이다. 복사본을 첫 인자에 넣으면 서버는 서명과 `exp`만 본다. `exp` 이전이면 수락이다. 핸들러가 204를 돌려도 그 사실이 `verify`에 전달되지 않으면, 로그아웃의 성공은 다음 요청의 거절이 아니다.

그래서 「토큰을 쓰면 세션이 필요 없다」는 구현 선택이 로그아웃의 의미를 바꾼다. 세션 삭제는 다음 `verify`의 결과를 바꾼다. 스테이트리스 액세스 토큰의 로그아웃은, 서버가 개별 토큰을 기억하지 않는 한, 다음 `verify`의 결과를 바꾸지 못한다. 지울 행이 없는데 지웠다고 말하는 것이 실패 조건이다.

아래 전체 스크립트의 `SessionStore`는 삭제 후 `unknown_session`으로 거절한다. 같은 파일의 `verify`에는 폐기 목록 인자가 없고, 클라이언트가 토큰을 버렸는지를 읽지 않는다. 세션 쪽 제약(행이 없으면 거절)을 토큰 쪽으로 옮기면서 조용히 빼 먹은 것이 아니다. 그 제약을 만족할 상태가 검증 경로에 없다. 없는 상태를 있는 것처럼 처리하지 않으려고 폐기 조회를 넣지 않았다. 조회를 넣는 순간 그 경로는 스테이트리스가 아니다.

발급은 한 번이고 사본은 여러 개일 수 있다. 이 검증기는 사용 횟수를 줄이지 않는다. 줄이려면 서버가 직전 횟수를 기억해야 하고, 그 기억은 토큰 밖의 상태다. 사본이 하나이든 여럿이든 같은 바이트열은 같은 `exp`까지 같은 결과를 낸다.

<!-- sec:body-2 -->

## 2. 서명 다음의 거절 조건은 만료 시각뿐이다

이 검증기의 순서는 고정이다.

1. `.`로 나눈 조각이 세 개가 아니거나 빈 조각이 있으면 `malformed`다.
2. 헤더, 페이로드, 서명의 base64url이 깨지거나 JSON이 아니면 `malformed`다. 서명은 파싱된 JSON을 다시 직렬화한 값이 아니라, 토큰에 있던 두 구간의 원문 바이트에 대해 계산한다. 키 순서를 서버가 재배열하면 발급 때와 다른 바이트가 되어 맞는 토큰을 거절하게 되므로, 재직렬화 비교는 쓰지 않는다.
3. 헤더가 객체가 아니거나 `alg`가 `HS256`이 아니면 `unexpected_alg`다. `typ`는 거절 조건이 아니다. 알고리즘은 토큰에서 고르지 않는다.
4. 서버 비밀로 HMAC-SHA256을 계산하고 `compare_digest`로 서명과 비교한다. 불일치면 `bad_signature`다. 여기까지 페이로드 클레임은 인가에 쓰지 않는다.
5. 페이로드가 객체가 아니면 `malformed`다. 허용 클레임은 `sub`, `iat`, `exp`뿐이다. 그 밖이 있으면 `unenforced_claim`이다. `jti`나 `nbf`를 받아 두고 집행하지 않으면, 없는 폐기가 있는 것처럼 보인다. 그래서 집행하지 않을 클레임은 수락하지 않는다.
6. `exp`가 정수이고 불리언이 아닐 때만 시각 비교로 간다. 없거나 소수점이거나 불리언이면 `missing_exp`다. RFC 7519의 NumericDate는 현재 시각이 만료 시각보다 이전일 것을 요구하므로 `now >= exp`이면 `expired`다. `now == exp`도 만료다. 시계 여유는 넣지 않는다. 여유는 경계를 `exp` 뒤로 민다.

`iat`는 허용하지만 비교하지 않는다. `iat`가 없거나 `now`보다 뒤여도 `exp`만 맞으면 수락한다. 발급 시각으로 자르려면 사용자별 최소 `iat` 같은 서버 상태가 필요하다. 그 비교를 넣지 않은 이유는 빼먹어서가 아니라, 넣는 순간 사용자 레코드가 검증 입력이 되기 때문이다. `sub`는 누구의 토큰인지 말할 뿐 거절 조건이 아니다.

따라서 이 발급기가 만드는 토큰, 즉 `sub`와 `iat`와 정수 `exp`만 가진 토큰에 대해, 서명이 맞은 뒤 서버가 개별적으로 닫을 수 있는 분기는 없다. 남은 거절은 `now >= exp`뿐이다. 무효화 경계는 만료 시각이다.

다음 파일은 그 순서와 경계를 실행한다. 비밀은 예제용이다. `demonstrate`는 세션 삭제가 다음 조회를 거절하는지, 짧은 토큰이 발급 60초 뒤에 수락되고 `now == exp`에 `expired`인지, `exp` 1초 전은 아직 수락인지, 같은 시각의 7일 토큰은 수락인지, 미래 `iat`를 무시하는지, 다른 비밀과 `exp` 누락과 여분 클레임이 각각 `bad_signature`, `missing_exp`, `unenforced_claim`인지 단언한다. 뒤의 세 단언을 빼면 서명 뒤의 거절 조건이 깨진 입력과 여분 클레임을 삼킨 것처럼 보인다.

<!-- sec:code-2 -->
```python
"""서명만 검사하는 액세스 토큰 검증기.

발급 클레임은 sub, iat, exp뿐이다.
서명이 맞는 그 토큰의 거절 조건은 exp뿐이다.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(payload: dict[str, Any], secret: bytes) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    head = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{head}.{body}".encode("ascii")
    signature = hmac.new(secret, signing_input, hashlib.sha256).digest()
    return f"{head}.{body}.{_b64url(signature)}"


def issue(subject: str, issued_at: int, ttl_seconds: int, secret: bytes) -> str:
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    return _sign(
        {"sub": subject, "iat": issued_at, "exp": issued_at + ttl_seconds},
        secret,
    )


class Rejected(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


_ALLOWED_CLAIMS = frozenset({"sub", "iat", "exp"})


def verify(token: str, secret: bytes, now: int) -> dict[str, Any]:
    """서명 확인 후 exp만으로 수락 또는 거절한다. 폐기 목록 인자는 없다."""
    pieces = token.split(".")
    if len(pieces) != 3 or any(piece == "" for piece in pieces):
        raise Rejected("malformed")
    head_b64, body_b64, sig_b64 = pieces
    try:
        header = json.loads(_b64url_decode(head_b64))
        payload = json.loads(_b64url_decode(body_b64))
        actual_sig = _b64url_decode(sig_b64)
    except (ValueError, json.JSONDecodeError) as exc:
        raise Rejected("malformed") from exc
    if not isinstance(header, dict) or header.get("alg") != "HS256":
        raise Rejected("unexpected_alg")
    signing_input = f"{head_b64}.{body_b64}".encode("ascii")
    expected_sig = hmac.new(secret, signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(expected_sig, actual_sig):
        raise Rejected("bad_signature")
    if not isinstance(payload, dict):
        raise Rejected("malformed")
    if any(key not in _ALLOWED_CLAIMS for key in payload):
        raise Rejected("unenforced_claim")
    exp = payload.get("exp")
    if not isinstance(exp, int) or isinstance(exp, bool):
        raise Rejected("missing_exp")
    if now >= exp:
        raise Rejected("expired")
    return payload


class SessionStore:
    """대조군. 행 삭제가 다음 조회의 거절이 된다."""

    def __init__(self) -> None:
        self._rows: dict[str, str] = {}

    def create(self, session_id: str, subject: str) -> None:
        self._rows[session_id] = subject

    def logout(self, session_id: str) -> None:
        self._rows.pop(session_id, None)

    def verify(self, session_id: str) -> str:
        try:
            return self._rows[session_id]
        except KeyError as exc:
            raise Rejected("unknown_session") from exc


def demonstrate() -> None:
    secret = b"dev-only-secret"
    issued_at = 1_700_000_000
    short_ttl = 15 * 60
    long_ttl = 7 * 24 * 60 * 60
    theft_time = issued_at + 60

    sessions = SessionStore()
    sessions.create("sid-1", "user-42")
    sessions.logout("sid-1")
    try:
        sessions.verify("sid-1")
        raise AssertionError("세션 삭제는 다음 조회를 거절해야 한다")
    except Rejected as err:
        assert err.reason == "unknown_session"

    short_token = issue("user-42", issued_at, short_ttl, secret)
    long_token = issue("user-42", issued_at, long_ttl, secret)

    accepted = verify(short_token, secret, theft_time)
    assert accepted["sub"] == "user-42"
    assert accepted["exp"] == issued_at + short_ttl
    assert accepted["iat"] == issued_at

    last_open = verify(short_token, secret, issued_at + short_ttl - 1)
    assert last_open["sub"] == "user-42"

    try:
        verify(short_token, secret, issued_at + short_ttl)
        raise AssertionError("now == exp 는 만료다")
    except Rejected as err:
        assert err.reason == "expired"

    still_open = verify(long_token, secret, issued_at + short_ttl)
    assert still_open["exp"] == issued_at + long_ttl

    future_iat = issue("user-42", theft_time + 1_000, short_ttl, secret)
    ignored_iat = verify(future_iat, secret, theft_time)
    assert ignored_iat["iat"] > theft_time

    forged = issue("user-42", issued_at, short_ttl, b"other-secret")
    try:
        verify(forged, secret, theft_time)
        raise AssertionError("다른 키로 서명한 토큰은 거절되어야 한다")
    except Rejected as err:
        assert err.reason == "bad_signature"

    missing_exp = _sign({"sub": "user-42", "iat": issued_at}, secret)
    try:
        verify(missing_exp, secret, theft_time)
        raise AssertionError("exp가 없으면 수락하면 안 된다")
    except Rejected as err:
        assert err.reason == "missing_exp"

    extra = _sign(
        {
            "sub": "user-42",
            "iat": issued_at,
            "exp": issued_at + short_ttl,
            "jti": "x",
        },
        secret,
    )
    try:
        verify(extra, secret, theft_time)
        raise AssertionError("집행하지 않는 클레임은 거절해야 한다")
    except Rejected as err:
        assert err.reason == "unenforced_claim"

    print("boundary-holds")


if __name__ == "__main__":
    demonstrate()
```

`verify`는 토큰을 저장하지 않는다. `issue`가 돌려준 문자열을 호출자가 들고 있다가 다시 넣는다. 수락은 서버가 그 발급을 기억해서가 아니라, 비밀로 원문 구간의 서명을 재계산했을 때 바이트가 같고 `now`가 `exp`보다 이전이기 때문이다. 로그아웃은 이 호출의 어느 인자도 바꾸지 못한다.

<!-- sec:body-3 -->

## 3. 짧은 만료가 줄이는 것은 남은 창이다

사본이 통과하는 구간은 로그아웃 시각이 아니라, 사본이 쓰이는 시각부터 그 토큰의 `exp`까지다. 쓰이는 시각이 `exp`보다 이전일 때만 창이 남아 있다. 스크립트에서 짧은 TTL은 15분, 발급 시각은 `1_700_000_000`, 사본이 쓰이는 시각은 그 60초 뒤다. `verify(short_token, secret, theft_time)`은 페이로드를 반환한다. 로그아웃 여부는 호출 인자에 없다.

창의 끝은 `issued_at + short_ttl`이다. 그 1초 전 호출은 수락이고, 그 시각의 호출은 `expired`다. 경계는 발급 함수가 아니라 검증의 `now >= exp`에 있다. `issue`는 `ttl_seconds <= 0`을 거절해 이미 만료된 토큰을 만들지 않지만, 그 검사는 발급 시점의 입력 검증일 뿐이다. 이미 나간 문자열의 수명은 `verify`의 시각 비교가 닫는다.

짧은 만료는 개별 폐기가 아니다. 서버가 특정 사본을 집어 무효화하는 문장은 이 파일에 없다. 하는 일은 창의 상한을 발급 시점에 잘라 두는 것이다. 15분을 고르면 발급 직후 복사된 토큰의 남은 수명은 15분을 넘지 못한다. 발급 14분 뒤에 복사되면 남은 수명은 1분 이하다. 어느 쪽이든 상한은 그 토큰의 `exp`다. 사용자 단위로 창을 즉시 0으로 만드는 연산은 없다.

그래서 짧은 만료를 「로그아웃이 잘 된다」로 설명하면 인과가 틀린다. 맞는 설명은 이것이다. 무효화 경계가 `exp`뿐이라면, 허용할 오용 구간의 상한은 액세스 토큰 TTL이어야 한다. TTL을 줄이면 그 상한이 줄어든다. 이미 검증을 통과한 요청을 되돌리지는 못한다. 경계 이전에 도착한 요청은 여전히 통과한다.

`now`를 검증기 안에 숨기지 않은 이유도 같다. 경계가 시각 비교라는 사실이 인자 목록에 있어야, 로그아웃이 그 비교를 대체하지 못한다는 점이 호출부에서 보인다. 시계를 서버 안에서 읽도록 바꿔도 인자가 사라질 뿐, 폐기 목록이 생기지는 않는다.

<!-- sec:body-4 -->

## 4. 긴 만료는 그 창을 다시 연다

같은 `verify`, 같은 비밀, 같은 `now`에서 결과가 갈리는 입력은 `exp`뿐이다. 스크립트는 7일 토큰을 `issued_at + short_ttl`에 넣는다. 짧은 토큰이 `expired`인 바로 그 시각이다. 반환된 `exp`는 `issued_at + long_ttl`이다. 검증 순서를 바꾸지 않았는데 수락이 돌아온다. 창을 다시 연 것은 폐기 기능이 아니라 설정값이다.

<!-- sec:code-3 -->
```python-snippet
verify(short_token, secret, issued_at + short_ttl)  # Rejected("expired")
verify(long_token, secret, issued_at + short_ttl)   # exp가 7일 뒤라 수락
```

액세스 토큰 TTL을 일 단위로 두는 관행은 「세션 저장소를 없앴으니 만료를 세션 수명처럼 길게 잡자」로 이어지기 쉽다. 그 순간 로그아웃과 탈취 대응은 전부 `exp`에 남는다. 사용자는 클라이언트에서 토큰을 지우고 로그아웃이 끝났다고 보지만, 검증기는 그 사건을 읽지 못한다. 7일로 둔 `exp`는 그 사건과 무관하게 그 시각까지 수락을 계속한다. 15분으로 줄였던 상한이 설정 한 줄로 돌아온다.

긴 만료가 서명 검사를 약하게 만들지는 않는다. `bad_signature` 분기는 그대로다. 길어지는 것은 서명이 맞은 토큰을 거절하지 못하는 구간이다. 무효화 경계가 `exp`라는 사실과, 그 값을 세션 수명과 같게 두는 결정은 별개다. 앞의 사실을 받아들이고도 뒤의 결정을 하면, 남은 창의 상한은 세션을 두었을 때의 수명과 비슷해진다. 차이는 그 수명 안에 서버가 행을 지울 수 있느냐다. 이 검증기에는 그 삭제가 없다.

`iat`를 길게 만료된 토큰의 대체 경계로 쓸 수도 없다. 같은 파일이 미래 `iat`를 수락한다는 단언이 그 이유다. 발급 시각은 페이로드에 적혀 있을 뿐, 서버가 비교하지 않으면 거절 조건이 아니다. 만료를 길게 두고 `iat`가 있으니 안전하다고 보면, 검사하지 않는 클레임을 무효화 경계로 오인하는 것이다. `jti`를 넣어 폐기할 수 있다고 보는 것도 같다. 이 검증기는 그 클레임을 `unenforced_claim`으로 거절한다. 목록 조회를 대신 수행하지 않는다.

<!-- sec:tradeoff -->

## 비용과 적용 기준

이 경계에 로그아웃을 맡기면 비용은 즉시성이 아니라 창의 길이로 나간다. TTL을 줄이면 액세스 토큰만으로는 긴 이용 구간을 커버하지 못한다. 재인증이 잦아지거나, 만료 전에 새 액세스 토큰을 받는 다른 자격 증명이 필요해진다. 그 자격 증명의 로테이션은 구현하지 않는다. 범위 밖으로 둔 이유는, 액세스 토큰 `verify`에 폐기 인자를 몰래 넣으면 지금 고정한 경계가 달라지기 때문이다. 로테이션이 있다 해도 이미 발급된 액세스 토큰의 사본은 그 토큰의 `exp`까지 이 검증기를 통과한다. 로테이션은 다음 발급을 바꿀 수 있어도, 서명만 보는 현재 토큰의 남은 창을 0으로 만들지 못한다.

시계 차이도 비용이다. 이 검증기는 여유 없이 `now >= exp`에서 거절한다. 노드 간 시각이 어긋나면 경계 근처의 정상 토큰이 `expired`가 된다. 여유를 더하면 사본이 통과하는 구간도 같은 초만큼 길어진다. 정상 요청의 거절과 사본의 창을 동시에 줄이는 매개변수는 이 함수 안에 없다.

즉시 무효화가 요구사항이면 이 메커니즘으로 로그아웃을 구현하지 않는다. 비밀번호 변경 직후 기존 액세스 토큰을 모두 거절해야 하거나, 특정 발급 건을 지금 끊어야 하거나, 로그아웃 요청의 성공이 다음 요청의 거절과 같아야 한다면, 서명과 `exp`만으로는 그 문장을 만족할 수 없다. 만족시키려면 검증이 읽는 서버 상태가 필요하고, 그때 경로는 스테이트리스가 아니다. 폐기 목록은 여기 구현하지 않는다. 목록을 넣는 순간 거절 조건이 `exp` 하나가 아니게 되고, 이 글의 경계는 다른 설계가 된다.

써도 되는 조건은 따로 있다. 허용 오용 구간의 상한이 액세스 토큰 TTL과 같아도 되고, 그 상한 안의 요청은 발급이 유효한 것으로 보는 경우다. 그때 짧은 `exp`는 폐기의 대체가 아니라 상한의 선언이다. 그 선언을 제품이 받아들이지 못하면 TTL을 늘리는 대신 검증이 읽는 상태를 도입해야 한다.

저장 위치는 이 경계와 독립이다. 문자열이 쿠키에 있든 다른 저장소에 있든 검증 입력은 그 문자열이고, 서버는 이미 나간 사본을 모른다. 알고리즘을 바꿔도 개별 폐기가 생기지 않는다. HS256 고정은 헤더의 `alg`를 신뢰하지 않기 위한 제약이지, 다른 서명이 행 삭제를 대신한다는 뜻이 아니다.

가정은 다음으로 고정한다. 검증기는 발급 때 쓴 비밀을 갖고 있다. 토큰마다의 서버 행은 없다. `now`는 호출자가 준다. 이 발급기의 클레임은 `sub`, `iat`, `exp`뿐이고 `exp`는 정수다. `iat`는 기록일 뿐 경계가 아니다. 시계 여유는 0이다. 이 가정 밖에서 폐기 목록, 세션 행, 사용자별 최소 `iat`를 읽기 시작하면, 만료 시각만이 무효화 경계라는 문장은 더 이상 참이 아니다.

<!-- sec:conclusion -->

서명만 검사하는 스테이트리스 액세스 토큰에서 로그아웃은 만료가 아니다. 세션 저장소는 행 삭제가 다음 조회의 거절이 되지만, 대응 행이 없는 `verify`는 클라이언트가 문자열을 버렸는지 읽지 못한다. 조각과 서명이 맞고 허용 클레임만 있으면 남은 거절 조건은 `now >= exp`뿐이다. `now == exp`도 만료이고, `exp`가 정수가 아니면 수락하지 않으며, 서명이 틀리면 페이로드의 만료 시각과 무관하게 거절한다. `iat`와 집행하지 않는 클레임은 그 경계를 대신하지 않는다.

짧은 TTL은 그 비교의 상한을 줄여 사본의 남은 창을 줄인다. 만료를 다시 길게 잡으면 같은 함수가 같은 시각에 수락을 돌려 창을 연다. 즉시 끊어야 하는 요구가 있으면 이 경계에 로그아웃을 올리지 않는다. 그 요구는 검증이 읽는 상태가 있어야 만족되고, 그 상태를 읽는 순간 액세스 토큰 검증은 더 이상 서명과 만료만의 문제가 아니다.
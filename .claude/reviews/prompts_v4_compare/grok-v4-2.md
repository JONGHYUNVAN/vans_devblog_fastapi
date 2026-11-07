# 만료 전에는 로그아웃이 아니다: 스테이트리스 액세스 토큰의 무효화 경계

> 서명만 보는 스테이트리스 액세스 토큰은 만료 전 개별 폐기가 불가능하다. 로그아웃 후의 수락과 탈취 창은 exp가 정한다.

<!-- sec:intro -->
## 로그아웃 뒤에도 같은 토큰이 수락된다

로그아웃 함수가 성공을 반환한 뒤에도 같은 액세스 토큰이 다음 요청에서 수락되는 실패가 있다. 검증기는 그 요청에서 토큰을 저장소에서 찾지 않는다. 찾을 키가 발급 때 만들어지지 않기 때문이다. 검증 입력은 제시된 토큰, 서버의 서명 키, 검증 시각 `now` 세 개뿐이다. 서명이 키와 맞으면 페이로드는 발급 때 서버가 서명한 값이다. 그 페이로드의 `exp`가 `now`보다 크면 검증은 수락을 반환한다. 로그아웃 함수의 반환값은 세 입력의 어디에도 기록되지 않는다.

이 실패는 삭제 호출을 빼먹은 저장소 버그가 아니다. 서명만 검사하는 스테이트리스 액세스 토큰은 만료 시각 이전에 서버가 그 한 장만 거절하도록 입력을 바꿀 방법이 없다. **무효화 경계**는 검증 결과가 수락에서 거절로 바뀌는 입력 조건이다. 여기서 그 조건은 `now`가 `exp` 이상이 되는 지점뿐이다.

다루는 범위는 검증 순서가 시간 거절을 그 조건 하나로 남기는 방식, 발급 시 `exp`를 당기면 탈취 후 남은 시간이 줄어드는 방식, 같은 인자를 뒤로 밀면 그 시간이 다시 늘어나는 지점이다.

<!-- sec:body-1 -->
## 스테이트리스는 서버에서 지웠다는 뜻이 아니다

**스테이트리스**는 검증 함수가 이전에 발급한 토큰의 행을 조회하지 않고, 이번 요청의 토큰과 서버 키와 `now`만으로 수락을 계산한다는 뜻이다. 서버가 로그아웃 처리 중에 그 토큰을 메모리에서 지웠다는 뜻이 아니다. `issue`는 서명된 문자열만 반환한다. 그 문자열을 서버 쪽 딕셔너리에 넣지 않는다. 발급이 끝난 프로세스에는 토큰별로 삭제할 키가 없다.

**개별 폐기**는 이미 발급된 토큰 문자열 하나를 지정해, 그 문자열의 이후 검증만 거절로 바꾸는 일이다. 시각이 지나 만료된 토큰이 함께 거절되는 것은 개별 폐기가 아니다. 서명 키를 바꿔 미만료 토큰이 함께 `bad_signature`가 되는 것도 아니다. 이 모듈에는 문자열 하나를 인자로 받는 폐기 함수가 없다. `logout`은 인자가 없고 본문은 `return None`이다.

세션 저장소를 쓰는 로그아웃은 세션 키의 행을 지우거나 그 행의 만료를 앞당긴다. 다음 요청은 같은 키로 조회하고, 행이 없으면 거절한다. 거절의 원인은 조회 결과의 변화다. 스테이트리스 검증에는 그 조회가 없다. 따라서 행 삭제와 같은 효과를 내는 연산도 없다.

로그아웃 핸들러가 성공을 반환해도 `verify`의 매개변수는 그대로다. 반환 코드는 다음 요청의 HMAC 입력에 들어가지 않는다. 핸들러가 예외를 던지지 않았다는 사실과, 토큰이 아직 수락된다는 사실은 모순이 아니다. 두 함수가 상태를 공유하지 않기 때문이다.

같은 바이트열을 가진 호출자는 검증 결과도 같다. 브라우저가 버린 복사본과 다른 클라이언트가 아직 가진 복사본은 문자열이 같다. 서버는 어느 쪽이 사용자의 정본인지 적을 곳이 없다. 탈취로 만들어진 복사본도 같은 입력이다. 로그아웃이 사용자 복사본만 버린다면, 남은 복사본의 `evaluate` 결과는 바뀌지 않는다.

페이로드에 `jti`를 넣어도 거절 분기는 늘지 않는다. `jti`는 토큰 안의 식별자일 뿐, `verify`가 조회하는 키가 아니다. 이 모듈의 `verify`는 `jti` 키를 읽지 않는다. 값이 `t-short`이든 다른 문자열이든 서명과 `exp`가 같으면 이유가 같다.

프로세스가 여러 개여도 입력은 늘지 않는다. 공유 폐기 집합이 없으면, 로그아웃을 받은 인스턴스와 다음 요청을 받은 인스턴스가 서로 다른 프로세스라도 `verify`에 보이는 값은 토큰과 키와 `now`뿐이다. 인스턴스 로컬 집합을 이 모듈은 만들지 않는다. 그래서 어느 인스턴스도 로그아웃 사실만으로 거절하지 않는다.

`prove`는 이 부재를 실행으로 고정한다. `issue`로 만든 짧은 토큰을 `now`에 `evaluate`하면 `accepted`다. 이어서 `logout`을 호출하고 같은 토큰, 같은 키, 같은 `now`로 다시 `evaluate`한다. 결과도 `accepted`다. 호출 전후에 검증 입력이 같으므로 출력이 같을 수밖에 없다.

이 두 번의 `accepted`가 로그아웃의 의미다. 무효화는 `evaluate`가 `accepted`가 아닌 이유를 돌려줄 때 일어난다. `logout` 직후의 이유가 `accepted`이면 그 호출은 무효화가 아니다. 만료 시각 전의 로그아웃은 응답으로는 성공이어도, 토큰 수락을 기준으로 하면 아직 로그아웃이 아니다.

<!-- sec:body-2 -->
## 서명 뒤에 남는 거절은 exp다

모듈의 발급 경로는 `issue` 하나다. `issue`는 헤더를 `alg`가 `HS256`이고 `typ`이 `JWT`인 객체로 고정한 뒤 `_sign`을 호출한다. `_sign`은 헤더와 페이로드를 구분자 없는 JSON으로 만들고 `_b64url`로 바꾼다. `_b64url`은 base64url이며 패딩 `=`를 붙이지 않는다. 두 조각을 `.`로 이은 문자열이 서명 입력이다. 그 입력에 서버 키로 HMAC-SHA256을 친 결과가 세 번째 조각이다. 디코드는 `_b64url_decode`가 길이를 4의 배수로 맞춘 뒤 수행한다. `_sign`은 헤더의 `alg`를 읽어 알고리즘을 고르지 않는다. 항상 SHA-256 HMAC이다.

`verify`는 저장소를 받지 않는다. 인자는 `token`, `secret`, `now`뿐이다. **주입 시각** `now`는 호출자가 넣는 초 단위 정수다. 스크립트는 벽시계 대신 `1700000000`을 넣어 경계 단언이 기계 시각에 흔들리지 않게 한다. 운영에서 호출자가 시스템 시계를 넘기더라도 비교식은 같다.

거절 분기는 코드에 적힌 순서대로만 탄다.

1. `now`가 불리언이거나 정수가 아니면 `bad_now`다. 토큰을 자르기 전에 거절한다.
2. `.`로 나눈 조각이 세 개가 아니거나 빈 조각이 있으면 `malformed`다.
3. 서명 조각의 디코드가 실패하면 `malformed`다. 길이가 HMAC과 다르거나 `compare_digest`가 거짓이면 `bad_signature`다.
4. 헤더나 페이로드 JSON이 깨지면 `malformed`다. 페이로드가 객체가 아니어도 `malformed`다.
5. 헤더가 객체가 아니거나 `alg`가 `HS256`이 아니면 `unexpected_alg`다.
6. `exp`가 없거나 정수가 아니거나 불리언이면 `missing_exp`다.
7. `now >= exp`이면 `expired`다. 그 외에는 페이로드를 반환한다.

`prove`는 `abc`와 `1.5`를 넣어 1번이 `bad_now`인지를 확인한다. 시계 값이 정수가 아니면 만료 비교를 하지 않는다. `abc`, `..`, 토큰 뒤에 `.x`를 붙인 문자열은 정수 `now`에서 2번의 `malformed`다. 빈 조각은 서명 입력이 중간에 끊긴 것이므로 서명 비교로 넘어가지 않는다.

`compare_digest`는 두 바이트열이 어디서부터 다른지 조기 반환으로 알리지 않으려고 쓴다. 거절 이유는 여전히 `bad_signature` 하나다. `_flip_sig`는 서명 조각의 첫 문자를 `A` 또는 `B`로 바꾼다. `exp`가 미래여도 결과는 `bad_signature`다.

서명이 맞은 다음에야 JSON을 읽는다. 서명이 깨진 페이로드의 `exp`를 시계와 비교하면 안 되기 때문이다. 서명 일치는 페이로드가 발급 때 서버가 넣은 값이라는 뜻이다. 서버가 그 토큰을 아직 유효한 목록에 두고 있다는 뜻은 아니다.

5번의 `alg` 검사는 서명 알고리즘을 선택하지 않는다. 서명은 이미 서버 키의 HMAC으로 끝났다. `prove`는 헤더만 `RS256`으로 두고 같은 `_sign`으로 HMAC을 친 토큰을 넣는다. 서명은 맞고 `alg`가 달라 `unexpected_alg`가 된다. 헤더를 `none`으로 바꾸고 서명은 원래 HS256 토큰의 것을 붙이면 서명 입력이 달라져 `bad_signature`다. `exp`가 아직 미래인지는 두 결과 모두에 영향을 주지 않는다. 알고리즘을 다른 것으로 교체하는 발급 경로는 없다.

불리언을 6번에서 따로 거르는 이유는 `True`가 정수의 하위 형이라 `isinstance`만으로는 걸리지 않기 때문이다. `exp`가 없는 토큰은 서명이 맞아도 시간 경계가 없다. 그 상태를 수락으로 두지 않으려고 `missing_exp`로 거절한다. `prove`는 `exp`를 뺀 발급과 `exp`에 `True`를 넣은 발급이 둘 다 `missing_exp`인지를 확인한다.

7번의 비교가 `now > exp`가 아니라 `now >= exp`인 이유는 `exp`의 포함 방향 때문이다. RFC 7519에서 `exp`는 그 시각과 그 이후에는 처리에 수락하면 안 되는 시각이다. **NumericDate**는 1970-01-01 UTC부터의 초를 나타내는 정수다. `now == exp`인 초에도 `expired`다. `now == exp - 1`이면 아직 수락이다. 포함 방향이 경계의 한 초를 정한다.

`iat`, `nbf`, `aud`, `iss`, `sub`, `jti`는 거절 조건이 아니다. `verify`는 이 클레임을 읽지 않고 페이로드 전체를 반환한다. `sub`가 없는 토큰도 서명과 `exp`만 맞으면 `accepted`다. `sub`로 사용자를 인가하는 일은 `verify` 다음 단계다. `nbf`를 발급 때 미래로 넣는 것은 이 함수가 보지 않으며, 보더라도 이미 유통 중인 토큰을 로그아웃으로 거절하는 상태가 아니다. 이 파일의 목적은 그 다음 단계가 아니라 무효화 경계의 단언이다.

`evaluate`는 `verify`가 예외 없이 반환하면 `accepted`를, `TokenRejected`이면 `reason`을 돌려준다. `prove`가 비교하는 것은 이 문자열이다. 통과하면 프로세스가 끝나고 출력은 없다. 단언이 어긋나면 `AssertionError`다. 이 실행이 재는 지연이나 처리량은 없다. 반환 이유가 분기와 같은지만 확인한다.

<!-- sec:code-1 -->
```python
"""HS256 서명과 exp만으로 수락을 결정하는 액세스 토큰 검증.

logout은 검증 입력을 바꾸지 않는다. 폐기 목록은 없다.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json


class TokenRejected(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _b64url_decode(segment: str) -> bytes:
    padding = '=' * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def _sign(header: dict, payload: dict, secret: bytes) -> str:
    signing_input = '.'.join([
        _b64url(json.dumps(header, separators=(',', ':')).encode('utf-8')),
        _b64url(json.dumps(payload, separators=(',', ':')).encode('utf-8')),
    ])
    digest = hmac.new(
        secret, signing_input.encode('ascii'), hashlib.sha256
    ).digest()
    return signing_input + '.' + _b64url(digest)


def issue(payload: dict, secret: bytes) -> str:
    return _sign({'alg': 'HS256', 'typ': 'JWT'}, payload, secret)


def logout() -> None:
    return None


def verify(token: str, secret: bytes, now: int) -> dict:
    if isinstance(now, bool) or not isinstance(now, int):
        raise TokenRejected('bad_now')
    parts = token.split('.')
    if len(parts) != 3 or not all(parts):
        raise TokenRejected('malformed')
    signing_input = parts[0] + '.' + parts[1]
    try:
        actual = _b64url_decode(parts[2])
    except Exception as exc:
        raise TokenRejected('malformed') from exc
    expected = hmac.new(
        secret, signing_input.encode('ascii'), hashlib.sha256
    ).digest()
    if len(actual) != len(expected) or not hmac.compare_digest(expected, actual):
        raise TokenRejected('bad_signature')
    try:
        header = json.loads(_b64url_decode(parts[0]))
        payload = json.loads(_b64url_decode(parts[1]))
    except Exception as exc:
        raise TokenRejected('malformed') from exc
    if not isinstance(header, dict) or header.get('alg') != 'HS256':
        raise TokenRejected('unexpected_alg')
    if not isinstance(payload, dict):
        raise TokenRejected('malformed')
    exp = payload.get('exp')
    if isinstance(exp, bool) or not isinstance(exp, int):
        raise TokenRejected('missing_exp')
    if now >= exp:
        raise TokenRejected('expired')
    return payload


def evaluate(token: str, secret: bytes, now: int) -> str:
    try:
        verify(token, secret, now)
    except TokenRejected as exc:
        return exc.reason
    return 'accepted'


def _flip_sig(token: str) -> str:
    header_b64, payload_b64, sig_b64 = token.split('.')
    first = 'A' if sig_b64[0] != 'A' else 'B'
    return header_b64 + '.' + payload_b64 + '.' + first + sig_b64[1:]


def prove() -> None:
    secret = b'test-secret'
    now = 1_700_000_000
    short_exp = now + 60
    short_token = issue(
        {'sub': 'user-1', 'jti': 't-short', 'exp': short_exp}, secret
    )

    assert evaluate(short_token, secret, now) == 'accepted'
    logout()
    assert evaluate(short_token, secret, now) == 'accepted'
    assert evaluate(short_token, secret, short_exp - 1) == 'accepted'
    assert evaluate(short_token, secret, short_exp) == 'expired'

    reissued_at = now + 30
    reissued = issue(
        {'sub': 'user-1', 'jti': 't-re', 'exp': reissued_at + 60}, secret
    )
    assert evaluate(short_token, secret, reissued_at) == 'accepted'
    assert evaluate(reissued, secret, reissued_at) == 'accepted'
    assert evaluate(short_token, secret, short_exp) == 'expired'
    assert evaluate(reissued, secret, short_exp) == 'accepted'

    long_exp = now + 86_400
    long_token = issue(
        {'sub': 'user-1', 'jti': 't-long', 'exp': long_exp}, secret
    )
    logout()
    assert evaluate(long_token, secret, now + 3_600) == 'accepted'
    assert long_exp - (now + 3_600) == 82_800
    assert evaluate(long_token, secret, long_exp) == 'expired'

    assert evaluate(_flip_sig(short_token), secret, now) == 'bad_signature'
    assert evaluate(issue({'sub': 'user-1'}, secret), secret, now) == 'missing_exp'
    assert evaluate(
        issue({'sub': 'user-1', 'exp': True}, secret), secret, now
    ) == 'missing_exp'
    assert evaluate('abc', secret, 1.5) == 'bad_now'

    rs = _sign(
        {'alg': 'RS256', 'typ': 'JWT'},
        {'sub': 'user-1', 'exp': short_exp},
        secret,
    )
    assert evaluate(rs, secret, now) == 'unexpected_alg'

    none_header = _b64url(
        json.dumps(
            {'alg': 'none', 'typ': 'JWT'}, separators=(',', ':')
        ).encode('utf-8')
    )
    _, payload_b64, sig_b64 = short_token.split('.')
    forged = none_header + '.' + payload_b64 + '.' + sig_b64
    assert evaluate(forged, secret, now) == 'bad_signature'
    assert evaluate('abc', secret, now) == 'malformed'
    assert evaluate('..', secret, now) == 'malformed'
    assert evaluate(short_token + '.x', secret, now) == 'malformed'


if __name__ == '__main__':
    prove()
```

<!-- sec:body-3 -->
## 짧은 exp가 줄이는 것은 잔여 창이다

**잔여 창**은 서명이 맞는 토큰에 대해 `evaluate`가 `accepted`를 반환하는 동안의 길이다. `now`가 `exp`보다 작을 때 그 길이는 `exp - now`초다. `now`가 `exp` 이상이면 길이는 0이고 이유는 `expired`다. `logout`은 `exp`도 `now`도 바꾸지 못한다. `exp`는 이미 서명된 페이로드 안에 있고, `now`는 시계가 지나야 증가한다.

탈취자가 문자열을 가진 시점의 창은 그 시점의 `exp - now`다. 창을 줄이는 연산은 `verify`에 폐기 분기를 추가하는 일이 아니다. 발급 때 `exp`를 `now`에 가깝게 넣는 일이다. `prove`의 짧은 토큰은 `exp`가 `now + 60`이다. `now`와 `short_exp - 1`에서는 `accepted`이고, `short_exp`에서는 `expired`다. 그 사이에 `logout`을 호출해도 앞의 두 시각의 이유는 바뀌지 않는다.

`short_exp - 1`과 `short_exp`의 차이는 1초다. 그 1초에 이유가 `accepted`에서 `expired`로 바뀐다. 로그아웃을 그 1초 전에 호출해도 바뀌는 입력은 없다. 경계를 움직이는 입력은 `now`의 증가뿐이다.

60은 권장 수명이 아니다. 창 공식을 고정하려고 고른 발급 인자다. 창의 길이는 `exp - now`에 선형으로 붙는다. 발급 시 `exp`를 30초 줄이면 창도 30초 준다. `verify`의 분기는 그대로다.

짧은 만료는 로그아웃을 즉시 거절로 만들지 않는다. 탈취 직후의 창은 0이 아니다. 남은 초 동안 서명이 맞는 제시는 수락된다. 짧다는 말은 그 초의 상한이 작다는 뜻이다. 상한을 0으로 만들려면 `exp`가 발급 시각과 같아야 하고, 그 토큰은 발급 시각의 `evaluate`에서도 `expired`다. 쓸 수 있는 액세스 토큰의 창은 0보다 크다.

창의 상한은 발급자가 서명한 `exp`에 고정된다. 제시자가 페이로드의 숫자를 고치면 서명 검사가 `bad_signature`로 끝난다. 탈취자는 남은 초를 늘리지 못한다. 줄이는 쪽도 탈취자에게는 없다. 줄이려면 서버가 `now`를 기다리거나, 발급 때 더 작은 `exp`를 넣어야 한다.

재발급은 이전 창을 닫지 않는다. `prove`는 `now + 30`에 `exp`가 `reissued_at + 60`인 두 번째 토큰을 만든다. 그 시각에 첫 토큰과 두 번째 토큰이 둘 다 `accepted`다. 첫 토큰은 자신의 `exp`인 `now + 60`에 `expired`가 된다. 그 시각에 두 번째 토큰은 아직 `accepted`다. 새 문자열을 만들어도 이전 문자열의 서명은 취소되지 않는다. 이전 창은 이전 `exp`까지 열려 있다.

이후 토큰을 어떤 절차로 발급하든 이전 창의 끝은 이전 `exp`다. 새 발급이 이전 HMAC을 서버에서 지우지는 않는다.

<!-- sec:body-4 -->
## 긴 exp는 같은 창을 뒤로 민다

세션 행을 두지 않은 채 액세스 토큰의 수명을 세션 수명처럼 두면, `exp`가 세션 삭제 대신 맡는 시각이 된다. 그 시각을 발급 시점부터 86400초 뒤로 두면 무효화 경계도 86400초 뒤로 간다. 검증 코드는 60초 발급과 같다. 다른 것은 `issue`에 넣은 `exp`뿐이다.

`prove`는 `long_exp = now + 86400`으로 발급하고 `logout`을 호출한 다음 `now + 3600`에 `evaluate`한다. 이유는 `accepted`다. 그때 남은 창은 `86400 - 3600 = 82800`초다. 이 수는 실행 시간을 재서 얻은 값이 아니다. 클레임과 입력 시각의 차다. `now`가 `long_exp`에 도달해야 `expired`가 된다. 3600초가 지난 로그아웃 이후에도 거절 분기에 들어가지 않는다.

`now`가 `1700000000`이면 `long_exp`는 `1700086400`이다. 로그아웃 직후인 `1700000000`에도, 한 시간 뒤인 `1700003600`에도 `now >= exp`는 거짓이다. 거절이 참이 되는 첫 정수는 `1700086400`이다. 그 전까지의 모든 정수는 서명이 맞는 한 `accepted`다.

60초 발급과 86400초 발급의 차이는 로그아웃이 거절을 만들지 못하는 구간의 길이다. 구간은 60초에서 86400초로 늘어난다. 긴 만료는 다른 무효화 메커니즘이 아니다. 같은 경계의 위치를 뒤로 옮긴 것이다. `logout` 호출이 그 위치를 앞당기지는 못한다.

창이 다시 열리는 지점은 저장소를 두지 않은 구조 자체가 아니다. 그 구조에서 경계를 맡긴 `exp`를 크게 넣는 발급 인자다. `verify`를 그대로 두고 `exp`만 줄이면 구간은 줄어든다. `verify`를 그대로 두고 `exp`를 늘리면 구간은 늘어난다. 어느 쪽도 `logout`이 경계 시각을 수정하지는 못한다.

재발급 횟수를 줄이려고 `exp`를 키우면 발급 HMAC은 줄어든다. 무효화가 불가능한 구간도 같은 만큼 길어진다. 두 결과는 `exp - now`에서 분리되지 않는다.

7일 상수도 같은 식이다. `exp`를 `now + 604800`으로 두면 로그아웃 직후의 창은 604800초다. `prove`는 그 상수를 따로 실행하지 않는다. `exp - now`가 이미 그 발급 값을 포함한다. 상수를 7일로 고르는 일은 검증 순서의 변화가 아니라 경계 시각의 선택이다.

<!-- sec:tradeoff -->
## 순수 검증이 떠안는 비용

이 경계의 비용은 요청마다 서명이 추가되는 것이 아니다. `verify`는 `exp`가 60초 뒤든 86400초 뒤든 HMAC을 한 번 계산한다. 만료를 짧게 잡아도 요청당 서명 비용은 늘지 않는다. 스테이트리스 검증의 요청 비용은 만료 길이와 독립이다.

늘어나는 비용은 발급 횟수다. 잔여 창의 상한을 T초로 두면, 연속 호출을 유지하는 동안 T초마다 새 액세스 토큰이 필요하다. 발급마다 HMAC 한 번이 든다. T를 줄이면 발급 횟수가 늘고 창은 줄어든다. 둘은 같은 인자에서 반대로 움직인다.

겹침도 비용이다. 재발급이 이전 창을 닫지 않으므로, 한 사용자에 대해 수락되는 문자열이 한 장으로 유지되지 않는다. T초 안에 발급한 횟수만큼 창이 겹친다. 로그아웃은 그 겹침을 한 장씩 지우지 못한다.

시계 차이도 비용이다. 이 모듈은 만료 비교에 여유 초를 더하지 않는다. **여유**는 `exp` 비교에 더하는 허용 초다. 검증기 시계가 발급기보다 빠르면, 발급기 시계로는 아직 `exp` 전인 토큰이 `expired`가 된다. 검증기 시계가 느리면 창이 그 차이만큼 길어진다. 여유를 넣으면 거절 시각이 `exp`보다 뒤로 밀려 창이 늘어난다. 어느 쪽이든 `logout`이 그 시각을 당기지는 못한다. 모듈은 여유를 0으로 고정한다.

서버 상태만으로 폐기 여부를 답하는 질의도 없다. 특정 `jti`가 로그아웃되었는지를 저장소에서 읽을 키가 없다. 답하려면 토큰 문자열이 필요하고, 그 답은 서명과 `exp`로 결정된다. 감사 로그에 로그아웃 시각을 적어도 `verify`는 그 로그를 읽지 않는다.

가정은 다음을 구현하지 않은 채 위 비용이 성립한다는 것이다. 토큰 문자열을 쿠키에 두는지 웹 스토리지에 두는지는 `verify`의 입력이 아니다. 같은 문자열이 제시되면 저장 위치와 무관하게 이유가 같다. 리프레시 토큰 로테이션은 모듈에 없다. 로테이션을 넣어도 이전 액세스 토큰의 서명은 지워지지 않으며, 이전 토큰의 경계는 그 토큰의 `exp`다. 서명 알고리즘 교체도 없다. 검증은 서버 키의 HS256만 수행하고, 헤더 `alg`가 그와 다르면 `unexpected_alg`로 거절한다.

검증 키가 토큰 수명 동안 그대로라는 가정이 깨지면 미만료 토큰이 함께 `bad_signature`가 된다. 그 경로는 개별 폐기가 아니다. 키 교체 절차는 모듈에 없다. 한 사용자의 토큰만 끊는 요구를 키 교체로 대체하지 못한다.

T초 창과 T초마다의 재발급을 요구사항이 비용으로 받아들이면, 무효화 경계를 `exp`에 두는 선택이 그 비용과 맞는다. 받아들이지 못하는 요구는 `exp`를 늘려 발급 횟수를 줄이는 쪽으로 해결되지 않는다. `exp`를 늘리면 창이 다시 열리기 때문이다.

<!-- sec:conclusion -->
## 경계만으로 충분한 조건

제품이 허용하는 잔여 창의 상한이 발급 `exp`로 고정되어 있고, 그 상한 동안의 제시를 수락으로 두는 요구이면 이 검증으로 충분하다. 기준은 로그아웃 버튼의 유무가 아니다. 버튼이 `logout`만 호출하면 경계 시각은 앞당겨지지 않는다. 기준은 요구가 `exp` 이전의 개별 거절을 포함하는지다.

요구가 로그아웃 또는 탈취 인지 직후, `exp` 전에 그 토큰만 거절하는 것이면 이 모듈은 그 요구를 충족하지 못한다. 충족하려면 `verify`가 토큰과 키와 `now` 밖의 상태를 읽어야 한다. 그때의 검증은 이 모듈의 함수가 아니다. 상태를 둘 저장소는 여기의 수락 조건 밖에 있다.

`exp`는 시각이지 토큰 한 장의 폐기 플래그가 아니다. 요구가 플래그를 필요로 하면 그 요구는 `exp` 인자로 표현되지 않는다. 입력 집합이 토큰과 키와 `now`를 넘어야 한다.
# 만료 전에는 로그아웃이 아니다: 무상태 액세스 토큰의 무효화 경계

> 서명만 검사하는 무상태 액세스 토큰은 만료 전에 개별 무효화되지 않는다. 만료 시각만 거절 경계로 남고, 짧은 만료는 탈취 후 남은 창만 줄인다.

<!-- sec:intro -->
## 로그아웃 뒤의 사본이 만료 전까지 통과한다

서명만 검사하는 서버는 클라이언트가 액세스 토큰(access token)을 버리고 로그아웃을 마쳐도, 그 문자열의 사본을 만료 시각(exp) 전에 거절하지 못한다. 로그아웃을 처리한 시각은 다음 요청의 수락 여부에 들어가지 않는다.

**무상태**(stateless) 검증의 입력은 토큰 바이트와 서명 키와 현재 시각뿐이다. 이 입력만으로 반환값이 결정되면, 서버가 요청 사이에 지운 데이터는 반환값을 바꾸지 못한다.

**무효화 경계**는 페이로드에 서명과 함께 박힌 만료 시각이다. 그 시각 이전의 요청은 사본을 누가 보내든 수락된다.

프록시 로그나 다른 단말에 같은 문자열이 남아 있으면, 사용자가 자기 화면의 참조를 끊은 뒤에 도착한 요청도 서명이 맞고 만료 전이면 통과한다. 검증기가 계산하는 값은 소지자가 바뀌어도 같다.

다루는 대상은 이 입력만으로 수락 여부를 결정하는 액세스 토큰 검증기다. 만료 시각은 그 함수가 시간이 흐르며 반환값을 거절로 바꾸는 유일한 클레임(claim)이다.

<!-- sec:body-1 -->
## 무상태는 저장소에서 지웠다는 뜻이 아니다

무상태는 검증이 서버에 둔 토큰 행을 조회하지 않는다는 성질이다. 발급 직후 서버 메모리에서 토큰을 제거했다는 뜻이 아니다. 제거는 이후의 조회가 그 행을 읽을 때만 반환값을 바꾼다. 조회가 없는 경로에서 제거는 아무 인자도 바꾸지 않는다.

지운다는 말은 세 가지 다른 연산을 가리킨다. 클라이언트가 자기 참조를 버리는 일, 서버가 세션 행을 삭제하는 일, 서명 검증의 반환값을 거절로 바꾸는 일이다.

앞의 둘은 어느 저장소의 내용이 바뀌었는지를 말한다. 세 번째는 `verify`가 예외를 내는지를 말한다. 서명만 검사하는 경로에는 만료 전에 세 번째를 일으키는 입력이 없다. 앞의 둘을 반환값의 거절로 보면, 로그아웃 응답의 상태 코드와 다음 요청의 수락이 어긋난다.

로그아웃 핸들러가 사용자 식별자로 삭제 쿼리를 실행해도 효과의 전달 경로는 같다. 삭제는 그 쿼리가 가리키는 저장소를 읽는 코드의 다음 조회에만 보인다. 서명 비교로 바로 들어가는 함수는 삭제 쿼리의 반환값을 매개변수로 받지 않는다. 핸들러가 돌려준 HTTP 200은 클라이언트에 대한 응답이다. 이미 서명된 바이트의 수락 여부를 전이시키지 않는다.

클라이언트 쪽 삭제도 검증 입력이 아니다. 검증 입력은 이번 요청에 실린 문자열이다. 로그아웃 전에 그 문자열이 복사되어 있으면 복사본의 서명은 원본과 같다. 서명 일치는 바이트가 같다는 결과다. 요청을 보낸 쪽이 발급 당시의 클라이언트라는 증명은 아니다.

서명 검증이 구별하는 것은 채널이 아니라 바이트다. 발급 문자열과 같은 세 조각을 가진 값은 어느 프로세스에 있든 같은 입력이다. 이 함수는 연결 식별자나 단말 식별자를 클레임과 비교하지 않는다. `sub`는 사용자 식별자이지, 이번 요청의 소지자를 묶는 값이 아니다.

이미 발급된 만료 시각은 서버가 고쳐 쓰지 못한다. 페이로드의 exp를 앞당긴 새 문자열은 서명이 깨져 만료 이전이어도 거절된다. 고쳐 쓰기 전의 문자열은 옛 서명과 옛 exp를 그대로 갖고 있으므로, 그 문자열을 가진 요청은 옛 경계까지 수락된다. 서명 검증 안에는 이미 발급된 경계를 앞당기는 연산이 없다.

토큰 식별자(jti)를 지정한 개별 무효화는, 검증 함수가 폐기 집합을 읽지 않으면 일어나지 않는다. 페이로드에 jti가 있고 서버의 집합에도 같은 문자열이 있어도, 함수가 그 집합을 읽지 않으면 두 값의 일치는 계산되지 않는다. 새 발급을 거절하는 분기도 이미 서명된 문자열의 창을 닫지 못한다. 그 분기는 이후의 `issue` 호출만 막고, `verify`는 그 분기를 읽지 않는다.

같은 `iat`로 `issue`를 두 번 호출해도 첫 번째 문자열의 서명은 그대로다. 두 번째 발급은 첫 번째를 대체하지 않는다. 사용자 단위로 묶어 무효화하려면 검증이 현재 유효한 발급분을 가리키는 저장 값을 읽고 이전 값과 비교해야 한다. 그 읽기가 없으면 사용자 단위의 무효화도 만료 시각까지 미뤄진다.

아래 파일이 이 경로의 실행이다. `verify`의 매개변수는 토큰, 키, 시각, 여유 시간뿐이다. `must_reject`는 지정한 이유로 예외가 나지 않으면 `SystemExit`로 끝낸다. 단언을 모두 통과하면 `ok`를 출력한다.

<!-- sec:code-1 -->
```python
import base64
import hashlib
import hmac
import json
from typing import Any

class Reject(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')

def b64url_decode(text: str) -> bytes:
    pad = '=' * ((4 - len(text) % 4) % 4)
    return base64.urlsafe_b64decode(text + pad)

def load_json(segment: str) -> dict[str, Any]:
    try:
        value = json.loads(b64url_decode(segment))
    except (ValueError, UnicodeDecodeError) as exc:
        raise Reject('malformed') from exc
    if not isinstance(value, dict):
        raise Reject('malformed')
    return value

def issue(payload: dict[str, Any], secret: bytes) -> str:
    header = {'alg': 'HS256', 'typ': 'JWT'}
    signing_input = '.'.join([
        b64url(json.dumps(header, separators=(',', ':')).encode()),
        b64url(json.dumps(payload, separators=(',', ':')).encode()),
    ])
    digest = hmac.new(secret, signing_input.encode('ascii'), hashlib.sha256).digest()
    return signing_input + '.' + b64url(digest)

def verify(token: str, secret: bytes, now: int, leeway: int = 0) -> dict[str, Any]:
    if leeway < 0:
        raise Reject('malformed')
    parts = token.split('.')
    if len(parts) != 3:
        raise Reject('malformed')
    header = load_json(parts[0])
    if header.get('alg') != 'HS256':
        raise Reject('alg')
    signing_input = parts[0] + '.' + parts[1]
    expected = hmac.new(secret, signing_input.encode('ascii'), hashlib.sha256).digest()
    try:
        actual = b64url_decode(parts[2])
    except ValueError as exc:
        raise Reject('malformed') from exc
    if len(actual) != len(expected) or not hmac.compare_digest(expected, actual):
        raise Reject('signature')
    payload = load_json(parts[1])
    exp = payload.get('exp')
    if type(exp) is not int:
        raise Reject('exp_missing')
    if now >= exp + leeway:
        raise Reject('exp')
    return payload

def must_reject(token: str, secret: bytes, now: int, reason: str, leeway: int = 0) -> None:
    try:
        verify(token, secret, now, leeway)
    except Reject as err:
        assert err.reason == reason
        return
    raise SystemExit(reason)

def main() -> None:
    secret = b'server-secret'
    issued_at = 1_700_000_000
    short_exp = issued_at + 15 * 60
    long_exp = issued_at + 7 * 24 * 60 * 60
    short = issue({'sub': 'user-1', 'jti': 'tok-short', 'iat': issued_at, 'exp': short_exp}, secret)
    long = issue({'sub': 'user-1', 'jti': 'tok-long', 'iat': issued_at, 'exp': long_exp}, secret)
    client_copy = short
    stolen = client_copy
    del client_copy
    revoked_jti = {'tok-short'}
    accepted = verify(stolen, secret, issued_at + 60)
    assert accepted['jti'] in revoked_jti and accepted['sub'] == 'user-1'
    assert verify(stolen, secret, short_exp - 1)['exp'] == short_exp
    must_reject(stolen, secret, short_exp, 'exp')
    assert verify(long, secret, issued_at + 60)['exp'] == long_exp
    assert verify(long, secret, short_exp)['exp'] == long_exp
    must_reject(long, secret, long_exp, 'exp')
    sig_at = stolen.rfind('.') + 2
    replacement = 'A' if stolen[sig_at] != 'A' else 'B'
    tampered = stolen[:sig_at] + replacement + stolen[sig_at + 1:]
    must_reject(tampered, secret, issued_at + 60, 'signature')
    header = load_json(stolen.split('.')[0])
    header['alg'] = 'none'
    forged_header = b64url(json.dumps(header, separators=(',', ':')).encode())
    forged = forged_header + '.' + '.'.join(stolen.split('.')[1:])
    must_reject(forged, secret, issued_at + 60, 'alg')
    no_exp = issue({'sub': 'user-1', 'iat': issued_at}, secret)
    must_reject(no_exp, secret, issued_at + 60, 'exp_missing')
    assert verify(stolen, secret, short_exp, leeway=60)['sub'] == 'user-1'
    must_reject(stolen, secret, short_exp + 60, 'exp', leeway=60)
    must_reject(stolen, secret, issued_at, 'malformed', leeway=-1)
    print('ok')

if __name__ == '__main__':
    main()
```

`revoked_jti`에 `tok-short`가 있는 동안 `issued_at + 60` 호출은 페이로드를 반환한다. 반환된 jti는 그 집합의 원소다. 함수 본문에 집합을 읽는 문장이 없으므로, 반환은 집합에 무엇을 넣었는지와 독립이다.

`del client_copy`는 그 이름만 지운다. `stolen`이 같은 문자열을 유지하면 검증 입력은 로그아웃 이전과 같다. 서버 검증은 클라이언트 프로세스의 지역 변수를 보지 않는다.

같은 `iat`의 `short`와 `long`은 `issued_at + 60`에 둘 다 반환된다. 두 번째 `issue` 호출은 첫 번째 문자열을 거절로 바꾸지 않는다.

<!-- sec:body-2 -->
## 서명 통과 뒤에 남는 거절 조건은 만료 시각이다

같은 인자로 `verify`를 다시 호출하면 같은 이유의 예외가 나거나 같은 페이로드가 반환된다. 호출 사이에 로그아웃 핸들러가 저장소에서 무엇을 지웠든, 토큰과 키와 시각과 여유 시간이 같으면 결과는 같다. 무효화 경계를 로그아웃 시각에 두려면 그 시각이 인자이거나, 인자가 가리키는 저장 값이 그 시각에 바뀌어야 한다. 이 함수는 어느 쪽도 아니다.

시각 now에 수락되는 문자열은 서명이 맞고, exp가 정수이며, `now < exp + leeway`인 토큰뿐이다. 이 집합의 정의에 사용자별 폐기 시각은 없다. 로그아웃은 이 집합에서 원소를 제거하지 못한다. 시간이 now를 경계 이상으로 옮길 때만 그 문자열이 집합에서 빠진다.

검사는 순서가 고정되어 있다. 여유 시간이 음수이거나 토큰이 점 두 개로 세 조각이 아니면 이유는 `malformed`다. base64url 복원이나 JSON 파싱이 실패해도, JSON 값이 객체가 아니어도 이유는 `malformed`다. 이 단계에서는 만료를 거절 이유로 남기지 않는다.

헤더의 `alg`가 HS256이 아니면 서명을 계산하기 전에 `alg`로 거절한다. 이것은 서명 알고리즘을 교체하는 절차가 아니다. 허용 값을 하나로 고정해, 다른 `alg`가 서명 검사를 건너뛰지 못하게 하는 가드다. 스크립트는 헤더의 `alg`만 `none`으로 바꾼 문자열을 `issued_at + 60`에 넘기고, 이유가 `alg`임을 단언한다. 헤더 바이트가 바뀌었으므로 서명도 어긋나지만, 거절은 서명 비교 전에 끝난다.

발급 함수는 헤더와 페이로드를 base64url로 잇고, 그 입력의 HMAC-SHA256을 세 번째 조각으로 붙인다. 검증은 같은 계산을 반복해 세 번째 조각과 비교한다. 길이 또는 바이트가 다르면 이유는 `signature`다. 서명이 확인되기 전의 exp는 발급자가 쓴 값으로 확정되지 않았다. 그래서 서명 실패의 이유는 `exp`가 되지 않는다. 만료가 미래여도 서명이 틀리면 수락하지 않는다. 스크립트는 서명 조각의 한 문자를 바꾼 토큰을 만료 전에 넘기고 이유가 `signature`임을 단언한다.

서명이 맞은 뒤에야 exp를 읽는다. 값이 없거나 정수가 아니면 `exp_missing`이다. 누락을 반환으로 처리하면 시간이 지나도 거짓이 되는 조건이 없어서, 그 토큰은 무효화 경계가 없는 자격 증명으로 남는다. 스크립트는 exp 없이 서명한 토큰이 `exp_missing`으로 거절됨을 단언한다.

`exp_missing`과 `exp`는 원인이 다르다. 전자는 now를 어떤 정수로 넣어도 거절이다. 후자는 now가 경계 이상일 때만 거절이다. 이유를 한 문자열로 합치면 발급 형식의 오류와 시각 경과에 따른 거절이 로그에서 구분되지 않는다. 스크립트가 이유를 나눈 것은 그 때문이다.

exp가 정수이면 비교식은 `now >= exp + leeway`다. `leeway`의 기본값은 0이다. 0일 때 이 식은 현재 시각이 exp보다 앞서야 한다는 만료 규칙과 같다. `now == exp`도 거절이고 `now == exp - 1`은 수락이다. 거절은 만료 시각 당초부터 시작하고, 그 1초 전까지는 로그아웃 여부와 상관없이 수락이다. exp와 now는 초 단위 정수다. 같은 초 안의 순서는 구분하지 않는다. `now`가 `exp - 1`인 초는 통째로 수락이고, `now`가 `exp`인 초는 통째로 거절이다.

서명 통과 뒤에 `sub`는 누구의 토큰인지만 말한다. `iat`는 발급 시각이지 수명의 끝이 아니다. 남은 수명은 `exp - now`다. `iat`로부터의 경과 시간에 별도 상한을 두지 않는다. 페이로드에 `nbf`가 있어도 이 함수는 읽지 않는다. `nbf`는 수락 시작을 늦추는 클레임이지, 이미 열린 구간의 끝을 닫는 클레임이 아니다. `typ`도 읽지 않는다. 따라서 서명 통과 뒤에 시간이 지나며 참이 되는 거절은 만료 비교뿐이다. 누락 거절은 발급 형식의 문제이고 now와 무관하다.

**여유 시간**(leeway)은 시계 오차를 수락 쪽으로 흡수하려고 경계에 더하는 초다. 경계는 `exp`에서 `exp + leeway`로 이동한다. 60이면 `now == exp`에서도 수락이고, 거절은 `now >= exp + 60`에서 시작한다. 스크립트는 그 두 시각을 단언한다. 음수 여유 시간은 경계를 만료 이전으로 당기므로 `malformed`로 거절한다. 비교식 안에 조기 폐기를 숨기지 않기 위해서다.

`now`는 검증 호스트가 넣는 시각이다. 호스트 사이 시계가 d초 어긋나면 같은 토큰의 거절 시각도 d초 어긋난다. 느린 시계는 경계를 늦게 지나므로 창을 늘리고, 빠른 시계는 경계를 일찍 지나므로 창을 줄인다. 여유 시간은 빠른 시계의 조기 거절을 줄이려고 경계를 뒤로 밀 뿐이다. 어느 호스트의 거절 시각도 로그아웃을 처리한 시각과 같아지지는 않는다.

아래 조각은 `verify`에서 서명 비교 이후만 자른 것이다. 조각 수, 헤더 `alg`, 음수 여유 시간, 세 번째 조각의 base64url 복원 실패는 이 조각 앞에서 끝난다. 길이 불일치를 서명 실패로 보는 조건은 빼지 않았다. `compare_digest`만 남기면 길이가 다른 서명이 어디서 거절되는지 조각 안에 남지 않기 때문이다.

<!-- sec:code-2 -->
```python-snippet
if len(actual) != len(expected) or not hmac.compare_digest(expected, actual):
    raise Reject('signature')
payload = load_json(parts[1])
exp = payload.get('exp')
if type(exp) is not int:
    raise Reject('exp_missing')
if now >= exp + leeway:
    raise Reject('exp')
return payload
```

<!-- sec:body-3 -->
## 짧은 만료가 줄이는 것은 탈취 후 남은 창이다

**남은 창**은 탈취 시각 t부터 무효화 경계까지의 수락 구간이다. 여유 시간이 0이면 길이는 `max(0, exp - t)`다. t가 발급 시각과 같으면 창은 발급 때 정한 TTL 전체다. t가 경계 이상이면 창은 0이고, 그 토큰은 이미 거절 구간이다.

창의 식에 들어가는 t는 사본이 요청으로 쓰이기 시작한 시각이다. 로그아웃 시각이 아니다. 로그아웃 시각을 알아도 `verify`에 넣을 매개변수가 없으므로, 그 시각은 거절을 호출하지 않는다.

이 창이 닫히는 사건은 서버가 탈취를 통지받는 일이 아니다. 검증 함수는 탈취 여부를 인자로 받지 않는다. 닫힘은 `now`가 경계에 도달하는 사건뿐이다. 짧은 만료는 로그아웃 처리를 거절로 바꾸는 장치가 아니다. 발급 시 exp를 앞당겨, 사본이 자격 증명으로 남는 구간의 상한을 줄이는 장치다.

스크립트의 짧은 토큰은 `iat`가 1700000000이고 TTL이 900초다. `exp`는 1700000900이다. 탈취 시각을 `iat + 60`으로 두면 남은 창은 840초다. 그 시각의 `verify(stolen, secret, issued_at + 60)`는 `sub`가 `user-1`인 페이로드를 반환한다. 840과 900은 운영 지연을 측정한 값이 아니다. 발급 인자와 탈취 시각으로 넣은 상수에서 뺀 결과다.

같은 짧은 토큰에서 t만 옮기면 창은 발급 시각에 900초, `iat + 60`에 840초다. t가 `short_exp`이면 창은 0초이고, `must_reject(stolen, secret, short_exp, 'exp')`가 그 거절을 단언한다. 840에서 0으로 줄어든 이유는 로그아웃을 처리해서가 아니다. t가 경계에 도달했기 때문이다. 서버가 탈취 시각을 모르면 남은 창의 실현값은 모르고, 발급 시점에 아는 상한은 TTL뿐이다.

사용자가 발급 직후 로그아웃해도, 발급 시각에 복사된 문자열의 창은 TTL에서 줄지 않는다. 로그아웃이 창의 기산점이 아니기 때문이다. 기산점은 사본이 생긴 시각이고, 종점은 발급 때 서명된 exp다. 로그아웃 버튼을 처리한 시각은 양 끝 어디에도 들어가지 않는다.

설정의 TTL을 900초보다 짧게 바꾸면, 그 변경 이후의 발급분만 거절 시각이 앞당겨진다. 이미 발급된 토큰의 페이로드는 발급 뒤에 바뀌지 않는다. 짧은 만료는 다음 발급분부터 상한을 줄이고, 이미 유출된 긴 토큰에는 소급되지 않는다.

<!-- sec:body-4 -->
## 긴 만료는 발급 인자에서 그 창을 다시 연다

TTL을 7일로 발급한 토큰의 경계는 1700604800이다. 7일은 604800초이고, 탈취 시각을 `iat + 60`으로 두면 남은 창은 604740초다. 짧은 토큰이 `exp`로 거절되는 1700000900에도 `now < long_exp`이라 이유는 `exp`가 되지 않고 페이로드가 반환된다. 거절은 `now >= long_exp`에서 `exp`로 난다. `verify(long, secret, short_exp)`의 반환과 `verify(long, secret, long_exp)`의 거절이 그 차이다. 604740과 840의 차이는 검증 순서가 아니라 발급 인자다.

두 토큰의 검증 순서는 같다. 달라진 것은 `issue`에 넘긴 exp다. 만료를 길게 잡는 발급은 비교식을 느슨하게 만들지 않는다. 서명으로 고정될 경계를 발급 시각에서 멀리 둔다. 경계가 멀어지면 로그아웃 시각과 실제 거절 사이의 간격이 그 거리만큼 열린다. 검증기는 발급 정책을 교정하지 않고, 페이로드에 적힌 정수를 경계로 쓴다.

창의 길이를 정하는 코드는 로그아웃 핸들러가 아니다. exp를 페이로드에 넣고 서명하는 발급 코드다. 로그아웃 핸들러는 그 정수를 읽지도, 고치지도, `verify`에 전달하지도 않는다. 만료를 하루로 두는 발급은 `exp = now + 86400`을 서명 입력에 넣는 일이다. 여유 시간이 0일 때 그 토큰이 발급 60초 뒤에 유출되면 남은 창은 86340초다. 86340은 측정값이 아니라 86400에서 60을 뺀 결과다.

재로그인을 뒤로 미루는 간격과 사본이 유효한 시간은 다른 양이다. 전자는 사용자가 다시 증명하기까지의 간격이고, 후자는 자격 증명의 수명이다. 액세스 토큰의 exp에 재로그인 간격을 그대로 넣으면 두 양이 한 정수가 된다. 그 정수가 커질수록 무효화 경계는 발급 시각에서 멀어진다.

세션 행이 없으니 액세스 토큰의 만료를 하루나 일주일로 둔다는 발급은 무효화를 그 끝에 고정한다. 사용자가 도중에 로그아웃해도 사본의 수락 구간은 그 끝에서 닫힌다. 토큰을 쓰면 세션 행이 필요 없다는 이유로 만료를 늘리면, 로그아웃과 탈취 대응은 그 만료 시각까지 수락으로 남는다. 그 크기는 정책 문장이 아니라 exp에 더해진 초다.

<!-- sec:tradeoff -->
## 만료에 무효화를 맡길 때의 간격과 전제

이 메커니즘의 비용은 남은 창이다. 여유 시간이 있으면 길이는 `max(0, exp + leeway - t)`가 되고, 상한은 발급 TTL에 여유 시간을 더한 값이다. 간격을 매 요청 0으로 맞추려면 검증마다 폐기 기록을 읽어야 한다. 그때부터 함수는 토큰과 키와 시각만의 함수가 아니다. 저장소 지연은 인증 지연이 되고, 저장소 장애는 인증 장애가 된다. 요청마다의 조회가 무상태를 버릴 때의 오버헤드다.

서명 계산 비용은 이 오버헤드에 포함되지 않는다. HMAC 비교는 폐기 기록을 두든 두지 않든 서명을 검사하면 발생한다. 만료만을 경계로 남길 때 추가로 치르는 비용은 잔여 창이다. 창을 매 요청 0으로 닫을 때 추가로 치르는 비용은 폐기 기록의 조회다. 두 비용은 연산 시간끼리의 비교가 아니다. 수락이 남는 시간과 저장소 의존을 맞바꾸는 관계다.

시계 오차를 없애려고 여유 시간을 크게 두면 그 초는 모든 토큰의 창에 더해진다. 탈취된 토큰도 같은 가산을 받는다. 여유 시간은 로그아웃이 없는 토큰과 로그아웃을 마친 사용자의 사본을 구분하지 못한다. 둘 다 `exp + leeway`까지 수락되기 때문이다. 여유 시간은 호스트 시계의 어긋남 상한으로 두고, 폐기 지연을 늘리는 용도로 키우지 않는다.

TTL을 줄이면 간격의 상한은 줄고, 같은 사용 구간을 이어 가려면 액세스 토큰 발급 횟수가 늘어난다. 사용 구간을 T초, TTL을 L초라 하면 발급 횟수는 최소 `ceil(T / L)`이다. T를 28800초, L을 900초로 두면 횟수는 32다. L을 604800초로 두면 같은 T에서 횟수는 1이다. 이 차이는 창의 상한을 맞바꾼 비용이다. 로그아웃이 즉시 거절이 되는 대가가 아니다. 재발급 경로의 리프레시 토큰 로테이션은 구현하지 않는다.

이 비용을 수용하는 조건은 발급 TTL과 여유 시간의 합이 제품이 허용하는 최대 폐기 지연을 넘지 않는 것이다. 넘으면 만료에 무효화를 맡기는 발급은 그 지연 한도와 어긋난다.

전제는 검증 경로에 토큰 저장소 조회가 없다는 것이다. 입력은 이미 분리된 JWT 문자열이다. 시각은 정수 초다. `nbf`와 `typ`은 검사하지 않는다. exp 누락과 비정수 exp는 수락하지 않는다. 여유 시간은 0 이상이다. 쿠키와 웹 스토리지 중 저장 위치 선택은 범위 밖이다. 그 선택은 사본이 요청에 남느냐를 바꿀 뿐, 남은 문자열의 서명 검사와 만료 비교는 바꾸지 않는다. 서명 알고리즘 교체도 범위 밖이다. 스크립트는 HS256만 받고 다른 `alg`는 거절한다.

900, 604800, 86340, 32, 28800은 장애 건수나 지연 분포가 아니다. 스크립트에 넣은 상수이거나, 그 상수와 하루를 초로 환산한 86400으로 적은 산술이다.

<!-- sec:conclusion -->
## 이 검증기를 쓰는 기준

로그아웃 요구가 클라이언트 참조의 삭제로 끝나고, 다른 소지자의 요청을 만료 전에 거절할 의무가 없으면 이 검증기를 쓴다. 그 의무가 요구사항에 있으면 쓰지 않는다. 서명 비교와 만료 비교만 있는 함수에는 그 의무를 만족하는 분기가 없다.

검증 코드가 토큰 식별자를 저장소에서 읽으면 무효화 경계는 만료 시각이 아니라 그 행이 사라지는 시각이다. 그 경로는 이 채택 기준의 대상이 아니다. 저장소 조회가 없는 발급에서만, 남아도 되는 최대 간격이 발급 TTL과 여유 시간의 합 이하일 때 이 검증기를 쓴다.
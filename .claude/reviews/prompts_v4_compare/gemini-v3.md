===== v1 REPEATABLE READ에서 정합성이 깨지는 이유: MVCC 스냅샷 읽기와 잠금 읽기의 동작 차이와 갱신 손실 방지 =====
<!-- sec:intro -->
# REPEATABLE READ에서 정합성이 깨지는 이유: MVCC 스냅샷 읽기와 잠금 읽기의 동작 차이와 갱신 손실 방지

많은 백엔드 개발자가 데이터베이스의 기본 트랜잭션 격리 수준을 `REPEATABLE READ`로 설정하면 단일 트랜잭션 내에서 조회한 데이터의 정합성이 끝까지 보장된다고 가정한다. 트랜잭션이 시작된 후 동일한 `SELECT` 쿼리를 반복 실행할 때 항상 같은 결과가 반환되기 때문이다.

하지만 다중 세션이 동시에 데이터를 읽고 수정하는 환경에서는 `REPEATABLE READ` 격리 수준에서도 갱신 손실(Lost Update)과 쓰기 스큐(Write Skew)가 발생한다. 나아가 하나의 트랜잭션 안에서 일반 `SELECT` 문과 `UPDATE` 또는 `SELECT ... FOR UPDATE` 문을 섞어 쓸 경우, 이전 조회에서는 보이지 않던 타 트랜잭션의 커밋 결과가 갑자기 나타나는 정합성 불일치 현상까지 나타난다.

이러한 현상이 발생하는 근본적인 원인은 MySQL InnoDB를 비롯한 다중 버전 동시성 제어(MVCC, Multi-Version Concurrency Control) 스토리지 엔진이 과거 시점의 스냅샷을 읽는 스냅샷 읽기(Consistent Nonlocking Read)와 최신 커밋 버전을 읽는 현재 읽기(Current Read)를 분리하여 처리하기 때문이다. 이 글에서는 ANSI SQL 표준과 MVCC 구현체의 구조적 차이를 분석하고, Undo 로그 버전 체인과 Read View의 가시성 판단 규칙을 통해 정합성이 깨지는 정확한 조건과 이를 방어하기 위한 잠금 및 쿼리 설계 전략을 정리한다.

---

<!-- sec:body-1 -->
## 1. ANSI SQL 표준 격리 수준과 MVCC 구현의 괴리

ANSI/ISO SQL-92 표준(ANSI X3.135-1992)은 트랜잭션 격리 수준을 정의할 때 세 가지 이상 현상(Phenomena)의 발생 여부만을 기준으로 삼았다. 당시 표준 제정 위원회는 데이터를 읽을 때 공유 잠금(Shared Lock)을 걸고 쓸 때 배타적 잠금(Exclusive Lock)을 거는 2단계 잠금(2PL, Two-Phase Locking) 기반의 스케줄러를 전제로 문서를 작성했다.

1995년 Hal Berenson 외 연구진이 발표한 논문 *A Critique of ANSI SQL Isolation Levels*(SIGMOD)는 ANSI SQL-92 표준이 다중 버전(Multi-Version) 기반 아키텍처를 반영하지 못해 동시성 이상 현상을 불완전하게 정의했음을 증명했다. 읽기 작업이 쓰기 작업을 차단하지 않는 MVCC 환경에서는 ANSI 표준이 명시한 세 가지 현상 외에도 갱신 손실(P4, Lost Update)과 쓰기 스큐(A5B, Write Skew)라는 치명적인 정합성 위배 현상이 존재한다.

| 이상 현상 (Anomaly) | 학술 분류 기호 | 현상 정의 | READ COMMITTED (InnoDB) | REPEATABLE READ (InnoDB) | REPEATABLE READ (PostgreSQL SI) | SERIALIZABLE |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Dirty Read | P1 | 커밋되지 않은 타 트랜잭션의 변경분을 읽음 | 방지됨 | 방지됨 | 방지됨 | 방지됨 |
| Non-Repeatable Read | P2 | 동일 트랜잭션 내 재조회 시 수정·삭제된 값이 다름 | 발생 가능 | 방지됨 (스냅샷 기준) | 방지됨 | 방지됨 |
| Phantom Read | P3 | 동일 범위 재조회 시 새로운 레코드가 나타남 | 발생 가능 | 스냅샷 읽기는 방지, 현재 읽기는 갭 락으로 방지 | 방지됨 | 방지됨 |
| Lost Update | P4 | 읽은 값을 기반으로 수정할 때 타 트랜잭션의 커밋을 덮어씀 | 발생 가능 | **발생 가능** | 방지됨 (First-Committer-Wins) | 방지됨 |
| Write Skew | A5B | 두 트랜잭션이 교집합을 읽고 서로 다른 행을 수정해 불변식을 깸 | 발생 가능 | **발생 가능** | **발생 가능** | 방지됨 |

위 표에서 확인할 수 있듯, MySQL InnoDB의 `REPEATABLE READ`는 단순 `SELECT`에 대해 Non-Repeatable Read(P2)와 Phantom Read(P3)를 막아주지만, 갱신 손실(P4)과 쓰기 스큐(A5B)는 차단하지 못한다. PostgreSQL의 `REPEATABLE READ`는 내부적으로 스냅샷 격리(SI, Snapshot Isolation)의 First-Committer-Wins 규칙을 구현하여 동일 행에 대한 갱신 손실은 에러(`ERROR: could not serialize access due to concurrent update`)로 차단하지만, 서로 다른 행을 수정하는 쓰기 스큐는 여전히 허용한다.

개발자가 `REPEATABLE READ` 환경에서 애플리케이션 메모리에 조회 결과를 담아 계산한 뒤 `UPDATE`를 수행하면, 데이터베이스 엔진은 그 읽기 작업과 쓰기 작업 사이의 인과 관계를 보호하지 않는다. 왜 이러한 동작 차이가 발생하는지 이해하려면 InnoDB가 레코드 버전과 스냅샷 가시성을 관리하는 내부 데이터 구조를 살펴보아야 한다.

---

<!-- sec:body-2 -->
## 2. Undo 로그 버전 체인과 Read View의 가시성 판단 규칙

MySQL InnoDB 스토리지 엔진은 잠금 없이 일관된 읽기(Consistent Nonlocking Read)를 제공하기 위해 클러스터드 인덱스(Clustered Index) 레코드에 숨겨진 시스템 컬럼을 유지하고, 변경 이전의 데이터를 Undo 로그 영역에 기록한다(참고: *MySQL 8.0 Reference Manual - 17.3 InnoDB Multi-Versioning*).

데이터베이스가 특정 레코드를 마지막으로 변경한 트랜잭션이 무엇인지 식별하려면 레코드마다 트랜잭션 번호를 기록할 공간이 필요하다. 이를 위해 InnoDB는 모든 레코드 헤더에 6바이트 크기의 `DB_TRX_ID` 컬럼을 둔다.

또한 현재 레코드가 자신의 스냅샷 시점보다 미래에 변경된 것일 때 과거 버전을 찾아 거슬러 올라가려면 이전 버전이 저장된 위치를 가리키는 주소값이 필요하다. InnoDB는 7바이트 크기의 롤백 포인터인 `DB_ROLL_PTR` 컬럼을 사용하여 Undo 세그먼트에 기록된 이전 버전 레코드들을 단방향 연결 리스트(Singly Linked List) 형태로 연결한다.

트랜잭션이 일반 `SELECT` 문을 실행하면 InnoDB는 현재 시점의 활성 트랜잭션 상태를 캡처한 `Read View` 구조체를 생성한다. `READ COMMITTED` 격리 수준에서는 매 `SELECT` 문이 시작될 때마다 새로운 `Read View`를 생성하지만, `REPEATABLE READ` 격리 수준에서는 트랜잭션 내에서 첫 번째 스냅샷 읽기가 실행될 때 단 한 번만 `Read View`를 생성하고 트랜잭션이 종료될 때까지 이를 재사용한다.

`Read View`는 레코드의 `DB_TRX_ID`가 현재 트랜잭션에게 보여도 되는지 판단하기 위해 네 가지 핵심 변수를 관리한다.

- `m_creator_trx_id`: 해당 `Read View`를 생성한 현재 트랜잭션 자신의 ID다.
- `m_ids`: `Read View` 생성 시점에 시스템에서 활성 상태(시작되었으나 아직 커밋되지 않은 상태)인 트랜잭션 ID 목록이다.
- `m_up_limit_id`: `m_ids` 목록에 포함된 활성 트랜잭션 ID 중 가장 작은 값(Low Watermark)이다. 이 값보다 작은 `DB_TRX_ID`를 가진 트랜잭션은 `Read View` 생성 이전에 이미 모두 커밋되었음을 의미한다.
- `m_low_limit_id`: `Read View` 생성 시점에 시스템이 다음에 할당할 예정인 트랜잭션 ID(High Watermark)다. 이 값보다 크거나 같은 `DB_TRX_ID`를 가진 트랜잭션은 `Read View`가 만들어진 이후에 시작된 트랜잭션이다.

스토리지 엔진은 클러스터드 인덱스에서 레코드를 읽을 때 해당 레코드의 `DB_TRX_ID`를 `Read View`의 변수들과 대조하여 아래 표의 순서대로 가시성(Visibility)을 판별한다.

| 평가 단계 | 비교 조건 | 가시성 판정 | 엔진의 후속 동작 |
| :--- | :--- | :--- | :--- |
| 1단계 | `DB_TRX_ID == m_creator_trx_id` | 가시(Visible) | 현재 트랜잭션 자신이 수정한 레코드이므로 현재 버전을 그대로 반환한다. |
| 2단계 | `DB_TRX_ID < m_up_limit_id` | 가시(Visible) | 스냅샷 생성 이전에 이미 커밋이 완료된 버전이므로 현재 버전을 그대로 반환한다. |
| 3단계 | `DB_TRX_ID >= m_low_limit_id` | 비가시(Invisible) | 스냅샷 생성 이후에 시작된 트랜잭션이 수정한 버전이므로 `DB_ROLL_PTR`를 따라 Undo 로그의 이전 버전으로 이동한 뒤 1단계부터 재평가한다. |
| 4단계 | `m_up_limit_id <= DB_TRX_ID < m_low_limit_id` | 조건부 판정 | `DB_TRX_ID`가 활성 목록인 `m_ids`에 포함되어 있으면 스냅샷 시점에 미커밋 상태였으므로 비가시로 판정하고 Undo 로그를 탐색한다. `m_ids`에 없으면 이미 커밋된 상태이므로 가시로 판정한다. |

아래 다이어그램은 `trx_id=100`인 트랜잭션이 생성한 `Read View`가 최신 커밋 버전(`DB_TRX_ID=102`)을 만나 가시성 검사를 수행하고, Undo 로그 버전 체인을 따라가 자신이 볼 수 있는 `DB_TRX_ID=95` 버전을 찾아내는 과정을 나타낸다.

<!-- sec:code-1 -->
```text
[트랜잭션 100의 Read View 상태]
- m_creator_trx_id : 100
- m_ids            : [100, 101]
- m_up_limit_id    : 100
- m_low_limit_id   : 102

[Buffer Pool: Clustered Index 최신 레코드]
+-------------------------------------------------------+
| id: 1 | balance: 800 | DB_TRX_ID: 102 | DB_ROLL_PTR   |
+-------------------------------------------------------+
  |  판정: DB_TRX_ID(102) >= m_low_limit_id(102) -> 비가시(Invisible)
  v  (DB_ROLL_PTR를 따라 Undo 로그 체인 역추적)

[Undo Log Version 1 (트랜잭션 101이 수정했던 버전)]
+-------------------------------------------------------+
| id: 1 | balance: 900 | DB_TRX_ID: 101 | DB_ROLL_PTR   |
+-------------------------------------------------------+
  |  판정: DB_TRX_ID(101) in m_ids([100, 101]) -> 비가시(Invisible)
  v  (DB_ROLL_PTR를 따라 Undo 로그 체인 역추적)

[Undo Log Version 2 (트랜잭션 95가 커밋했던 버전)]
+-------------------------------------------------------+
| id: 1 | balance: 1000 | DB_TRX_ID: 95 | DB_ROLL_PTR   |
+-------------------------------------------------------+
     판정: DB_TRX_ID(95) < m_up_limit_id(100) -> 가시(Visible)! (balance=1000 반환)
```

이처럼 일반 `SELECT` 문은 오직 `Read View`와 Undo 로그 체인만을 참조하기 때문에, 타 트랜잭션(`trx_id=102`)이 해당 행을 수정하고 커밋하더라도 영향을 받지 않고 `1000`이라는 과거 값을 일관되게 반환한다. 문제는 이 트랜잭션이 데이터를 변경하거나 잠금 읽기를 시도하는 순간 발생한다.

---

<!-- sec:body-3 -->
## 3. 스냅샷 읽기와 현재 읽기(Current Read) 혼용 시 정합성이 깨지는 시나리오

InnoDB는 데이터를 읽는 방식을 스냅샷 읽기(Consistent Nonlocking Read)와 현재 읽기(Current Read) 두 가지로 엄격하게 구분한다. 일반 `SELECT` 문은 스냅샷 읽기를 수행하지만, `SELECT ... FOR SHARE`, `SELECT ... FOR UPDATE`, `UPDATE`, `DELETE` 문은 모두 현재 읽기를 수행한다.

데이터를 변경하는 `UPDATE`나 잠금을 획득하는 `SELECT ... FOR UPDATE`가 과거의 `Read View`를 기준으로 동작한다면, 이미 커밋된 다른 트랜잭션의 최신 변경 내역이 유실되거나 과거 버전 위에 잠금을 거는 모순이 발생한다. 따라서 현재 읽기는 트랜잭션이 보유한 `Read View`를 무시하고, 클러스터드 인덱스에 존재하는 가장 최신의 커밋된 레코드 버전을 직접 읽으면서 해당 레코드에 배타적 잠금(`X` Lock) 또는 공유 잠금(`S` Lock)을 획득한다.

하나의 트랜잭션 안에서 스냅샷 읽기와 현재 읽기가 결합될 때 정합성이 깨지는 세 가지 대표적인 시나리오를 살펴본다.

### 3.1 시나리오 A: Read-Modify-Write 패턴의 갱신 손실(Lost Update)

가장 흔하게 발생하는 장애 유형은 애플리케이션이 일반 `SELECT`로 잔액이나 재고를 조회한 뒤, 애플리케이션 메모리에서 차감 연산을 수행하고 계산된 결과값을 `UPDATE ... SET balance = {계산값}` 형태로 반영하는 경우다.

| 시간 | 트랜잭션 A (`trx_id=100`) | 트랜잭션 B (`trx_id=101`) | 클러스터드 인덱스 최신 값 (`id=1`) | 트랜잭션 A의 Read View 가시 값 |
| :--- | :--- | :--- | :--- | :--- |
| T1 | `BEGIN;` | `BEGIN;` | `balance=1000, DB_TRX_ID=90` | 생성 전 |
| T2 | `SELECT balance FROM wallet WHERE id = 1;` | (대기) | `balance=1000, DB_TRX_ID=90` | `1000` (`Read View` 고정) |
| T3 | (애플리케이션 메모리에서 `1000 - 300 = 700` 계산) | `SELECT balance FROM wallet WHERE id = 1;` | `balance=1000, DB_TRX_ID=90` | `1000` |
| T4 | (외부 API 호출 또는 비즈니스 로직 수행) | `UPDATE wallet SET balance = 800 WHERE id = 1;` | `balance=800, DB_TRX_ID=101` (`X` Lock) | `1000` |
| T5 | (대기) | `COMMIT;` | `balance=800, DB_TRX_ID=101` (Lock 해제) | `1000` |
| T6 | `UPDATE wallet SET balance = 700 WHERE id = 1;` | (종료) | `balance=700, DB_TRX_ID=100` (`X` Lock) | `700` (`DB_TRX_ID == m_creator_trx_id`) |
| T7 | `COMMIT;` | (종료) | `balance=700, DB_TRX_ID=100` | (종료) |

T6 시점에 트랜잭션 A가 실행한 `UPDATE` 문은 현재 읽기를 수행하여 클러스터드 인덱스의 최신 버전(`balance=800, DB_TRX_ID=101`)을 찾아낸다. 그러나 `SET balance = 700`이라는 구문은 현재 레코드의 값(`800`)을 참조하지 않고 상수 `700`을 그대로 덮어쓴다. 결과적으로 T4~T5에서 트랜잭션 B가 정상적으로 커밋한 `200` 차감 내역은 아무런 에러나 잠금 대기 없이 사라진다.

### 3.2 시나리오 B: 스냅샷 읽기와 현재 읽기 혼용 시 발생하는 상태 역전

단일 트랜잭션 내에서 일반 `SELECT`로 조회하던 행을 `UPDATE`하거나 `SELECT ... FOR UPDATE`로 읽으면, `REPEATABLE READ`임에도 불구하고 이전 `SELECT`와 다른 결과가 반환된다. 아래 SQL 재현 코드는 이 현상이 발생하는 메커니즘을 보여준다.

<!-- sec:code-2 -->
```sql
-- 초기 데이터: id = 1, balance = 1000, status = 'ACTIVE' (DB_TRX_ID = 90)

-- [Session A: trx_id = 100]
START TRANSACTION;
-- 1. 첫 번째 스냅샷 읽기: Read View 생성 (m_creator_trx_id = 100, m_low_limit_id = 100)
SELECT id, balance, status FROM wallet WHERE id = 1;
-- 결과: id = 1, balance = 1000, status = 'ACTIVE'

-- [Session B: trx_id = 101]
START TRANSACTION;
UPDATE wallet SET balance = balance - 200 WHERE id = 1;
COMMIT;
-- 클러스터드 인덱스 최신 레코드: balance = 800, DB_TRX_ID = 101

-- [Session A: trx_id = 100]
-- 2. 두 번째 스냅샷 읽기: DB_TRX_ID(101) >= m_low_limit_id(100) 이므로 Undo 로그의 1000을 읽음
SELECT id, balance, status FROM wallet WHERE id = 1;
-- 결과: id = 1, balance = 1000, status = 'ACTIVE'

-- 3. 현재 읽기(UPDATE) 실행: 최신 버전(balance = 800, DB_TRX_ID = 101)을 기준으로 status만 변경
UPDATE wallet SET status = 'CHECKED' WHERE id = 1;
-- 클러스터드 인덱스 최신 레코드: balance = 800, status = 'CHECKED', DB_TRX_ID = 100

-- 4. 세 번째 스냅샷 읽기: 최신 레코드의 DB_TRX_ID가 100(m_creator_trx_id)으로 변경되었음!
SELECT id, balance, status FROM wallet WHERE id = 1;
-- 결과: id = 1, balance = 800, status = 'CHECKED' (갑자기 balance가 1000에서 800으로 변경됨)
COMMIT;
```

위 SQL에서 Session A의 3번 `UPDATE` 문은 `status` 컬럼만 변경했지만, 내부적으로는 현재 읽기를 통해 Session B가 커밋한 최신 레코드(`balance = 800, DB_TRX_ID = 101`)를 가져와 `status`를 수정하고 해당 레코드의 `DB_TRX_ID`를 자신의 트랜잭션 ID인 `100`으로 갱신한다.

그 직후 4번 `SELECT` 문이 실행되면, 가시성 판단 규칙 1단계(`DB_TRX_ID == m_creator_trx_id`)에 의해 최신 레코드가 가시(Visible) 판정을 받는다. 이로 인해 Session A는 자신이 수정하지도 않은 `balance` 컬럼의 값이 트랜잭션 도중 `1000`에서 `800`으로 바뀌는 Non-Repeatable Read 현상을 겪게 된다.

### 3.3 시나리오 C: 다중 행 불변식을 파괴하는 쓰기 스큐(Write Skew)

쓰기 스큐는 두 트랜잭션이 동일한 데이터 집합을 스냅샷 읽기로 조회하여 비즈니스 불변식(Invariant)을 검증한 뒤, 서로 다른 행을 수정할 때 발생한다. 예를 들어 한 사용자의 주 계좌(`id=1`)와 서브 계좌(`id=2`)의 잔액 합계가 항상 `0` 이상이어야 한다는 규칙이 있을 때, 두 계좌에 각각 `500`원이 들어 있는 상황을 가정한다.

트랜잭션 A와 B가 동시에 두 계좌를 일반 `SELECT`로 조회하면 두 트랜잭션 모두 합계 잔액을 `1000`원으로 인식한다. 이어서 트랜잭션 A는 `id=1` 계좌에서 `800`원을 인출(`balance = -300`)하고, 트랜잭션 B는 `id=2` 계좌에서 `800`원을 인출(`balance = -300`)한다.

두 트랜잭션이 수정하는 대상 행(`id=1`과 `id=2`)이 서로 다르기 때문에 레코드 잠금 충돌이 전혀 발생하지 않으며, PostgreSQL의 First-Committer-Wins 검사조차 통과한다. 두 트랜잭션이 모두 커밋되고 나면 계좌 잔액의 합계는 `-600`원이 되어 비즈니스 불변식이 완전히 깨진다.

---

<!-- sec:body-4 -->
## 4. 갱신 손실과 쓰기 스큐 방지를 위한 코드 전환: 비관적 잠금과 원자적 연산

실제 백엔드 애플리케이션에서 정합성 문제를 해결하는 과정을 확인하기 위해, 계좌 출금 시 구간별 수수료를 계산하고 최소 유지 잔액 조건을 검증한 뒤 원장(Ledger)에 변경 전후 잔액을 기록하는 파이썬 코드를 살펴본다.

### 4.1 변경 전: 스냅샷 읽기에 의존하여 갱신 손실이 발생하는 코드

아래 코드는 `REPEATABLE READ` 격리 수준에서 일반 `SELECT`로 계좌 상태를 읽어 파이썬 애플리케이션 메모리에서 수수료 계산과 잔액 검증을 수행한다.

<!-- sec:code-3 -->
```python
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol


class CursorProtocol(Protocol):
    rowcount: int

    def execute(self, query: str, params: tuple[Any, ...]) -> None: ...
    def fetchone(self) -> dict[str, Any] | None: ...
    def fetchall(self) -> list[dict[str, Any]]: ...


class ConnectionProtocol(Protocol):
    def cursor(self) -> CursorProtocol: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


@dataclass(frozen=True)
class WithdrawalResult:
    wallet_id: int
    before_balance: Decimal
    after_balance: Decimal
    fee: Decimal


class InsufficientBalanceError(Exception):
    pass


def calculate_tiered_fee(amount: Decimal, vip_grade: str) -> Decimal:
    if vip_grade == "VIP":
        return Decimal("0.00")
    if amount >= Decimal("100000.00"):
        return (amount * Decimal("0.001")).quantize(Decimal("0.01"))
    return Decimal("500.00")


def withdraw_vulnerable(
    conn: ConnectionProtocol,
    user_id: int,
    target_wallet_id: int,
    amount: Decimal,
    min_total_balance: Decimal,
) -> WithdrawalResult:
    cursor = conn.cursor()
    try:
        # 문제점 1: 일반 SELECT(스냅샷 읽기)로 사용자의 전체 계좌 목록을 조회함
        # 동시 트랜잭션이 다른 계좌나 현재 계좌를 수정 중이어도 잠금 없이 과거 스냅샷을 읽음
        cursor.execute(
            "SELECT id, balance, vip_grade FROM wallets WHERE user_id = %s",
            (user_id,),
        )
        wallets = cursor.fetchall()
        if not wallets:
            raise ValueError(f"No wallets found for user_id={user_id}")

        total_balance = sum(( Decimal(str(w["balance"])) for w in wallets ), Decimal("0.00"))
        target_wallet = next((w for w in wallets if int(w["id"]) == target_wallet_id), None)
        if target_wallet is None:
            raise ValueError(f"Target wallet {target_wallet_id} not found")

        before_balance = Decimal(str(target_wallet["balance"]))
        vip_grade = str(target_wallet["vip_grade"])
        fee = calculate_tiered_fee(amount, vip_grade)
        total_deduction = amount + fee

        # 다중 계좌 합산 최소 잔액 불변식 검증 및 개별 계좌 잔액 검증
        if (total_balance - total_deduction) < min_total_balance:
            raise InsufficientBalanceError("Total balance across wallets falls below minimum limit")
        if before_balance < total_deduction:
            raise InsufficientBalanceError("Insufficient balance in target wallet")

        after_balance = before_balance - total_deduction

        # 문제점 2: 스냅샷 기반으로 계산된 after_balance 상수를 그대로 덮어씀 (Lost Update 발생)
        cursor.execute(
            "UPDATE wallets SET balance = %s WHERE id = %s",
            (str(after_balance), target_wallet_id),
        )

        # 문제점 3: 원장(wallet_ledger)에 기록되는 before_balance와 after_balance가 실제 최신 상태와 불일치함
        cursor.execute(
            """
            INSERT INTO wallet_ledger (wallet_id, amount, fee, before_balance, after_balance)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                target_wallet_id,
                str(amount),
                str(fee),
                str(before_balance),
                str(after_balance),
            ),
        )
        conn.commit()
        return WithdrawalResult(
            wallet_id=target_wallet_id,
            before_balance=before_balance,
            after_balance=after_balance,
            fee=fee,
        )
    except Exception:
        conn.rollback()
        raise
```

위 코드의 `withdraw_vulnerable` 함수는 세 가지 심각한 동시성 결함을 안고 있다.

첫째, `SELECT id, balance, vip_grade FROM wallets WHERE user_id = %s` 쿼리가 스냅샷 읽기로 수행되기 때문에 동시에 실행된 다른 출금 요청의 변경분을 인지하지 못한다.

둘째, 한 사용자의 여러 계좌 잔액 합계(`total_balance`)가 `min_total_balance` 이상이어야 한다는 불변식을 검증할 때 잠금을 걸지 않으므로, 서로 다른 계좌에서 동시에 출금하면 쓰기 스큐(Write Skew)가 발생하여 합산 최소 잔액 제한이 무력화된다.

셋째, `after_balance`를 파이썬 변수로 계산하여 `UPDATE` 문에 상수로 전달하므로 먼저 커밋된 트랜잭션의 차감 내역을 덮어쓰며, `wallet_ledger` 테이블에는 잘못된 `before_balance`와 `after_balance`가 기록되어 감사 로그(Audit Log)의 무결성까지 깨진다.

### 4.2 변경 후 패턴 1: 비관적 잠금(SELECT FOR UPDATE)을 통한 현재 읽기 강제

복잡한 수수료 계산, 다중 계좌 불변식 검증, 변경 전후 잔액의 정확한 원장 기록을 모두 안전하게 처리하는 가장 직관적인 방법은 트랜잭션의 첫 번째 조회부터 `SELECT ... FOR UPDATE`를 사용하여 현재 읽기(Current Read)와 배타적 잠금을 강제하는 것이다.

이때 다중 행에 걸친 쓰기 스큐를 방지하려면 불변식 검증에 참여하는 모든 계좌 행을 잠가야 하며, 여러 트랜잭션이 교차 순서로 잠금을 획득하다가 데드락(Deadlock)에 빠지지 않도록 반드시 고정된 정렬 순서(`ORDER BY id ASC`)로 잠금을 획득해야 한다.

<!-- sec:code-4 -->
```python
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol


class CursorProtocol(Protocol):
    rowcount: int

    def execute(self, query: str, params: tuple[Any, ...]) -> None: ...
    def fetchone(self) -> dict[str, Any] | None: ...
    def fetchall(self) -> list[dict[str, Any]]: ...


class ConnectionProtocol(Protocol):
    def cursor(self) -> CursorProtocol: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


@dataclass(frozen=True)
class WithdrawalResult:
    wallet_id: int
    before_balance: Decimal
    after_balance: Decimal
    fee: Decimal


class InsufficientBalanceError(Exception):
    pass


def calculate_tiered_fee(amount: Decimal, vip_grade: str) -> Decimal:
    if vip_grade == "VIP":
        return Decimal("0.00")
    if amount >= Decimal("100000.00"):
        return (amount * Decimal("0.001")).quantize(Decimal("0.01"))
    return Decimal("500.00")


def withdraw_with_pessimistic_lock(
    conn: ConnectionProtocol,
    user_id: int,
    target_wallet_id: int,
    amount: Decimal,
    min_total_balance: Decimal,
) -> WithdrawalResult:
    cursor = conn.cursor()
    try:
        # 해결 1: FOR UPDATE를 통해 Read View를 무시하고 최신 커밋 버전(Current Read)을 읽음
        # 데드락 방지를 위해 id 오름차순(ORDER BY id ASC)으로 사용자의 모든 계좌 레코드에 X Lock 획득
        cursor.execute(
            """
            SELECT id, balance, vip_grade
            FROM wallets
            WHERE user_id = %s
            ORDER BY id ASC
            FOR UPDATE
            """,
            (user_id,),
        )
        wallets = cursor.fetchall()
        if not wallets:
            raise ValueError(f"No wallets found for user_id={user_id}")

        # 모든 관련 계좌에 배타적 잠금이 걸려 있으므로 쓰기 스큐(Write Skew)가 원천 차단됨
        total_balance = sum(( Decimal(str(w["balance"])) for w in wallets ), Decimal("0.00"))
        target_wallet = next((w for w in wallets if int(w["id"]) == target_wallet_id), None)
        if target_wallet is None:
            raise ValueError(f"Target wallet {target_wallet_id} not found")

        before_balance = Decimal(str(target_wallet["balance"]))
        vip_grade = str(target_wallet["vip_grade"])
        fee = calculate_tiered_fee(amount, vip_grade)
        total_deduction = amount + fee

        if (total_balance - total_deduction) < min_total_balance:
            raise InsufficientBalanceError("Total balance across wallets falls below minimum limit")
        if before_balance < total_deduction:
            raise InsufficientBalanceError("Insufficient balance in target wallet")

        after_balance = before_balance - total_deduction

        # 해결 2: 이미 X Lock을 보유한 상태에서 최신 버전을 기준으로 계산된 값을 반영
        cursor.execute(
            "UPDATE wallets SET balance = %s WHERE id = %s",
            (str(after_balance), target_wallet_id),
        )

        # 해결 3: 잠금으로 직렬화된 상태이므로 원장의 before_balance와 after_balance가 100% 일치함
        cursor.execute(
            """
            INSERT INTO wallet_ledger (wallet_id, amount, fee, before_balance, after_balance)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                target_wallet_id,
                str(amount),
                str(fee),
                str(before_balance),
                str(after_balance),
            ),
        )
        conn.commit()
        return WithdrawalResult(
            wallet_id=target_wallet_id,
            before_balance=before_balance,
            after_balance=after_balance,
            fee=fee,
        )
    except Exception:
        conn.rollback()
        raise
```

`withdraw_with_pessimistic_lock` 함수에서는 `WHERE user_id = %s ORDER BY id ASC FOR UPDATE` 쿼리가 실행되는 즉시 해당 사용자의 모든 계좌 레코드에 배타적 잠금(`X` Lock)이 걸린다(이때 `user_id` 컬럼에는 반드시 인덱스가 존재해야 한다). 다른 트랜잭션이 같은 사용자의 어떤 계좌에 접근하더라도 첫 번째 `SELECT ... FOR UPDATE` 단계에서 대기 상태에 들어가며, 선행 트랜잭션이 `COMMIT`을 마친 뒤 최신 커밋 버전을 읽게 된다.

### 4.3 변경 후 패턴 2: 조건부 원자적 연산(Conditional Atomic Update)과 버전 검증

만약 조회 시점과 수정 시점 사이에 외부 네트워크 호출이나 무거운 연산이 포함되어 있어 데이터베이스 레코드 잠금을 길게 유지할 수 없다면, 조회는 스냅샷 읽기로 수행하되 `UPDATE` 구문에서 원자적 차감 연산과 버전(`version`) 검증을 결합해야 한다.

이때 앞서 살펴본 다중 계좌 불변식(쓰기 스큐 방지)과 원장 기록(`before_balance`, `after_balance`의 정확성 보장)이라는 요구 사항이 조용히 누락되어서는 안 된다. 다중 행 불변식을 잠금 없이 보호하려면 사용자 단위의 상위 집계 레코드(예: `user_wallet_summary` 또는 루트 엔티티의 `version`)를 함께 조건부 갱신하거나, 대상 계좌의 `version`과 잔액 조건을 `UPDATE`의 `WHERE` 절에 명시하여 스냅샷 시점의 상태가 그대로 유지되었는지 검증해야 한다.

<!-- sec:code-5 -->
```python
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol


class CursorProtocol(Protocol):
    rowcount: int

    def execute(self, query: str, params: tuple[Any, ...]) -> None: ...
    def fetchone(self) -> dict[str, Any] | None: ...
    def fetchall(self) -> list[dict[str, Any]]: ...


class ConnectionProtocol(Protocol):
    def cursor(self) -> CursorProtocol: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


@dataclass(frozen=True)
class WithdrawalResult:
    wallet_id: int
    before_balance: Decimal
    after_balance: Decimal
    fee: Decimal


class InsufficientBalanceError(Exception):
    pass


class ConcurrencyConflictError(Exception):
    pass


def calculate_tiered_fee(amount: Decimal, vip_grade: str) -> Decimal:
    if vip_grade == "VIP":
        return Decimal("0.00")
    if amount >= Decimal("100000.00"):
        return (amount * Decimal("0.001")).quantize(Decimal("0.01"))
    return Decimal("500.00")


def withdraw_with_atomic_optimistic(
    conn: ConnectionProtocol,
    user_id: int,
    target_wallet_id: int,
    amount: Decimal,
    min_total_balance: Decimal,
) -> WithdrawalResult:
    cursor = conn.cursor()
    try:
        # 1. 스냅샷 읽기로 사용자 전체 계좌 상태와 각 계좌의 version을 조회
        cursor.execute(
            "SELECT id, balance, vip_grade, version FROM wallets WHERE user_id = %s",
            (user_id,),
        )
        wallets = cursor.fetchall()
        if not wallets:
            raise ValueError(f"No wallets found for user_id={user_id}")

        total_balance = sum(( Decimal(str(w["balance"])) for w in wallets ), Decimal("0.00"))
        target_wallet = next((w for w in wallets if int(w["id"]) == target_wallet_id), None)
        if target_wallet is None:
            raise ValueError(f"Target wallet {target_wallet_id} not found")

        before_balance = Decimal(str(target_wallet["balance"]))
        expected_version = int(target_wallet["version"])
        vip_grade = str(target_wallet["vip_grade"])
        fee = calculate_tiered_fee(amount, vip_grade)
        total_deduction = amount + fee

        if (total_balance - total_deduction) < min_total_balance:
            raise InsufficientBalanceError("Total balance across wallets falls below minimum limit")
        if before_balance < total_deduction:
            raise InsufficientBalanceError("Insufficient balance in target wallet")

        # 2. 다중 계좌 쓰기 스큐 방지를 위해 비대상 계좌들의 버전이 변경되지 않았는지 현재 읽기로 검증 및 버전 증가
        other_wallets = [w for w in wallets if int(w["id"]) != target_wallet_id]
        for other in sorted(other_wallets, key=lambda x: int(x["id"])):
            cursor.execute(
                """
                UPDATE wallets
                SET version = version + 1
                WHERE id = %s AND version = %s
                """,
                (int(other["id"]), int(other["version"])),
            )
            if cursor.rowcount == 0:
                raise ConcurrencyConflictError(
                    f"Concurrent modification detected on sibling wallet {other['id']}"
                )

        # 3. 조건부 원자적 연산(Atomic Update): 현재 읽기 시점에 version과 잔액 조건을 동시에 검증
        cursor.execute(
            """
            UPDATE wallets
            SET balance = balance - %s,
                version = version + 1
            WHERE id = %s
              AND version = %s
              AND balance >= %s
            """,
            (
                str(total_deduction),
                target_wallet_id,
                expected_version,
                str(total_deduction),
            ),
        )

        # 타 트랜잭션이 먼저 수정하여 version이 달라졌거나 잔액이 부족해졌다면 rowcount가 0이 됨
        if cursor.rowcount == 0:
            raise ConcurrencyConflictError(
                f"Concurrent modification or balance shortage on wallet {target_wallet_id}"
            )

        # version 조건이 통과되었다는 것은 스냅샷 시점의 before_balance가 현재 읽기 시점의 값과 동일했음을 보장함
        after_balance = before_balance - total_deduction

        cursor.execute(
            """
            INSERT INTO wallet_ledger (wallet_id, amount, fee, before_balance, after_balance)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                target_wallet_id,
                str(amount),
                str(fee),
                str(before_balance),
                str(after_balance),
            ),
        )
        conn.commit()
        return WithdrawalResult(
            wallet_id=target_wallet_id,
            before_balance=before_balance,
            after_balance=after_balance,
            fee=fee,
        )
    except Exception:
        conn.rollback()
        raise
```

`withdraw_with_atomic_optimistic` 함수는 `UPDATE` 문이 현재 읽기로 실행된다는 특성을 역이용한다. `WHERE id = %s AND version = %s AND balance >= %s` 조건은 클러스터드 인덱스의 최신 커밋 버전을 기준으로 평가된다.

만약 T2(스냅샷 읽기)와 T6(`UPDATE`) 사이에 다른 트랜잭션이 대상 계좌나 형제 계좌를 수정하고 커밋했다면 최신 레코드의 `version`이 이미 증가했으므로 `cursor.rowcount`는 `0`을 반환한다. 애플리케이션은 즉시 `ConcurrencyConflictError`를 발생시키고 트랜잭션을 롤백하여 갱신 손실과 부정확한 원장 기록을 모두 차단한다.

---

<!-- sec:tradeoff -->
## 5. 잠금 전략별 트레이드오프와 설계 한계

동시성 제어 방식은 워크로드의 경합도(Contention)와 불변식의 범위에 따라 선택해야 한다. 각 기법의 특성과 운영상 트레이드오프를 비교하면 아래 표와 같다.

| 비교 항목 | 비관적 잠금 (`SELECT ... FOR UPDATE`) | 조건부 원자적 연산 + 낙관적 버전 검증 | 단순 원자적 연산 (`SET col = col - val`) | `SERIALIZABLE` 격리 수준 |
| :--- | :--- | :--- | :--- | :--- |
| 잠금 획득 시점 | 트랜잭션 내 첫 조회 시점 | `UPDATE` 구문 실행 시점 | `UPDATE` 구문 실행 시점 | 모든 `SELECT` 시 공유 잠금(`S`), 수정 시 배타적 잠금(`X`) |
| 경합 발생 시 동작 | 후행 트랜잭션이 잠금 해제까지 대기(Blocking) | `rowcount == 0` 즉시 반환 후 애플리케이션 재시도 | `UPDATE` 순간에만 짧게 대기 후 순차 차감 | 잠금 대기 또는 데드락 롤백 발생 |
| 변경 전후 값(`before`/`after`) 추적 | 스냅샷과 최신 값이 일치하여 정확히 추적 가능 | `version` 일치 시에만 성공하므로 정확히 추적 가능 | `UPDATE`만 실행하면 변경 전후 정확한 스냅샷 값을 알기 어려움 | 정확히 추적 가능 |
| 다중 행 쓰기 스큐 방지 | 관련 행 전체를 `FOR UPDATE`로 잠가 방지 | 관련 행들의 `version`을 검증·갱신하거나 상위 행 버전으로 방지 | 단일 행 연산만 가능하여 다중 행 불변식 보호 불가 | 엔진 수준에서 자동 방지 |
| 주요 운영 리스크 | 외부 I/O 결합 시 커넥션 풀 고갈 및 데드락 위험 | 경합이 높을수록 롤백 및 재시도 비용 급증 | 복잡한 비즈니스 규칙을 SQL 내부에 넣기 어려움 | 처리량(Throughput) 급감 |

비관적 잠금(`SELECT ... FOR UPDATE`)이나 `UPDATE`를 사용할 때 시스템 엔지니어가 가장 주의해야 할 요소는 인덱스 설계다. InnoDB의 레코드 잠금은 테이블의 행 자체가 아니라 스토리지 엔진이 탐색한 인덱스 레코드에 걸린다.

만약 `WHERE user_id = %s` 조건절에 인덱스가 없다면 InnoDB는 풀 테이블 스캔(Full Table Scan)을 수행하면서 스캔 과정에서 거치는 모든 클러스터드 인덱스 레코드와 그 사이의 간격(Gap Lock)에 배타적 잠금을 건다. 이로 인해 전혀 무관한 사용자의 출금 요청까지 모두 차단되는 장애가 발생하므로, 잠금 읽기 쿼리는 반드시 실행 계획(`EXPLAIN`)을 통해 세컨더리 인덱스 또는 기본 키 인덱스를 타는지 검증해야 한다.

한편 이 글에서는 단일 관계형 데이터베이스 인스턴스 내부의 MVCC 가시성 규칙과 레코드 잠금 메커니즘만을 다루었다. 서로 다른 데이터베이스 인스턴스나 마이크로서비스 간에 걸친 정합성을 제어하는 분산 트랜잭션 기법—예를 들어 **2PC(Two-Phase Commit)** 프로토콜이나 보상 트랜잭션 기반의 **Saga 패턴**—은 이 글의 범위 밖이다. 또한 다중 잠금 경합 과정에서 발생하는 **데드락 탐지 대기 그래프(Wait-for Graph) 알고리즘**의 내부 동작과 `innodb_lock_wait_timeout` 등 엔진별 타임아웃 파라미터 튜닝 역시 별도의 인프라 엔지니어링 주제로 다루어야 한다.

---

<!-- sec:conclusion -->
## 6. 결론 및 아키텍처 체크리스트

`REPEATABLE READ` 격리 수준은 잠금 없는 `SELECT` 쿼리가 동일한 `Read View`를 재사용하도록 하여 반복 읽기 일관성을 제공할 뿐, 애플리케이션의 Read-Modify-Write 트랜잭션을 자동으로 직렬화해 주지 않는다. 과거 시점을 읽는 스냅샷 읽기와 최신 커밋 버전을 읽는 현재 읽기(`UPDATE`, `SELECT ... FOR UPDATE`)의 동작 차이를 간과하면 동시성 환경에서 데이터 정합성이 즉시 파괴된다.

트랜잭션 로직을 설계하고 코드 리뷰를 수행할 때는 다음 네 가지 기준을 점검해야 한다.

1. **조회 후 수정(Read-Modify-Write) 분리 금지**: 일반 `SELECT`로 읽은 값을 애플리케이션 변수에서 가공한 뒤 `UPDATE ... SET col = {상수}` 형태로 반영하는 코드는 `REPEATABLE READ`에서도 반드시 갱신 손실을 일으킨다. 첫 조회를 `SELECT ... FOR UPDATE`로 전환하거나 `UPDATE` 절에 `version` 검증 조건을 추가한다.
2. **단일 트랜잭션 내 읽기 모드 일관성 유지**: 동일 레코드에 대해 일반 `SELECT`를 수행한 뒤 `UPDATE`나 `SELECT ... FOR UPDATE`를 실행하면 트랜잭션 도중 레코드의 `DB_TRX_ID`가 현재 트랜잭션 ID로 갱신되어 가시성이 뒤집힌다. 수정이 예정된 데이터는 트랜잭션 시작 시점부터 잠금 읽기로 조회한다.
3. **다중 행 불변식 검증 시 잠금 범위와 순서 고정**: 여러 행의 합계나 상태를 검증한 뒤 일부 행만 수정하는 로직은 쓰기 스큐에 노출된다. 검증 대상 행 전체를 `ORDER BY id ASC FOR UPDATE`로 정렬하여 잠그거나 상위 집계 레코드의 버전을 함께 갱신한다.
4. **잠금 쿼리의 인덱스 필수 검증**: 현재 읽기를 수행하는 모든 `SELECT ... FOR UPDATE`와 `UPDATE` 문의 `WHERE` 조건절이 인덱스를 통해 최소한의 레코드만 탐색하는지 확인하여 불필요한 레코드 잠금 및 갭 락 확산을 차단한다.

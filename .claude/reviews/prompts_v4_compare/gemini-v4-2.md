# REPEATABLE READ에서 정합성이 깨지는 이유: MVCC 스냅샷 읽기와 잠금 읽기의 동작 차이와 갱신 손실 방지

> MySQL InnoDB의 REPEATABLE READ 격리 수준에서 MVCC 스냅샷 읽기와 잠금 읽기가 혼용될 때 발생하는 갱신 손실과 쓰기 스큐의 내부 원인을 분석하고, 비관적 잠금 및 원자적 연산 전환 전략을 설명한다.

<!-- sec:intro -->
## REPEATABLE READ 격리 수준에서도 발생하는 데이터 유실

트랜잭션 격리 수준을 `REPEATABLE READ`로 설정하더라도, 단일 트랜잭션 안에서 일반 `SELECT`로 조회한 값을 애플리케이션 메모리에서 가공해 `UPDATE`하면 동시 트랜잭션의 변경분이 덮어쓰여 사라지는 **갱신 손실(Lost Update)**이 발생한다. 또한 두 개 이상의 행에 걸친 비즈니스 불변식을 일반 `SELECT`로 검증한 뒤 서로 다른 행을 수정하면, 각 트랜잭션이 상대방의 변경을 인지하지 못한 채 커밋되어 정합성 제약이 깨지는 **쓰기 스큐(Write Skew)**가 나타난다.

이러한 현상이 발생하는 근본 원인은 스토리지 엔진이 읽기 연산과 쓰기 연산에 서로 다른 데이터 버전을 제공하기 때문이다. MySQL InnoDB를 비롯한 다중 버전 동시성 제어 기반 엔진은 읽기 작업 시 잠금을 걸지 않기 위해 트랜잭션 시작 시점의 스냅샷을 반환하지만, 데이터를 수정하거나 잠금 읽기를 수행할 때는 스냅샷이 아닌 가장 최근에 커밋된 현재 버전을 참조한다. 과거 시점의 Read View를 읽는 스냅샷 읽기와 최신 커밋 버전을 읽는 잠금 읽기가 단일 트랜잭션 내에서 혼용될 때 발생하는 데이터 정합성 불일치 메커니즘을 파악해야, 워크로드에 맞는 비관적 잠금 및 원자적 연산 전략을 설계할 수 있다.

<!-- sec:body-1 -->
## ANSI SQL 표준 격리 수준과 MVCC 스토리지 엔진 구현의 괴리

ANSI SQL-92 표준은 트랜잭션 격리 수준을 오손 읽기(Dirty Read), 반복 불가능 읽기(Non-Repeatable Read), 유령 읽기(Phantom Read)라는 세 가지 읽기 이상 현상의 허용 여부로만 정의했다. 이 표준 명세는 작성 당시 행 단위 공유 잠금(Shared Lock)과 배타적 잠금(Exclusive Lock)에 기반한 **2단계 잠금(2PL, Two-Phase Locking)** 구현을 암묵적인 전제로 삼았다. 2PL 기반의 전통적인 `REPEATABLE READ` 환경에서는 트랜잭션이 읽은 모든 행에 트랜잭션 종료 시점까지 공유 잠금이 유지되므로, 다른 트랜잭션이 해당 행에 배타적 잠금을 획득해 값을 수정하는 것 자체가 불가능하여 갱신 손실이 원천적으로 차단된다.

반면 현대 관계형 데이터베이스 엔진은 읽기 작업이 쓰기 작업을 블로킹하여 동시 처리량이 급격히 저하되는 문제를 해결하기 위해 **MVCC(Multi-Version Concurrency Control)**를 도입했다. MVCC는 행이 수정될 때마다 변경 이전의 데이터를 별도의 버전으로 보존하고, 일반 `SELECT` 쿼리가 잠금을 획득하는 대신 특정 시점의 스냅샷 버전을 읽도록 허용한다. 읽기 작업이 공유 잠금을 걸지 않으므로 다른 트랜잭션은 동일한 행을 즉시 수정하고 커밋할 수 있다.

이로 인해 ANSI SQL 표준이 정의한 세 가지 이상 현상 목록만으로는 설명되지 않는 동시성 문제가 `REPEATABLE READ` 격리 수준에서 나타난다. MVCC 환경의 `REPEATABLE READ`는 동일 트랜잭션 내에서 같은 `SELECT` 쿼리를 반복 실행했을 때 처음 읽었던 시점과 동일한 스냅샷 결과를 반환한다는 읽기 일관성만 보장한다. 즉 스냅샷 읽기가 보장하는 반복 가능성은 과거 상태의 고정된 조회를 의미할 뿐, 읽은 값을 기반으로 수행하는 후속 쓰기 연산의 직렬 가능성(Serializability)까지 보장하지 않는다.

<!-- sec:body-2 -->
## Undo 로그 버전 체인과 Read View의 가시성 판단 규칙

InnoDB 스토리지 엔진은 잠금 없이 일관된 스냅샷 데이터를 제공하기 위해 클러스터드 인덱스의 레코드 구조와 Undo 로그를 결합한 **비잠금 일관성 읽기(Consistent Nonlocking Read)**를 수행한다. 클러스터드 인덱스의 모든 물리적 레코드에는 사용자가 정의한 컬럼 외에 스토리지 엔진이 내부적으로 관리하는 두 가지 숨김 필드가 포함된다. 행을 마지막으로 삽입하거나 수정한 트랜잭션의 고유 식별자를 기록하는 `DB_TRX_ID`와, 변경 이전 버전의 데이터가 기록된 Undo 로그 레코드의 주소를 가리키는 롤백 포인터인 `DB_ROLL_PTR`이다. 트랜잭션이 특정 행을 수정할 때마다 엔진은 기존 값을 Undo 로그 영역에 복사하고 새 레코드의 `DB_ROLL_PTR`가 해당 Undo 로그를 가리키도록 연결하여, 최신 버전에서 과거 버전으로 이어지는 단방향 연결 리스트인 버전 체인을 구성한다.

수많은 과거 버전 중 현재 트랜잭션이 어느 버전까지 볼 수 있는지를 결정하기 위해, 엔진은 트랜잭션이 첫 번째 일반 `SELECT`를 실행하는 순간 그 시점의 시스템 활성 트랜잭션 상태를 캡처한 **Read View**를 생성한다. `READ COMMITTED` 격리 수준에서는 매 쿼리 실행 시마다 새로운 Read View를 생성하지만, `REPEATABLE READ` 격리 수준에서는 첫 조회 시 생성한 Read View를 트랜잭션이 종료될 때까지 재사용한다.

Read View는 버전 가시성을 판별하기 위해 네 가지 상태 변수를 유지한다. Read View가 생성되는 시점에 시스템에서 시작되었으나 아직 커밋되지 않은 활성 트랜잭션 ID들의 목록인 `m_ids`, 해당 목록에서 가장 작은 트랜잭션 ID를 나타내는 저수위선(Low Watermark)인 `m_up_limit_id`, Read View 생성 시점에 시스템이 다음 신규 트랜잭션에 할당할 예정인 ID를 나타내는 고수위선(High Watermark)인 `m_low_limit_id`, 그리고 현재 Read View를 생성한 본인 트랜잭션의 ID인 `m_creator_trx_id`다.

스토리지 엔진이 버퍼 풀의 데이터 페이지에서 특정 행을 읽을 때는 해당 레코드에 기록된 `DB_TRX_ID`를 Read View의 네 변수와 순서대로 대조하여 가시성을 판단한다. 가장 먼저 `DB_TRX_ID`가 `m_creator_trx_id`와 일치하는지 확인하며, 두 값이 같다면 현재 트랜잭션 자신이 수정한 데이터이므로 최신 버전을 즉시 읽는다. 자신이 수정한 행이 아니라면 `DB_TRX_ID < m_up_limit_id` 조건을 검사하며, 이 조건이 참이면 Read View가 생성되기 이전에 이미 커밋을 마친 트랜잭션이 만든 버전이므로 가시성을 인정한다.

반대로 `DB_TRX_ID >= m_low_limit_id`이면 현재 트랜잭션이 스냅샷을 찍은 이후에 시작된 미래의 트랜잭션이 수정한 버전이므로 가시성을 거부한다. 만약 `DB_TRX_ID`가 `m_up_limit_id` 이상이고 `m_low_limit_id` 미만인 구간에 속한다면, 엔진은 `m_ids` 목록 안에 해당 `DB_TRX_ID`가 존재하는지 검색한다. 목록에 포함되어 있다면 스냅샷 생성 시점에 아직 커밋되지 않았던 활성 트랜잭션의 변경분이므로 볼 수 없고, 목록에 없다면 스냅샷 생성 이전에 이미 커밋된 트랜잭션의 변경분이므로 읽을 수 있다. 현재 레코드 버전이 가시성 조건을 통과하지 못하면, 엔진은 가시성 조건을 만족하는 버전을 찾을 때까지 `DB_ROLL_PTR`를 따라 Undo 로그 체인을 역방향으로 순회한다.

<!-- sec:body-3 -->
## 스냅샷 읽기와 현재 읽기가 혼용될 때 발생하는 정합성 불일치

일반 `SELECT`가 Read View에 의존해 과거 시점의 Undo 로그를 읽는 스냅샷 읽기로 동작하는 것과 달리, `SELECT ... FOR UPDATE`나 `SELECT ... FOR SHARE` 같은 잠금 읽기와 `UPDATE`, `DELETE` 같은 데이터 변경 구문은 Read View를 무시하고 클러스터드 인덱스의 가장 최신 커밋 버전을 직접 읽어 배타적 또는 공유 잠금을 획득하는 **현재 읽기(Current Read)**로 동작한다. 데이터 변경 구문이 과거의 스냅샷 버전을 기준으로 수행된다면 이미 커밋된 다른 트랜잭션의 최신 레코드와 Undo 로그 체인의 연결 고리가 끊어져 스토리지 엔진의 버전 무결성 자체가 파괴되기 때문에, 쓰기 연산과 잠금 읽기는 반드시 최신 커밋 버전을 참조해야만 한다.

단일 트랜잭션 안에서 스냅샷 읽기와 현재 읽기를 혼용하면 애플리케이션이 인지하는 상태와 스토리지 엔진이 실제로 수정하는 상태가 분리된다. 초기 잔액(`balance`)이 `1000`인 계좌(`id = 1`)에 대해 두 트랜잭션 `T1(trx_id = 100)`과 `T2(trx_id = 101)`가 동시에 차감 로직을 수행하는 타임라인을 통해 이 불일치 과정을 확인할 수 있다.

| 시점 | 트랜잭션 T1 (trx_id = 100) | 트랜잭션 T2 (trx_id = 101) | 클러스터드 인덱스 최신 버전 | Undo 로그 체인 상태 |
| :--- | :--- | :--- | :--- | :--- |
| 1 | `BEGIN;` | `BEGIN;` | `balance=1000, trx_id=90` | 없음 |
| 2 | `SELECT balance WHERE id=1;` (결과: 1000, Read View 생성) | `SELECT balance WHERE id=1;` (결과: 1000, Read View 생성) | `balance=1000, trx_id=90` | 없음 |
| 3 | 앱 메모리 계산: `1000 - 300 = 700` | 앱 메모리 계산: `1000 - 400 = 600` | `balance=1000, trx_id=90` | 없음 |
| 4 | `UPDATE SET balance=700 WHERE id=1;` `COMMIT;` | 대기 없음 | `balance=700, trx_id=100` | `balance=1000, trx_id=90` |
| 5 | 종료됨 | `SELECT balance WHERE id=1;` (결과: 1000) | `balance=700, trx_id=100` | `balance=1000, trx_id=90` |
| 6 | 종료됨 | `UPDATE SET balance=600 WHERE id=1;` (현재 읽기 후 덮어쓰기) | `balance=600, trx_id=101` | `700(trx_id=100)` -> `1000(trx_id=90)` |
| 7 | 종료됨 | `SELECT balance WHERE id=1;` (결과: 600, 본인 trx_id 가시화) | `balance=600, trx_id=101` | `700(trx_id=100)` -> `1000(trx_id=90)` |

시점 2에서 `T1`과 `T2`는 각각 Read View를 생성하고 `balance = 1000`을 읽는다. 이때 `T2`의 Read View에서 `m_ids`에는 활성 상태인 `100`이 기록된다. 시점 4에서 `T1`이 잔액을 `700`으로 수정하고 커밋하면 클러스터드 인덱스의 최신 레코드는 `DB_TRX_ID = 100`이 되지만, 시점 5에서 `T2`가 일반 `SELECT`를 다시 실행하더라도 `100`은 `T2`의 `m_ids` 목록에 포함되어 있으므로 가시성이 거부되어 Undo 로그의 `1000`이 반환된다. 그러나 시점 6에서 `T2`가 애플리케이션 메모리에서 계산한 `600`으로 `UPDATE`를 실행하는 순간, 엔진은 현재 읽기를 수행하여 최신 레코드(`balance = 700`)에 배타적 잠금을 걸고 그 값을 `600`으로 덮어쓴다. 결과적으로 `T1`이 차감한 `300`은 완전히 유실된다.

더 나아가 `T2`가 시점 6에서 상대적 갱신 구문인 `UPDATE accounts SET balance = balance - 400 WHERE id = 1`을 실행했다면 또 다른 이상 현상이 나타난다. 해당 `UPDATE`는 현재 읽기로 동작하므로 `T1`이 커밋한 `700`에서 `400`을 뺀 `300`을 최신 레코드에 기록하고, 레코드의 `DB_TRX_ID`를 `T2` 자신의 ID인 `101`로 갱신한다. 직후 시점 7에서 `T2`가 다시 일반 `SELECT`를 실행하면, Read View 가시성 첫 번째 규칙인 `DB_TRX_ID == m_creator_trx_id` 조건이 참이 되어 Undo 로그를 탐색하지 않고 최신 버전인 `300`을 반환한다. 동일 트랜잭션 내에서 직전 조회까지 `1000`으로 보이던 잔액이 자신의 `UPDATE` 한 번으로 인해 다른 트랜잭션 `T1`의 변경분까지 합산된 `300`으로 급변하여, 반복 읽기 일관성이 트랜잭션 내부에서 깨진다.

스냅샷 읽기의 한계는 단일 행 수정뿐만 아니라 복수의 행에 걸친 불변식을 검증하는 쓰기 스큐 시나리오에서도 동일하게 작용한다. 예를 들어 한 사용자가 보유한 두 계좌(`id = 1`, `id = 2`)의 잔액 합계가 최소 `0` 이상이어야 한다는 비즈니스 규칙이 있을 때, 두 계좌에 각각 `500`씩 총 `1000`이 예치된 상황을 가정한다. `T1`과 `T2`가 동시에 시작되어 두 계좌를 일반 `SELECT`로 조회하면 두 트랜잭션 모두 합계 잔액을 `1000`으로 인식한다. 이어서 `T1`은 `id = 1` 계좌에서 `800`을 차감(`500 -> -300`)하고, `T2`는 `id = 2` 계좌에서 `800`을 차감(`500 -> -300`)한다. 두 트랜잭션은 서로 다른 행을 수정하므로 현재 읽기 단계에서도 배타적 잠금 충돌이 전혀 발생하지 않고 모두 정상 커밋된다. 커밋 완료 후 두 계좌의 합계는 `-600`이 되어 도메인 불변식이 파괴된다.

<!-- sec:body-4 -->
## 갱신 손실과 쓰기 스큐 방지를 위한 잠금 읽기 및 원자적 연산 전환

데이터 정합성 훼손을 막으려면 트랜잭션이 의사결정을 내리는 읽기 시점과 데이터를 변경하는 쓰기 시점 사이의 버전 괴리를 제거해야 한다. 먼저 문제가 발생하는 변경 전 코드는 일반 `SELECT`를 통해 계좌의 존재 여부, 활성 상태(`status == 'ACTIVE'`), 잔액 부족 여부(`balance < amount`)를 검증한 뒤 파이썬 애플리케이션 메모리에서 차감 연산을 수행한다.

<!-- sec:code-1 -->
```python-snippet
class AccountNotFoundError(Exception):
    pass

class InactiveAccountError(Exception):
    pass

class InsufficientBalanceError(Exception):
    pass

def withdraw_snapshot_unsafe(conn, account_id: int, amount: int) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT status, balance FROM accounts WHERE id = %s",
            (account_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise AccountNotFoundError(f"Account {account_id} not found")

        status, balance = row
        if status != "ACTIVE":
            raise InactiveAccountError(f"Account {account_id} is {status}")
        if balance < amount:
            raise InsufficientBalanceError("Insufficient balance")

        new_balance = balance - amount
        cur.execute(
            "UPDATE accounts SET balance = %s WHERE id = %s",
            (new_balance, account_id),
        )
    conn.commit()
    return new_balance
```

위 `withdraw_snapshot_unsafe` 함수는 첫 번째 `SELECT` 실행 시 스냅샷 읽기를 수행하므로 대상 행에 아무런 잠금을 설정하지 않는다. 두 워커 스레드가 동시에 진입하면 두 스레드 모두 과거 Read View를 기준으로 `status == 'ACTIVE'`와 `balance >= amount` 조건을 통과하고, 각자 계산한 `new_balance`를 마지막 `UPDATE` 구문에 전달해 먼저 커밋된 트랜잭션의 결과를 덮어쓴다.

첫 번째 해결 방식은 조회 쿼리에 `FOR UPDATE` 절을 명시하여 읽기 단계부터 현재 읽기를 강제하는 **비관적 잠금(Pessimistic Locking)** 전환이다. 변경 전 코드가 처리하던 계좌 미존재, 비활성 계좌, 잔액 부족 예외 조건을 그대로 유지하면서 조회 시점에 배타적 레코드 잠금을 획득한다.

<!-- sec:code-2 -->
```python-snippet
def withdraw_pessimistic_lock(conn, account_id: int, amount: int) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT status, balance FROM accounts WHERE id = %s FOR UPDATE",
            (account_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise AccountNotFoundError(f"Account {account_id} not found")

        status, balance = row
        if status != "ACTIVE":
            raise InactiveAccountError(f"Account {account_id} is {status}")
        if balance < amount:
            raise InsufficientBalanceError("Insufficient balance")

        new_balance = balance - amount
        cur.execute(
            "UPDATE accounts SET balance = %s WHERE id = %s",
            (new_balance, account_id),
        )
    conn.commit()
    return new_balance
```

`withdraw_pessimistic_lock` 함수가 `SELECT ... FOR UPDATE`를 실행하면 스토리지 엔진은 Read View를 참조하지 않고 클러스터드 인덱스의 최신 커밋 버전을 읽으면서 해당 레코드에 배타적 잠금(X-Lock)을 설정한다. 동시에 같은 계좌에 접근한 다른 트랜잭션의 `SELECT ... FOR UPDATE`는 선행 트랜잭션이 `conn.commit()` 또는 롤백을 호출해 잠금을 해제할 때까지 스토리지 엔진의 잠금 대기 큐에서 블로킹된다. 선행 트랜잭션이 커밋을 완료하면 대기가 해제된 후행 트랜잭션은 선행 트랜잭션이 수정한 최신 `status`와 `balance` 값을 현재 읽기로 가져오므로, 상태 검증과 차감 계산이 완벽하게 직렬화된다. 앞서 살펴본 쓰기 스큐 시나리오에서도 불변식 계산에 참여하는 두 계좌 행을 모두 `WHERE id IN (1, 2) ORDER BY id FOR UPDATE`로 잠그면 동시 차감을 차단할 수 있다.

단일 행에 대한 수치 차감과 상태 검증만 필요한 경우라면, 조회와 수정을 분리하지 않고 단일 `UPDATE` 구문 내부에서 조건 검증과 연산을 동시에 처리하는 **원자적 연산(Atomic Update)**으로 전환할 수 있다. 이때 변경 전 코드가 구분해서 던지던 세 가지 도메인 예외(`AccountNotFoundError`, `InactiveAccountError`, `InsufficientBalanceError`)를 누락하지 않으려면, `UPDATE`의 변경 행 수(`rowcount`)가 `0`일 때 원인을 판별하는 진단 조회를 결합해야 한다.

<!-- sec:code-3 -->
```python-snippet
def withdraw_atomic_update(conn, account_id: int, amount: int) -> int:
    with conn.cursor() as cur:
         affected = cur.execute(
            """
            UPDATE accounts
               SET balance = balance - %s
             WHERE id = %s
               AND status = 'ACTIVE'
               AND balance >= %s
            """,
            (amount, account_id, amount),
        )
        if affected == 0:
            cur.execute(
                "SELECT status, balance FROM accounts WHERE id = %s FOR SHARE",
                (account_id,),
            )
            row = cur.fetchone()
            conn.rollback()
            if row is None:
                raise AccountNotFoundError(f"Account {account_id} not found")
            status, balance = row
            if status != "ACTIVE":
                raise InactiveAccountError(f"Account {account_id} is {status}")
            raise InsufficientBalanceError("Insufficient balance")

        cur.execute(
            "SELECT balance FROM accounts WHERE id = %s",
            (account_id,),
        )
        new_balance = cur.fetchone()[0]
    conn.commit()
    return new_balance
```

`withdraw_atomic_update` 함수에서 첫 번째 `UPDATE` 문은 현재 읽기로 동작하므로, 클러스터드 인덱스의 최신 레코드에 배타적 잠금을 획득한 상태에서 `status = 'ACTIVE'` 및 `balance >= amount` 조건을 평가하고 즉시 차감을 수행한다. 정상 차감된 경우 해당 레코드의 `DB_TRX_ID`가 현재 트랜잭션 ID(`m_creator_trx_id`)로 갱신되므로, 직후 실행하는 일반 `SELECT balance` 쿼리는 추가 잠금 없이도 방금 갱신된 정확한 최신 잔액을 반환한다. 반면 조건을 만족하지 못해 `affected == 0`이 반환된 경우에는 `FOR SHARE`(현재 읽기)로 최신 상태를 조회하여 계좌 미존재, 비활성 상태, 잔액 부족 중 정확한 실패 원인을 식별하고 트랜잭션을 롤백한다.

아래 모듈은 두 개의 동시 작업 스레드가 `threading.Barrier`를 통해 스냅샷 읽기 시점을 동기화했을 때, 변경 전 구현에서 갱신 손실이 재현되고 비관적 잠금 및 원자적 연산 구현에서 정합성이 보존되는 것을 검증하는 전체 테스트 코드다.

<!-- sec:code-4 -->
```python
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable
import pymysql


class AccountNotFoundError(Exception):
    pass


class InactiveAccountError(Exception):
    pass


class InsufficientBalanceError(Exception):
    pass


def create_connection(db_config: dict) -> pymysql.connections.Connection:
    conn = pymysql.connect(**db_config, autocommit=False)
    with conn.cursor() as cur:
        cur.execute("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
    return conn


def setup_table(db_config: dict, initial_balance: int = 1000) -> None:
    conn = create_connection(db_config)
    try:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS accounts")
            cur.execute(
                """
                CREATE TABLE accounts (
                    id INT PRIMARY KEY,
                    status VARCHAR(16) NOT NULL,
                    balance INT NOT NULL
                ) ENGINE=InnoDB
                """
            )
            cur.execute(
                "INSERT INTO accounts (id, status, balance) VALUES (1, 'ACTIVE', %s)",
                (initial_balance,),
            )
        conn.commit()
    finally:
        conn.close()


def run_concurrent_withdrawal_test(
    db_config: dict,
    strategy: Callable[[pymysql.connections.Connection, int, int, threading.Barrier], int],
) -> int:
    setup_table(db_config, initial_balance=1000)
    barrier = threading.Barrier(2)

    def worker(amount: int) -> int:
        conn = create_connection(db_config)
        try:
            return strategy(conn, 1, amount, barrier)
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(worker, 300)
        f2 = executor.submit(worker, 400)
        f1.result()
        f2.result()

    check_conn = create_connection(db_config)
    try:
        with check_conn.cursor() as cur:
            cur.execute("SELECT balance FROM accounts WHERE id = 1")
            final_balance = cur.fetchone()[0]
        return final_balance
    finally:
        check_conn.close()


def synced_snapshot_unsafe(
    conn: pymysql.connections.Connection,
    account_id: int,
    amount: int,
    barrier: threading.Barrier,
) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT status, balance FROM accounts WHERE id = %s", (account_id,))
        status, balance = cur.fetchone()
        barrier.wait()
        if status != "ACTIVE" or balance < amount:
            raise InsufficientBalanceError()
        new_balance = balance - amount
        cur.execute(
            "UPDATE accounts SET balance = %s WHERE id = %s",
            (new_balance, account_id),
        )
    conn.commit()
    return new_balance


def synced_pessimistic_lock(
    conn: pymysql.connections.Connection,
    account_id: int,
    amount: int,
    barrier: threading.Barrier,
) -> int:
    barrier.wait()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT status, balance FROM accounts WHERE id = %s FOR UPDATE",
            (account_id,),
        )
        status, balance = cur.fetchone()
        if status != "ACTIVE" or balance < amount:
            raise InsufficientBalanceError()
        new_balance = balance - amount
        cur.execute(
            "UPDATE accounts SET balance = %s WHERE id = %s",
            (new_balance, account_id),
        )
    conn.commit()
    return new_balance
```

`run_concurrent_withdrawal_test`에 `synced_snapshot_unsafe`를 전달해 실행하면, 두 스레드가 `barrier.wait()` 지점에서 모두 `1000`을 읽은 뒤 순차적으로 `700`과 `600`을 덮어쓰므로 최종 잔액이 정상 값인 `300`이 아니라 `600` 또는 `700`으로 남는다. 반면 `synced_pessimistic_lock`을 전달하면 `SELECT ... FOR UPDATE` 지점에서 두 번째 스레드가 블로킹되었다가 첫 번째 스레드가 커밋한 `700`을 읽은 뒤 `400`을 차감하므로 최종 잔액이 정확히 `300`으로 유지된다.

<!-- sec:tradeoff -->
## 잠금 읽기와 원자적 연산 도입 시의 시스템 비용과 트레이드오프

비관적 잠금(`SELECT ... FOR UPDATE`)은 정합성을 직관적으로 보장하지만 잠금 보유 시간과 잠금 범위 확장이라는 두 가지 시스템 비용을 수반한다. 첫째, 조회 시점에 획득한 배타적 잠금은 트랜잭션이 커밋되거나 롤백될 때까지 해제되지 않는다. 만약 `SELECT ... FOR UPDATE` 이후 커밋이 호출되기 전에 외부 결제 게이트웨이 호출이나 복잡한 파일 I/O가 포함되면, 해당 시간 동안 동일 행에 접근하는 모든 다른 트랜잭션이 대기 상태에 빠져 데이터베이스 커넥션 풀이 급격히 고갈된다.

둘째, InnoDB의 `REPEATABLE READ` 격리 수준에서 `FOR UPDATE`를 실행할 때는 검색 조건에 사용된 인덱스의 특성에 따라 **넥스트 키 잠금(Next-Key Lock)**과 갭 잠금(Gap Lock)이 함께 설정된다. 기본 키(PK)나 고유 인덱스(Unique Index)에 대해 단일 등치(`=`) 조건으로 존재하는 행을 조회할 때만 순수 레코드 잠금(Record Lock)으로 동작한다. 비고유 보조 인덱스를 사용하거나, 범위 조건(`BETWEEN`, `>`, `<`)을 사용하거나, 심지어 고유 인덱스라도 조건에 해당하는 행이 존재하지 않으면 엔진은 유령 읽기(Phantom Read)를 방지하기 위해 인덱스 레코드 사이의 간격에 갭 잠금을 건다. 이로 인해 대상 행의 수정뿐만 아니라 인접한 키 범위에 대한 다른 트랜잭션의 신규 `INSERT`까지 함께 블로킹되어 동시 처리량이 저하된다.

원자적 연산(`UPDATE ... SET col = col - val`)은 애플리케이션과 데이터베이스 간의 왕복(Round-trip) 시간 동안 잠금을 쥐고 있지 않고, 단일 `UPDATE` 구문이 실행되는 시점에만 잠금을 획득하므로 비관적 잠금보다 잠금 보유 시간이 짧다. 그러나 차감 금액이나 변경 상태를 단일 SQL 표현식으로 작성할 수 없는 복잡한 도메인 로직에는 적용할 수 없다. 또한 여러 테이블에 걸친 불변식을 검증해야 하는 쓰기 스큐 시나리오에서는 수정 대상이 아닌 연관 행까지 함께 보호해야 하므로 단일 테이블에 대한 원자적 `UPDATE`만으로는 정합성을 유지할 수 없다.

<!-- sec:conclusion -->
## 워크로드별 동시성 제어 선택 기준과 적용 경계

실제 시스템에서 동시성 제어 메커니즘을 선택할 때는 수정 대상의 범위와 도메인 로직의 복잡도를 기준으로 판단한다. 단일 행의 수치 증감과 고정된 상태 값 검증만 수행하는 고경합 워크로드에서는 잠금 보유 시간을 최소화하는 원자적 연산(Atomic Update)을 기본 전략으로 채택한다. 반면 복수의 행을 읽어 합계나 비율 같은 비즈니스 불변식을 검증한 뒤 데이터를 수정해야 하거나, 조회한 값을 바탕으로 애플리케이션 계층에서 복잡한 계산을 거쳐야 하는 경우에는 `SELECT ... FOR UPDATE`를 통한 비관적 잠금을 적용한다. 이때 비관적 잠금은 반드시 기본 키나 고유 인덱스의 등치 조건으로 잠금 대상을 특정하여 불필요한 갭 잠금 확산을 차단해야 하며, 충돌 빈도가 낮고 읽기 비중이 압도적인 환경이라면 레코드에 버전 컬럼을 두고 `UPDATE ... WHERE id = %s AND version = %s`로 변경 여부를 검증하는 **낙관적 잠금(Optimistic Locking)**으로 블로킹 비용을 회피한다.

본문에서 다룬 잠금 읽기와 원자적 연산 전략은 단일 데이터베이스 인스턴스 내부의 로컬 트랜잭션과 스토리지 엔진의 버퍼 풀 잠금 관리자를 전제로 동작한다. 마이크로서비스 아키텍처에서 분리된 이기종 데이터베이스 간에 걸친 잔액 차감이나 상태 동기화는 분산 데이터베이스 환경의 2PC(Two-Phase Commit) 및 Saga 패턴의 영역이므로 단일 엔진의 MVCC 잠금 메커니즘만으로 해결할 수 없다. 아울러 복수의 행을 비관적 잠금으로 획득할 때 트랜잭션 간 잠금 순서가 엇갈리면 발생하는 교착 상태(Deadlock)를 처리하기 위한 스토리지 엔진 내부의 데드락 탐지 그래프 알고리즘과 엔진별 타임아웃 파라미터 튜닝은 별도의 데이터베이스 운영 설계 범위에 속한다.
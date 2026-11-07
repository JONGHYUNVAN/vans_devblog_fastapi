# REPEATABLE READ에서 정합성이 깨지는 이유: MVCC 스냅샷 읽기와 잠금 읽기의 동작 차이와 갱신 손실 방지

> MVCC 환경의 REPEATABLE READ에서 스냅샷 읽기(Read View)와 잠금 읽기(Current Read)가 혼용될 때 발생하는 갱신 손실의 내부 원인을 분석하고 비관적 잠금 및 원자적 UPDATE 전환 전략을 설명합니다.

<!-- sec:intro -->
## 들어가며: REPEATABLE READ라는 이름이 주는 착시

백엔드 애플리케이션에서 포인트 차감, 재고 예약, 쿠폰 발급처럼 동시 요청이 집중되는 비즈니스 로직을 구현할 때 가장 먼저 점검하는 설정 중 하나가 데이터베이스의 트랜잭션 격리 수준(Isolation Level)이다. MySQL InnoDB 스토리지 엔진의 기본 격리 수준인 `REPEATABLE READ`는 트랜잭션이 시작된 이후부터 종료될 때까지 동일한 쿼리에 대해 항상 일관된 결과를 반환한다고 알려져 있다. 이 정의만 놓고 보면, 하나의 트랜잭션 안에서 잔액을 조회하고 차감 금액을 계산한 뒤 업데이트하는 과정이 다른 트랜잭션의 간섭으로부터 안전하게 보호될 것처럼 보인다.

하지만 실제 운영 환경이나 동시성 테스트 환경에서 `REPEATABLE READ`만 믿고 조회 후 수정(Read-Modify-Write) 로직을 실행하면, 먼저 커밋된 트랜잭션의 변경 사항이 흔적도 없이 덮어씌워지는 **갱신 손실(Lost Update)**이나 비즈니스 제약 조건이 깨지는 **쓰기 스큐(Write Skew)**가 발생한다. 심지어 단일 트랜잭션 내부에서 일반 `SELECT`를 수행하다가 `UPDATE`나 `SELECT ... FOR UPDATE`를 실행한 직후에는, 이전까지 보이지 않던 외부 트랜잭션의 커밋 데이터가 갑자기 현재 트랜잭션의 조회 결과에 섞여 들어오는 현상까지 나타난다.

이러한 현상이 발생하는 근본적인 원인은 MVCC(Multi-Version Concurrency Control) 스토리지 엔진이 읽기 작업을 두 가지 서로 다른 경로로 분리하여 처리하기 때문이다. 일반 `SELECT` 구문은 과거 시점에 생성된 `Read View`와 Undo 로그 버전 체인을 따라가는 **비잠금 스냅샷 읽기(Consistent Nonlocking Read)**로 동작하는 반면, `SELECT ... FOR UPDATE`나 `UPDATE`, `DELETE` 구문은 스냅샷을 무시하고 버퍼 풀의 최신 커밋 페이지를 직접 읽고 잠그는 **잠금 읽기 및 현재 읽기(Locking Read / Current Read)**로 동작한다. 본 글에서는 단일 트랜잭션 내에서 스냅샷 읽기와 현재 읽기가 혼용될 때 발생하는 데이터 정합성 불일치 메커니즘을 Undo 로그와 `Read View` 데이터 구조 수준에서 해부하고, 갱신 손실을 원천 차단하기 위한 비관적 잠금 및 원자적 연산(Atomic Update) 전환 전략을 변경 전후 코드로 검증한다.

<!-- sec:body-1 -->
## 1. ANSI SQL 표준 격리 수준 정의와 실제 MVCC 스토리지 엔진 구현의 괴리

트랜잭션 격리 수준을 둘러싼 혼란은 1992년에 제정된 ANSI/ISO SQL-92 표준 문서의 정의 방식에서 출발한다. ANSI SQL 표준은 격리 수준을 정의할 때 데이터 구조나 잠금 메커니즘을 직접 명시하는 대신, 동시성 환경에서 발생할 수 있는 세 가지 이상 현상(Phenomena)인 **Dirty Read(커밋되지 않은 데이터 읽기)**, **Non-Repeatable Read(반복 불가능한 읽기)**, **Phantom Read(유령 읽기)** 중 무엇을 허용하고 무엇을 방지하는지를 기준으로 4단계(`READ UNCOMMITTED`, `READ COMMITTED`, `REPEATABLE READ`, `SERIALIZABLE`)를 분류했다.

문제는 ANSI SQL-92 표준이 작성될 당시의 암묵적인 전제가 **2단계 잠금(Two-Phase Locking, 2PL)** 기반의 동시성 제어였다는 점이다. 엄격한 2PL 환경에서 `REPEATABLE READ`를 구현하려면, 트랜잭션이 데이터를 읽을 때 해당 레코드에 공유 잠금(Shared Lock, `S-Lock`)을 획득하고 이를 트랜잭션이 끝날 때까지 유지해야 한다. 다른 트랜잭션이 같은 레코드를 수정하려면 배타적 잠금(Exclusive Lock, `X-Lock`)이 필요한데, 선행 트랜잭션이 `S-Lock`을 쥐고 있으므로 후행 트랜잭션의 쓰기 작업은 선행 트랜잭션이 커밋될 때까지 물리적으로 대기(Block)한다. 즉, 잠금 기반의 `REPEATABLE READ`에서는 데이터를 읽는 행위 자체가 다른 트랜잭션의 수정을 막는 방어막 역할을 수행하므로, 두 트랜잭션이 동시에 같은 값을 읽고 각자 수정하여 덮어쓰는 갱신 손실이 원천적으로 발생할 수 없다.

그러나 현대적인 관계형 데이터베이스 스토리지 엔진(MySQL InnoDB, PostgreSQL 등)은 읽기 작업이 쓰기 작업을 블로킹하여 처리량이 급락하는 문제를 해결하기 위해 2PL 대신 **MVCC**를 도입했다. 1995년 Hal Berenson 등이 발표한 논문 *A Critique of ANSI SQL Isolation Levels*에서 지적했듯, 다중 버전을 관리하는 MVCC 아키텍처에서는 ANSI SQL의 3가지 이상 현상만으로는 데이터 정합성 위배를 온전히 설명할 수 없다. MVCC 환경의 `REPEATABLE READ`는 읽은 레코드에 `S-Lock`을 걸어 다른 트랜잭션의 접근을 차단하는 대신, 트랜잭션 시작 시점의 데이터 스냅샷을 찍어 과거 버전을 읽게 함으로써 Non-Repeatable Read를 방지한다.

| 구분 항목 | 2PL 기반 REPEATABLE READ (고전적 모델) | MVCC 기반 REPEATABLE READ (MySQL InnoDB) |
| :--- | :--- | :--- |
| 일반 `SELECT` 동작 | 대상 레코드에 `S-Lock` 획득 (트랜잭션 종료 시 해제) | 잠금 없이 `Read View`와 Undo 로그를 통해 스냅샷 읽기 |
| 읽기-쓰기 경합 | 읽기가 진행 중이면 다른 트랜잭션의 `UPDATE` 대기 | 읽기는 과거 버전을, 쓰기는 최신 버전을 다루므로 상호 비차단 |
| `UPDATE` 참조 버전 | 자신이 `S-Lock`으로 보호한 최신 버전 ( `X-Lock` 승격) | 스냅샷과 무관하게 버퍼 풀의 **최신 커밋 버전(Current Read)** |
| 갱신 손실(Lost Update) | `S-Lock` 충돌(또는 데드락)로 인해 덮어쓰기 원천 차단 | 스냅샷 읽기와 현재 쓰기 사이의 간극으로 인해 **갱신 손실 발생** |

표에서 확인할 수 있듯, InnoDB의 `REPEATABLE READ`에서 일반 `SELECT`는 아무런 잠금도 획득하지 않는다. 따라서 두 트랜잭션 T1과 T2는 동일한 레코드의 초기 버전을 동시에 읽을 수 있다. 더 결정적인 차이는 읽기 경로의 이원화에 있다. InnoDB는 일반 `SELECT`를 수행할 때는 **스냅샷 읽기(Snapshot Read)**를 사용하지만, `SELECT ... FOR UPDATE`, `SELECT ... FOR SHARE`, `UPDATE`, `DELETE`를 수행할 때는 **현재 읽기(Current Read)**를 사용한다. 스냅샷 읽기는 과거 시점에 고정된 데이터를 보여주는 반면, 현재 읽기는 다른 트랜잭션이 방금 커밋한 가장 최신 데이터를 찾아 잠금을 걸고 읽어 들인다. 이 두 가지 읽기 메커니즘이 하나의 트랜잭션 안에서 만날 때 개발자가 예상하지 못한 정합성 파괴가 시작된다.

<!-- sec:body-2 -->
## 2. Undo 로그 기반 비잠금 스냅샷 읽기(Consistent Nonlocking Read)의 가시성 판단 규칙

단일 트랜잭션 내에서 왜 과거 데이터와 최신 데이터가 뒤섞이는지 이해하려면, 먼저 InnoDB가 어떤 데이터 구조를 통해 스냅샷 읽기를 구현하는지 정확히 짚어야 한다. InnoDB의 클러스터드 인덱스(Clustered Index) 레코드에는 사용자가 정의한 컬럼 외에도 스토리지 엔진이 내부적으로 관리하는 숨겨진 시스템 컬럼이 존재한다.

첫째는 `DB_TRX_ID`(6바이트)로, 해당 레코드를 마지막으로 삽입하거나 수정한 트랜잭션의 고유 식별자다. 둘째는 `DB_ROLL_PTR`(7바이트)로, 레코드가 수정되기 직전의 이전 버전 데이터가 기록된 Undo 로그 레코드를 가리키는 롤백 포인터다. 어떤 레코드가 여러 트랜잭션에 의해 순차적으로 `UPDATE`되면, 버퍼 풀의 데이터 페이지에는 가장 마지막에 수정된 최신 레코드만 위치하고, 그 이전 버전들은 `DB_ROLL_PTR` 포인터를 통해 단방향 연결 리스트(Singly Linked List) 형태의 **Undo 버전 체인**을 구성한다.

스냅샷 읽기가 실행될 때, 스토리지 엔진은 이 Undo 버전 체인의 수많은 버전 중 현재 트랜잭션에게 보여줄 수 있는 버전이 무엇인지 판별하기 위해 **`Read View`**라는 데이터 구조를 생성한다. `READ COMMITTED` 격리 수준에서는 매 `SELECT` 구문이 실행될 때마다 새로운 `Read View`를 생성하지만, `REPEATABLE READ` 격리 수준에서는 트랜잭션 내에서 **첫 번째 스냅샷 읽기가 실행되는 시점에 단 한 번 `Read View`를 생성**하고 트랜잭션이 종료될 때까지 이를 재사용한다. `Read View`는 생성 시점의 시스템 트랜잭션 상태를 담는 네 가지 핵심 필드로 구성된다.

1. `m_creator_trx_id`: 현재 `Read View`를 생성한 트랜잭션 자신의 ID다. (단, 아직 쓰기 작업을 수행하지 않은 읽기 전용 트랜잭션은 0으로 설정된다.)
2. `m_ids`: `Read View`가 생성되는 바로 그 순간, 시스템에서 시작되었으나 아직 커밋되지 않은 활성(Active) 읽기/쓰기 트랜잭션 ID들의 목록이다.
3. `m_up_limit_id`: `m_ids` 목록에 포함된 트랜잭션 ID 중 가장 작은 값(Low-Water Mark)이다. 어떤 레코드의 `DB_TRX_ID`가 이 값보다 작다면, 해당 트랜잭션은 `Read View`가 만들어지기 전에 이미 커밋이 완료되었음을 의미한다.
4. `m_low_limit_id`: `Read View`가 생성되는 시점에 시스템이 다음에 할당할 예정인 트랜잭션 ID(High-Water Mark, 즉 현재까지 할당된 최대 트랜잭션 ID + 1)다. 어떤 레코드의 `DB_TRX_ID`가 이 값 이상이라면, 해당 트랜잭션은 스냅샷이 찍힌 이후에 시작된 미래의 트랜잭션임을 의미한다.

InnoDB는 클러스터드 인덱스의 최신 레코드부터 시작하여 해당 레코드의 `DB_TRX_ID`를 `Read View`의 네 가지 필드와 대조한다. 만약 현재 버전이 가시성 조건을 만족하지 못하면, `DB_ROLL_PTR`를 따라 한 단계 이전의 Undo 로그 레코드로 이동하여 가시성 검사를 반복한다. 이 가시성 판단 알고리즘과 Undo 체인 탐색 과정을 파이썬 코드로 모델링하면 다음과 같다.

<!-- sec:code-1 -->
```python
from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class RowVersion:
    balance: int
    db_trx_id: int
    db_roll_ptr: Optional["RowVersion"] = None


@dataclass
class ReadView:
    m_creator_trx_id: int
    m_ids: List[int] = field(default_factory=list)
    m_up_limit_id: int = 0
    m_low_limit_id: int = 0

    def __post_init__(self) -> None:
        if self.m_ids:
            self.m_up_limit_id = min(self.m_ids)
        else:
            self.m_up_limit_id = self.m_low_limit_id

    def is_visible(self, record_trx_id: int) -> bool:
        # 규칙 1: 현재 트랜잭션 자신이 수정한 레코드는 무조건 가시적이다.
        if record_trx_id == self.m_creator_trx_id:
            return True

        # 규칙 2: Read View 생성 시점의 최소 활성 ID보다 작다면 이미 커밋된 버전이므로 가시적이다.
        if record_trx_id < self.m_up_limit_id:
            return True

        # 규칙 3: Read View 생성 이후에 시작된 트랜잭션이 수정한 버전은 볼 수 없다.
        if record_trx_id >= self.m_low_limit_id:
            return False

        # 규칙 4: m_up_limit_id 이상이고 m_low_limit_id 미만인 경우,
        # 스냅샷 생성 당시 미커밋 활성 목록(m_ids)에 있었다면 볼 수 없고, 없었다면 커밋된 것이므로 볼 수 있다.
        return record_trx_id not in self.m_ids


def consistent_snapshot_read(latest_version: RowVersion, read_view: ReadView) -> Optional[RowVersion]:
    current: Optional[RowVersion] = latest_version
    while current is not None:
        if read_view.is_visible(current.db_trx_id):
            return current
        current = current.db_roll_ptr
    return None


if __name__ == "__main__":
    # 초기 상태: trx_id=90이 생성하고 커밋한 잔액 10,000원 레코드
    v1 = RowVersion(balance=10000, db_trx_id=90, db_roll_ptr=None)

    # 트랜잭션 A(trx_id=100)가 Read View를 생성 (당시 활성 트랜잭션: 100, 101 / 다음 할당 ID: 102)
    tx_a_view = ReadView(m_creator_trx_id=100, m_ids=[100, 101], m_low_limit_id=102)

    # 트랜잭션 B(trx_id=101)가 잔액을 7,000원으로 차감하고 커밋함 -> 최신 버전 v2 생성
    v2 = RowVersion(balance=7000, db_trx_id=101, db_roll_ptr=v1)

    # 트랜잭션 A가 스냅샷 읽기를 수행하면 v2(trx_id=101)는 m_ids에 속해 불가시하므로 v1(10,000원)을 반환
    visible_to_a = consistent_snapshot_read(latest_version=v2, read_view=tx_a_view)
    assert visible_to_a is not None and visible_to_a.balance == 10000

    # 만약 트랜잭션 A(trx_id=100)가 직접 UPDATE를 수행해 최신 버전 v3가 만들어지면,
    # 규칙 1(record_trx_id == m_creator_trx_id)에 의해 v3가 즉시 가시화됨
    v3 = RowVersion(balance=5000, db_trx_id=100, db_roll_ptr=v2)
    visible_after_self_update = consistent_snapshot_read(latest_version=v3, read_view=tx_a_view)
    assert visible_after_self_update is not None and visible_after_self_update.balance == 5000
    print("Snapshot visibility simulation passed.")
```

위 코드의 `is_visible` 메서드가 보여주는 네 가지 규칙 중 특히 주목해야 할 대목은 **규칙 1(`record_trx_id == self.m_creator_trx_id`)**과 **규칙 4(`record_trx_id not in self.m_ids`)**의 상호작용이다. 트랜잭션 B(`trx_id = 101`)가 잔액을 `7000`원으로 변경하고 커밋을 완료했더라도, 트랜잭션 A(`trx_id = 100`)의 `Read View`에는 여전히 `101`이 `m_ids`에 남아 있으므로 트랜잭션 A의 일반 `SELECT`는 `v2`를 건너뛰고 Undo 로그의 `v1`(`10000`원)을 읽는다.

하지만 트랜잭션 A가 이후에 해당 레코드에 대해 `UPDATE`를 수행하면 상황이 급변한다. `UPDATE`는 최신 버전인 `v2`를 기반으로 값을 수정한 뒤, 레코드의 `DB_TRX_ID`를 자신의 ID인 `100`으로 덮어쓴다(`v3`). 그 직후 트랜잭션 A가 다시 일반 `SELECT`를 실행하면, `Read View`는 최신 레코드 `v3`의 `DB_TRX_ID`가 `m_creator_trx_id`(`100`)와 일치함을 확인하고 규칙 1에 따라 `v3`를 즉시 반환한다. 즉, 자신이 수행한 `UPDATE`가 징검다리 역할을 하여 트랜잭션 B의 커밋 내역이 반영된 최신 버전을 현재 트랜잭션의 스냅샷 가시성 안으로 끌어들이는 것이다.

<!-- sec:body-3 -->
## 3. 잠금 읽기(SELECT FOR UPDATE)와 UPDATE 구문이 현재 버전(Current Read)을 참조할 때 발생하는 정합성 불일치 시나리오

그렇다면 왜 `UPDATE`나 `SELECT ... FOR UPDATE`는 트랜잭션이 보유한 `Read View`를 사용하지 않고 무조건 최신 커밋 버전(Current Read)을 읽도록 설계되었을까? 이유는 명확하다. 데이터베이스에서 쓰기 작업은 과거의 불변(Immutable) 기록인 Undo 로그에 적용할 수 없으며, 반드시 버퍼 풀의 B+Tree 데이터 페이지에 존재하는 **유일한 최신 레코드** 위에 적용되어야 하기 때문이다. 만약 두 트랜잭션이 각자의 과거 스냅샷 버전에 대고 쓰기를 수행한다면 버전 체인이 분기(Branching)되어 직렬화 가능한 히스토리를 유지할 수 없게 된다. 따라서 InnoDB에서 데이터를 변경하는 모든 구문(`UPDATE`, `DELETE`)과 잠금 읽기 구문(`SELECT ... FOR UPDATE`, `SELECT ... FOR SHARE`)은 `Read View`를 우회하여 인덱스 페이지의 최신 레코드를 읽고 해당 레코드에 배타적 잠금(`X-Lock`) 또는 공유 잠금(`S-Lock`)을 획득하는 **현재 읽기(Current Read)**를 수행한다.

문제는 애플리케이션 개발자가 단일 트랜잭션 안에서 **스냅샷 읽기(`SELECT`)로 읽어 온 값을 기준으로 비즈니스 로직을 계산한 뒤, 현재 읽기(`UPDATE`)로 결과를 반영**할 때 발생한다. 구체적인 타임라인을 통해 두 가지 대표적인 정합성 파괴 시나리오를 확인해 보자.

| 시점 | 트랜잭션 A (`trx_id = 100`, 2,000원 차감 요청) | 트랜잭션 B (`trx_id = 101`, 3,000원 차감 요청) | 버퍼 풀 최신 레코드 (`balance`, `DB_TRX_ID`) |
| :--- | :--- | :--- | :--- |
| T1 | `BEGIN;` | `BEGIN;` | `(10000, 90)` |
| T2 | `SELECT balance FROM wallets WHERE id = 1;` -> **스냅샷 읽기**: `Read View` 생성, `10000` 반환 | `SELECT balance FROM wallets WHERE id = 1;` -> **스냅샷 읽기**: `Read View` 생성, `10000` 반환 | `(10000, 90)` |
| T3 | 애플리케이션 메모리에서 `10000 - 2000 = 8000` 계산 중 | 애플리케이션 메모리에서 `10000 - 3000 = 7000` 계산 후 `UPDATE wallets SET balance = 7000 WHERE id = 1;` (**현재 읽기** 및 `X-Lock` 획득) | `(7000, 101)` (Undo: `10000, 90`) |
| T4 | (아직 `UPDATE` 실행 전, 일반 `SELECT` 수행 시 여전히 `10000` 조회됨) | `COMMIT;` (`X-Lock` 해제) | `(7000, 101)` (커밋 완료) |
| T5 | `UPDATE wallets SET balance = 8000 WHERE id = 1;` -> **현재 읽기**로 `(7000, 101)`을 찾은 뒤 `8000`으로 덮어씀 | 종료 | `(8000, 100)` (Undo: `7000, 101`) |
| T6 | `COMMIT;` | 종료 | 최종 잔액 `8000` (**트랜잭션 B의 3,000원 차감 증발**) |

첫 번째 시나리오는 위 표에 나타난 **Read-Modify-Write 갱신 손실(Lost Update)**이다. T2 시점에 트랜잭션 A와 B는 모두 스냅샷 읽기를 수행했으므로 서로 아무런 잠금 충돌 없이 `10000`원을 읽어 간다. T3~T4 시점에 트랜잭션 B가 먼저 `7000`원으로 업데이트하고 커밋했지만, T5 시점에 트랜잭션 A는 자신이 T2에서 읽었던 스냅샷 값(`10000`)에서 `2000`원을 뺀 `8000`원을 `SET balance = 8000` 형태로 밀어 넣는다. 이때 `UPDATE`는 현재 읽기로 동작하므로 버퍼 풀의 최신 레코드인 `(7000, 101)`을 찾아 `X-Lock`을 걸고 그 위에 `8000`을 덮어써 버린다. 결과적으로 총 `5000`원이 차감되어 `5000`원이 남아야 할 지갑에 `8000`원이 남게 된다.

두 번째 시나리오는 **쓰기 스큐(Write Skew)**와 **가시성 역전(Visibility Jump)**이다. 만약 T5 시점에 트랜잭션 A가 절대값 대입(`SET balance = 8000`) 대신 상대값 연산(`SET balance = balance - 2000`)을 실행했다면 어떻게 될까? `UPDATE` 구문은 현재 읽기를 수행하므로 스냅샷 값인 `10000`이 아니라 최신 커밋 값인 `7000`에서 `2000`을 차감하여 잔액을 `5000`원으로 만들고 `DB_TRX_ID`를 `100`으로 갱신한다. 만약 T2 시점의 초기 잔액이 `4000`원이었다면, 트랜잭션 A는 T2 스냅샷 읽기에서 `4000 >= 2000` 조건을 통과했더라도 T5의 현재 읽기 기반 `UPDATE`에서는 `4000 - 3000 - 2000 = -1000`원이 되어 음수 잔액 방지 제약 조건(불변식)이 깨지게 된다. 게다가 이 `UPDATE` 직후 트랜잭션 A가 일반 `SELECT`를 다시 실행하면, 앞서 살펴본 `Read View`의 규칙 1(`DB_TRX_ID == m_creator_trx_id`)이 발동하여 직전까지 `4000`원으로 보이던 잔액이 갑자기 `-1000`원으로 튀어 오르는 가시성 역전이 발생한다.

이러한 정합성 불일치가 실제 백엔드 애플리케이션 코드에서 어떻게 발생하는지 확인하기 위해, 지갑 상태 검증(`is_active`), 잔액 부족 검사(`InsufficientBalanceError`), 원장(Ledger) 기록을 포함한 변경 전(Before) 코드를 살펴보자.

<!-- sec:code-2 -->
```python
from dataclasses import dataclass
from typing import Protocol, Optional, Tuple, Any


class WalletNotFoundError(Exception):
    pass


class InactiveWalletError(Exception):
    pass


class InsufficientBalanceError(Exception):
    pass


class CursorProtocol(Protocol):
    rowcount: int

    def execute(self, query: str, params: Tuple[Any, ...]) -> None:
        ...

    def fetchone(self) -> Optional[Tuple[Any, ...]]:
        ...


class ConnectionProtocol(Protocol):
    def cursor(self) -> CursorProtocol:
        ...

    def commit(self) -> None:
        ...

    def rollback(self) -> None:
        ...


@dataclass
class WithdrawalResult:
    wallet_id: int
    deducted_amount: int
    remaining_balance: int


def withdraw_vulnerable_snapshot_read(
    conn: ConnectionProtocol,
    wallet_id: int,
    amount: int,
) -> WithdrawalResult:
    if amount <= 0:
        raise ValueError("차감 금액은 0보다 커야 합니다.")

    cursor = conn.cursor()
    try:
        # [문제 지점 1] 일반 SELECT는 비잠금 스냅샷 읽기(Consistent Nonlocking Read)로 동작한다.
        # 동시 실행 중인 다른 트랜잭션의 접근을 전혀 차단하지 않으며, 과거 Read View 시점의 값을 읽는다.
        cursor.execute(
            "SELECT balance, is_active FROM wallets WHERE id = %s",
            (wallet_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise WalletNotFoundError(f"지갑(id={wallet_id})이 존재하지 않습니다.")

        snapshot_balance, is_active = int(row[0]), bool(row[1])
        if not is_active:
            raise InactiveWalletError(f"지갑(id={wallet_id})이 비활성 상태입니다.")

        if snapshot_balance < amount:
            raise InsufficientBalanceError(
                f"잔액이 부족합니다. (현재 잔액: {snapshot_balance}, 요청 금액: {amount})"
            )

        # [문제 지점 2] 스냅샷 읽기로 가져온 과거 값을 기준으로 애플리케이션 메모리에서 차감 결과를 계산한다.
        new_balance = snapshot_balance - amount

        # [문제 지점 3] UPDATE는 현재 읽기(Current Read)로 동작하여 최신 버전에 X-Lock을 걸고
        # 앞서 다른 트랜잭션이 커밋한 변경 사항 위에 스냅샷 기반 계산값(new_balance)을 그대로 덮어쓴다.
        cursor.execute(
            "UPDATE wallets SET balance = %s WHERE id = %s",
            (new_balance, wallet_id),
        )
        cursor.execute(
            "INSERT INTO wallet_ledgers (wallet_id, amount, balance_after) VALUES (%s, %s, %s)",
            (wallet_id, -amount, new_balance),
        )
        conn.commit()
        return WithdrawalResult(
            wallet_id=wallet_id,
            deducted_amount=amount,
            remaining_balance=new_balance,
        )
    except Exception:
        conn.rollback()
        raise
```

위 `withdraw_vulnerable_snapshot_read` 함수는 격리 수준이 `REPEATABLE READ`로 설정되어 있더라도 동시 요청 앞에서 완전히 무방비 상태다. 첫 번째 `SELECT`가 잠금을 걸지 않는 스냅샷 읽기이므로 두 개의 워커 스레드가 동시에 `snapshot_balance`를 동일하게 읽어 들이고, `is_active` 검사와 `snapshot_balance < amount` 검사를 모두 통과한다. 이후 실행되는 `UPDATE`는 현재 읽기로 최신 레코드를 덮어쓰며, `wallet_ledgers` 테이블에는 잘못 계산된 `new_balance`가 기록되어 원장 데이터와 실제 차감 내역 간의 정합성마저 영구적으로 훼손된다.

<!-- sec:body-4 -->
## 4. 갱신 손실 방지를 위한 비관적 잠금 및 원자적 연산(Atomic Update) 전환 전후 쿼리 비교

`REPEATABLE READ` 환경에서 스냅샷 읽기와 현재 읽기의 불일치로 인한 정합성 깨짐을 해결하려면, 트랜잭션 내에서 데이터를 읽는 시점과 쓰는 시점의 참조 버전을 **현재 읽기(Current Read)**로 통일해야 한다. 이를 구현하는 핵심 아키텍처 전환 방법은 **비관적 잠금(`SELECT ... FOR UPDATE`)**을 사용하는 방식과 **조건부 원자적 연산(`UPDATE ... WHERE`)**으로 병합하는 방식 두 가지다.

### 전환 전략 1: 비관적 잠금(SELECT ... FOR UPDATE)을 통한 초기 현재 읽기 강제

가장 직관적인 해결책은 조회 쿼리에 `FOR UPDATE` 절을 추가하여 트랜잭션의 첫 번째 읽기 자체를 스냅샷 읽기가 아닌 **잠금 읽기(Current Read)**로 전환하는 것이다. `SELECT balance, is_active FROM wallets WHERE id = %s FOR UPDATE`가 실행되면, InnoDB는 `Read View`를 참조하지 않고 클러스터드 인덱스의 최신 커밋 레코드를 찾아 배타적 레코드 잠금(`X-Lock`)을 획득한다.

만약 트랜잭션 A가 `id = 1` 레코드에 대해 `SELECT ... FOR UPDATE`를 수행하여 `X-Lock`을 획득한 상태에서 트랜잭션 B가 동일한 쿼리를 실행하면, 트랜잭션 B는 스냅샷을 읽고 지나가는 대신 트랜잭션 A가 `COMMIT` 또는 `ROLLBACK`을 통해 잠금을 해제할 때까지 잠금 대기 큐(Lock Wait Queue)에서 블로킹된다. 트랜잭션 A가 차감된 금액을 업데이트하고 커밋을 완료하면, 그제야 잠금을 획득한 트랜잭션 B는 현재 읽기를 통해 트랜잭션 A가 방금 커밋한 최신 `balance`와 `is_active` 값을 읽어 들인다. 이로써 조회부터 검증, 계산, 수정, 원장 기록에 이르는 전체 과정이 직렬화(Serialize)되어 갱신 손실과 쓰기 스큐가 완벽히 차단된다.

### 전환 전략 2: 조건부 원자적 연산(Atomic Conditional Update)과 예외 세분화 보존

비즈니스 로직이 단일 테이블 레코드의 상대적 증감(`balance = balance - amount`)으로 표현될 수 있다면, 애플리케이션 메모리로 값을 가져와 계산하는 과정 자체를 제거하고 `UPDATE` 구문 내부에 상태 변경과 검증 조건을 통합하는 **조건부 원자적 연산**이 잠금 유지 시간을 최소화하는 데 유리하다.

구체적으로 `UPDATE wallets SET balance = balance - %s WHERE id = %s AND is_active = 1 AND balance >= %s` 쿼리를 실행하면, 스토리지 엔진은 `UPDATE`를 수행하는 바로 그 순간에 현재 읽기로 최신 레코드의 `X-Lock`을 획득하고, 최신 커밋 버전의 `is_active`와 `balance`가 `WHERE` 조건을 만족하는지 원자적으로 평가한 뒤 값을 차감한다.

이때 주의해야 할 점은 변경 전 코드(`withdraw_vulnerable_snapshot_read`)가 처리하던 **비즈니스 예외의 구체성(Constraint Continuity)**을 누락 없이 유지해야 한다는 것이다. 변경 전 코드는 지갑 미존재(`WalletNotFoundError`), 비활성 지갑(`InactiveWalletError`), 잔액 부족(`InsufficientBalanceError`)을 명확히 구분하여 호출자에게 전달했고, 차감 후 잔액(`remaining_balance`)을 원장(`wallet_ledgers`)에 기록했다. 반면 조건부 원자적 `UPDATE`는 조건이 하나라도 맞지 않으면 단순히 영향을 받은 행의 수(`cursor.rowcount`)를 `0`으로 반환한다.

따라서 `cursor.rowcount == 0`이 반환된 실패 경로에서는 현재 트랜잭션 내에서 조회 쿼리를 실행하여 지갑이 없는 것인지, 비활성 상태인지, 아니면 잔액이 부족한 것인지 원인을 판별해 동일한 도메인 예외를 발생시켜야 한다. 반대로 `cursor.rowcount == 1`이 반환된 성공 경로에서는, 현재 트랜잭션이 방금 해당 레코드를 직접 수정하여 레코드의 `DB_TRX_ID`가 현재 트랜잭션의 `m_creator_trx_id`와 같아졌으므로(앞서 살펴본 `Read View` 가시성 규칙 1), 이어서 `SELECT balance FROM wallets WHERE id = %s`를 수행하더라도 과거 스냅샷이 아닌 방금 자신이 차감한 정확한 최신 잔액을 읽어 원장에 기록할 수 있다.

아래 코드는 두 가지 전환 전략을 모두 구현하고, 멀티스레드 동시성 시뮬레이터를 통해 변경 전 코드의 갱신 손실 발생 여부와 변경 후 코드의 정합성 보장 결과를 비교 검증하는 완전한 모듈이다.

<!-- sec:code-3 -->
```python
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any


class WalletNotFoundError(Exception):
    pass


class InactiveWalletError(Exception):
    pass


class InsufficientBalanceError(Exception):
    pass


@dataclass
class WithdrawalResult:
    wallet_id: int
    deducted_amount: int
    remaining_balance: int


# 1. 비관적 잠금(SELECT ... FOR UPDATE) 기반 전환 구현
def withdraw_with_pessimistic_lock(
    conn: Any,
    wallet_id: int,
    amount: int,
) -> WithdrawalResult:
    if amount <= 0:
        raise ValueError("차감 금액은 0보다 커야 합니다.")

    cursor = conn.cursor()
    try:
        # [해결 1] FOR UPDATE를 통해 첫 읽기부터 Current Read를 수행하고 레코드에 X-Lock을 획득한다.
        cursor.execute(
            "SELECT balance, is_active FROM wallets WHERE id = %s FOR UPDATE",
            (wallet_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise WalletNotFoundError(f"지갑(id={wallet_id})이 존재하지 않습니다.")

        current_balance, is_active = int(row[0]), bool(row[1])
        if not is_active:
            raise InactiveWalletError(f"지갑(id={wallet_id})이 비활성 상태입니다.")

        if current_balance < amount:
            raise InsufficientBalanceError(
                f"잔액이 부족합니다. (현재 잔액: {current_balance}, 요청 금액: {amount})"
            )

        new_balance = current_balance - amount
        cursor.execute(
            "UPDATE wallets SET balance = %s WHERE id = %s",
            (new_balance, wallet_id),
        )
        cursor.execute(
            "INSERT INTO wallet_ledgers (wallet_id, amount, balance_after) VALUES (%s, %s, %s)",
            (wallet_id, -amount, new_balance),
        )
        conn.commit()
        return WithdrawalResult(
            wallet_id=wallet_id,
            deducted_amount=amount,
            remaining_balance=new_balance,
        )
    except Exception:
        conn.rollback()
        raise


# 2. 조건부 원자적 연산(Atomic Conditional Update) 기반 전환 구현
def withdraw_with_atomic_update(
    conn: Any,
    wallet_id: int,
    amount: int,
) -> WithdrawalResult:
    if amount <= 0:
        raise ValueError("차감 금액은 0보다 커야 합니다.")

    cursor = conn.cursor()
    try:
        # [해결 2] 상태 검증(is_active, balance >= amount)과 차감 연산을 단일 Current Read UPDATE로 병합한다.
        cursor.execute(
            "UPDATE wallets SET balance = balance - %s "
            "WHERE id = %s AND is_active = 1 AND balance >= %s",
            (amount, wallet_id, amount),
        )

        # 변경 전 코드의 세분화된 예외 조건(미존재, 비활성, 잔액 부족)을 동일하게 보존한다.
        if cursor.rowcount == 0:
            cursor.execute(
                "SELECT balance, is_active FROM wallets WHERE id = %s FOR SHARE",
                (wallet_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise WalletNotFoundError(f"지갑(id={wallet_id})이 존재하지 않습니다.")
            current_balance, is_active = int(row[0]), bool(row[1])
            if not is_active:
                raise InactiveWalletError(f"지갑(id={wallet_id})이 비활성 상태입니다.")
            raise InsufficientBalanceError(
                f"잔액이 부족합니다. (현재 잔액: {current_balance}, 요청 금액: {amount})"
            )

        # 자신이 직접 UPDATE한 레코드는 DB_TRX_ID == m_creator_trx_id이므로 최신 값이 즉시 조회된다.
        cursor.execute(
            "SELECT balance FROM wallets WHERE id = %s",
            (wallet_id,),
        )
        updated_row = cursor.fetchone()
        assert updated_row is not None
        new_balance = int(updated_row[0])

        cursor.execute(
            "INSERT INTO wallet_ledgers (wallet_id, amount, balance_after) VALUES (%s, %s, %s)",
            (wallet_id, -amount, new_balance),
        )
        conn.commit()
        return WithdrawalResult(
            wallet_id=wallet_id,
            deducted_amount=amount,
            remaining_balance=new_balance,
        )
    except Exception:
        conn.rollback()
        raise


# --- InnoDB MVCC(Snapshot Read vs Current Read) 동시성 검증 하네스 ---
class MockMVCCEngine:
    def __init__(self, initial_balance: int, is_active: int = 1) -> None:
        self.lock = threading.RLock()
        self.row_lock = threading.Lock()
        self.latest_balance = initial_balance
        self.is_active = is_active
        self.latest_trx_id = 1
        self.ledgers: List[Tuple[int, int, int]] = []


class MockConnection:
    def __init__(self, engine: MockMVCCEngine, barrier: Optional[threading.Barrier] = None) -> None:
        self.engine = engine
        self.barrier = barrier
        self.snapshot_balance: Optional[int] = None
        self.holds_row_lock = False
        self.last_result: Optional[Tuple[Any, ...]] = None
        self.rowcount = 0

    def cursor(self) -> "MockConnection":
        return self

    def execute(self, query: str, params: Tuple[Any, ...]) -> None:
        normalized = " ".join(query.strip().split()).upper()
        if normalized == "SELECT BALANCE, IS_ACTIVE FROM WALLETS WHERE ID = %S":
            with self.engine.lock:
                if self.snapshot_balance is None:
                    self.snapshot_balance = self.engine.latest_balance
                self.last_result = (self.snapshot_balance, self.engine.is_active)
            if self.barrier is not None:
                self.barrier.wait()

        elif normalized.endswith("FOR UPDATE") or normalized.endswith("FOR SHARE"):
            if not self.holds_row_lock:
                self.engine.row_lock.acquire()
                self.holds_row_lock = True
            with self.engine.lock:
                self.snapshot_balance = self.engine.latest_balance
                self.last_result = (self.engine.latest_balance, self.engine.is_active)

        elif normalized.startswith("UPDATE WALLETS SET BALANCE = BALANCE - %S"):
            amount, _, min_balance = int(params[0]), int(params[1]), int(params[2])
            if not self.holds_row_lock:
                self.engine.row_lock.acquire()
                self.holds_row_lock = True
            with self.engine.lock:
                if self.engine.is_active == 1 and self.engine.latest_balance >= min_balance:
                    self.engine.latest_balance -= amount
                    self.snapshot_balance = self.engine.latest_balance
                    self.rowcount = 1
                else:
                    self.rowcount = 0

        elif normalized.startswith("UPDATE WALLETS SET BALANCE = %S"):
            new_balance = int(params[0])
            if not self.holds_row_lock:
                self.engine.row_lock.acquire()
                self.holds_row_lock = True
            with self.engine.lock:
                self.engine.latest_balance = new_balance
                self.snapshot_balance = new_balance
                self.rowcount = 1

        elif normalized == "SELECT BALANCE FROM WALLETS WHERE ID = %S":
            with self.engine.lock:
                val = self.snapshot_balance if self.snapshot_balance is not None else self.engine.latest_balance
                self.last_result = (val,)

        elif normalized.startswith("INSERT INTO WALLET_LEDGERS"):
            with self.engine.lock:
                self.engine.ledgers.append((int(params[0]), int(params[1]), int(params[2])))

    def fetchone(self) -> Optional[Tuple[Any, ...]]:
        return self.last_result

    def commit(self) -> None:
        if self.holds_row_lock:
            self.holds_row_lock = False
            self.engine.row_lock.release()

    def rollback(self) -> None:
        if self.holds_row_lock:
            self.holds_row_lock = False
            self.engine.row_lock.release()


if __name__ == "__main__":
    # 테스트 1: 비관적 잠금(SELECT ... FOR UPDATE) 동시 차감 검증 (10,000원에서 6,000원씩 2회 요청)
    engine_pessimistic = MockMVCCEngine(initial_balance=10000)
    results_pessimistic: List[str] = []

    def worker_pessimistic() -> None:
        conn = MockConnection(engine_pessimistic)
        try:
            withdraw_with_pessimistic_lock(conn, wallet_id=1, amount=6000)
            results_pessimistic.append("SUCCESS")
        except InsufficientBalanceError:
            results_pessimistic.append("INSUFFICIENT")

    t1 = threading.Thread(target=worker_pessimistic)
    t2 = threading.Thread(target=worker_pessimistic)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert sorted(results_pessimistic) == ["INSUFFICIENT", "SUCCESS"]
    assert engine_pessimistic.latest_balance == 4000

    # 테스트 2: 조건부 원자적 UPDATE 동시 차감 검증 (10,000원에서 6,000원씩 2회 요청)
    engine_atomic = MockMVCCEngine(initial_balance=10000)
    results_atomic: List[str] = []

    def worker_atomic() -> None:
        conn = MockConnection(engine_atomic)
        try:
            withdraw_with_atomic_update(conn, wallet_id=1, amount=6000)
            results_atomic.append("SUCCESS")
        except InsufficientBalanceError:
            results_atomic.append("INSUFFICIENT")

    t3 = threading.Thread(target=worker_atomic)
    t4 = threading.Thread(target=worker_atomic)
    t3.start()
    t4.start()
    t3.join()
    t4.join()
    assert sorted(results_atomic) == ["INSUFFICIENT", "SUCCESS"]
    assert engine_atomic.latest_balance == 4000
    print("All concurrency verification tests passed.")
```

위 검증 코드에서 확인할 수 있듯, 초기 잔액이 `10000`원인 지갑에 대해 두 개의 스레드가 동시에 `6000`원 차감을 요청할 때, 스냅샷 읽기에 의존하던 변경 전 방식에서는 두 스레드가 모두 `10000 >= 6000` 조건을 통과하여 총 `12000`원이 출금되면서도 잔액은 `4000`원으로 덮어씌워지는 치명적인 정합성 오류가 발생한다. 반면 `withdraw_with_pessimistic_lock`과 `withdraw_with_atomic_update`는 모두 현재 읽기를 통해 레코드의 최신 상태를 직렬화하여 평가하므로, 정확히 한 건만 성공(`SUCCESS`, 잔액 `4000`원)하고 다른 한 건은 `InsufficientBalanceError`(`INSUFFICIENT`)로 안전하게 거부된다.

<!-- sec:tradeoff -->
## 5. 도입 기준, 잠금 오버헤드 및 시스템 경계 조건

실제 시스템 설계에서 스냅샷 읽기, 비관적 잠금(`SELECT ... FOR UPDATE`), 조건부 원자적 연산(`UPDATE ... WHERE`) 중 어느 방식을 채택할지는 비즈니스 로직의 복잡도와 잠금 유지 시간(Lock Hold Duration) 사이의 트레이드오프를 기준으로 결정해야 한다.

| 비교 항목 | 스냅샷 읽기 (`SELECT`) | 비관적 잠금 (`SELECT ... FOR UPDATE`) | 조건부 원자적 연산 (`UPDATE ... WHERE`) |
| :--- | :--- | :--- | :--- |
| 참조 데이터 버전 | `Read View` 생성 시점의 스냅샷 버전 | 버퍼 풀의 최신 커밋 버전 (Current Read) | 버퍼 풀의 최신 커밋 버전 (Current Read) |
| 배타적 잠금(`X-Lock`) 획득 시점 | 획득하지 않음 | 트랜잭션 초반 `SELECT` 실행 시점 | 실제 `UPDATE` 구문 실행 시점 |
| 잠금 유지 구간 | 없음 | 조회 -> 애플리케이션 연산 -> 수정 -> 커밋 전체 | `UPDATE` 실행 -> 커밋까지의 짧은 구간 |
| 적합한 워크로드 | 데이터 수정이 수반되지 않는 순수 조회 및 통계 | 다중 레코드 불변식 검증(쓰기 스큐 방지) 및 복잡한 분기 계산 | 단일 레코드의 상대적 증감(재고 차감, 포인트/잔액 차감) |

### 구체적인 도입 기준과 잠금 오버헤드

첫째, **단일 레코드에 대한 수치 증감 및 상태 전이**에는 **조건부 원자적 연산(`UPDATE ... WHERE`)**을 최우선으로 채택해야 한다. `SELECT ... FOR UPDATE`는 조회 시점부터 트랜잭션이 끝날 때까지 `X-Lock`을 점유하므로, 조회와 `UPDATE` 사이에 애플리케이션 레벨의 무거운 연산이나 네트워크 왕복(Round-Trip)이 끼어들면 동일 레코드에 접근하는 모든 후속 트랜잭션이 대기 상태에 빠져 커넥션 풀이 급격히 고갈될 수 있다. 반면 원자적 `UPDATE`는 데이터베이스 내부에서 조건 평가와 값 갱신을 한 번에 처리하므로 잠금 점유 시간을 최소화한다.

둘째, **여러 레코드에 걸친 비즈니스 불변식(Invariant)을 검증해야 하거나 SQL 수식만으로 다음 상태를 계산할 수 없는 경우**에는 **비관적 잠금(`SELECT ... FOR UPDATE`)**을 사용해야 한다. 예를 들어 한 사용자의 여러 계좌 잔액 합계를 검사하여 출금 가능 여부를 판단하는 쓰기 스큐(Write Skew) 방지 시나리오에서는, 조건부 `UPDATE`만으로는 다른 계좌 레코드의 동시 변경을 막을 수 없다. 이때는 검증 대상이 되는 모든 레코드를 일관된 정렬 순서(예: `ORDER BY id ASC`)로 `SELECT ... FOR UPDATE`하여 잠금을 선점한 뒤 검증과 수정을 수행해야 한다.

셋째, `REPEATABLE READ` 격리 수준에서 현재 읽기(`FOR UPDATE` 및 `UPDATE`)를 사용할 때는 **Next-Key Lock과 Gap Lock으로 인한 잠금 범위 확장 비용**을 반드시 고려해야 한다. InnoDB는 `REPEATABLE READ`에서 현재 읽기 시 유령 읽기(Phantom Read)를 방지하기 위해 레코드 자체만 잠그는 Record Lock에 더해 인덱스 레코드 사이의 간격을 잠그는 Gap Lock이 결합된 **Next-Key Lock**을 기본으로 사용한다. 만약 `WHERE` 절의 조건 컬럼에 고유 인덱스(Unique Index)가 없거나 조건에 부합하는 레코드가 존재하지 않으면, 인접한 인덱스 구간 전체에 Gap Lock이 걸려 다른 트랜잭션의 무관한 `INSERT` 작업까지 블로킹될 수 있다. 따라서 잠금 읽기나 원자적 `UPDATE`의 `WHERE` 절에는 반드시 기본키(Primary Key)나 고유 인덱스의 동등 비교 조건을 명시하여 잠금 범위를 단일 레코드(Record Lock)로 한정해야 한다.

넷째, 장시간 유지되는 스냅샷 읽기 트랜잭션이 유발하는 **Undo 로그 퍼지 지연(Purge Lag)** 비용도 주의해야 한다. `REPEATABLE READ`에서 한 번 생성된 `Read View`는 트랜잭션이 종료될 때까지 유지되므로, InnoDB의 백그라운드 퍼지 스레드는 현재 활성화된 가장 오래된 `Read View`의 `m_up_limit_id` 이후에 생성된 Undo 로그를 삭제(Purge)하지 못한다. 이는 History List Length의 급증과 버퍼 풀 페이지 탐색 비용 증가로 이어지므로, 읽기 전용 트랜잭션이라 하더라도 불필요하게 트랜잭션 범위를 길게 유지해서는 안 된다.

### 시스템 경계 조건 (Out of Scope)

본문에서 다룬 MVCC의 `Read View` 가시성 규칙과 잠금 읽기(`SELECT ... FOR UPDATE`), 원자적 `UPDATE` 전략은 **단일 관계형 데이터베이스 인스턴스 내부의 로컬 트랜잭션**을 전제로 동작한다. 만약 지갑 서비스와 주문 서비스가 물리적으로 분리된 데이터베이스를 사용하는 분산 환경이라면, 로컬 스토리지 엔진의 `X-Lock`이나 MVCC 버전 체인만으로는 서비스 간 정합성을 보장할 수 없다. 이러한 분산 트랜잭션 환경의 2PC(Two-Phase Commit) 프로토콜이나 보상 트랜잭션 기반의 Saga 패턴, 그리고 다중 잠금 경합 시 발생하는 스토리지 엔진 내부의 데드락 탐지 그래프(Wait-for Graph) 알고리즘 및 엔진별 타임아웃 파라미터 튜닝은 본 글의 범위를 벗어나므로 별도의 아키텍처 계층에서 다루어야 한다.

<!-- sec:conclusion -->
## 결론: 읽기와 쓰기의 시점을 일치시키는 설계

`REPEATABLE READ` 격리 수준이 보장하는 일관성은 트랜잭션 내부에서 수행되는 **비잠금 스냅샷 읽기(`SELECT`)가 동일한 `Read View`를 통해 과거 시점의 고정된 데이터를 반복해서 읽을 수 있다**는 의미일 뿐, 읽은 데이터를 수정하여 다시 반영하는 Read-Modify-Write 과정의 배타적 격리를 자동으로 보장한다는 뜻이 아니다.

MVCC 스토리지 엔진에서 일반 `SELECT`는 과거의 스냅샷을 읽고, `UPDATE`는 현재의 최신 커밋 버전을 덮어쓴다. 이 두 작업의 참조 시점 차이를 간과하고 스냅샷 읽기로 가져온 값을 애플리케이션 메모리에서 가공해 `UPDATE`에 전달하는 순간, 그사이에 커밋된 다른 트랜잭션의 결과물은 소리 없이 증발한다. 따라서 상태 변경이 수반되는 동시성 로직을 작성할 때는 다음 두 가지 원칙을 일관되게 적용해야 한다.

1. 조회한 값을 기반으로 후속 수정 작업이 이어진다면, 일반 `SELECT`에 의존하지 말고 `SELECT ... FOR UPDATE`를 통해 첫 읽기부터 **현재 읽기(Current Read)**와 배타적 잠금을 강제하라.
2. 단일 레코드의 상태 전이와 수치 차감은 애플리케이션 메모리 계산을 거치지 말고, 상태 검증 조건을 `WHERE` 절에 포함한 **조건부 원자적 `UPDATE`**로 병합하여 스토리지 엔진이 최신 버전 위에서 검증과 갱신을 동시에 수행하도록 위임하라.

격리 수준 설정값이라는 추상적인 이름 뒤에 숨겨진 Undo 버전 체인과 `Read View`, 그리고 스냅샷 읽기와 현재 읽기의 동작 차이를 정확히 이해할 때 비로소 동시성 환경에서도 흔들리지 않는 데이터 정합성을 구축할 수 있다.
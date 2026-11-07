# Jest 모킹이 늘어나는 건 설계의 신호다: 의존성 역전으로 테스트 더블을 줄이는 과정

> jest.mock이 늘어나는 원인을 의존 방향으로 진단하고, 포트 추출·생성자 주입·페이크와 스파이로 바꾸는 과정을 변경 전후 코드와 테스트로 단계별로 설명합니다.

<!-- sec:intro -->
## 들어가며: jest.mock이 늘어나는 건 도구 문제가 아니다

서비스 테스트 파일 맨 위에 `jest.mock` 호출이 서너 개씩 쌓이는 경우가 있습니다. 팩토리 안에는 SDK의 중첩 객체 구조가 그대로 복제되어 있고, `as unknown as jest.Mock` 캐스팅이 줄줄이 붙습니다. 이런 테스트는 동작이 그대로여도 import 경로를 옮기거나 ORM 호출을 `update`에서 `upsert`로 바꾸는 순간 실패합니다. 반대로 실제 버그는 그대로 통과시키기도 합니다. 리팩터링을 지켜 줘야 할 테스트가 오히려 리팩터링을 막는 셈입니다.

이 글은 이 불편함을 Jest 사용법이 아니라 **의존 방향**의 문제로 다룹니다. 서비스가 구체 모듈(외부 SDK, DB 클라이언트)을 직접 import하면, 테스트가 서비스를 격리하는 방법은 모듈 경로를 가로채는 것밖에 남지 않습니다. 그래서 다룰 메커니즘은 다음 한 가지입니다.

> 서비스가 구체 모듈 대신 자신이 정의한 인터페이스에 의존하도록 의존 방향을 뒤집으면, 모듈 경로 모킹 없이 주입된 테스트 더블만으로 행위를 검증할 수 있다.

예제는 주문 결제 서비스 하나입니다. 변경 전 코드와 테스트를 먼저 보고, 인터페이스 추출, 생성자 주입, 테스트 더블 교체를 한 단계씩 적용합니다. 각 단계에서 변경 전 코드가 처리하던 경계 조건(주문 없음, 이미 결제됨, 카드 거절, 예상치 못한 오류, 멱등 키)이 변경 후에도 살아 있는지 함께 확인합니다.

<!-- sec:body-1 -->
## 문제 정의: 모듈 경로를 가로채는 jest.mock이 테스트를 깨지기 쉽게 만드는 이유

`jest.mock(path, factory)`는 Jest의 모듈 레지스트리에서 해당 경로의 모듈을 팩토리가 돌려준 객체로 바꿔 끼웁니다. 대상 코드는 바뀐 사실을 모른 채 평소처럼 `import`하고, 바꿔 끼운 객체를 받습니다. 대상 코드를 한 줄도 고치지 않고 격리할 수 있다는 점이 이 방식의 장점입니다. 그런데 그 대가로 테스트는 다음 세 가지를 알아야 합니다.

1. **대상 코드가 어떤 경로를 import하는가.** 경로 문자열이 테스트에 하드코딩됩니다. 파일을 옮기거나 배럴 파일을 거치도록 바꾸면 mock이 엉뚱한 모듈에 걸리거나 아예 걸리지 않습니다.
2. **그 모듈이 무엇을 어떤 모양으로 export하는가.** 클래스인지 인스턴스인지, `client.charges.create`처럼 몇 단계로 중첩되는지, 오류를 어떤 클래스로 던지는지까지 팩토리에 복제해야 합니다.
3. **대상 코드가 그 모듈을 어떤 인자로 호출하는가.** 결과를 확인할 방법이 mock의 호출 기록뿐이라서 `toHaveBeenCalledWith`로 인자 객체의 모양을 단언하게 됩니다.

셋 다 서비스의 **구현 세부**입니다. 서비스가 해야 할 일, 즉 주문을 결제하고 결과를 기록하는 행위와는 상관이 없습니다.

여기에 Jest의 실행 모델이 만드는 마찰이 더해집니다. babel-jest와 ts-jest는 `jest.mock` 호출을 파일 최상단, import보다 위로 끌어올립니다(hoisting). 그래서 팩토리 안에서는 바깥 스코프 변수를 자유롭게 참조할 수 없고, `mock` 접두사가 붙은 변수만 예외로 허용됩니다. Jest 문서도 이 변수가 제때 초기화되는지는 작성자가 보장해야 한다고 밝히고 있습니다. 대상 모듈이 최상단에서 `new SdkClient()`를 실행하면 import 시점에 팩토리가 실행됩니다. 이때 팩토리가 아직 선언되지 않은 `mockCreate`를 참조하면 TDZ 오류가 납니다. 이를 피하려고 `jest.requireMock`이나 우회용 export 같은 장치가 테스트에 들어옵니다.

이 글에서 말하는 깨지기 쉬움은 **행위가 같은데도 테스트가 실패하는 상황**을 뜻합니다. 거짓 실패가 잦으면 개발자는 테스트 실패를 신호가 아니라 잡음으로 여기게 되고, 결국 테스트를 구현에 맞춰 고치는 습관이 생깁니다. 그 시점부터 테스트는 회귀를 잡지 못합니다.

<!-- sec:body-2 -->
## 개념 정리: 의존성 역전 원칙과 포트(인터페이스)의 역할

의존성 역전 원칙(DIP)은 두 문장으로 요약됩니다. 고수준 모듈은 저수준 모듈에 의존하지 않고 둘 다 추상에 의존한다. 추상은 세부에 의존하지 않고 세부가 추상에 의존한다. 이 글의 맥락에서 실무적으로 중요한 점은 **추상의 소유권이 고수준 쪽에 있다**는 사실입니다.

- 변경 전: `OrderService → @acme/payments-sdk`, `OrderService → infra/db`. 화살표가 구체 모듈을 향합니다.
- 변경 후: `OrderService → PaymentGateway(포트)`, `AcmePaymentGateway(어댑터) → PaymentGateway`. 어댑터가 서비스 쪽에서 정의한 인터페이스를 구현하므로 화살표가 서비스 쪽으로 돌아옵니다.

**포트**는 서비스가 바깥 세계에 요구하는 행위를 서비스의 언어로 적은 인터페이스입니다. **어댑터**는 그 포트를 구현해 실제 SDK나 DB와 연결하는 코드입니다. 흔히 SDK 메서드를 그대로 옮겨 적은 인터페이스를 만들곤 하는데, 이렇게 하면 mock의 위치만 바뀔 뿐 테스트가 아는 내용은 똑같습니다. 포트는 SDK가 무엇을 제공하는지가 아니라 **서비스가 무엇을 필요로 하는지**에서 출발해야 합니다.

TypeScript는 구조적 타이핑을 쓰기 때문에, 포트의 모양만 맞으면 `implements` 선언이 없는 객체 리터럴도 테스트 더블로 쓸 수 있습니다. 테스트 더블을 만드는 비용이 낮다는 점이 이후 단계의 전제가 됩니다. 이 글에서는 두 종류의 테스트 더블을 씁니다.

- **페이크(fake)**: 실제로 동작하는 단순한 구현입니다. 메모리 위의 Map으로 만든 저장소처럼 상태를 갖고, 테스트는 그 상태를 관찰합니다.
- **스파이(spy)**: 호출 기록을 남기는 함수입니다. `jest.fn`이 이 역할을 합니다. 외부로 나가는 명령이 실제로 나갔는지, 어떤 내용으로 나갔는지 확인할 때 씁니다.

<!-- sec:body-3 -->
## 변경 전 코드: 외부 SDK와 DB 클라이언트를 직접 import하는 서비스와 그 테스트

예제의 `@acme/payments-sdk`는 설명을 위해 만든 가상의 결제 SDK입니다. `db`는 Prisma 스타일의 `findUnique`/`update` API를 가진 DB 클라이언트 인스턴스라고 가정합니다. 이 함수가 처리하는 경계 조건은 다섯 가지입니다.

- 주문이 없으면 `OrderNotFoundError`를 던진다.
- 이미 `PAID`인 주문은 다시 청구하지 않는다.
- 청구 시 주문별 멱등 키 `order-{id}`를 보내, 재시도해도 이중 청구가 일어나지 않게 한다.
- 카드 거절(`CardDeclinedError`)은 예외가 아니라 결과로 다루고, 주문을 `PAYMENT_FAILED`로 기록한다.
- 그 밖의 오류는 그대로 전파한다. 청구 이후 DB 갱신이 실패한 경우도 여기에 포함된다.

<!-- sec:code-1 -->
```typescript
// src/order/pay-order.ts (변경 전)
import { PaymentsClient, CardDeclinedError } from '@acme/payments-sdk';
import { db } from '../infra/db';

const payments = new PaymentsClient({ apiKey: process.env.PAYMENTS_API_KEY ?? '' });

export class OrderNotFoundError extends Error {
  constructor(orderId: string) {
    super(`order not found: ${orderId}`);
    this.name = 'OrderNotFoundError';
  }
}

export type PayResult =
  | { status: 'paid'; chargeId: string }
  | { status: 'already_paid'; chargeId: string | null }
  | { status: 'declined'; reason: string };

export async function payOrder(orderId: string): Promise<PayResult> {
  const order = await db.order.findUnique({ where: { id: orderId } });
  if (!order) {
    throw new OrderNotFoundError(orderId);
  }
  if (order.status === 'PAID') {
    return { status: 'already_paid', chargeId: order.chargeId };
  }

  try {
    const charge = await payments.charges.create({
      amount: order.amount,
      currency: 'KRW',
      idempotencyKey: `order-${order.id}`,
    });
    await db.order.update({
      where: { id: order.id },
      data: { status: 'PAID', chargeId: charge.id },
    });
    return { status: 'paid', chargeId: charge.id };
  } catch (error) {
    if (error instanceof CardDeclinedError) {
      await db.order.update({
        where: { id: order.id },
        data: { status: 'PAYMENT_FAILED' },
      });
      return { status: 'declined', reason: error.message };
    }
    throw error;
  }
}
```

SDK 클라이언트가 모듈 최상단에서 생성된다는 점에 주목해야 합니다. 이 파일을 import하는 순간 `new PaymentsClient(...)`가 실행되므로, 테스트는 import 전에 SDK를 반드시 가로채야 합니다.

<!-- sec:code-2 -->
```typescript
// src/order/pay-order.test.ts (변경 전)
import { payOrder, OrderNotFoundError } from './pay-order';
import { db } from '../infra/db';

jest.mock('../infra/db', () => ({
  db: {
    order: {
      findUnique: jest.fn(),
      update: jest.fn(),
    },
  },
}));

// 서비스가 import 시점에 new PaymentsClient()를 호출하므로
// 팩토리 바깥 변수를 참조하면 초기화 순서 문제가 생긴다.
// 그래서 create를 팩토리 안에서 만들고 우회 export로 꺼낸다.
jest.mock('@acme/payments-sdk', () => {
  class CardDeclinedError extends Error {}
  const create = jest.fn();
  return {
    CardDeclinedError,
    PaymentsClient: jest.fn(() => ({ charges: { create } })),
    __create: create,
  };
});

const sdk = jest.requireMock('@acme/payments-sdk');
const createCharge: jest.Mock = sdk.__create;
const findUnique = db.order.findUnique as unknown as jest.Mock;
const update = db.order.update as unknown as jest.Mock;

const pendingRow = { id: 'o-1', amount: 15000, status: 'PENDING', chargeId: null };

beforeEach(() => {
  jest.clearAllMocks();
});

describe('payOrder', () => {
  it('결제에 성공하면 주문을 PAID로 갱신한다', async () => {
    findUnique.mockResolvedValue(pendingRow);
    createCharge.mockResolvedValue({ id: 'ch-1' });

    const result = await payOrder('o-1');

    expect(result).toEqual({ status: 'paid', chargeId: 'ch-1' });
    expect(createCharge).toHaveBeenCalledWith({
      amount: 15000,
      currency: 'KRW',
      idempotencyKey: 'order-o-1',
    });
    expect(update).toHaveBeenCalledWith({
      where: { id: 'o-1' },
      data: { status: 'PAID', chargeId: 'ch-1' },
    });
  });

  it('카드가 거절되면 PAYMENT_FAILED로 갱신한다', async () => {
    findUnique.mockResolvedValue(pendingRow);
    createCharge.mockRejectedValue(new sdk.CardDeclinedError('insufficient_funds'));

    const result = await payOrder('o-1');

    expect(result).toEqual({ status: 'declined', reason: 'insufficient_funds' });
    expect(update).toHaveBeenCalledWith({
      where: { id: 'o-1' },
      data: { status: 'PAYMENT_FAILED' },
    });
  });

  it('이미 결제된 주문은 다시 청구하지 않는다', async () => {
    findUnique.mockResolvedValue({ ...pendingRow, status: 'PAID', chargeId: 'ch-0' });

    const result = await payOrder('o-1');

    expect(result).toEqual({ status: 'already_paid', chargeId: 'ch-0' });
    expect(createCharge).not.toHaveBeenCalled();
  });

  it('주문이 없으면 OrderNotFoundError를 던진다', async () => {
    findUnique.mockResolvedValue(null);

    await expect(payOrder('o-1')).rejects.toBeInstanceOf(OrderNotFoundError);
  });
});
```

이 테스트가 알고 있는 내용을 정리하면 다음과 같습니다. 두 개의 모듈 경로, SDK가 `PaymentsClient` 생성자를 거쳐 `charges.create`를 노출한다는 사실, 거절 오류가 SDK 모듈의 `CardDeclinedError` 클래스라는 사실, DB 호출 인자가 `{ where, data }` 모양이라는 사실, 그리고 실제 타입과 mock 타입이 맞지 않아 `as unknown as`로 타입 검사를 꺼야 한다는 사실입니다. 마지막 항목 때문에 서비스가 SDK의 다른 메서드를 호출하도록 바뀌어도 컴파일러는 알려 주지 않습니다.

<!-- sec:body-4 -->
## 1단계: 서비스가 필요로 하는 행위를 인터페이스로 추출하기

먼저 서비스가 바깥 세계에 실제로 요구하는 것을 적어 봅니다. SDK나 Prisma가 제공하는 기능 목록에서 출발하지 않고, 서비스 코드가 하는 일을 기준으로 삼습니다.

- 주문을 ID로 읽는다.
- 주문을 결제 완료로 기록한다(청구 ID 포함).
- 주문을 결제 실패로 기록한다.
- 금액, 통화, 멱등 키를 주고 청구한다. 결과는 성공(청구 ID) 아니면 거절(사유)이다.

<!-- sec:code-3 -->
```typescript
// src/order/ports.ts
export type OrderStatus = 'PENDING' | 'PAID' | 'PAYMENT_FAILED';

export interface Order {
  id: string;
  amount: number;
  status: OrderStatus;
  chargeId: string | null;
}

export interface OrderRepository {
  findById(orderId: string): Promise<Order | null>;
  markPaid(orderId: string, chargeId: string): Promise<void>;
  markPaymentFailed(orderId: string): Promise<void>;
}

export interface ChargeRequest {
  amount: number;
  currency: 'KRW';
  idempotencyKey: string;
}

export type ChargeOutcome =
  | { kind: 'succeeded'; chargeId: string }
  | { kind: 'declined'; reason: string };

export interface PaymentGateway {
  charge(request: ChargeRequest): Promise<ChargeOutcome>;
}
```

이 인터페이스에는 의도적인 설계 결정이 세 가지 들어 있습니다.

첫째, **범용 `update` 대신 의도를 드러내는 메서드를 둡니다.** `markPaid`와 `markPaymentFailed`는 서비스가 하려는 일 자체입니다. 나중에 저장소 구현이 `upsert`나 트랜잭션, 이벤트 기록으로 바뀌어도 포트는 그대로입니다. 테스트가 `{ where, data }` 모양을 단언할 이유도 함께 사라집니다.

둘째, **카드 거절을 예외에서 반환값으로 바꿉니다.** 변경 전에는 서비스가 SDK의 오류 클래스를 import해 `instanceof`로 판별했고, 그래서 테스트도 SDK 모듈의 클래스 정체성까지 흉내 내야 했습니다. 거절은 예상 가능한 업무 결과이므로 판별 유니온 `ChargeOutcome`으로 표현합니다. SDK 예외를 이 유니온으로 번역하는 책임은 어댑터에 둡니다. 네트워크 오류처럼 예상하지 못한 실패는 여전히 예외로 남습니다. 이 구분이 변경 전의 다섯 번째 경계 조건(그 밖의 오류는 전파)을 그대로 보존합니다.

셋째, **멱등 키는 요청에 포함하되 그 값은 서비스가 정합니다.** 멱등 키를 `order-{id}`로 만드는 규칙은 재시도 시 이중 청구를 막는 업무 규칙이므로 서비스에 남겨 둡니다. 어댑터는 받은 키를 전달하기만 합니다. 덕분에 이 규칙은 서비스 단위 테스트에서 검증할 수 있습니다.

<!-- sec:body-5 -->
## 2단계: 생성자 주입으로 의존 방향 뒤집기

이제 서비스는 구체 모듈을 import하지 않습니다. 필요한 포트를 생성자로 받습니다. 함수였던 `payOrder`는 의존성을 보관하기 위해 클래스의 `pay` 메서드가 됩니다. 클래스 대신 `createPayOrder(deps)` 같은 팩토리 함수로 클로저를 써도 원리는 같습니다.

<!-- sec:code-4 -->
```typescript
// src/order/order-payment-service.ts (변경 후)
import type { OrderRepository, PaymentGateway } from './ports';

export class OrderNotFoundError extends Error {
  constructor(orderId: string) {
    super(`order not found: ${orderId}`);
    this.name = 'OrderNotFoundError';
  }
}

export type PayResult =
  | { status: 'paid'; chargeId: string }
  | { status: 'already_paid'; chargeId: string | null }
  | { status: 'declined'; reason: string };

export class OrderPaymentService {
  constructor(
    private readonly orders: OrderRepository,
    private readonly payments: PaymentGateway,
  ) {}

  async pay(orderId: string): Promise<PayResult> {
    const order = await this.orders.findById(orderId);
    if (!order) {
      throw new OrderNotFoundError(orderId);
    }
    if (order.status === 'PAID') {
      return { status: 'already_paid', chargeId: order.chargeId };
    }

    const outcome = await this.payments.charge({
      amount: order.amount,
      currency: 'KRW',
      idempotencyKey: `order-${order.id}`,
    });

    if (outcome.kind === 'declined') {
      await this.orders.markPaymentFailed(order.id);
      return { status: 'declined', reason: outcome.reason };
    }

    await this.orders.markPaid(order.id, outcome.chargeId);
    return { status: 'paid', chargeId: outcome.chargeId };
  }
}
```

`try/catch`가 사라진 이유는 거절이 반환값이 되었기 때문입니다. 예상하지 못한 오류는 잡을 필요가 없으므로 자연스럽게 전파됩니다. 청구는 성공했는데 `markPaid`가 실패하는 경우도 변경 전과 마찬가지로 예외가 호출자에게 전달됩니다.

SDK와 DB에 대한 지식은 어댑터로 옮겨 갑니다. 변경 전 서비스에 있던 `instanceof CardDeclinedError` 분기와 `{ where, data }` 호출이 이곳에 그대로 있습니다. 지식이 사라진 것이 아니라 위치가 바뀐 것입니다.

<!-- sec:code-5 -->
```typescript
// src/order/adapters.ts
import { CardDeclinedError } from '@acme/payments-sdk';
import type { PaymentsClient } from '@acme/payments-sdk';
import type { db } from '../infra/db';
import type {
  ChargeOutcome,
  ChargeRequest,
  Order,
  OrderRepository,
  PaymentGateway,
} from './ports';

export class AcmePaymentGateway implements PaymentGateway {
  constructor(private readonly client: PaymentsClient) {}

  async charge(request: ChargeRequest): Promise<ChargeOutcome> {
    try {
      const charge = await this.client.charges.create({
        amount: request.amount,
        currency: request.currency,
        idempotencyKey: request.idempotencyKey,
      });
      return { kind: 'succeeded', chargeId: charge.id };
    } catch (error) {
      if (error instanceof CardDeclinedError) {
        return { kind: 'declined', reason: error.message };
      }
      throw error;
    }
  }
}

export class PrismaOrderRepository implements OrderRepository {
  constructor(private readonly client: typeof db) {}

  async findById(orderId: string): Promise<Order | null> {
    const row = await this.client.order.findUnique({ where: { id: orderId } });
    if (!row) {
      return null;
    }
    return { id: row.id, amount: row.amount, status: row.status, chargeId: row.chargeId };
  }

  async markPaid(orderId: string, chargeId: string): Promise<void> {
    await this.client.order.update({
      where: { id: orderId },
      data: { status: 'PAID', chargeId },
    });
  }

  async markPaymentFailed(orderId: string): Promise<void> {
    await this.client.order.update({
      where: { id: orderId },
      data: { status: 'PAYMENT_FAILED' },
    });
  }
}
```

`findById`의 `row.status`는 DB 스키마의 상태 enum이 `OrderStatus`와 같은 값을 쓴다고 가정했습니다. 값이 다르다면 이 매핑 지점에서 변환해야 하며, 그 변환이 바로 어댑터가 존재하는 이유입니다.

남은 일은 구체 구현을 조립하는 것입니다. 변경 전에는 SDK 클라이언트 생성과 환경 변수 읽기가 서비스 파일의 import 부수 효과로 일어났습니다. 변경 후에는 애플리케이션 시작 지점(컴포지션 루트) 한 곳에서 일어납니다.

<!-- sec:code-6 -->
```typescript-snippet
// src/main.ts 의 일부 (컴포지션 루트)
const orderPaymentService = new OrderPaymentService(
  new PrismaOrderRepository(db),
  new AcmePaymentGateway(
    new PaymentsClient({ apiKey: process.env.PAYMENTS_API_KEY ?? '' }),
  ),
);
```

호출부도 `payOrder(id)`에서 `orderPaymentService.pay(id)`로 바뀝니다. 호출하는 쪽(컨트롤러나 핸들러)이 서비스 인스턴스를 받는 방식은 기존 구조에 맞추면 됩니다. 계층 전체를 DI 컨테이너 중심으로 재편하는 일은 이 글의 범위가 아닙니다.

<!-- sec:body-6 -->
## 3단계: jest.mock을 손으로 쓴 페이크 구현과 jest.fn 스파이로 대체하기

포트가 생겼으니 테스트 더블은 포트를 구현하기만 하면 됩니다. 저장소는 **상태를 가진 페이크**로 만듭니다. 테스트는 저장소에 무엇이 기록되었는지를 상태로 확인하고, 어떤 메서드가 어떤 인자로 불렸는지는 확인하지 않습니다. 결제 게이트웨이는 외부로 나가 되돌릴 수 없는 부수 효과를 대표하므로 **`jest.fn` 스파이**로 감싸 호출 기록을 남깁니다.

<!-- sec:code-7 -->
```typescript
// src/order/testing/fakes.ts
import type {
  ChargeOutcome,
  ChargeRequest,
  Order,
  OrderRepository,
  PaymentGateway,
} from '../ports';

export class InMemoryOrderRepository implements OrderRepository {
  private readonly rows = new Map<string, Order>();

  constructor(initial: Order[] = []) {
    for (const order of initial) {
      this.rows.set(order.id, { ...order });
    }
  }

  get(orderId: string): Order | undefined {
    const row = this.rows.get(orderId);
    return row ? { ...row } : undefined;
  }

  async findById(orderId: string): Promise<Order | null> {
    return this.get(orderId) ?? null;
  }

  async markPaid(orderId: string, chargeId: string): Promise<void> {
    const row = this.requireRow(orderId);
    this.rows.set(orderId, { ...row, status: 'PAID', chargeId });
  }

  async markPaymentFailed(orderId: string): Promise<void> {
    const row = this.requireRow(orderId);
    this.rows.set(orderId, { ...row, status: 'PAYMENT_FAILED' });
  }

  private requireRow(orderId: string): Order {
    const row = this.rows.get(orderId);
    if (!row) {
      throw new Error(`fake: unknown order ${orderId}`);
    }
    return row;
  }
}

export function createPaymentGateway(
  respond: (request: ChargeRequest) => Promise<ChargeOutcome> = async () => ({
    kind: 'succeeded',
    chargeId: 'ch-test',
  }),
) {
  const charge = jest.fn(respond);
  const gateway: PaymentGateway = { charge };
  return { gateway, charge };
}
```

페이크는 값을 복사해서 저장하고 돌려줍니다. 그래서 테스트가 꺼낸 객체를 수정해도 저장소 상태는 바뀌지 않습니다. 이런 작은 배려가 없으면 페이크가 실제 DB와 다르게 동작해 테스트가 우연히 통과할 수 있습니다. `createPaymentGateway`는 `respond` 함수로 성공, 거절, 예외를 시나리오별로 바꿔 끼울 수 있게 하고, 스파이 `charge`를 함께 돌려줍니다.

<!-- sec:code-8 -->
```typescript
// src/order/order-payment-service.test.ts (변경 후)
import { OrderNotFoundError, OrderPaymentService } from './order-payment-service';
import type { Order } from './ports';
import { InMemoryOrderRepository, createPaymentGateway } from './testing/fakes';

const pending: Order = { id: 'o-1', amount: 15000, status: 'PENDING', chargeId: null };

describe('OrderPaymentService.pay', () => {
  it('결제에 성공하면 주문이 PAID가 되고 청구 ID가 남는다', async () => {
    const orders = new InMemoryOrderRepository([pending]);
    const { gateway } = createPaymentGateway(async () => ({ kind: 'succeeded', chargeId: 'ch-1' }));
    const service = new OrderPaymentService(orders, gateway);

    await expect(service.pay('o-1')).resolves.toEqual({ status: 'paid', chargeId: 'ch-1' });
    expect(orders.get('o-1')).toMatchObject({ status: 'PAID', chargeId: 'ch-1' });
  });

  it('주문 금액과 주문별 멱등 키로 한 번 청구한다', async () => {
    const orders = new InMemoryOrderRepository([pending]);
    const { gateway, charge } = createPaymentGateway();
    const service = new OrderPaymentService(orders, gateway);

    await service.pay('o-1');

    expect(charge).toHaveBeenCalledTimes(1);
    expect(charge).toHaveBeenCalledWith({
      amount: 15000,
      currency: 'KRW',
      idempotencyKey: 'order-o-1',
    });
  });

  it('카드가 거절되면 PAYMENT_FAILED가 되고 사유를 돌려준다', async () => {
    const orders = new InMemoryOrderRepository([pending]);
    const { gateway } = createPaymentGateway(async () => ({
      kind: 'declined',
      reason: 'insufficient_funds',
    }));
    const service = new OrderPaymentService(orders, gateway);

    await expect(service.pay('o-1')).resolves.toEqual({
      status: 'declined',
      reason: 'insufficient_funds',
    });
    expect(orders.get('o-1')?.status).toBe('PAYMENT_FAILED');
  });

  it('이미 결제된 주문은 다시 청구하지 않는다', async () => {
    const orders = new InMemoryOrderRepository([{ ...pending, status: 'PAID', chargeId: 'ch-0' }]);
    const { gateway, charge } = createPaymentGateway();
    const service = new OrderPaymentService(orders, gateway);

    await expect(service.pay('o-1')).resolves.toEqual({ status: 'already_paid', chargeId: 'ch-0' });
    expect(charge).not.toHaveBeenCalled();
    expect(orders.get('o-1')).toMatchObject({ status: 'PAID', chargeId: 'ch-0' });
  });

  it('주문이 없으면 OrderNotFoundError를 던지고 청구하지 않는다', async () => {
    const orders = new InMemoryOrderRepository();
    const { gateway, charge } = createPaymentGateway();
    const service = new OrderPaymentService(orders, gateway);

    await expect(service.pay('o-1')).rejects.toBeInstanceOf(OrderNotFoundError);
    expect(charge).not.toHaveBeenCalled();
  });

  it('게이트웨이의 예상치 못한 오류는 전파되고 주문은 PENDING으로 남는다', async () => {
    const orders = new InMemoryOrderRepository([pending]);
    const { gateway } = createPaymentGateway(async () => {
      throw new Error('network');
    });
    const service = new OrderPaymentService(orders, gateway);

    await expect(service.pay('o-1')).rejects.toThrow('network');
    expect(orders.get('o-1')?.status).toBe('PENDING');
  });

  it('청구 후 저장에 실패해도 재시도는 같은 멱등 키로 청구한다', async () => {
    class FlakyRepository extends InMemoryOrderRepository {
      private failNext = true;

      async markPaid(orderId: string, chargeId: string): Promise<void> {
        if (this.failNext) {
          this.failNext = false;
          throw new Error('db unavailable');
        }
        return super.markPaid(orderId, chargeId);
      }
    }

    const orders = new FlakyRepository([pending]);
    const { gateway, charge } = createPaymentGateway(async () => ({ kind: 'succeeded', chargeId: 'ch-1' }));
    const service = new OrderPaymentService(orders, gateway);

    await expect(service.pay('o-1')).rejects.toThrow('db unavailable');
    await expect(service.pay('o-1')).resolves.toEqual({ status: 'paid', chargeId: 'ch-1' });
    expect(charge.mock.calls.map(([request]) => request.idempotencyKey)).toEqual([
      'order-o-1',
      'order-o-1',
    ]);
  });
});
```

이 파일에는 `jest.mock`도, 모듈 경로 문자열도, `as unknown as` 캐스팅도 없습니다. 모든 테스트가 새 인스턴스로 시작하므로 `beforeEach`에서 mock을 초기화할 필요도 없습니다. 페이크와 스파이가 포트 타입을 만족하지 않으면 컴파일러가 즉시 알려 줍니다.

변경 전의 경계 조건 다섯 가지가 모두 테스트로 남아 있습니다. 변경 전 테스트에는 없던 두 경우(예상치 못한 오류의 전파, 저장 실패 후 재시도)도 추가되었습니다. 상태를 가진 페이크가 있으니 이런 시나리오를 쉽게 표현할 수 있습니다. 다만 범위는 분명히 해 두어야 합니다. 마지막 테스트가 보장하는 것은 **서비스가 재시도 시 같은 멱등 키를 보낸다**는 사실까지입니다. 같은 키로 들어온 두 번째 요청을 결제사가 실제로 중복 제거하는지는 결제사의 계약이며, 이 단위 테스트가 검증하는 대상이 아닙니다.

<!-- sec:body-7 -->
## 변경 후 비교: 테스트가 검증하는 대상이 '호출 방식'에서 '행위'로 바뀌는 지점

| 테스트가 알아야 하는 것 | 변경 전 | 변경 후 |
| --- | --- | --- |
| 모듈 경로 | `../infra/db`, `@acme/payments-sdk` | 포트와 페이크 파일뿐 |
| 외부 API 모양 | `charges.create`, `{ where, data }` | 없음 |
| 거절 표현 | SDK 모듈의 오류 클래스 정체성 | `kind: 'declined'` 값 |
| DB 결과 확인 | `update` 호출 인자 단언 | 저장소 상태 조회 |
| 타입 안전성 | `as unknown as jest.Mock`로 해제 | 포트 계약을 컴파일러가 검사 |
| 모듈 로드 순서 | 팩토리 hoisting, `requireMock` 우회 | 고려할 필요 없음 |

이 차이는 구현을 바꿔 보면 더 분명하게 드러납니다. 다음 변경은 모두 주문 결제라는 행위를 바꾸지 않습니다.

- `db.order.update`를 `upsert`나 트랜잭션으로 교체한다. 변경 전 테스트는 `update` 단언에서 실패합니다. 변경 후에는 `PrismaOrderRepository`만 바뀌고 서비스 테스트는 그대로입니다.
- `infra/db`를 다른 경로로 옮긴다. 변경 전 테스트는 mock이 걸리지 않아 실제 DB에 접속을 시도합니다. 변경 후 서비스 테스트는 이 경로를 모릅니다.
- SDK를 올렸더니 메서드가 `charges.create`에서 다른 이름으로 바뀌었다. 변경 전에는 서비스와 테스트 팩토리를 모두 고쳐야 합니다. 변경 후에는 어댑터 한 곳만 고치면 됩니다.

그렇다고 변경 후 테스트가 호출 검증을 완전히 버린 것은 아닙니다. `charge` 스파이에 대해서는 여전히 `toHaveBeenCalledWith`와 `not.toHaveBeenCalled`를 씁니다. 차이는 **무엇의 호출을 검증하느냐**에 있습니다. 변경 전에는 서비스가 SDK를 어떻게 부르는지, 즉 외부 API의 모양을 검증했습니다. 변경 후에는 서비스가 직접 정의한 포트로 어떤 명령을 내보내는지를 검증합니다. 이미 결제된 주문에 청구 명령을 보내지 않는 것, 그리고 멱등 키 규칙을 지키는 것은 그 자체로 요구 사항입니다.

여기서 쓸 만한 판단 기준이 나옵니다. **상태로 관찰할 수 있는 결과는 페이크의 상태로 검증하고, 바깥으로 나가 되돌릴 수 없는 명령은 스파이로 검증한다.** 저장소 쓰기는 다시 읽어 확인할 수 있으니 상태로 봅니다. 결제 청구는 외부 시스템에 대한 부수 효과이므로 명령이 나갔는지를 봅니다.

<!-- sec:tradeoff -->
## 트레이드오프: 모킹이 여전히 필요한 경계와 판단 기준

### 이 전환이 들이는 비용

- **파일과 간접층이 늘어납니다.** 포트, 어댑터, 페이크, 컴포지션 루트가 새로 생깁니다. 호출 하나를 따라가려면 인터페이스를 거쳐 구현을 찾아야 합니다.
- **포트 설계가 틀리면 효과가 없습니다.** SDK 메서드를 그대로 옮겨 적은 포트는 테스트를 SDK의 모양에 다시 묶습니다. `PaymentGateway`가 `chargesCreate(params: SdkParams)` 같은 형태라면 mock의 위치만 옮긴 것입니다.
- **페이크는 실제와 어긋날 수 있습니다.** 인메모리 저장소는 유니크 제약, 트랜잭션 격리, 동시 갱신을 흉내 내지 않습니다. 이런 성질에 의존하는 로직이 있다면 페이크 위의 테스트는 거짓 성공을 냅니다. 페이크도 유지 보수해야 하는 코드입니다.
- **어댑터는 서비스 단위 테스트 밖에 남습니다.** SDK 예외를 `declined`로 번역하는 로직과 DB 상태 매핑은 이제 어댑터에 있습니다. 이 부분은 실제 DB나 결제사 샌드박스를 상대로 하는 통합 테스트로 확인해야 합니다. 그 전략은 이 글의 범위가 아닙니다.
- **배선 책임이 생깁니다.** 생성자 주입은 누군가 객체를 조립해야 한다는 뜻입니다. 의존성이 많아지면 컴포지션 루트가 길어지고, 프레임워크의 DI 컨테이너와 어떻게 맞물릴지도 결정해야 합니다.

### jest.mock이 여전히 맞는 경계

의존성 역전은 `jest.mock`을 없애는 기법이 아닙니다. 모듈 경로 가로채기가 필요한 지점을 시스템의 가장자리로 밀어내는 기법입니다. 다음 경우에는 여전히 모듈 모킹이 합리적입니다.

- **주입 지점이 없는 레거시 코드를 점진적으로 전환하는 중일 때.** 모듈 최상단에서 부수 효과를 일으키는 코드를 당장 고칠 수 없다면, 리팩터링 전 현재 동작을 고정하는 안전망으로 `jest.mock`을 씁니다. 이때는 전환이 끝나면 지울 임시 테스트로 명시해 두는 편이 좋습니다.
- **제어할 수 없는 코드가 내부에서 import하는 모듈.** 서드파티 라이브러리가 안에서 직접 쓰는 모듈은 주입할 방법이 없습니다.
- **시간과 타이머.** `setTimeout` 기반 재시도 같은 코드는 `jest.useFakeTimers`가 다루기 쉽습니다. 다만 현재 시각을 업무 규칙에 쓰는 경우라면 `Clock` 포트를 주입하는 편이 같은 원리로 더 명확합니다.

반대로 어댑터 자체의 번역 로직을 단위 테스트하고 싶을 때는 `jest.mock`이 필요하지 않을 수 있습니다. 예제의 `AcmePaymentGateway`는 SDK 클라이언트를 생성자로 받으므로 `charges.create`를 가진 객체를 넘기면 됩니다. 다만 `instanceof CardDeclinedError` 분기를 검증하려면 진짜 SDK의 오류 클래스를 import해 던져야 합니다. 이 부분은 SDK 패키지의 실제 export에 의존한다는 점을 받아들여야 합니다.

### 적용 판단 기준

| 관찰되는 신호 | 판단 |
| --- | --- |
| 한 테스트 파일에서 같은 외부 모듈의 내부 구조를 팩토리로 재현하고 있다 | 그 모듈을 쓰는 행위를 포트로 추출할 후보 |
| `toHaveBeenCalledWith`가 외부 API의 인자 모양을 단언한다 | 의도를 드러내는 포트 메서드로 바꾸고 상태 검증으로 전환 |
| 같은 SDK를 여러 서비스가 직접 import한다 | 어댑터 하나로 모아 번역 지점을 단일화 |
| 대상이 짧은 스크립트, 일회성 마이그레이션처럼 수명이 짧다 | 간접층 비용이 이득보다 크므로 jest.mock 유지 |
| 로직이 사실상 외부 호출의 단순 전달뿐이다 | 단위 테스트로 얻을 것이 적으므로 통합 테스트로 확인 |
| 페이크가 실제 저장소의 제약(유니크, 트랜잭션)을 흉내 내야만 의미가 있다 | 그 로직은 페이크 대신 실제 저장소를 상대로 검증 |

<!-- sec:conclusion -->
## 마치며

`jest.mock`이 늘어난다는 것은 테스트가 서비스를 격리할 방법이 모듈 경로 가로채기밖에 없다는 뜻입니다. 그리고 그 원인은 대개 서비스가 구체 모듈을 직접 import하는 의존 방향에 있습니다. 이 글에서는 같은 결제 로직을 세 단계로 바꿨습니다. 서비스가 필요로 하는 행위를 포트로 추출했고, 생성자 주입으로 의존 방향을 뒤집었고, 모듈 모킹을 페이크와 스파이로 대체했습니다. 그 결과 테스트가 검증하는 대상이 외부 API를 어떻게 호출하는지에서 서비스가 무엇을 하는지로 옮겨 갔습니다. 변경 전의 경계 조건인 주문 없음, 이미 결제됨, 카드 거절, 예상치 못한 오류, 멱등 키는 모두 변경 후 테스트에 그대로 남아 있습니다.

외부 시스템에 대한 지식이 사라진 것은 아닙니다. 어댑터로 모였을 뿐입니다. 따라서 이 전환은 서비스 단위 테스트를 단단하게 만드는 대신, 어댑터를 실제 환경에서 확인하는 일을 별도의 과제로 남깁니다.

이 글은 다음 경계를 전제로 했습니다. NestJS 모듈 구조를 재설계하거나 계층 전체를 클린 아키텍처로 바꾸는 작업은 다루지 않았습니다. 예제는 서비스 하나와 그 의존성 두 개에 한정됩니다. Jest 설정과 커버리지 리포트 구성은 다루지 않았습니다. 어댑터를 검증하는 통합 테스트와 E2E 테스트 전략도 범위 밖입니다. 예제의 `@acme/payments-sdk`는 설명용 가상 SDK이므로, 실제 SDK를 쓸 때는 해당 SDK의 오류 타입과 멱등성 계약을 문서로 확인한 뒤 어댑터의 번역 규칙을 정해야 합니다.
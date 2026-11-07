# Jest 모킹이 늘어나는 건 설계의 신호다: 의존성 역전으로 테스트 더블을 줄이는 과정

> jest.mock이 테스트를 모듈 경로와 SDK 호출 형태에 묶는 원인을 짚는다. 포트 추출, 생성자 주입, 페이크 대체 3단계로 의존 방향을 뒤집어 행위를 검증하는 과정을 변경 전후 코드로 설명한다.

<!-- sec:intro -->
## 파일을 옮겼을 뿐인데 실패하는 단위 테스트

주문 서비스의 테스트 파일이 `jest.mock('stripe')`와 `jest.mock('../infra/db')`로 시작한다고 하자. 커넥션 풀 모듈을 `src/infra/db.ts`에서 `src/infra/postgres/pool.ts`로 옮기기만 해도 테스트가 실패한다. 서비스 로직은 한 줄도 바뀌지 않았다. `pool.query`를 쿼리 빌더로 바꾸면 SQL 문자열을 검사하던 단언을 전부 다시 써야 한다. 리팩터링을 보호해야 할 테스트가 오히려 리팩터링의 비용이 된다.

이 불편함은 Jest를 잘못 써서 생기지 않는다. 서비스가 구체 모듈을 직접 import하면, 테스트가 그 의존을 끊을 수 있는 지점은 모듈 로더뿐이다. 그래서 `jest.mock` 호출 수는 서비스가 직접 아는 구체 모듈의 수를 그대로 따라간다. 모킹이 늘어나는 현상은 의존 방향이 테스트에 그대로 드러난 결과다.

해법의 핵심은 의존 방향을 뒤집는 것이다. 서비스가 구체 모듈 대신 자신이 정의한 인터페이스에 의존하면, 테스트는 모듈 경로를 가로채지 않는다. 생성자로 주입한 테스트 더블만으로 서비스의 행위를 검증할 수 있다. 아래에서는 결제와 주문 저장을 처리하는 서비스 하나를 세 단계로 바꾼다. 단계마다 테스트의 무엇이 왜 바뀌는지 변경 전후 코드로 확인한다.

<!-- sec:body-1 -->
## jest.mock이 테스트를 모듈 경로에 묶는 방식

`jest.mock(moduleName, factory)`는 Jest의 모듈 레지스트리에 대체 구현을 등록한다. 이후 테스트 대상이 같은 모듈을 `require`하면 실제 모듈 대신 팩토리가 반환한 객체를 받는다. 대상 코드를 건드리지 않고 의존을 바꿔치기하므로 편리하다. 대신 테스트는 세 가지에 결합한다.

첫째는 **경로 결합**이다. 테스트는 서비스가 어떤 경로의 모듈을 import하는지 알아야 한다. `jest.mock`에 넘긴 경로는 테스트 파일 기준으로 해석된다. 이 경로는 서비스가 import한 것과 같은 모듈을 가리켜야 한다. 따라서 서비스 쪽 import가 바뀌면 테스트도 따라 바뀐다. 서비스가 모듈을 하나 더 import하면 테스트는 그 사실을 실패로 알게 된다.

둘째는 **형태 결합**이다. 팩토리는 실제 모듈의 export 모양을 재현해야 한다. 서비스가 사용하는 호출 체인도 재현해야 한다. 테스트가 다시 적어야 하는 정보는 다음과 같다. default export인지, `__esModule` 플래그가 필요한지, `stripe.paymentIntents.create`처럼 몇 단계를 거쳐 메서드에 도달하는지. 이 재현에는 타입 검사가 걸리지 않는다. SDK 메이저 버전이 바뀌어 실제 시그니처가 달라져도 팩토리는 예전 모양 그대로 통과한다.

셋째는 **시점 결합**이다. babel-jest와 ts-jest는 `jest.mock` 호출을 파일 최상단으로 끌어올린다(hoisting). 서비스가 모듈 최상위에서 SDK 인스턴스를 만들면 그 생성자는 import 시점에 실행된다. 이 시점은 테스트 파일 본문의 변수 선언보다 앞선다. 팩토리가 참조할 수 있는 바깥 변수는 `mock` 접두사가 붙은 것으로 제한된다. 이 규칙과 아래 예제의 지연 호출 래퍼는 모두 실행 순서를 맞추기 위한 장치다.

세 결합의 원인은 같다. 의존이 서비스 내부의 import 문에서 고정된다. 그래서 테스트가 끼어들 수 있는 이음매(seam)가 모듈 레지스트리 하나뿐이다.

<!-- sec:body-2 -->
## 의존성 역전: 인터페이스를 누가 소유하는가

의존성 역전 원칙은 두 문장으로 요약된다. 상위 모듈은 하위 모듈에 의존하지 않고, 둘 다 추상에 의존한다. 추상은 세부에 의존하지 않고, 세부가 추상에 의존한다. 이 예제에서 상위 모듈은 주문 규칙을 가진 서비스다. 하위 모듈은 Stripe SDK와 PostgreSQL 클라이언트다.

자주 놓치는 쪽은 두 번째 문장이다. Stripe SDK를 감싸는 인터페이스를 만들면서 `createPaymentIntent(params: Stripe.PaymentIntentCreateParams)`처럼 SDK의 모양을 그대로 옮겼다고 하자. 이 인터페이스는 여전히 Stripe 타입에 의존한다. 간접 계층은 생겼지만 방향은 뒤집히지 않았다. 테스트 더블도 여전히 SDK의 파라미터 구조를 재현해야 한다.

방향이 뒤집히려면 인터페이스를 서비스가 소유해야 한다. 서비스의 언어로, 서비스가 필요로 하는 행위만 선언한 인터페이스를 **포트**라고 부른다. 포트를 구현해 실제 SDK를 호출하는 클래스는 **어댑터**다. 런타임 호출은 여전히 서비스 → 어댑터 → SDK 순으로 흐른다. 반면 소스 코드의 import 화살표는 경계에서 반대가 된다. 서비스는 어댑터를 import하지 않고, 어댑터가 서비스 쪽 포트를 import한다.

이 전환이 테스트에 주는 효과는 이음매의 위치가 바뀌는 것이다. 대체 지점이 모듈 레지스트리에서 생성자 파라미터로 옮겨 간다. 생성자 파라미터는 타입 검사를 받는 일반 값이다.

<!-- sec:body-3 -->
## 변경 전: SDK와 DB 클라이언트를 직접 import하는 서비스

변경 전 서비스는 모듈 최상위에서 Stripe 클라이언트를 만들고, 공용 커넥션 풀을 import해 쓴다.

<!-- sec:code-1 -->
```typescript-snippet
// src/order/orderService.ts (변경 전)
import Stripe from 'stripe';
import { pool } from '../infra/db';
import { EmptyOrderError, PaymentNotCompletedError, type PlaceOrderInput } from './types';

const stripe = new Stripe(process.env.STRIPE_SECRET_KEY!);

export async function placeOrder(input: PlaceOrderInput): Promise<string> {
  if (input.items.length === 0) throw new EmptyOrderError(input.orderId);
  const amount = input.items.reduce((sum, i) => sum + i.priceCents * i.quantity, 0);

  const intent = await stripe.paymentIntents.create(
    { amount, currency: 'usd', customer: input.customerId,
      payment_method: input.paymentMethodId, confirm: true },
    { idempotencyKey: input.orderId },
  );
  if (intent.status !== 'succeeded') throw new PaymentNotCompletedError(intent.status);

  await pool.query(
    'INSERT INTO orders (id, customer_id, amount_cents, payment_id) VALUES ($1, $2, $3, $4)',
    [input.orderId, input.customerId, amount, intent.id],
  );
  return intent.id;
}
```

이 함수에는 테스트가 지켜야 할 제약이 네 가지 있다.

1. 항목이 비면 결제를 시도하지 않는다.
2. 금액은 센트 단위 정수로 계산한다.
3. 결제 상태가 `succeeded`가 아니면 주문을 저장하지 않는다.
4. 주문 ID를 멱등 키로 넘겨 재시도 때 이중 청구를 막는다.

변경 후에도 이 네 가지는 어딘가에서 검증되어야 한다. 이 서비스의 테스트는 다음과 같다.

<!-- sec:code-2 -->
```typescript-snippet
// src/order/orderService.test.ts (변경 전)
const mockCreate = jest.fn();
jest.mock('stripe', () => ({
  __esModule: true,
  default: jest.fn().mockImplementation(() => ({
    paymentIntents: { create: (...args: unknown[]) => mockCreate(...args) },
  })),
}));
jest.mock('../infra/db', () => ({ pool: { query: jest.fn() } }));

import { pool } from '../infra/db';
import { placeOrder } from './orderService';
import { input } from './fixtures'; // 1000센트×2 + 1500센트×1, 'ord_1', 'cus_1'

it('결제가 성공하면 주문을 저장한다', async () => {
  mockCreate.mockResolvedValue({ id: 'pi_1', status: 'succeeded' });
  await placeOrder(input);

  expect(mockCreate).toHaveBeenCalledWith(
    expect.objectContaining({ amount: 3500, currency: 'usd' }),
    { idempotencyKey: 'ord_1' },
  );
  expect(pool.query).toHaveBeenCalledWith(
    expect.stringContaining('INSERT INTO orders'),
    ['ord_1', 'cus_1', 3500, 'pi_1'],
  );
});
```

`create: (...args) => mockCreate(...args)` 래퍼는 앞에서 말한 시점 결합 때문에 필요하다. 서비스를 import하는 순간 `new Stripe()`가 실행된다. 그때는 아직 `const mockCreate = jest.fn()` 줄이 실행되기 전이다. 래퍼 없이 `create: mockCreate`라고 쓰면 오류가 난다. 트랜스파일 설정에 따라 ReferenceError가 나거나 `undefined`가 들어간다. 테스트를 쓰는 사람은 주문 규칙이 아니라 모듈 로딩 순서를 고민하게 된다.

단언도 같은 문제를 드러낸다. 첫 번째 단언은 Stripe 파라미터 이름과 두 번째 인자의 옵션 구조를 확인한다. 두 번째 단언은 SQL 문자열의 일부를 확인한다. 둘 다 서비스가 외부와 *어떻게* 대화하는지를 고정할 뿐이다. 주문이 저장되었다는 행위는 SQL 문자열을 통해 간접적으로만 추정한다.

<!-- sec:body-4 -->
## 1단계: 서비스가 필요로 하는 행위를 포트로 추출하기

포트는 다음 기준으로 정의한다. 서비스 코드에서 SDK가 등장하는 줄을 지우고, 그 자리에 서비스가 원래 하고 싶었던 말을 적는다. 서비스는 결제 인텐트를 만들고 싶은 것이 아니라 금액을 청구하고 싶다. SQL을 실행하고 싶은 것이 아니라 주문을 저장하고 싶다. 포트 파일은 서비스와 같은 `order` 디렉터리에 둔다. 소유권이 서비스 쪽에 있다는 것을 위치로도 드러내기 위해서다.

<!-- sec:code-3 -->
```typescript
// src/order/ports.ts
export interface ChargeRequest {
  orderId: string;
  customerId: string;
  paymentMethodId: string;
  amountCents: number;
  currency: 'usd';
}

export type ChargeResult =
  | { status: 'succeeded'; paymentId: string }
  | { status: 'not_completed'; reason: string };

export interface PaymentGateway {
  /** 같은 orderId로 다시 호출되어도 한 번만 청구해야 한다(멱등). */
  charge(request: ChargeRequest): Promise<ChargeResult>;
}

export interface Order {
  id: string;
  customerId: string;
  amountCents: number;
  paymentId: string;
}

export interface OrderRepository {
  save(order: Order): Promise<void>;
}
```

이 포트에는 세 가지 결정이 들어 있다.

첫째, 메서드는 서비스가 실제로 쓰는 것만 둔다. `OrderRepository`에 `findById`나 `delete`를 미리 넣지 않는다. 포트가 넓어질수록 페이크가 구현해야 할 면적도 넓어진다.

둘째, 반환 타입은 SDK 타입이 아니라 서비스가 판단에 쓰는 형태로 정한다. `ChargeResult`는 판별 유니온이므로 서비스는 `status`만 보고 분기한다. Stripe의 `requires_action`, `processing` 같은 상태 문자열을 해석하는 책임은 어댑터로 넘어간다. 반면 결제가 완료되지 않으면 저장하지 않는다는 규칙은 서비스에 남는다. 외부 상태의 해석과 도메인 규칙이 여기서 분리된다.

셋째, 멱등성은 포트의 계약으로 명시한다. 변경 전에는 `idempotencyKey` 옵션이 서비스 코드에 직접 있었다. 이제 서비스는 `orderId`를 넘길 뿐이다. 같은 `orderId`에 두 번 청구하지 않는 것은 포트를 구현하는 쪽의 의무다. 주석으로 적은 이 계약은 사라지지 않았다. 3단계의 어댑터 테스트가 이 계약을 검증한다.

<!-- sec:body-5 -->
## 2단계: 생성자 주입으로 의존 방향 뒤집기

포트가 생겼으면 서비스는 포트를 생성자로 받는다. 모듈 최상위의 `new Stripe()`와 `import { pool }`은 서비스에서 사라진다. 이 코드는 어댑터와 조립 지점으로 옮겨 간다.

<!-- sec:code-4 -->
```typescript-snippet
// src/order/orderService.ts (변경 후)
import type { OrderRepository, PaymentGateway } from './ports';
import { EmptyOrderError, PaymentNotCompletedError, type PlaceOrderInput } from './types';

export class OrderService {
  constructor(
    private readonly payments: PaymentGateway,
    private readonly orders: OrderRepository,
  ) {}

  async placeOrder(input: PlaceOrderInput): Promise<string> {
    if (input.items.length === 0) throw new EmptyOrderError(input.orderId);
    const amountCents = input.items.reduce((sum, i) => sum + i.priceCents * i.quantity, 0);

    const result = await this.payments.charge({
      orderId: input.orderId, customerId: input.customerId,
      paymentMethodId: input.paymentMethodId, amountCents, currency: 'usd',
    });
    if (result.status !== 'succeeded') throw new PaymentNotCompletedError(result.reason);

    await this.orders.save({
      id: input.orderId, customerId: input.customerId, amountCents, paymentId: result.paymentId,
    });
    return result.paymentId;
  }
}
```

`orderService.ts`의 import 목록에는 이제 `stripe`도 `pg`도 없다. 서비스가 아는 것은 같은 디렉터리의 포트와 도메인 타입뿐이다. 판별 유니온 덕분에 `result.status !== 'succeeded'` 분기 안에서는 `reason`에, 분기 뒤에서는 `paymentId`에 타입 안전하게 접근한다.

<!-- sec:code-5 -->
```typescript-snippet
// src/infra/stripePaymentGateway.ts
import type Stripe from 'stripe';
import type { ChargeRequest, ChargeResult, PaymentGateway } from '../order/ports';

export class StripePaymentGateway implements PaymentGateway {
  constructor(private readonly stripe: Pick<Stripe, 'paymentIntents'>) {}

  async charge(req: ChargeRequest): Promise<ChargeResult> {
    const intent = await this.stripe.paymentIntents.create(
      { amount: req.amountCents, currency: req.currency, customer: req.customerId,
        payment_method: req.paymentMethodId, confirm: true },
      { idempotencyKey: req.orderId },
    );
    return intent.status === 'succeeded'
      ? { status: 'succeeded', paymentId: intent.id }
      : { status: 'not_completed', reason: intent.status };
  }
}

// src/main.ts — 조립 지점
const orderService = new OrderService(
  new StripePaymentGateway(new Stripe(process.env.STRIPE_SECRET_KEY!)),
  new PgOrderRepository(pool),
);
```

어댑터는 `import type`으로 `order/ports`를 가져온다. 소스 의존 화살표가 인프라에서 주문 모듈 쪽으로 향하므로 의존이 역전되었다. `PgOrderRepository`도 같은 방식으로 작성한다. `Pick<Pool, 'query'>`를 생성자로 받고, 변경 전의 INSERT 문을 `save` 안으로 그대로 옮긴다.

어댑터도 SDK 인스턴스를 직접 만들지 않고 생성자로 받는다. `Pick<Stripe, 'paymentIntents'>`처럼 필요한 속성만 요구하므로, 어댑터 테스트에서도 모듈 경로 모킹이 필요 없다. 인스턴스 생성은 `main.ts` 한 곳에서만 일어난다. import 시점의 부수 효과가 사라졌으므로 변경 전 테스트의 hoisting 문제와 지연 래퍼도 필요 없어진다.

<!-- sec:body-6 -->
## 3단계: jest.mock을 페이크와 jest.fn 스파이로 대체하기

이제 테스트는 서비스를 직접 생성하면서 더블을 넘긴다. 두 포트에는 서로 다른 종류의 더블을 쓴다.

`OrderRepository`에는 손으로 쓴 **페이크**를 쓴다. 페이크는 실제로 동작하는 단순 구현이다. 여기서는 저장한 주문을 `Map`에 보관한다. 테스트는 `save`가 어떤 인자로 호출되었는지 확인하지 않는다. 호출이 끝난 뒤 저장소의 상태를 확인한다. 저장이라는 행위를 결과 상태로 검증하면, 저장 방식이 바뀌어도 단언은 그대로 유지된다.

`PaymentGateway`에는 `jest.fn`으로 만든 스텁 겸 스파이를 쓴다. 서비스 입장에서 결제 결과는 외부가 주는 입력이다. 그래서 시나리오마다 반환값을 정해 주는 방식이 자연스럽다. 결제를 시도하지 않아야 하는 경우에는 호출 여부를 스파이로 확인한다.

<!-- sec:code-6 -->
```typescript-snippet
// src/order/orderService.test.ts (변경 후) — jest.mock 없음
class InMemoryOrderRepository implements OrderRepository {
  readonly saved = new Map<string, Order>();
  async save(order: Order): Promise<void> {
    this.saved.set(order.id, order);
  }
}

function setup(result: ChargeResult) {
  const payments = { charge: jest.fn(async (_req: ChargeRequest) => result) };
  const orders = new InMemoryOrderRepository();
  return { service: new OrderService(payments, orders), payments, orders };
}

it('결제가 성공하면 결제 ID와 함께 주문이 저장된다', async () => {
  const { service, orders } = setup({ status: 'succeeded', paymentId: 'pi_1' });
  await expect(service.placeOrder(input)).resolves.toBe('pi_1');
  expect(orders.saved.get('ord_1')).toEqual(
    { id: 'ord_1', customerId: 'cus_1', amountCents: 3500, paymentId: 'pi_1' });
});

it('결제가 완료되지 않으면 주문을 남기지 않는다', async () => {
  const { service, orders } = setup({ status: 'not_completed', reason: 'requires_action' });
  await expect(service.placeOrder(input)).rejects.toThrow(PaymentNotCompletedError);
  expect(orders.saved.size).toBe(0);
});

it('빈 주문은 결제를 시도하지 않는다', async () => {
  const { service, payments } = setup({ status: 'succeeded', paymentId: 'pi_1' });
  await expect(service.placeOrder({ ...input, items: [] })).rejects.toThrow(EmptyOrderError);
  expect(payments.charge).not.toHaveBeenCalled();
});

it('주문 ID와 센트 단위 금액으로 청구를 요청한다', async () => {
  const { service, payments } = setup({ status: 'succeeded', paymentId: 'pi_1' });
  await service.placeOrder(input);
  expect(payments.charge).toHaveBeenCalledWith(
    expect.objectContaining({ orderId: 'ord_1', amountCents: 3500, currency: 'usd' }));
});
```

`payments` 객체는 별도의 타입 단언 없이 `OrderService` 생성자에 들어간다. `jest.fn`에 넘긴 구현의 시그니처가 `PaymentGateway['charge']`와 맞지 않으면 컴파일 단계에서 걸린다. 변경 전의 팩토리에는 없던 보호다.

변경 전의 네 가지 제약이 어디로 갔는지 대응시켜 본다. 빈 주문은 세 번째 테스트가, 미완료 결제는 두 번째 테스트가 행위로 검증한다. 센트 단위 금액은 저장된 주문의 `amountCents`와 `charge`에 전달된 요청으로 확인한다. 남은 멱등 키는 포트 계약이 되었으므로 어댑터 테스트가 맡는다.

<!-- sec:code-7 -->
```typescript-snippet
// src/infra/stripePaymentGateway.test.ts
it('주문 ID를 멱등 키로 넘기고 미완료 상태를 매핑한다', async () => {
  const create = jest.fn().mockResolvedValue({ id: 'pi_1', status: 'requires_action' });
  const stripe = { paymentIntents: { create } } as unknown as Pick<Stripe, 'paymentIntents'>;

  const result = await new StripePaymentGateway(stripe).charge({
    orderId: 'ord_1', customerId: 'cus_1', paymentMethodId: 'pm_1',
    amountCents: 3500, currency: 'usd',
  });

  expect(create).toHaveBeenCalledWith(
    expect.objectContaining({ amount: 3500 }), { idempotencyKey: 'ord_1' });
  expect(result).toEqual({ status: 'not_completed', reason: 'requires_action' });
});
```

이 테스트는 SDK 호출 형태를 고정한다. 이것은 어댑터 테스트에서는 정당하다. 포트 요청을 SDK 호출로 번역하는 일이 어댑터의 책임 자체이기 때문이다. 이 테스트에도 `jest.mock`은 없다. 다만 `as unknown as` 단언으로 타입 검사를 우회한다는 한계가 있다. Stripe 클라이언트 타입 전체를 손으로 채우는 비용이 커서 감수한 선택이다. 이 우회의 영향은 어댑터 테스트 파일 하나에 그친다.

같은 이유로 `PgOrderRepository`의 SQL이 실제 스키마와 맞는지는 단위 테스트로 검증하지 않는다. 그 확인은 실제 DB를 붙이는 통합 테스트의 몫이며, 이 글의 범위 밖이다.

<!-- sec:body-7 -->
## 변경 후 비교: 호출 방식에서 행위로

| 비교 축 | 변경 전 | 변경 후 |
|---|---|---|
| 의존을 바꿔치기하는 지점 | 모듈 레지스트리(`jest.mock`) | 생성자 파라미터 |
| 테스트가 알아야 하는 것 | import 경로, export 모양, SDK 호출 체인 | 서비스가 소유한 포트 |
| 저장 검증 | SQL 문자열 부분 일치 | 페이크 저장소의 상태 |
| 더블의 타입 검사 | 없음 | 포트 타입으로 검사 |
| 서비스 테스트가 깨지는 조건 | 파일 이동, SDK 교체, 쿼리 변경 | 주문 규칙이나 포트 계약 변경 |

표에서 가장 미묘한 부분은 네 번째 테스트의 `toHaveBeenCalledWith`다. 호출 인자를 검증하는 단언이 남아 있으므로 여전히 호출 방식을 검증하는 것처럼 보인다. 차이는 검증 대상이 누구의 계약인가에 있다. 변경 전에는 Stripe SDK의 파라미터 구조를 고정했다. 이것은 서비스 내부 구현의 일부다. 변경 후에는 서비스가 스스로 정의한 포트에 무엇을 요청하는지 확인한다. 외부에 청구를 요청하는 것은 서비스의 관찰 가능한 출력이다. 따라서 이 단언은 행위를 검증한다.

이 차이는 실제 변경 상황에서 드러난다. 결제사를 다른 PG로 바꾸면 새 어댑터와 그 테스트를 작성하고 `main.ts`의 한 줄을 고친다. 서비스 테스트는 바뀌지 않는다. 커넥션 풀 파일을 옮기거나 쿼리 빌더로 바꿀 때도 마찬가지다. 서비스 테스트가 실패하는 경우는 주문 규칙이나 포트 계약을 바꾼 경우로 좁혀진다. 그래서 테스트 실패는 곧 행위가 바뀌었다는 신호가 된다.

<!-- sec:tradeoff -->
## 역전이 만드는 비용과 적용 기준

의존 하나를 역전할 때마다 포트 인터페이스, 어댑터 클래스, 조립 코드가 생긴다. 코드를 읽는 사람은 `charge` 호출에서 실제 Stripe 호출까지 한 단계를 더 따라가야 한다. 의존이 두세 개인 서비스라면 감당할 만하다. 하지만 모든 모듈에 기계적으로 적용하면 파일 수와 탐색 비용이 로직의 양을 넘어선다.

페이크는 실제 구현과 어긋날 수 있다. 위의 `InMemoryOrderRepository`에 같은 `id`로 두 번 저장하면 `Map.set`이 조용히 덮어쓴다. 반면 실제 `orders` 테이블의 `id`가 기본 키라면 PostgreSQL은 고유 제약 위반 오류를 낸다. 중복 주문 시나리오를 이 페이크로만 검증하면, 운영 환경에서 다르게 동작하는 코드가 단위 테스트를 통과한다. 따라서 해당 시나리오가 실제 저장소의 제약에 걸리는지 먼저 판단해야 한다. 걸린다면 페이크가 같은 오류를 던지게 하거나, 그 시나리오를 통합 테스트로 넘긴다.

어댑터는 서비스 단위 테스트의 보호 밖에 있다. Stripe 상태 문자열을 `ChargeResult`로 바꾸는 매핑이 틀려도 서비스 테스트는 통과한다. 어댑터의 분기가 늘어날수록 어댑터 자체의 테스트가 필요하다.

다음 조건이 많이 겹칠수록 역전의 이득이 비용보다 크다.

- 서비스가 외부 호출 결과에 따라 분기하는 규칙을 가진다.
- 의존이 네트워크나 DB처럼 느리거나 비결정적이다.
- `jest.mock` 팩토리가 SDK의 export 모양과 호출 체인을 재현하고 있다.
- 같은 의존을 다른 구현으로 바꿀 가능성이 있다.

반대 경우도 있다. 서비스가 자체 규칙 없이 SDK 호출을 그대로 전달하기만 한다면, 포트는 SDK를 1:1로 복제하게 된다. 방향은 그대로인 채 간접 계층만 늘어난다. 이런 코드에서는 단위 테스트보다 실제 의존을 붙인 테스트가 더 많은 것을 알려 준다.

<!-- sec:conclusion -->
## 모듈 모킹이 여전히 필요한 경계

의존을 역전한 뒤에도 `jest.mock`이 맞는 자리가 있다. 생성자로 주입할 수 없는 라이브러리를 감싸는 어댑터의 테스트가 그렇다. 클라이언트 객체 없이 모듈 수준 함수만 노출하는 SDK가 그 예다. 이때 모듈 모킹은 어댑터 테스트 파일 하나에 갇히고, 서비스 테스트로 번지지 않는다. 레거시 코드를 한 모듈씩 전환하는 중간 단계도 정당한 사용처다. 아직 역전하지 않은 의존을 임시로 가로채는 데 쓸 수 있다.

시간 의존은 따로 판단한다. `Date.now()`가 고정된 값을 돌려주기만 하면 되는 테스트라면 `jest.useFakeTimers()`와 `jest.setSystemTime()`으로 충분하다. 반면 영업일 계산이나 만료 판정처럼 시간이 규칙의 입력이라면, 시계를 포트로 정의해 주입하는 편이 낫다. 그래야 규칙이 시간에 의존한다는 사실이 코드에 드러난다.

판단 기준은 하나로 줄일 수 있다. 테스트가 실패했을 때 그 이유가 무엇인지 묻는다. 서비스의 약속이 깨졌기 때문인가, 서비스 내부의 배선이 바뀌었기 때문인가. 도메인 서비스의 단위 테스트에 `jest.mock(경로)`가 있고 그 팩토리가 외부 API의 모양을 재현한다면, 그 테스트는 후자의 이유로 깨진다. 이 신호를 받으면 모킹 기법을 다듬기 전에 서비스가 무엇을 직접 import하는지부터 확인한다.

다루는 범위는 서비스 하나의 의존 방향이다. NestJS 모듈 구조나 DI 컨테이너 설계, 계층 전체의 아키텍처 전환은 포함하지 않는다. 여기서 쓴 생성자 주입은 프레임워크 없이도 동작한다. 이를 프레임워크의 프로바이더로 옮기는 설계는 별도의 결정이다. Jest 설정과 커버리지 구성도 다루지 않는다. 어댑터의 SQL과 외부 API 호출을 실제 의존으로 확인하는 통합·E2E 테스트 전략 역시 범위 밖이다.
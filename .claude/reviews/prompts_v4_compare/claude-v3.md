===== v1 Jest 모킹이 늘어나는 건 설계의 신호다: 의존성 역전으로 테스트 더블을 줄이는 과정 =====
<!-- sec:intro -->
## 문제 정의: jest.mock이 테스트를 구현에 묶는 방식

`jest.mock`은 테스트 대상이 import하는 모듈을 경로 단위로 가로챈다. 개발자가 `jest.mock("../infra/db")`라고 쓰면, Jest는 테스트 파일 안에서 그 경로를 요청하는 모든 import에 진짜 모듈 대신 가짜 모듈을 돌려준다. 서비스 코드를 고치지 않고도 외부 의존을 끊을 수 있으니 처음에는 편리하다.

이 편리함에는 대가가 따른다. 모킹하는 순간 테스트는 서비스가 *무엇을 하는지*보다 서비스가 *어느 경로에서 무엇을 어떤 모양으로 꺼내 쓰는지*를 알아야 한다. 개발자가 파일을 옮기거나 SDK 호출 방식을 바꾸면 서비스의 행위가 그대로여도 테스트가 깨진다.

깨지는 이유는 대개 세 가지로 모인다.

- **경로 결합**: 모킹 대상이 문자열 경로라서, 디렉터리 구조가 바뀌면 테스트도 함께 고쳐야 한다.
- **모양 결합**: 가짜 모듈은 SDK의 객체 구조(`client.charges.create` 같은 중첩)를 그대로 흉내 내야 한다.
- **순서 결합**: 같은 함수를 여러 번 부르는 코드라면 `mockResolvedValueOnce`를 호출 순서대로 쌓아야 하고, 내부 호출 순서가 바뀌면 응답이 엇갈린다.

이 글은 이런 불편함을 Jest 사용법의 문제로 보지 않는다. 서비스가 구체 모듈을 직접 import하고 있다는 *의존 방향*의 문제로 본다. 서비스가 자신이 정의한 인터페이스에 의존하도록 방향을 뒤집으면, 테스트는 모듈 경로를 가로챌 필요 없이 생성자로 넣어 준 테스트 더블만으로 행위를 검증할 수 있다. 아래에서는 주문 서비스 하나를 예제로 그 과정을 단계별로 따라간다.

<!-- sec:body-1 -->
## 개념 정리: 의존성 역전 원칙과 포트

의존성 역전 원칙(Dependency Inversion Principle)은 두 문장으로 정리된다. 고수준 모듈은 저수준 모듈에 의존하지 않고, 둘 다 추상에 의존해야 한다. 그리고 추상은 세부 사항에 의존하지 않고, 세부 사항이 추상에 의존해야 한다.

여기서 고수준 모듈은 업무 규칙을 담은 코드다. 이 글의 예제에서는 "주문을 만들고, 결제하고, 결과에 따라 주문 상태를 바꾼다"는 규칙을 가진 주문 서비스가 고수준 모듈이다. 저수준 모듈은 결제 SDK나 DB 클라이언트처럼 그 규칙을 실제로 실행하는 수단이다.

원칙에서 중요한 부분은 *추상을 누가 소유하느냐*다. 결제 SDK가 제공하는 타입을 그대로 인터페이스로 삼으면 서비스는 여전히 SDK의 모양에 묶인다. 의존을 역전하려면 서비스 쪽이 "나는 이런 행위가 필요하다"는 인터페이스를 직접 정의하고, SDK를 감싸는 코드가 그 인터페이스를 구현해야 한다.

헥사고날 아키텍처에서는 이렇게 고수준 모듈이 소유한 인터페이스를 **포트(port)**라고 부른다. 포트를 구현해 실제 기술에 연결하는 코드는 **어댑터(adapter)**라고 부른다. 이 글은 아키텍처 전체를 바꾸지 않고, 서비스 하나에 포트 두 개를 두는 범위에서만 이 용어를 쓴다.

포트가 생기면 테스트 입장에서 달라지는 점이 하나 있다. 테스트가 가짜로 바꿔 끼워야 할 대상이 "모듈 경로"에서 "인터페이스 구현체"로 바뀐다. 인터페이스 구현체는 평범한 TypeScript 객체이므로, 개발자는 Jest의 모듈 시스템 개입 없이 생성자 인자로 넘기기만 하면 된다.

<!-- sec:body-2 -->
## 변경 전 코드: 외부 모듈을 직접 import하는 서비스

예제 서비스는 주문을 `pending` 상태로 저장한 뒤 결제를 요청한다. 결제가 성공하면 주문을 `paid`로 바꾸고, 실패하면 `payment_failed`로 바꾼 뒤 오류를 다시 던진다. 결제 SDK와 DB 클라이언트는 각각 `src/infra` 아래 모듈에서 싱글턴으로 export된다고 가정한다.

<!-- sec:code-1 -->
```typescript
// src/order/place-order.ts
import { paymentClient } from "../infra/payment-client";
import { db } from "../infra/db";

export interface OrderItem {
  sku: string;
  price: number;
  quantity: number;
}

export async function placeOrder(userId: string, items: OrderItem[]): Promise<string> {
  const amount = items.reduce((sum, item) => sum + item.price * item.quantity, 0);
  const inserted = await db.query(
    "INSERT INTO orders (user_id, amount, status) VALUES ($1, $2, 'pending') RETURNING id",
    [userId, amount],
  );
  const orderId: string = inserted.rows[0].id;

  try {
    const charge = await paymentClient.charges.create({
      amount,
      currency: "KRW",
      metadata: { orderId },
    });
    await db.query("UPDATE orders SET status = 'paid', charge_id = $1 WHERE id = $2", [charge.id, orderId]);
  } catch (error) {
    await db.query("UPDATE orders SET status = 'payment_failed' WHERE id = $1", [orderId]);
    throw error;
  }

  return orderId;
}
```

이 코드에서 놓치면 안 되는 부분은 `catch` 블록이다. 결제가 실패하든 결제 후 상태 갱신이 실패하든, 서비스는 주문을 `payment_failed`로 남기고 원래 오류를 호출자에게 그대로 전달한다. 리팩터링 뒤에도 이 실패 경로는 같은 의미로 남아 있어야 한다.

이 서비스를 테스트하려면 개발자는 두 모듈을 모두 경로로 모킹해야 한다.

<!-- sec:code-2 -->
```typescript
// src/order/place-order.test.ts
import { placeOrder } from "./place-order";
import { paymentClient } from "../infra/payment-client";
import { db } from "../infra/db";

jest.mock("../infra/payment-client", () => ({
  paymentClient: { charges: { create: jest.fn() } },
}));
jest.mock("../infra/db", () => ({
  db: { query: jest.fn() },
}));

const mockedCreate = paymentClient.charges.create as jest.Mock;
const mockedQuery = db.query as jest.Mock;

describe("placeOrder", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it("결제에 성공하면 주문을 paid로 바꾼다", async () => {
    mockedQuery
      .mockResolvedValueOnce({ rows: [{ id: "order-1" }] })
      .mockResolvedValueOnce({ rows: [] });
    mockedCreate.mockResolvedValueOnce({ id: "ch_1" });

    const orderId = await placeOrder("user-1", [{ sku: "A", price: 1000, quantity: 2 }]);

    expect(orderId).toBe("order-1");
    expect(mockedQuery).toHaveBeenNthCalledWith(
      2,
      "UPDATE orders SET status = 'paid', charge_id = $1 WHERE id = $2",
      ["ch_1", "order-1"],
    );
  });
});
```

이 테스트는 서론에서 말한 세 가지 결합을 모두 보여 준다. 우선 `jest.mock` 두 줄은 `../infra/payment-client`와 `../infra/db`라는 경로 문자열에 묶여 있다. 개발자가 `infra` 디렉터리 이름만 바꿔도 이 두 줄을 함께 고쳐야 한다.

다음으로 팩토리 함수가 반환하는 객체는 SDK의 중첩 구조를 그대로 흉내 낸다. 개발자가 SDK를 다른 버전이나 다른 회사 제품으로 바꾸면, 서비스의 업무 규칙은 그대로인데도 가짜 모듈의 모양을 처음부터 다시 맞춰야 한다.

마지막으로 `mockedQuery`는 같은 함수 하나로 INSERT와 UPDATE를 모두 받는다. 그래서 테스트는 첫 번째 호출에 무엇을 돌려줄지, 두 번째 호출에 무엇을 돌려줄지를 순서대로 쌓아야 한다. 검증 부분도 "두 번째 쿼리가 이 SQL 문자열이었는가"를 확인하므로, SQL에 공백 하나만 바뀌어도 테스트가 실패한다.

결국 이 테스트가 확인하는 것은 "주문이 결제 완료 상태가 되었는가"가 아니다. 테스트는 "서비스가 DB 클라이언트를 이 순서, 이 문자열로 호출했는가"를 확인한다. 테스트가 행위 대신 호출 방식을 검증하고 있다는 점이 이 글이 풀려는 핵심 문제다.

<!-- sec:body-3 -->
## 1단계: 서비스가 필요로 하는 행위를 인터페이스로 추출하기

첫 단계에서 개발자는 서비스 코드를 읽으며 "이 서비스가 바깥에 요구하는 일"을 동사로 적는다. 서비스는 DB에게 SQL을 실행해 달라고 요구하는 것처럼 보이지만, 업무 관점에서 보면 요구하는 일은 세 가지다. 대기 중인 주문을 만드는 일, 주문을 결제 완료로 표시하는 일, 주문을 결제 실패로 표시하는 일이다.

결제 쪽도 같은 방식으로 읽는다. 서비스는 `charges.create`라는 SDK 메서드 자체가 필요한 게 아니다. 서비스가 필요한 일은 "이 주문에 대해 이 금액을 결제하고, 결제 식별자를 돌려받는 것"이다. 통화 코드나 메타데이터 형식은 결제 수단의 세부 사항이다.

이렇게 뽑아낸 행위를 서비스와 같은 디렉터리의 포트 파일에 인터페이스로 정의한다.

<!-- sec:code-3 -->
```typescript
// src/order/ports.ts
export type OrderStatus = "pending" | "paid" | "payment_failed";

export interface OrderRepository {
  createPending(userId: string, amount: number): Promise<string>;
  markPaid(orderId: string, chargeId: string): Promise<void>;
  markPaymentFailed(orderId: string): Promise<void>;
}

export interface PaymentGateway {
  charge(orderId: string, amount: number): Promise<{ chargeId: string }>;
}
```

이 파일이 `src/order` 안에 있다는 점이 중요하다. 인터페이스를 서비스 옆에 두면 추상의 소유자가 주문 모듈이라는 사실이 디렉터리 구조에 드러난다. 개발자가 `src/infra`에 인터페이스를 두면, 이름만 인터페이스일 뿐 여전히 인프라 쪽이 서비스에게 모양을 강요하는 구조가 된다.

메서드 이름도 SQL이나 SDK 용어를 피해서 짓는다. `query`나 `create` 대신 `markPaid`처럼 업무 상태 변화를 이름에 담으면, 나중에 테스트가 이 인터페이스를 흉내 낼 때도 업무 언어로 쓸 수 있다.

<!-- sec:body-4 -->
## 2단계: 생성자 주입으로 의존 방향 뒤집기

인터페이스를 정의했다고 해서 의존 방향이 바로 바뀌지는 않는다. 서비스가 여전히 `../infra/db`를 import한다면 화살표는 그대로 서비스에서 인프라로 향한다. 방향을 뒤집으려면 서비스가 구체 객체를 직접 가져오지 않고, 바깥에서 인터페이스 구현체를 받아야 한다.

가장 단순한 방법은 생성자 주입이다. 서비스를 클래스로 바꾸고, 필요한 포트를 생성자 매개변수로 받는다.

<!-- sec:code-4 -->
```typescript
// src/order/order-service.ts
import { OrderRepository, PaymentGateway } from "./ports";

export interface OrderItem {
  sku: string;
  price: number;
  quantity: number;
}

export class OrderService {
  constructor(
    private readonly orders: OrderRepository,
    private readonly payments: PaymentGateway,
  ) {}

  async placeOrder(userId: string, items: OrderItem[]): Promise<string> {
    const amount = items.reduce((sum, item) => sum + item.price * item.quantity, 0);
    const orderId = await this.orders.createPending(userId, amount);

    try {
      const { chargeId } = await this.payments.charge(orderId, amount);
      await this.orders.markPaid(orderId, chargeId);
    } catch (error) {
      await this.orders.markPaymentFailed(orderId);
      throw error;
    }

    return orderId;
  }
}
```

이제 이 파일의 import 목록에는 `./ports`만 남는다. 서비스는 결제 SDK가 어떤 모양인지, 주문이 어떤 테이블에 저장되는지 알지 못한다. 변경 전 코드의 `catch` 블록은 같은 위치에 같은 의미로 남아 있다. 결제가 실패해도, 결제 후 `markPaid`가 실패해도 주문은 `payment_failed`로 표시되고 원래 오류가 다시 던져진다.

SQL과 SDK 호출은 사라진 게 아니라 어댑터로 옮겨 간다. 어댑터는 포트를 구현하면서 실제 기술을 호출하는 클래스다.

<!-- sec:code-5 -->
```typescript
// src/adapters/order-adapters.ts
import { paymentClient } from "../infra/payment-client";
import { db } from "../infra/db";
import { OrderRepository, PaymentGateway } from "../order/ports";

export class SdkPaymentGateway implements PaymentGateway {
  async charge(orderId: string, amount: number): Promise<{ chargeId: string }> {
    const charge = await paymentClient.charges.create({
      amount,
      currency: "KRW",
      metadata: { orderId },
    });
    return { chargeId: charge.id };
  }
}

export class SqlOrderRepository implements OrderRepository {
  async createPending(userId: string, amount: number): Promise<string> {
    const inserted = await db.query(
      "INSERT INTO orders (user_id, amount, status) VALUES ($1, $2, 'pending') RETURNING id",
      [userId, amount],
    );
    return inserted.rows[0].id;
  }

  async markPaid(orderId: string, chargeId: string): Promise<void> {
    await db.query("UPDATE orders SET status = 'paid', charge_id = $1 WHERE id = $2", [chargeId, orderId]);
  }

  async markPaymentFailed(orderId: string): Promise<void> {
    await db.query("UPDATE orders SET status = 'payment_failed' WHERE id = $1", [orderId]);
  }
}
```

어댑터 파일은 `../order/ports`를 import한다. 즉 인프라 쪽 코드가 주문 모듈이 정한 추상을 바라본다. 변경 전에는 서비스가 인프라를 향했지만, 이제는 인프라가 서비스의 포트를 향한다. 의존성 역전 원칙이 말하는 "세부 사항이 추상에 의존한다"는 문장이 코드의 import 방향으로 드러나는 지점이다.

누군가는 서비스와 어댑터를 실제로 연결해야 한다. 이 역할은 애플리케이션 시작 지점 한 곳에서 맡는다.

<!-- sec:code-6 -->
```typescript
// src/composition-root.ts
import { OrderService } from "./order/order-service";
import { SdkPaymentGateway, SqlOrderRepository } from "./adapters/order-adapters";

export const orderService = new OrderService(new SqlOrderRepository(), new SdkPaymentGateway());
```

구체 클래스 이름이 등장하는 곳은 이 파일뿐이다. 개발자가 결제 수단을 바꾸려면 새 어댑터를 만들고 이 한 줄을 고치면 된다. 서비스 코드와 서비스 테스트는 손댈 필요가 없다.

<!-- sec:body-5 -->
## 3단계: jest.mock을 페이크 구현과 jest.fn 스파이로 대체하기

테스트 더블에는 여러 종류가 있다. 이 글에서는 두 가지만 쓴다. **페이크(fake)**는 실제로 동작하지만 운영에 쓰기엔 단순한 구현이고, **스파이(spy)**는 호출 기록을 남겨 나중에 확인할 수 있게 하는 더블이다.

주문 저장소에는 페이크가 어울린다. 서비스가 저장소에 남긴 결과, 즉 주문 상태를 테스트가 나중에 읽어 보고 싶기 때문이다. 개발자는 `Map`으로 주문을 보관하는 인메모리 구현을 손으로 쓴다.

결제 게이트웨이에는 `jest.fn` 스파이가 어울린다. 결제는 서비스가 바깥 세계에 보내는 명령이고, 테스트가 확인하고 싶은 것은 "이 주문에 대해 이 금액으로 결제를 요청했는가"다. 스파이는 응답을 정해 주는 동시에 그 요청을 기록한다.

<!-- sec:code-7 -->
```typescript
// src/order/order-service.test.ts
import { OrderService } from "./order-service";
import { OrderRepository, OrderStatus, PaymentGateway } from "./ports";

interface StoredOrder {
  userId: string;
  amount: number;
  status: OrderStatus;
  chargeId?: string;
}

class InMemoryOrderRepository implements OrderRepository {
  private readonly orders = new Map<string, StoredOrder>();
  private sequence = 0;

  async createPending(userId: string, amount: number): Promise<string> {
    this.sequence += 1;
    const orderId = `order-${this.sequence}`;
    this.orders.set(orderId, { userId, amount, status: "pending" });
    return orderId;
  }

  async markPaid(orderId: string, chargeId: string): Promise<void> {
    const order = this.require(orderId);
    order.status = "paid";
    order.chargeId = chargeId;
  }

  async markPaymentFailed(orderId: string): Promise<void> {
    this.require(orderId).status = "payment_failed";
  }

  find(orderId: string): StoredOrder | undefined {
    return this.orders.get(orderId);
  }

  private require(orderId: string): StoredOrder {
    const order = this.orders.get(orderId);
    if (!order) {
      throw new Error(`unknown order: ${orderId}`);
    }
    return order;
  }
}

describe("OrderService.placeOrder", () => {
  const items = [{ sku: "A", price: 1000, quantity: 2 }];

  it("결제에 성공하면 주문을 결제 완료 상태로 남긴다", async () => {
    const orders = new InMemoryOrderRepository();
    const payments: PaymentGateway = {
      charge: jest.fn().mockResolvedValue({ chargeId: "ch_1" }),
    };
    const service = new OrderService(orders, payments);

    const orderId = await service.placeOrder("user-1", items);

    expect(orders.find(orderId)).toEqual({
      userId: "user-1",
      amount: 2000,
      status: "paid",
      chargeId: "ch_1",
    });
    expect(payments.charge).toHaveBeenCalledWith(orderId, 2000);
  });

  it("결제가 실패하면 주문을 실패 상태로 남기고 오류를 그대로 던진다", async () => {
    const orders = new InMemoryOrderRepository();
    const declined = new Error("card declined");
    const payments: PaymentGateway = {
      charge: jest.fn().mockRejectedValue(declined),
    };
    const service = new OrderService(orders, payments);

    await expect(service.placeOrder("user-1", items)).rejects.toBe(declined);
    expect(orders.find("order-1")?.status).toBe("payment_failed");
  });
});
```

이 테스트 파일에는 `jest.mock`이 한 줄도 없다. 개발자는 Jest의 모듈 레지스트리에 개입하지 않고, 평범한 객체를 생성자에 넘겼을 뿐이다. 그래서 `jest.mock` 호출이 import보다 먼저 실행되도록 끌어올려진다는 사실(호이스팅)이나, 테스트 사이에 `jest.clearAllMocks`로 상태를 비워야 한다는 규칙을 신경 쓸 필요가 없다. 각 테스트가 자기 더블을 새로 만들기 때문이다.

두 번째 테스트는 변경 전 코드의 실패 경로를 그대로 검증한다. 결제가 거절되면 주문 상태가 `payment_failed`가 되고, 호출자가 받는 오류가 원래 오류 객체와 같은지 `rejects.toBe`로 확인한다. 리팩터링 과정에서 `catch` 블록의 의미가 바뀌었다면 이 테스트가 실패한다.

두 번째 테스트에서 `"order-1"`이라는 식별자를 직접 쓴 점은 짚어 둘 만하다. 이 값은 운영 DB의 동작이 아니라 테스트가 직접 작성한 페이크의 규칙에서 나온다. 페이크의 규칙은 개발자가 통제하므로, 변경 전 테스트에서 DB 클라이언트의 응답 순서에 기대던 것과는 성격이 다르다.

<!-- sec:body-6 -->
## 변경 후 비교: 검증 대상이 호출 방식에서 행위로 바뀌는 지점

두 테스트를 나란히 놓으면, 같은 시나리오를 검증하면서도 테스트가 붙잡고 있는 대상이 다르다는 점이 드러난다.

| 관점 | 변경 전 (`jest.mock`) | 변경 후 (주입된 더블) |
| --- | --- | --- |
| 더블을 끼우는 방법 | 모듈 경로 문자열을 가로챈다 | 생성자 인자로 넘긴다 |
| 더블이 흉내 내는 모양 | SDK와 DB 클라이언트의 실제 API | 서비스가 정의한 포트 |
| 저장소 검증 | 두 번째 `query` 호출의 SQL 문자열 | 페이크에 남은 주문 상태 |
| 결제 검증 | `charges.create`의 응답 설정 | 포트 메서드 `charge`의 인자 |
| 깨지는 계기 | 경로 이동, SQL 수정, 호출 순서 변경 | 포트 계약이나 업무 규칙 변경 |

저장소 쪽 변화가 가장 크다. 변경 전 테스트는 "UPDATE 문을 이 순서로 보냈는가"를 물었다. 변경 후 테스트는 "주문이 결제 완료 상태가 되었는가"를 묻는다. 개발자가 SQL을 고치거나 쿼리를 하나로 합쳐도, 업무 결과가 같다면 서비스 테스트는 통과한다.

결제 쪽에서도 여전히 호출을 확인한다는 점은 정직하게 봐야 한다. `toHaveBeenCalledWith(orderId, 2000)`은 분명 호출 검증이다. 다만 이 호출은 SDK의 내부 모양이 아니라 서비스가 직접 정의한 포트의 계약이다. 결제 요청은 서비스가 바깥 세계에 남기는 부수 효과이므로, 그 요청을 확인하는 일이 곧 서비스의 행위를 확인하는 일이다.

결과적으로 테스트가 깨지는 이유가 좁아진다. 변경 전에는 리팩터링 같은 무해한 변경에도 테스트가 깨졌다. 변경 후에는 포트 계약이나 업무 규칙이 바뀔 때만 테스트가 깨진다. 테스트 실패가 "무언가 의미 있는 게 바뀌었다"는 신호가 된다.

<!-- sec:tradeoff -->
## 트레이드오프: 모킹이 여전히 필요한 경계

의존성 역전은 모킹을 없애지 않는다. 모킹이 필요한 위치를 서비스 바깥의 경계로 밀어낼 뿐이다. `SdkPaymentGateway`와 `SqlOrderRepository` 안에는 여전히 SDK 호출과 SQL이 있고, 이 코드가 맞는지는 서비스 테스트로 확인되지 않는다.

어댑터를 검증하는 방법은 크게 두 가지다. 하나는 실제 DB나 결제 샌드박스에 붙이는 통합 테스트이고, 다른 하나는 어댑터 테스트에 한해 `jest.mock`으로 SDK를 가로채는 방식이다. 어댑터는 얇은 번역 계층이라 경로 모킹의 결합 비용이 서비스보다 작다. 어떤 방식을 고를지는 통합 테스트 전략의 문제이며, 통합 테스트와 E2E 테스트 전략은 이 글에서 다루지 않는다.

페이크에는 고유한 위험이 있다. 손으로 쓴 `InMemoryOrderRepository`가 실제 저장소와 다르게 동작하면, 서비스 테스트는 통과해도 운영에서는 실패할 수 있다. 예를 들어 실제 DB는 존재하지 않는 주문을 UPDATE해도 오류 없이 0건을 갱신하지만, 이 글의 페이크는 예외를 던진다. 개발자는 페이크와 실제 어댑터가 같은 포트 계약을 지키는지 별도로 확인해야 하며, 그 방법 역시 통합 테스트의 영역이다.

구조를 바꾸는 비용도 있다. 함수 하나였던 서비스가 포트 파일, 서비스 클래스, 어댑터 파일, 조립 지점으로 나뉜다. 외부 의존이 하나뿐이고 업무 규칙이 거의 없는 코드라면, 이 분리가 주는 이득보다 파일을 오가는 비용이 더 클 수 있다.

이 글이 다루지 않은 범위도 밝혀 둔다.

- **결제와 DB 상태의 일관성**: 결제는 성공했는데 `markPaid`가 실패하면 실제로는 돈이 빠져나갔는데 주문은 `payment_failed`가 된다. 변경 전 코드도 이 문제를 해결하지 않았고, 리팩터링은 그 동작을 그대로 보존했을 뿐이다. 보상 트랜잭션이나 멱등 키 설계는 별도 주제다.
- **프레임워크 차원의 재설계**: NestJS의 DI 컨테이너나 모듈 구조를 이용해 계층 전체를 바꾸는 작업은 다루지 않는다. 이 글의 생성자 주입은 프레임워크 없이도 동작하는 최소 형태다.
- **Jest 설정**: ts-jest 같은 변환기 설정이나 커버리지 리포트 구성은 다루지 않는다.

<!-- sec:conclusion -->
## 정리: jest.mock을 쓸지 판단하는 기준

`jest.mock` 호출이 늘어난다는 것은 테스트 대상이 구체 모듈을 직접 끌어다 쓰고 있다는 신호다. 이 글은 그 신호를 따라 의존 방향을 세 단계로 바꿨다. 서비스가 필요한 행위를 포트로 추출했고, 생성자 주입으로 구체 모듈을 바깥에서 받게 했고, 테스트에서는 경로 모킹 대신 페이크와 스파이를 주입했다.

새 테스트를 쓰다가 `jest.mock`을 꺼내 들고 싶어질 때, 다음 질문으로 판단할 수 있다.

1. 가로채려는 대상이 업무 규칙을 담은 코드의 의존인가, 아니면 SDK를 감싸는 어댑터 내부의 의존인가? 업무 규칙 쪽이라면 포트를 먼저 의심한다.
2. 가짜 객체가 흉내 내야 하는 모양을 내 코드가 정했는가, 외부 라이브러리가 정했는가? 외부가 정한 모양을 흉내 내고 있다면 그 결합은 서비스 테스트에 들어오면 안 된다.
3. 검증 문장이 업무 결과를 말하는가, 내부 호출 순서를 말하는가? 호출 순서를 말한다면 그 의존을 페이크로 바꿀 수 있는지 살핀다.
4. 생성자로 넘길 방법이 정말 없는가? 수정할 수 없는 레거시 모듈처럼 주입 지점이 없는 경우라면, 경로 모킹을 그 경계에 한정해서 쓴다.

모킹 도구는 잘못이 없다. 다만 서비스 테스트에서 경로 모킹이 필요해지는 순간, 개발자는 그 지점의 의존 방향부터 다시 보는 편이 낫다.

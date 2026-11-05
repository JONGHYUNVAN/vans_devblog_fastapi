import type { A } from './typeKeyword';
import { B } from './usedAsType';
import { type C } from './typeSpecifier';
import { shape } from './typeofOnly';
import { value, type D } from './value';
import './sideEffect';
import { Comp } from './Comp';

export type { E } from './reexportedType';
export { again } from './reexportedValue';

type Shape = typeof shape;

export function main(a: A, b: B, c: C, d: D, s: Shape) {
  void import('./lazy');
  return <Comp key={value} data-a={a} data-b={b} data-c={c} data-d={d} data-s={s} />;
}

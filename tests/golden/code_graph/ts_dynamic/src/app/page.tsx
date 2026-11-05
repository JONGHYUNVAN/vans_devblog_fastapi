import dynamic from 'next/dynamic';

const Lazy = dynamic(() => import('@/components/Lazy'));

export default function Page({ name }: { name: string }) {
  void import(`@/components/${name}`);
  return <Lazy />;
}

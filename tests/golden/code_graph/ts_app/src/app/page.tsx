import { Button } from '@/components/Button';
import { useThing } from '@/hooks/useThing';

export default function Page() {
  const thing = useThing();
  return <Button label={thing} />;
}

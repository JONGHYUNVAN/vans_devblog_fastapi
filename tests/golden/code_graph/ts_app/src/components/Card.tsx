import { fetchPosts } from '../lib/api';
import { format } from '@/lib/format.service';

export function Card() {
  return <div>{format(String(fetchPosts))}</div>;
}

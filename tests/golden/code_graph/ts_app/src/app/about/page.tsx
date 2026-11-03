import { fetchPosts } from '@/lib/api';

export default function AboutPage() {
  return <p>{String(fetchPosts)}</p>;
}

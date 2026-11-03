import { fetchPosts } from '@/lib/api';

export async function GET() {
  return fetchPosts();
}

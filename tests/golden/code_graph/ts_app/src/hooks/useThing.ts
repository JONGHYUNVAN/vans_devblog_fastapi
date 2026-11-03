import { fetchPosts } from '@/lib/api';

export const useThing = () => String(fetchPosts);

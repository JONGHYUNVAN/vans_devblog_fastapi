import { API_URL } from '@/constants/site';
import { store } from '@/store';

interface Post {
  id: Id;
}
type Id = string;
enum Kind {
  A,
}

class Client {
  get(): Post[] {
    return [];
  }
}

export const fetchPosts = async () => fetch(API_URL + String(store) + Kind.A + new Client().get());

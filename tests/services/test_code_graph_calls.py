"""호출 지점·inbound 엔드포인트 추출 테스트. vans_story BFF·NestJS 클라이언트·이 앱 발행 클라이언트에 실제로 있는 패턴을 그대로 쓴다."""

from tree_sitter_language_pack import get_parser

from app.services.code_graph_calls import extract_next_routes, extract_python_calls, extract_ts_calls


def _ts_calls(source: str, path: str = "src/app/api/demo/route.ts") -> list[tuple[str, str, str, str]]:
    root = get_parser("typescript").parse(source.encode()).root_node
    return [
        (each.caller_symbol, each.env_var, each.http_method, each.raw_path) for each in extract_ts_calls(path, root)
    ]


def test_ts_const_alias_and_fetch_method():
    source = """
export async function POST(request) {
  const apiUrl = process.env.POST_API_URL || 'http://localhost:3001/api/v1';
  const res = await fetch(`${apiUrl}/posts`, { method: 'POST', body: '{}' });
}
"""
    assert _ts_calls(source) == [("POST", "POST_API_URL", "POST", "/posts")]


def test_ts_ternary_into_const_then_fetch_defaults_to_get():
    source = """
export async function GET(request) {
  const apiUrl = process.env.POST_API_URL || 'x';
  const fullUrl = queryString ? `${apiUrl}/posts?${queryString}` : `${apiUrl}/posts`;
  console.log('API 호출:', fullUrl);
  const res = await fetch(fullUrl, { headers: {} });
}
"""
    assert _ts_calls(source) == [
        ("GET", "POST_API_URL", "GET", "/posts"),
        ("GET", "POST_API_URL", "GET", "/posts?${queryString}"),
    ]


def test_ts_argless_function_alias():
    source = """
const djangoApiUrl = () => process.env.DJANGO_API_URL || 'http://localhost:8001';
export async function GET(_request, { params }) {
  const res = await fetch(`${djangoApiUrl()}/api/kis/snapshot/${symbol}/`, { cache: 'no-store' });
}
"""
    assert _ts_calls(source) == [("GET", "DJANGO_API_URL", "GET", "/api/kis/snapshot/${symbol}/")]


def test_ts_env_directly_in_template():
    source = """
export async function POST(request) {
  const res = await fetch(`${process.env.AUTH_API_URL}/auth/login`, { method: 'POST' });
}
"""
    assert _ts_calls(source) == [("POST", "AUTH_API_URL", "POST", "/auth/login")]


def test_ts_proxy_helper_object_in_second_argument():
    source = """
export async function GET(request) {
  return proxyGet(request, {
    baseUrl: process.env.SEARCH_API_URL || API_CONFIG.ENV_DEFAULTS.SEARCH_API_URL,
    path: `/api/v1/search/autocomplete/?query=${encodedQuery}`,
    revalidate: 300,
  });
}
"""
    assert _ts_calls(source) == [("GET", "SEARCH_API_URL", "GET", "/api/v1/search/autocomplete/?query=${encodedQuery}")]


def test_ts_new_url_then_to_string():
    source = """
export async function getPostList(page) {
  const apiUrl = process.env.POST_API_URL || 'x';
  const url = new URL(`${apiUrl}/posts`);
  const response = await fetch(url.toString(), { method: 'GET' });
}
"""
    assert _ts_calls(source, "src/lib/posts/clientActions.ts") == [("getPostList", "POST_API_URL", "GET", "/posts")]


def test_ts_nestjs_config_service_and_http_service():
    source = """
export class UserServiceClient {
  private readonly userServiceUrl: string;
  constructor(private readonly httpService: HttpService, configService: ConfigService) {
    this.userServiceUrl = configService.getOrThrow<string>('USER_SERVICE_URL');
  }
  async getNicknameByEmail(email: string) {
    const { data } = await firstValueFrom(this.httpService.get(`${this.userServiceUrl}/api/v1/users/email/${email}`, {}));
  }
}
export class SearchServiceClient {
  async removePostFromIndex(postId: string) {
    const searchServiceUrl = this.configService.get<string>('SEARCH_SERVICE_URL');
    await fetch(`${searchServiceUrl}/api/internal/search/index/${postId}/`, { method: 'DELETE' });
  }
}
"""
    assert _ts_calls(source, "src/user-service/user-service.client.ts") == [
        (
            "SearchServiceClient.removePostFromIndex",
            "SEARCH_SERVICE_URL",
            "DELETE",
            "/api/internal/search/index/${postId}/",
        ),
        ("UserServiceClient.getNicknameByEmail", "USER_SERVICE_URL", "GET", "/api/v1/users/email/${email}"),
    ]


def test_ts_ignores_templates_not_passed_to_a_call_and_non_env_bases():
    source = """
export async function GET(request) {
  const apiUrl = process.env.POST_API_URL || 'x';
  console.log(`${apiUrl}/posts`);
  const other = `${apiUrl}/never-called`;
  await fetch(`${API_URL}/constant-base`);
  const timeout = Number(process.env.AI_API_TIMEOUT_MS || 60000);
}
"""
    assert _ts_calls(source) == []


def test_ts_alias_scope_is_the_enclosing_function():
    source = """
export async function GET() {
  const apiUrl = process.env.POST_API_URL || 'x';
  await fetch(`${apiUrl}/posts`);
}
export async function POST() {
  const apiUrl = process.env.AUTH_API_URL || 'y';
  await fetch(`${apiUrl}/auth/login`, { method: 'POST' });
}
"""
    assert _ts_calls(source) == [
        ("GET", "POST_API_URL", "GET", "/posts"),
        ("POST", "AUTH_API_URL", "POST", "/auth/login"),
    ]


def test_python_self_url_field_f_string():
    source = """
class PostBEPublisher:
    async def _login(self, client):
        response = await client.post(f"{self._user_service_url}/api/v1/auth/login", json={})

    async def list_categories(self, client):
        await client.get(f"{self._post_be_url}/api/v1/categories", params={})
        logger.info(f"{self._post_be_url}/not-a-call")
        await client.get(f"{self._rest_token}/not-a-url-field")
"""
    root = get_parser("python").parse(source.encode()).root_node
    calls = extract_python_calls("app/clients/publisher/post_be.py", root)

    assert [(each.caller_symbol, each.env_var, each.http_method, each.raw_path) for each in calls] == [
        ("PostBEPublisher._login", "USER_SERVICE_URL", "POST", "/api/v1/auth/login"),
        ("PostBEPublisher.list_categories", "POST_BE_URL", "GET", "/api/v1/categories"),
    ]


def test_next_routes_from_file_path_and_exports():
    source = """
export async function GET(request) {}
export const PATCH = async () => {};
export async function helper() {}
export const dynamic = 'force-dynamic';
"""
    root = get_parser("typescript").parse(source.encode()).root_node

    routes = extract_next_routes("src/app/api/posts/(server)/[id]/route.ts", root, "src/app")

    assert [(each.http_method, each.path) for each in routes] == [
        ("GET", "/api/posts/{id}"),
        ("PATCH", "/api/posts/{id}"),
    ]
    assert extract_next_routes("src/app/api/posts/page.tsx", root, "src/app") == []
    assert extract_next_routes("src/lib/route.ts", root, "src/app") == []

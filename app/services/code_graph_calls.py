"""서비스 간 호출 지점·Next.js inbound 엔드포인트 추출 (code_graph_design/README.md "서비스 그래프" 1절).

파싱이 끝난 tree-sitter 트리를 받아 저장소 설정의 call_sites 파일에서만 찾는다. 상류 URL 은 기준 URL(환경변수) 뒤에
경로를 붙인 문자열로 만들어진다는 것만 가정한다.

- TS: 기준 URL 은 `process.env.X`·`configService.get(OrThrow)('X')` 이거나, 그것을 받은 `const`, 인자 없는 함수,
  `this.필드` 다. 템플릿 리터럴 `${기준}/경로` 와 `proxyGet({baseUrl: 기준, path: ...})` 같은 객체 리터럴을 본다.
  메서드는 그 값을 받는 호출에서 읽는다: `fetch` 는 옵션의 `method`(기본 GET), 그 밖은 함수 이름(`get`, `proxyPost`).
  호출을 찾지 못한 URL 은 버린다(로그 문자열 등).
- Python: f-string `f"{self._x_url}/경로"` 이고 환경변수는 필드 이름을 대문자로 바꾼 것이다(Settings 의 이름 규칙).
"""

import posixpath
import re
from collections.abc import Iterator

from tree_sitter import Node

from app.models.code_graph import InboundRoute, OutboundCall

HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
_ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]*")
_METHOD_SUFFIX = re.compile(r"(get|post|put|patch|delete)$", re.I)
_TS_SCOPES = {"function_declaration", "method_definition", "arrow_function", "function_expression", "program"}
# URL 값이 호출 인자로 가기 전에 거칠 수 있는 식
_TS_PASS_THROUGH = {"ternary_expression", "parenthesized_expression", "binary_expression", "await_expression"}
_PY_URL_FIELD = re.compile(r"_?([a-z][a-z0-9_]*_url)")


def extract_ts_calls(path: str, root_node: Node) -> list[OutboundCall]:
    aliases = _ts_aliases(root_node)
    calls = []
    for node in _walk(root_node):
        if node.type == "template_string":
            site = _ts_template_site(node, aliases)
        elif node.type == "object":
            site = _ts_proxy_site(node, aliases)
        else:
            continue
        if site is None:
            continue
        env_var, raw_path = site
        # 헬퍼 설정 객체는 `proxyGet(request, {...})` 처럼 첫 인자가 아니어도 된다
        method = _ts_method(node, first_argument_only=node.type == "template_string")
        if method is not None:
            calls.append(
                OutboundCall(
                    caller_file=path,
                    caller_symbol=_ts_caller(node),
                    env_var=env_var,
                    http_method=method,
                    raw_path=raw_path,
                )
            )
    return _unique(calls)


def extract_python_calls(path: str, root_node: Node) -> list[OutboundCall]:
    calls = []
    for node in _walk(root_node):
        if node.type != "string" or not node.children or not _text(node.children[0]).lower().startswith("f"):
            continue
        parts = node.children[1:-1]
        if not parts or parts[0].type != "interpolation":
            continue
        expression = parts[0].child_by_field_name("expression")
        field = _py_self_field(expression)
        match = _PY_URL_FIELD.fullmatch(field or "")
        if match is None:
            continue
        method = _py_method(node)
        if method is not None:
            calls.append(
                OutboundCall(
                    caller_file=path,
                    caller_symbol=_py_caller(node),
                    env_var=match.group(1).upper(),
                    http_method=method,
                    raw_path="".join(_text(part) for part in parts[1:]),
                )
            )
    return _unique(calls)


def extract_next_routes(path: str, root_node: Node, route_root: str) -> list[InboundRoute]:
    """route_root 아래 route.ts 가 export 하는 GET·POST 등. `(group)` 디렉터리는 URL 에 안 들어가고 `[id]` 는 `{id}` 다."""
    if posixpath.basename(path) not in ("route.ts", "route.tsx") or not path.startswith(route_root.rstrip("/") + "/"):
        return []
    segments = []
    for segment in posixpath.dirname(path)[len(route_root.rstrip("/")) :].split("/"):
        if not segment or (segment.startswith("(") and segment.endswith(")")):
            continue
        if segment.startswith("[") and segment.endswith("]"):
            segment = "{" + segment.strip("[]").removeprefix("...") + "}"
        segments.append(segment)
    url = "/" + "/".join(segments)
    routes = []
    for statement in root_node.named_children:
        declaration = statement.child_by_field_name("declaration") if statement.type == "export_statement" else None
        for name in _exported_names(declaration):
            if name in HTTP_METHODS:
                routes.append(InboundRoute(file=path, http_method=name, path=url))
    return routes


def _exported_names(declaration: Node | None) -> list[str]:
    if declaration is None:
        return []
    if declaration.type == "function_declaration":
        name = declaration.child_by_field_name("name")
        return [_text(name)] if name is not None else []
    if declaration.type == "lexical_declaration":
        return [
            _text(name)
            for child in declaration.named_children
            if child.type == "variable_declarator" and (name := child.child_by_field_name("name")) is not None
        ]
    return []


def _ts_aliases(root_node: Node) -> dict[tuple[int, str], str]:
    """(스코프 시작 바이트, 이름) → 환경변수. 인자 없는 함수는 `이름()`, 필드는 `this.이름` 이고 둘 다 파일 전체 스코프다."""
    aliases: dict[tuple[int, str], str] = {}
    for node in _walk(root_node):
        if node.type == "variable_declarator":
            name, value = node.child_by_field_name("name"), node.child_by_field_name("value")
            if name is None or value is None or name.type != "identifier":
                continue
            if value.type == "arrow_function":
                body = value.child_by_field_name("body")
                parameters = value.child_by_field_name("parameters")
                env_var = _ts_env(body) if parameters is not None and not parameters.named_children else None
                if env_var is not None:
                    aliases[(0, _text(name) + "()")] = env_var
            elif (env_var := _ts_env(value)) is not None:
                aliases[(_ts_scope(node).start_byte, _text(name))] = env_var
        elif node.type == "assignment_expression":
            left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
            if left is not None and right is not None and _text(left).startswith("this."):
                env_var = _ts_env(right)
                if env_var is not None:
                    aliases[(0, _text(left))] = env_var
    return aliases


def _ts_env(node: Node | None) -> str | None:
    """`process.env.X`, `configService.get('X')`·`getOrThrow`, 그리고 그것의 `|| 기본값`·`??`·괄호·`!`."""
    if node is None:
        return None
    if node.type in ("parenthesized_expression", "non_null_expression", "as_expression", "await_expression"):
        return _ts_env(node.named_children[0] if node.named_children else None)
    if node.type == "binary_expression":
        return _ts_env(node.child_by_field_name("left"))
    if node.type == "member_expression":
        target = node.child_by_field_name("object")
        prop = node.child_by_field_name("property")
        if target is not None and prop is not None and _text(target) == "process.env":
            return _text(prop)
        return None
    if node.type == "call_expression":
        function = node.child_by_field_name("function")
        arguments = node.child_by_field_name("arguments")
        prop = function.child_by_field_name("property") if function is not None else None
        if prop is not None and _text(prop) in ("get", "getOrThrow") and arguments is not None:
            first = next(iter(arguments.named_children), None)
            if first is not None and first.type == "string":
                name = _text(first).strip("'\"`")
                return name if _ENV_NAME.fullmatch(name) else None
    return None


def _ts_base(node: Node, aliases: dict[tuple[int, str], str]) -> str | None:
    """기준 URL 식의 환경변수. 직접 읽기, 별칭 이름, 인자 없는 함수 호출, this 필드."""
    env_var = _ts_env(node)
    if env_var is not None:
        return env_var
    if node.type == "identifier":
        name = _text(node)
        scope = node.parent
        while scope is not None:
            if scope.type in _TS_SCOPES and (scope.start_byte, name) in aliases:
                return aliases[(scope.start_byte, name)]
            scope = scope.parent
        return aliases.get((0, name))
    if node.type == "call_expression":
        arguments = node.child_by_field_name("arguments")
        if arguments is not None and not arguments.named_children:
            return aliases.get((0, _text(node.child_by_field_name("function")) + "()"))
    if node.type == "member_expression" and _text(node).startswith("this."):
        return aliases.get((0, _text(node)))
    return None


def _ts_template_site(node: Node, aliases: dict[tuple[int, str], str]) -> tuple[str, str] | None:
    parts = [child for child in node.children if child.type != "`"]
    if not parts or parts[0].type != "template_substitution" or not parts[0].named_children:
        return None
    env_var = _ts_base(parts[0].named_children[0], aliases)
    if env_var is None:
        return None
    return env_var, "".join(_text(part) for part in parts[1:])


def _ts_proxy_site(node: Node, aliases: dict[tuple[int, str], str]) -> tuple[str, str] | None:
    """`{baseUrl: 기준, path: '/경로'}` 객체 리터럴 (vans_story lib/api/apiProxy.ts 헬퍼 인자)."""
    pairs = {}
    for pair in node.named_children:
        key, value = pair.child_by_field_name("key"), pair.child_by_field_name("value")
        if pair.type == "pair" and key is not None and value is not None:
            pairs[_text(key)] = value
    if "baseUrl" not in pairs or "path" not in pairs or pairs["path"].type not in ("string", "template_string"):
        return None
    env_var = _ts_base(pairs["baseUrl"], aliases)
    if env_var is None:
        return None
    return env_var, _text(pairs["path"])[1:-1]


def _ts_method(node: Node, *, first_argument_only: bool) -> str | None:
    """URL 값을 인자로 받는 호출의 HTTP 메서드. `new URL(...)` 과 const 한 단계는 따라간다."""
    current = node
    while (parent := current.parent) is not None:
        if parent.type == "arguments":
            call = parent.parent
            if call is None:
                return None
            if call.type == "new_expression":
                current = call
                continue
            if first_argument_only and parent.named_children[0] != current:
                return None
            return _ts_call_method(call)
        if parent.type in _TS_PASS_THROUGH:
            current = parent
            continue
        if parent.type == "variable_declarator":
            name = parent.child_by_field_name("name")
            return _ts_method_by_name(_text(name), _ts_scope(parent)) if name is not None else None
        return None
    return None


def _ts_method_by_name(name: str, scope: Node) -> str | None:
    # const fullUrl = `...`; fetch(fullUrl) 또는 fetch(url.toString())
    for node in _walk(scope):
        if node.type != "call_expression":
            continue
        arguments = node.child_by_field_name("arguments")
        first = next(iter(arguments.named_children), None) if arguments is not None else None
        if first is not None and _text(first) in (name, f"{name}.toString()", f"{name}.href"):
            return _ts_call_method(node)
    return None


def _ts_call_method(call: Node) -> str | None:
    function = call.child_by_field_name("function")
    if function is None:
        return None
    name = _text(function.child_by_field_name("property") or function)
    if name == "fetch":
        arguments = call.child_by_field_name("arguments")
        options = arguments.named_children[1] if arguments is not None and len(arguments.named_children) > 1 else None
        for pair in options.named_children if options is not None and options.type == "object" else []:
            key, value = pair.child_by_field_name("key"), pair.child_by_field_name("value")
            if key is not None and _text(key) == "method" and value is not None and value.type == "string":
                return _text(value).strip("'\"`").upper()
        return "GET"
    match = _METHOD_SUFFIX.search(name)
    return match.group(1).upper() if match else None


def _ts_scope(node: Node) -> Node:
    scope = node.parent
    while scope is not None and scope.type not in _TS_SCOPES:
        scope = scope.parent
    return scope if scope is not None else node


def _ts_caller(node: Node) -> str:
    """감싼 함수 이름. 메서드는 `클래스.메서드`, const 화살표 함수는 그 이름이다."""
    current = node.parent
    while current is not None:
        if current.type in ("function_declaration", "method_definition"):
            name = _text(current.child_by_field_name("name"))
            owner = _ancestor(current, "class_declaration")
            return f"{_text(owner.child_by_field_name('name'))}.{name}" if owner is not None else name
        if (
            current.type == "arrow_function"
            and current.parent is not None
            and current.parent.type == "variable_declarator"
        ):
            return _text(current.parent.child_by_field_name("name"))
        current = current.parent
    return ""


def _py_self_field(node: Node | None) -> str | None:
    if node is None or node.type != "attribute":
        return None
    target, attribute = node.child_by_field_name("object"), node.child_by_field_name("attribute")
    if target is None or attribute is None or _text(target) != "self":
        return None
    return _text(attribute)


def _py_method(node: Node) -> str | None:
    """f-string 을 첫 인자로 받는 `client.get(...)`·`client.post(...)` 의 메서드."""
    arguments = node.parent
    if arguments is None or arguments.type != "argument_list" or arguments.named_children[0] != node:
        return None
    call = arguments.parent
    function = call.child_by_field_name("function") if call is not None else None
    attribute = function.child_by_field_name("attribute") if function is not None else None
    name = _text(attribute).upper() if attribute is not None else ""
    return name if name in HTTP_METHODS else None


def _py_caller(node: Node) -> str:
    function = _ancestor(node, "function_definition")
    if function is None:
        return ""
    name = _text(function.child_by_field_name("name"))
    owner = _ancestor(function, "class_definition")
    return f"{_text(owner.child_by_field_name('name'))}.{name}" if owner is not None else name


def _ancestor(node: Node, node_type: str) -> Node | None:
    current = node.parent
    while current is not None and current.type != node_type:
        current = current.parent
    return current


def _unique(calls: list[OutboundCall]) -> list[OutboundCall]:
    keyed = {(each.caller_symbol, each.env_var, each.http_method, each.raw_path): each for each in calls}
    return [keyed[key] for key in sorted(keyed)]


def _walk(node: Node) -> Iterator[Node]:
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def _text(node: Node | None) -> str:
    return node.text.decode("utf-8", "replace") if node is not None and node.text is not None else ""

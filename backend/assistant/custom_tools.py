"""
自定义工具 — 管理员在界面上添加，无需改代码

  http  调用企业内部 / 第三方 HTTP 接口（CRM、工单、库存…）
        - 地址由管理员固定，模型只能填参数；参数按 URL 编码放进路径占位符、查询串或 JSON 请求体
        - 默认禁止访问内网 / 本机地址（防 SSRF），确需访问的内网服务加入 TOOL_HTTP_ALLOWED_HOSTS
        - 凭证（如 Authorization 头）加密存储，响应超长截断
  sql   参数化的 SQL 模板（如「按省份和月份查 GMV」），把常用口径固化下来
        - 保存时经过 sql_guard 校验；执行时参数由数据库驱动绑定，仍走只读角色 + READ ONLY 事务
出于安全考虑不支持执行任意代码。
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from backend.assistant.tokens import truncate_to_tokens
from backend.config import TOOL_HTTP_ALLOWED_HOSTS, TOOL_HTTP_TIMEOUT_SECONDS
from backend.database import execute_readonly_sql
from backend.secrets_store import decrypt
from backend.sql_guard import strip_trailing_semicolon, validate_sql

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
_PARAM_TYPES = {"string", "integer", "number", "boolean"}
_SQL_PARAM = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")
_MAX_RESPONSE_BYTES = 200_000


class ToolConfigError(ValueError):
    pass


# ---------------------------------------------------------------------------
# 参数 Schema
# ---------------------------------------------------------------------------


def validate_schema(schema: dict) -> dict:
    """只接受扁平的对象 Schema（string / integer / number / boolean，可带 enum），足够覆盖业务工具。"""
    if not isinstance(schema, dict) or schema.get("type", "object") != "object":
        raise ToolConfigError("参数定义必须是 JSON Schema 对象")
    props = schema.get("properties") or {}
    if not isinstance(props, dict) or len(props) > 20:
        raise ToolConfigError("参数最多 20 个")
    clean = {}
    for name, spec in props.items():
        if not re.fullmatch(r"[A-Za-z_]\w{0,40}", name):
            raise ToolConfigError(f"参数名 {name} 不合法（字母、数字、下划线）")
        if spec.get("type") not in _PARAM_TYPES:
            raise ToolConfigError(f"参数 {name} 的类型必须是 {', '.join(sorted(_PARAM_TYPES))}")
        item = {"type": spec["type"], "description": str(spec.get("description", ""))[:200]}
        if spec.get("enum"):
            item["enum"] = [str(v) if spec["type"] == "string" else v for v in spec["enum"]][:50]
        clean[name] = item
    required = [r for r in schema.get("required") or [] if r in clean]
    return {"type": "object", "properties": clean, "required": required}


def coerce_args(schema: dict, args: dict) -> dict:
    """按 Schema 校验并转换模型给出的参数，错误信息会回传给模型以便修正。"""
    props = schema.get("properties", {})
    out = {}
    for name in schema.get("required", []):
        if args.get(name) in (None, ""):
            raise ValueError(f"缺少必填参数 {name}")
    for name, value in args.items():
        if name not in props:
            continue
        typ = props[name]["type"]
        try:
            if typ == "integer":
                value = int(value)
            elif typ == "number":
                value = float(value)
            elif typ == "boolean":
                value = value if isinstance(value, bool) else str(value).lower() in {"true", "1", "yes", "是"}
            else:
                value = str(value)[:500]
        except (TypeError, ValueError):
            raise ValueError(f"参数 {name} 应为 {typ}，收到 {value!r}") from None
        if props[name].get("enum") and value not in props[name]["enum"]:
            raise ValueError(f"参数 {name} 只能是 {props[name]['enum']} 之一")
        out[name] = value
    return out


# ---------------------------------------------------------------------------
# HTTP 工具
# ---------------------------------------------------------------------------


def validate_http_config(config: dict) -> dict:
    method = str(config.get("method", "GET")).upper()
    if method not in {"GET", "POST"}:
        raise ToolConfigError("HTTP 方法只支持 GET / POST")
    url = str(config.get("url", "")).strip()
    parsed = urlparse(url.replace("{", "").replace("}", ""))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ToolConfigError("URL 必须是完整的 http(s) 地址")
    if "{" in parsed.netloc or "{" in (urlparse(url).netloc or ""):
        raise ToolConfigError("参数占位符只能出现在路径中，不能出现在主机名里")
    headers = config.get("headers") or {}
    if not isinstance(headers, dict):
        raise ToolConfigError("headers 必须是对象")
    return {"method": method, "url": url, "headers": {str(k): str(v) for k, v in headers.items()},
            "response_path": str(config.get("response_path", "")).strip()}


def _check_host(url: str) -> None:
    host = (urlparse(url).hostname or "").lower()
    if host in TOOL_HTTP_ALLOWED_HOSTS:
        return
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValueError(f"无法解析主机 {host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError(f"出于安全考虑禁止访问内网地址 {host}（{ip}）；如确需访问，请把它加入 TOOL_HTTP_ALLOWED_HOSTS")


def _pick(data: Any, path: str) -> Any:
    """response_path 形如 data.items，用于只把有用的部分交给模型。"""
    for part in [p for p in path.split(".") if p]:
        if isinstance(data, dict):
            data = data.get(part)
        elif isinstance(data, list) and part.isdigit():
            data = data[int(part)] if int(part) < len(data) else None
        else:
            return None
    return data


def run_http_tool(tool: dict, args: dict) -> tuple[str, dict]:
    config = tool["config"]
    args = coerce_args(tool["parameters"], args)
    url = config["url"]
    remaining = dict(args)
    for name in re.findall(r"\{(\w+)\}", url):
        if name not in remaining:
            raise ValueError(f"缺少路径参数 {name}")
        url = url.replace("{" + name + "}", quote(str(remaining.pop(name)), safe=""))
    _check_host(url)
    secrets = json.loads(decrypt(tool.get("secrets_encrypted")) or "{}")
    headers = {**config.get("headers", {}), **secrets, "Accept": "application/json"}
    with httpx.Client(timeout=TOOL_HTTP_TIMEOUT_SECONDS, follow_redirects=False) as client:
        if config["method"] == "GET":
            resp = client.get(url, params=remaining, headers=headers)
        else:
            resp = client.post(url, json=remaining, headers=headers)
    body = resp.content[:_MAX_RESPONSE_BYTES]
    display: dict[str, Any] = {"url": url.split("?")[0], "status": resp.status_code}
    try:
        data = json.loads(body)
        if config.get("response_path"):
            data = _pick(data, config["response_path"])
        text_out = json.dumps(data, ensure_ascii=False, indent=1)
        display["json"] = data
    except (json.JSONDecodeError, UnicodeDecodeError):
        text_out = body.decode("utf-8", errors="replace")
        display["text"] = text_out[:5000]
    if resp.status_code >= 400:
        raise ValueError(f"接口返回 HTTP {resp.status_code}：{text_out[:300]}")
    return truncate_to_tokens(text_out, 2500), display


# ---------------------------------------------------------------------------
# SQL 模板工具
# ---------------------------------------------------------------------------


def validate_sql_config(config: dict, schema: dict) -> dict:
    sql = str(config.get("sql", "")).strip()
    probe = _SQL_PARAM.sub("NULL", sql)  # 参数占位替换后做安全校验
    ok, reason = validate_sql(probe)
    if not ok:
        raise ToolConfigError(f"SQL 未通过安全校验：{reason}")
    used = set(_SQL_PARAM.findall(sql))
    declared = set(schema.get("properties", {}))
    if used - declared:
        raise ToolConfigError(f"SQL 中的参数 {', '.join(sorted(used - declared))} 没有在参数定义中声明")
    max_rows = int(config.get("max_rows") or 200)
    return {"sql": strip_trailing_semicolon(sql), "max_rows": max(1, min(max_rows, 2000))}


def run_sql_tool(tool: dict, args: dict) -> tuple[str, dict]:
    args = coerce_args(tool["parameters"], args)
    sql = tool["config"]["sql"]
    for name in set(_SQL_PARAM.findall(sql)):
        args.setdefault(name, None)
    # :name → %(name)s；先转义模板里原有的 %，避免被驱动当作占位符
    driver_sql = _SQL_PARAM.sub(lambda m: f"%({m.group(1)})s", sql.replace("%", "%%"))
    result = execute_readonly_sql(driver_sql, max_rows=tool["config"].get("max_rows", 200), params=args)
    from backend.assistant.tools import markdown_table

    text_out = f"返回 {result['row_count']} 行：\n{markdown_table(result['columns'], result['data'])}"
    return truncate_to_tokens(text_out, 2500), {
        "sql": sql, "params": args, "columns": result["columns"], "data": result["data"],
        "row_count": result["row_count"], "truncated": result["truncated"],
    }

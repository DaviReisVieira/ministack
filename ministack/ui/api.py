"""
UI Dashboard API — REST endpoints and SSE streams for the MiniStack dashboard.

Endpoints:
  GET /_ministack/api/stats              — aggregated resource counts
  GET /_ministack/api/requests           — recent request history
  GET /_ministack/api/requests/stream    — SSE real-time request stream
  GET /_ministack/api/logs/stream        — SSE real-time log stream
  GET /_ministack/api/resources/{svc}    — list resources for a service
  GET /_ministack/api/resources/{svc}/{type}/{id} — single resource detail
"""

import asyncio
import json
import logging

from ministack.ui import interceptor
from ministack.ui.log_handler import ui_log_handler

logger = logging.getLogger("ministack.ui")

API_PREFIX = "/_ministack/api"


def _build_resource_registry():
    """Build the registry lazily on first call (avoids import-time circular deps)."""
    from ministack.services import (
        acm,
        alb,
        apigateway,
        apigateway_v1,
        appsync,
        athena,
        cloudfront,
        cloudwatch,
        cloudwatch_logs,
        cognito,
        dynamodb,
        ec2,
        ecr,
        ecs,
        efs,
        elasticache,
        emr,
        eventbridge,
        firehose,
        glue,
        iam_sts,
        kinesis,
        kms,
        lambda_svc,
        rds,
        route53,
        s3,
        secretsmanager,
        ses,
        ses_v2,
        sns,
        sqs,
        ssm,
        stepfunctions,
        waf,
    )

    return {
        "s3": [("buckets", s3, "_buckets")],
        "sqs": [("queues", sqs, "_queues")],
        "sns": [("topics", sns, "_topics")],
        "dynamodb": [("tables", dynamodb, "_tables")],
        "lambda": [("functions", lambda_svc, "_functions"), ("layers", lambda_svc, "_layers")],
        "iam": [("users", iam_sts, "_users"), ("roles", iam_sts, "_roles"), ("policies", iam_sts, "_policies")],
        "secretsmanager": [("secrets", secretsmanager, "_secrets")],
        "logs": [("log_groups", cloudwatch_logs, "_log_groups")],
        "ssm": [("parameters", ssm, "_parameters")],
        "events": [("event_buses", eventbridge, "_event_buses"), ("rules", eventbridge, "_rules")],
        "kinesis": [("streams", kinesis, "_streams")],
        "monitoring": [("alarms", cloudwatch, "_alarms"), ("dashboards", cloudwatch, "_dashboards")],
        "ses": [("identities", ses, "_identities") if hasattr(ses, "_identities") else ("templates", ses, "_templates")],
        "states": [("state_machines", stepfunctions, "_state_machines"), ("executions", stepfunctions, "_executions")],
        "ecs": [("clusters", ecs, "_clusters"), ("task_definitions", ecs, "_task_defs"), ("services", ecs, "_services")],
        "rds": [("db_instances", rds, "_instances"), ("db_clusters", rds, "_clusters")],
        "elasticache": [("cache_clusters", elasticache, "_clusters") if hasattr(elasticache, "_clusters") else ("cache_clusters", elasticache, "_cache_clusters")],
        "glue": [("databases", glue, "_databases"), ("tables", glue, "_tables")],
        "athena": [("workgroups", athena, "_workgroups") if hasattr(athena, "_workgroups") else ("queries", athena, "_queries")],
        "apigateway": [("apis", apigateway, "_apis")],
        "firehose": [("delivery_streams", firehose, "_streams")],
        "route53": [("hosted_zones", route53, "_hosted_zones")],
        "cognito-idp": [("user_pools", cognito, "_user_pools")],
        "cognito-identity": [("identity_pools", cognito, "_identity_pools")],
        "ec2": [
            ("instances", ec2, "_instances"),
            ("vpcs", ec2, "_vpcs"),
            ("subnets", ec2, "_subnets"),
            ("security_groups", ec2, "_security_groups"),
            ("volumes", ec2, "_volumes"),
        ],
        "elasticmapreduce": [("clusters", emr, "_clusters")],
        "elasticloadbalancing": [("load_balancers", alb, "_load_balancers")],
        "elasticfilesystem": [("file_systems", efs, "_file_systems")],
        "kms": [("keys", kms, "_keys")],
        "cloudfront": [("distributions", cloudfront, "_distributions")],
        "appsync": [("graphql_apis", appsync, "_apis")],
        "ecr": [("repositories", ecr, "_repositories")],
        "acm": [("certificates", acm, "_certificates")],
        "wafv2": [("web_acls", waf, "_web_acls")],
        "cloudformation": [],
    }


_registry = None


def _get_registry():
    global _registry
    if _registry is None:
        _registry = _build_resource_registry()
    return _registry


async def handle(method: str, path: str, query_params: dict, receive, send):
    """Route /_ministack/api/* requests to the appropriate handler."""
    rel = path[len(API_PREFIX):]

    if rel == "/stats" and method == "GET":
        await _handle_stats(send)
    elif rel == "/requests/stream" and method == "GET":
        await _handle_sse(receive, send, interceptor.subscribe)
    elif rel == "/requests" and method == "GET":
        limit = int(query_params.get("limit", ["50"])[0] if isinstance(query_params.get("limit"), list) else query_params.get("limit", "50"))
        offset = int(query_params.get("offset", ["0"])[0] if isinstance(query_params.get("offset"), list) else query_params.get("offset", "0"))
        await _json_response(send, interceptor.get_requests(limit, offset))
    elif rel == "/logs/stream" and method == "GET":
        await _handle_sse(receive, send, ui_log_handler.subscribe)
    elif rel == "/logs" and method == "GET":
        limit = int(query_params.get("limit", ["100"])[0] if isinstance(query_params.get("limit"), list) else query_params.get("limit", "100"))
        await _json_response(send, {"logs": ui_log_handler.get_recent(limit)})
    elif rel.startswith("/resources/") and method == "GET":
        await _handle_resources(rel[len("/resources/"):], query_params, send)
    else:
        await _json_response(send, {"error": f"Unknown UI API endpoint: {path}"}, status=404)


async def _handle_stats(send):
    """Return aggregated resource counts for all services."""
    registry = _get_registry()
    services = {}
    total = 0

    for svc_name, entries in registry.items():
        resources = {}
        for label, module, attr_name in entries:
            try:
                obj = getattr(module, attr_name, None)
                count = len(obj) if obj is not None else 0
            except Exception:
                count = 0
            resources[label] = count
            total += count
        services[svc_name] = {
            "status": "available",
            "resources": resources,
        }

    await _json_response(send, {
        "services": services,
        "total_resources": total,
        "uptime_seconds": interceptor.get_uptime(),
    })


async def _handle_resources(rel_path: str, query_params: dict, send):
    """Handle /resources/{service} and /resources/{service}/{type}/{id}."""
    parts = [p for p in rel_path.split("/") if p]
    registry = _get_registry()

    if not parts:
        await _json_response(send, {"error": "Service name required"}, status=400)
        return

    svc_name = parts[0]
    if svc_name not in registry:
        await _json_response(send, {"error": f"Unknown service: {svc_name}"}, status=404)
        return

    entries = registry[svc_name]

    # GET /resources/{service} — list all resource types and their items
    if len(parts) == 1:
        type_filter = query_params.get("type", [""])[0] if isinstance(query_params.get("type"), list) else query_params.get("type", "")
        resources = {}
        for label, module, attr_name in entries:
            if type_filter and label != type_filter:
                continue
            try:
                obj = getattr(module, attr_name, {})
                if isinstance(obj, dict):
                    resources[label] = [
                        _summarize_resource(label, key, value)
                        for key, value in list(obj.items())[:200]
                    ]
                else:
                    resources[label] = [{"id": str(i)} for i in range(min(len(obj), 200))]
            except Exception as e:
                resources[label] = {"error": str(e)}

        await _json_response(send, {"service": svc_name, "resources": resources})
        return

    # GET /resources/{service}/{type}/{id} — single resource detail
    if len(parts) >= 3:
        res_type = parts[1]
        res_id = "/".join(parts[2:])
        for label, module, attr_name in entries:
            if label != res_type:
                continue
            try:
                obj = getattr(module, attr_name, {})
                if isinstance(obj, dict) and res_id in obj:
                    detail = obj[res_id]
                    await _json_response(send, {
                        "service": svc_name,
                        "type": res_type,
                        "id": res_id,
                        "detail": _safe_serialize(detail),
                    })
                    return
            except Exception as e:
                await _json_response(send, {"error": str(e)}, status=500)
                return

        await _json_response(send, {"error": f"Resource not found: {res_type}/{res_id}"}, status=404)
        return

    await _json_response(send, {"error": "Invalid resource path"}, status=400)


def _summarize_resource(res_type: str, key, value) -> dict:
    """Extract a summary dict from a resource state entry."""
    summary = {"id": str(key)}

    if isinstance(value, dict):
        # Common fields to surface in summaries
        for field in ("Name", "name", "Arn", "arn", "ARN", "Status", "status",
                      "State", "state", "CreatedAt", "created", "CreationDate",
                      "Engine", "Runtime", "runtime", "FunctionName", "TableName",
                      "BucketName", "QueueArn", "TopicArn", "Description"):
            if field in value:
                val = value[field]
                if isinstance(val, (str, int, float, bool)):
                    summary[field] = val

        # Count nested items if present (e.g., objects in a bucket)
        for count_field in ("objects", "items", "messages", "records"):
            if count_field in value and isinstance(value[count_field], (dict, list)):
                summary[f"{count_field}_count"] = len(value[count_field])

    return summary


def _safe_serialize(obj):
    """Convert an object to JSON-serializable form, handling bytes and non-serializable types."""
    if isinstance(obj, bytes):
        try:
            return obj.decode("utf-8")
        except UnicodeDecodeError:
            return f"<bytes: {len(obj)} bytes>"
    if isinstance(obj, dict):
        return {k: _safe_serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe_serialize(v) for v in obj]
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    return str(obj)


async def _handle_sse(receive, send, subscribe_fn):
    """Generic SSE handler — streams events from an async generator."""
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [
            (b"content-type", b"text/event-stream"),
            (b"cache-control", b"no-cache"),
            (b"connection", b"keep-alive"),
            (b"access-control-allow-origin", b"*"),
        ],
    })

    # Send initial keepalive
    await send({
        "type": "http.response.body",
        "body": b": connected\n\n",
        "more_body": True,
    })

    async def _watch_disconnect():
        """Wait for client disconnect."""
        while True:
            msg = await receive()
            if msg.get("type") == "http.disconnect":
                return

    disconnect_task = asyncio.create_task(_watch_disconnect())

    try:
        async for data in subscribe_fn():
            if disconnect_task.done():
                break
            payload = f"data: {json.dumps(data)}\n\n".encode()
            await send({
                "type": "http.response.body",
                "body": payload,
                "more_body": True,
            })
    except (asyncio.CancelledError, Exception):
        pass
    finally:
        disconnect_task.cancel()
        try:
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        except Exception:
            pass


async def _json_response(send, data: dict, status: int = 200):
    """Send a JSON response."""
    body = json.dumps(data).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"access-control-allow-origin", b"*"),
        ],
    })
    await send({"type": "http.response.body", "body": body})

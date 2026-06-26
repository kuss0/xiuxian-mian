import asyncio
import json
import re
import time

import requests


# HTTP only by design: this module must not shell out to local codex/claude
# CLIs or read local interactive AI configuration from the VPS.
CODEX_DEFAULT_BASE_URL = "https://api.openai.com/v1"
CLAUDE_DEFAULT_BASE_URL = "https://api.anthropic.com"
CLAUDE_VERSION = "2023-06-01"
RE_JSON_BLOCK = re.compile(r"\{[\s\S]*\}")
RE_ANSWER_FIELD = re.compile(r"(?:answer|答案|选项)\s*[:：=]\s*[\"']?\s*([A-D])\b", re.I)
RE_CONFIDENCE_FIELD = re.compile(r"(?:confidence|置信度)\s*[:：=]\s*([01](?:\.\d+)?|100(?:\.0+)?|\d{1,2}(?:\.\d+)?)", re.I)
MAX_QUIZ_AI_PROVIDERS = 6


def _clamp_float(value, default=0.0, *, min_value=0.0, max_value=1.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    return max(float(min_value), min(float(max_value), number))


def _normal_provider(provider):
    provider = str(provider or "codex").strip().lower()
    return provider if provider in {"codex", "openai", "claude", "anthropic"} else "codex"


def _provider_label(provider):
    provider = _normal_provider(provider)
    return "claude" if provider in {"claude", "anthropic"} else "codex"


def _provider_display_label(config):
    label = str((config or {}).get("label") or "").strip()
    return label or _provider_label((config or {}).get("provider"))


def _join_url(base_url, path):
    base_url = str(base_url or "").strip().rstrip("/")
    path = str(path or "").strip()
    if not base_url:
        return path
    if base_url.endswith(path):
        return base_url
    return base_url + path


def _format_options(options):
    return "\n".join(
        f"{key}. {str((options or {}).get(key) or '').strip()}"
        for key in ("A", "B", "C", "D")
        if str((options or {}).get(key) or "").strip()
    )


def _build_messages(question, options):
    system_prompt = (
        "你是玄骨考校答题辅助。只根据题面和四个选项判断最可能正确的一项。"
        "必须只返回 JSON，格式为 {\"answer\":\"A\",\"confidence\":0.0,\"reason\":\"简短理由\"}。"
        "answer 只能是 A/B/C/D，confidence 必须是 0 到 1 的数字。"
    )
    user_prompt = (
        "题目：\n"
        f"{str(question or '').strip()}\n\n"
        "选项：\n"
        f"{_format_options(options)}"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _post_json(url, headers, payload, timeout):
    response = requests.post(url, headers=headers, json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _extract_codex_text(payload):
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not choices:
        return ""
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str((part or {}).get("text") or "") for part in content if isinstance(part, dict))
    return str(first.get("text") or "")


def _extract_claude_text(payload):
    content = payload.get("content") if isinstance(payload, dict) else None
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and str(part.get("type") or "") == "text":
                parts.append(str(part.get("text") or ""))
        return "\n".join(parts)
    if isinstance(content, str):
        return content
    return ""


def _parse_ai_answer(raw_text):
    text = str(raw_text or "").strip()
    data = None
    if text:
        candidates = [text]
        block = RE_JSON_BLOCK.search(text)
        if block:
            candidates.insert(0, block.group(0))
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except Exception:
                continue
            if isinstance(parsed, dict):
                data = parsed
                break
    if isinstance(data, dict):
        answer = str(data.get("answer") or data.get("答案") or "").strip().upper()
        confidence = _clamp_float(data.get("confidence", data.get("置信度", 0)), 0)
        reason = str(data.get("reason") or data.get("理由") or "").strip()
    else:
        answer_match = RE_ANSWER_FIELD.search(text)
        answer = answer_match.group(1).upper() if answer_match else ""
        confidence_match = RE_CONFIDENCE_FIELD.search(text)
        confidence = _clamp_float(confidence_match.group(1), 0) if confidence_match else 0
        if confidence > 1:
            confidence = confidence / 100.0
        reason = text[:180]
    if answer not in {"A", "B", "C", "D"}:
        answer = ""
    return {
        "answer": answer,
        "confidence": max(0.0, min(1.0, confidence)),
        "reason": reason,
    }


async def _call_codex(question, options, config):
    base_url = str(config.get("base_url") or CODEX_DEFAULT_BASE_URL).strip().rstrip("/")
    model = str(config.get("model") or "").strip()
    if not model:
        return {"ok": False, "error": "AI model 未配置", "provider": _provider_label(config.get("provider"))}
    url = _join_url(base_url, "/chat/completions")
    headers = {"Content-Type": "application/json"}
    api_key = str(config.get("api_key") or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "messages": _build_messages(question, options),
        "temperature": _clamp_float(config.get("temperature"), 0, min_value=0, max_value=2),
        "max_tokens": 200,
    }
    timeout = max(3, int(config.get("timeout_sec") or 20))
    response = await asyncio.to_thread(_post_json, url, headers, payload, timeout)
    raw_text = _extract_codex_text(response)
    parsed = _parse_ai_answer(raw_text)
    return {"ok": bool(parsed.get("answer")), "raw_text": raw_text, "provider": _provider_label(config.get("provider")), **parsed}


async def _call_claude(question, options, config):
    base_url = str(config.get("base_url") or CLAUDE_DEFAULT_BASE_URL).strip().rstrip("/")
    model = str(config.get("model") or "").strip()
    if not model:
        return {"ok": False, "error": "AI model 未配置", "provider": _provider_label(config.get("provider"))}
    url = _join_url(base_url, "/v1/messages")
    api_key = str(config.get("api_key") or "").strip()
    headers = {
        "Content-Type": "application/json",
        "anthropic-version": CLAUDE_VERSION,
    }
    if api_key:
        headers["x-api-key"] = api_key
    messages = _build_messages(question, options)
    payload = {
        "model": model,
        "max_tokens": 200,
        "temperature": _clamp_float(config.get("temperature"), 0, min_value=0, max_value=2),
        "system": messages[0]["content"],
        "messages": [messages[1]],
    }
    timeout = max(3, int(config.get("timeout_sec") or 20))
    response = await asyncio.to_thread(_post_json, url, headers, payload, timeout)
    raw_text = _extract_claude_text(response)
    parsed = _parse_ai_answer(raw_text)
    return {"ok": bool(parsed.get("answer")), "raw_text": raw_text, "provider": _provider_label(config.get("provider")), **parsed}


async def _call_provider(question, options, config):
    started_at = time.time()
    config = config if isinstance(config, dict) else {}
    provider = _normal_provider(config.get("provider"))
    try:
        if provider in {"claude", "anthropic"}:
            result = await _call_claude(question, options, config)
        else:
            result = await _call_codex(question, options, config)
    except requests.RequestException as exc:
        result = {"ok": False, "error": f"AI 请求失败: {exc}", "provider": _provider_label(provider)}
    except Exception as exc:
        result = {"ok": False, "error": f"AI 解析失败: {exc}", "provider": _provider_label(provider)}
    result["elapsed_ms"] = int((time.time() - started_at) * 1000)
    if not result.get("ok") and not result.get("error"):
        result["error"] = "AI 未返回有效答案"
    result["id"] = str(config.get("id") or "").strip()
    result["label"] = _provider_display_label(config)
    result["provider"] = _provider_label(provider)
    return result


def _legacy_provider_from_config(config):
    config = config if isinstance(config, dict) else {}
    return {
        "id": "ai1",
        "enabled": True,
        "label": "AI 1",
        "provider": config.get("provider") or "codex",
        "base_url": config.get("base_url") or "",
        "model": config.get("model") or "",
        "api_key": config.get("api_key") or "",
        "timeout_sec": config.get("timeout_sec") or 20,
        "temperature": config.get("temperature") or 0,
    }


def _enabled_providers(config):
    providers = config.get("providers") if isinstance((config or {}).get("providers"), list) else []
    if not providers:
        providers = [_legacy_provider_from_config(config or {})]
    enabled = []
    for provider in providers[:MAX_QUIZ_AI_PROVIDERS]:
        if not isinstance(provider, dict) or not provider.get("enabled", True):
            continue
        if not str(provider.get("model") or "").strip():
            continue
        enabled.append(dict(provider))
    return enabled


def _sanitize_result(result):
    result = result if isinstance(result, dict) else {}
    answer = str(result.get("answer") or "").strip().upper()
    try:
        confidence = float(result.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0
    try:
        elapsed_ms = int(result.get("elapsed_ms") or 0)
    except (TypeError, ValueError):
        elapsed_ms = 0
    ok = bool(result.get("ok")) and answer in {"A", "B", "C", "D"}
    return {
        "id": str(result.get("id") or "").strip(),
        "label": str(result.get("label") or result.get("provider") or "").strip(),
        "provider": str(result.get("provider") or "").strip(),
        "ok": ok,
        "answer": answer if answer in {"A", "B", "C", "D"} else "",
        "confidence": max(0.0, min(1.0, confidence)),
        "reason": str(result.get("reason") or "").strip(),
        "error": str(result.get("error") or "").strip(),
        "elapsed_ms": max(0, elapsed_ms),
    }


def _select_quiz_ai_vote(results, *, confidence_threshold=0.0):
    threshold = _clamp_float(confidence_threshold, 0)
    sanitized = [_sanitize_result(result) for result in (results or [])]
    valid = [
        result
        for result in sanitized
        if result.get("ok")
        and result.get("answer") in {"A", "B", "C", "D"}
        and float(result.get("confidence") or 0) >= threshold
    ]
    if not valid:
        return {
            "ok": False,
            "answer": "",
            "confidence": 0,
            "reason": "",
            "provider": "",
            "label": "",
            "error": "没有 AI 在线路安全窗口内返回达标答案",
            "results": sanitized,
            "valid_count": 0,
        }
    grouped = {}
    for result in valid:
        grouped.setdefault(result["answer"], []).append(result)
    max_votes = max(len(items) for items in grouped.values())
    tied_answers = [answer for answer, items in grouped.items() if len(items) == max_votes]
    fastest_by_answer = {
        answer: min(items, key=lambda item: int(item.get("elapsed_ms") or 0))
        for answer, items in grouped.items()
    }
    if len(tied_answers) == 1:
        chosen_answer = tied_answers[0]
    else:
        chosen_answer = min(tied_answers, key=lambda answer: int(fastest_by_answer[answer].get("elapsed_ms") or 0))
    chosen_group = grouped[chosen_answer]
    chosen = fastest_by_answer[chosen_answer]
    total_valid = len(valid)
    vote_summary = "/".join(f"{answer}:{len(grouped.get(answer) or [])}" for answer in sorted(grouped))
    return {
        "ok": True,
        "answer": chosen_answer,
        "confidence": max(float(item.get("confidence") or 0) for item in chosen_group),
        "reason": chosen.get("reason") or "",
        "provider": f"vote:{vote_summary}",
        "label": chosen.get("label") or chosen.get("provider") or "",
        "elapsed_ms": int(chosen.get("elapsed_ms") or 0),
        "results": sanitized,
        "valid_count": total_valid,
        "vote_summary": vote_summary,
    }


async def suggest_quiz_answer(question, options, config):
    return await _call_provider(question, options, config)


async def suggest_quiz_answer_multi(question, options, config, *, decision_timeout_sec=None):
    config = config if isinstance(config, dict) else {}
    providers = _enabled_providers(config)
    if not providers:
        return {
            "ok": False,
            "answer": "",
            "confidence": 0,
            "error": "没有启用且已配置 model 的 AI 线路",
            "provider": "",
            "results": [],
            "valid_count": 0,
        }
    try:
        decision_timeout = float(decision_timeout_sec if decision_timeout_sec is not None else config.get("decision_timeout_sec") or 20)
    except (TypeError, ValueError):
        decision_timeout = 20
    decision_timeout = max(1.0, min(60.0, decision_timeout))
    task_providers = {}
    for provider in providers:
        provider = dict(provider)
        try:
            provider_timeout = float(provider.get("timeout_sec") or config.get("timeout_sec") or decision_timeout)
        except (TypeError, ValueError):
            provider_timeout = decision_timeout
        provider["timeout_sec"] = max(1.0, min(provider_timeout, decision_timeout))
        task = asyncio.create_task(_call_provider(question, options, provider))
        task_providers[task] = provider
    tasks = list(task_providers)
    done, pending = await asyncio.wait(tasks, timeout=decision_timeout)
    for task in pending:
        task.cancel()
    results = []
    for task in done:
        try:
            results.append(task.result())
        except Exception as exc:
            results.append({"ok": False, "error": f"AI 任务失败: {exc}", "elapsed_ms": int(decision_timeout * 1000)})
    for task in pending:
        provider = task_providers.get(task) or {}
        results.append({
            "id": str(provider.get("id") or "").strip(),
            "label": _provider_display_label(provider),
            "provider": _provider_label(provider.get("provider")),
            "ok": False,
            "error": "AI 线路超过本题安全等待窗口",
            "elapsed_ms": int(decision_timeout * 1000),
        })
    selected = _select_quiz_ai_vote(results, confidence_threshold=0.0)
    selected["provider_count"] = len(providers)
    selected["decision_timeout_sec"] = decision_timeout
    return selected


__all__ = [
    "suggest_quiz_answer_multi",
    "suggest_quiz_answer",
]

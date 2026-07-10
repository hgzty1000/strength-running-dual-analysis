"""可选 LLM 客户端 (OpenAI 兼容 chat completions)。

仅在配置了 LLM_BASE_URL / LLM_MODEL 且用户有 LLM Key 时使用。
用于在规则式报告基础上增强叙述层。失败时静默回退到规则报告。
只发送必要的结构化上下文, 不发送数据库全量, 不发送 Key 以外的敏感信息。
"""
from __future__ import annotations

import json

import httpx

from app.config import settings

SYSTEM_PROMPT = (
    "你是力量+跑步双线训练分析助手。基于给定的结构化训练上下文和规则引擎的初步结论,"
    "输出一段中文复盘叙述。要求: 只做方向性判断和短周期调整建议, 不生成完整训练计划,"
    "不给精确重量/组数/配速处方, 不替代医疗建议。当数据缺失时明确声明不确定性。"
)


def enhance_narrative(ctx: dict, structured: dict, llm_key: str) -> str | None:
    if not settings.llm_base_url or not settings.llm_model:
        return None
    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({"context": ctx, "rule_based": structured}, ensure_ascii=False)},
        ],
        "temperature": 0.4,
    }
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    try:
        resp = httpx.post(url, headers={"Authorization": f"Bearer {llm_key}"}, json=payload, timeout=60.0)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001
        return None


STANDARD_GROUPS = ["胸", "背", "腿", "肩", "二头", "三头", "前臂", "臀部", "小腿", "腹", "有氧", "全身"]

CLASSIFY_PROMPT = (
    "你是健身动作肌群分类助手。给定一组中文健身动作名, 为每个动作判定主要训练肌群。"
    "只能从以下标准肌群中选择 primary: " + "、".join(STANDARD_GROUPS) + "。"
    "可给出 0-2 个 secondary 次要肌群 (同样只能从标准肌群里选)。"
    "只输出 JSON, 格式: {\"动作名\": {\"primary\": \"腿\", \"secondary\": [\"臀部\"], \"confidence\": 0.9}}。"
    "confidence 是 0-1 的把握度。不要输出 JSON 以外的任何文字。"
)


def classify_muscles(action_names: list[str], llm_key: str) -> dict[str, dict] | None:
    """用 LLM 对动作批量判定肌群。返回 {action: {primary, secondary, confidence}} 或 None。"""
    if not settings.llm_base_url or not settings.llm_model or not llm_key or not action_names:
        return None
    import json as _json

    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": CLASSIFY_PROMPT},
            {"role": "user", "content": _json.dumps(action_names, ensure_ascii=False)},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    try:
        resp = httpx.post(url, headers={"Authorization": f"Bearer {llm_key}"}, json=payload, timeout=60.0)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        data = _json.loads(content)
    except Exception:  # noqa: BLE001
        return None
    # 校验: primary 必须在标准肌群里
    result: dict[str, dict] = {}
    if not isinstance(data, dict):
        return None
    for name, info in data.items():
        if not isinstance(info, dict):
            continue
        primary = info.get("primary")
        if primary not in STANDARD_GROUPS:
            continue
        secondary = [g for g in (info.get("secondary") or []) if g in STANDARD_GROUPS]
        conf = info.get("confidence")
        try:
            conf = float(conf)
        except (TypeError, ValueError):
            conf = 0.7
        result[name] = {"primary": primary, "secondary": secondary, "confidence": conf}
    return result or None


def test_connection(llm_key: str) -> dict:
    """最小连通性测试: 发一条极短请求, 验证 base_url/model/key 可用。"""
    import time

    if not settings.llm_base_url or not settings.llm_model:
        return {"ok": False, "message": "平台未配置 LLM_BASE_URL / LLM_MODEL (.env)"}
    if not llm_key:
        return {"ok": False, "message": "尚未配置 LLM Key"}
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": settings.llm_model,
        "messages": [{"role": "user", "content": "ping, reply with: ok"}],
        "max_tokens": 8,
        "temperature": 0,
    }
    start = time.monotonic()
    try:
        resp = httpx.post(url, headers={"Authorization": f"Bearer {llm_key}"}, json=payload, timeout=30.0)
    except httpx.TimeoutException:
        return {"ok": False, "message": "请求超时 (30s)"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"连接失败: {type(exc).__name__}"}
    latency = int((time.monotonic() - start) * 1000)
    if resp.status_code == 200:
        try:
            content = resp.json()["choices"][0]["message"]["content"]
        except Exception:  # noqa: BLE001
            content = ""
        return {"ok": True, "message": f"可用, 模型 {settings.llm_model} 响应正常", "latency_ms": latency, "reply": content[:40]}
    if resp.status_code in (401, 403):
        return {"ok": False, "message": f"鉴权失败 (HTTP {resp.status_code}), 请检查 Key", "latency_ms": latency}
    if resp.status_code == 404:
        return {"ok": False, "message": f"端点/模型不存在 (HTTP 404), 请检查 base_url 与 model", "latency_ms": latency}
    return {"ok": False, "message": f"HTTP {resp.status_code}", "latency_ms": latency}

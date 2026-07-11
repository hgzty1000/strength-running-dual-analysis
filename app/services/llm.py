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
    "若上下文含高温户外跑 (load_summary.heat), 结合热负荷解读心率: 高温会推高心率, "
    "勿把高温致心率升高误判为强度增加; 跑步机等室内跑不参与气温热负荷解读。"
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


GOAL_PRIMARY_OPTIONS = [
    "balanced", "running_race_priority", "strength_physique_priority",
    "recovery_priority", "fat_loss_priority", "custom",
]

CLARIFY_SYSTEM_PROMPT = (
    "你是力量+跑步双线训练目标澄清助手。任务: 通过对话帮用户把模糊的训练意图,"
    "澄清成一份结构化的「当前目标配置」。你只澄清目标, 不解释训练报告, 不闲聊跑题。\n"
    "澄清维度: 主目标倾向(双线平衡/跑步比赛优先/力量体型优先/恢复优先/减脂优先/自定义)、"
    "跑步目标(比赛日、目标成绩)、力量底线(可接受的力量或肌肉下降幅度)、"
    "冲突取舍(两者打架时保哪个)、恢复约束(伤病、疲劳、时间限制)。\n"
    "风格要求: \n"
    "- 一次只问 1-2 个最关键的问题, 不要连珠炮。\n"
    "- 带不确定性、不武断: 用户没说清的先问, 不替他下结论。\n"
    "- 存疑而非指责: 若用户提到过去某段缺训/异常(如伤病、出差), 温和指出这类『过去发生的事实』"
    "更适合记为『休整标注』而不是目标, 建议他去休整标注记一笔, 但不要替他决定。\n"
    "- 不生成完整训练计划, 不给精确重量/组数/配速处方, 不替代医疗建议。\n"
    "- 当你判断关键维度已聊得差不多, 主动提示用户: 可以点『汇成目标草案』生成待确认的配置。\n"
    "用中文回复, 简洁自然, 像一个懂训练的伙伴。"
)


def clarify_goal(messages: list[dict], training_brief: dict | None, llm_key: str) -> dict:
    """多轮目标澄清对话。messages 为 [{role, content}] (不含 system)。

    只读消费 training_brief 作为背景, 不产生任何数据。
    返回 {ok, reply} 或 {ok:False, message}。
    """
    if not settings.llm_base_url or not settings.llm_model:
        return {"ok": False, "message": "平台未配置 LLM (.env 的 LLM_BASE_URL / LLM_MODEL)"}
    if not llm_key:
        return {"ok": False, "message": "尚未配置 LLM Key, 请到「设置 → 凭证」填写"}
    sys_content = CLARIFY_SYSTEM_PROMPT
    if training_brief:
        sys_content += (
            "\n\n以下是该用户近期训练概况(仅供你理解背景, 让追问更贴合实际, 不要逐条复述): "
            + json.dumps(training_brief, ensure_ascii=False)
        )
    payload = {
        "model": settings.llm_model,
        "messages": [{"role": "system", "content": sys_content}] + messages,
        "temperature": 0.5,
    }
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    try:
        resp = httpx.post(url, headers={"Authorization": f"Bearer {llm_key}"}, json=payload, timeout=60.0)
        resp.raise_for_status()
        reply = resp.json()["choices"][0]["message"]["content"]
    except httpx.TimeoutException:
        return {"ok": False, "message": "请求超时 (60s)"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"调用失败: {type(exc).__name__}"}
    return {"ok": True, "reply": reply}


DRAFT_SYSTEM_PROMPT = (
    "你是目标配置结构化助手。根据给定的目标澄清对话, 汇总成一份『当前目标配置』草案。"
    "只输出 JSON, 不要输出 JSON 以外的任何文字。格式: {\n"
    '  "primary_goal": 从 [' + ", ".join(GOAL_PRIMARY_OPTIONS) + "] 中选一个,\n"
    '  "running_goal_text": "跑步目标说明, 含比赛日/目标成绩(若提到)",\n'
    '  "strength_baseline_text": "力量底线说明",\n'
    '  "conflict_policy_text": "冲突取舍说明",\n'
    '  "uncertainties_text": "对话中仍未聊清、需要用户补充的点; 没有则留空字符串",\n'
    '  "rest_note_hint": "若对话中出现应记为休整标注的过去异常事件, 用一句话提示; 没有则留空字符串"\n'
    "}\n"
    "规则: 没聊清的字段不要硬编, 宁可留空并写进 uncertainties_text。primary_goal 必须是列表中的英文枚举值之一。"
)


def draft_goal(messages: list[dict], llm_key: str) -> dict:
    """把澄清对话汇成结构化目标草案。返回 {ok, draft} 或 {ok:False, message}。"""
    if not settings.llm_base_url or not settings.llm_model:
        return {"ok": False, "message": "平台未配置 LLM (.env 的 LLM_BASE_URL / LLM_MODEL)"}
    if not llm_key:
        return {"ok": False, "message": "尚未配置 LLM Key, 请到「设置 → 凭证」填写"}
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in messages if m.get("role") in ("user", "assistant"))
    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": DRAFT_SYSTEM_PROMPT},
            {"role": "user", "content": "澄清对话如下:\n" + convo},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    try:
        resp = httpx.post(url, headers={"Authorization": f"Bearer {llm_key}"}, json=payload, timeout=60.0)
        resp.raise_for_status()
        data = json.loads(resp.json()["choices"][0]["message"]["content"])
    except httpx.TimeoutException:
        return {"ok": False, "message": "请求超时 (60s)"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"汇总失败: {type(exc).__name__}"}
    if not isinstance(data, dict):
        return {"ok": False, "message": "模型返回格式异常"}
    primary = data.get("primary_goal")
    if primary not in GOAL_PRIMARY_OPTIONS:
        primary = "custom"
    draft = {
        "primary_goal": primary,
        "running_goal_text": str(data.get("running_goal_text") or ""),
        "strength_baseline_text": str(data.get("strength_baseline_text") or ""),
        "conflict_policy_text": str(data.get("conflict_policy_text") or ""),
        "uncertainties_text": str(data.get("uncertainties_text") or ""),
        "rest_note_hint": str(data.get("rest_note_hint") or ""),
    }
    return {"ok": True, "draft": draft}


FOLLOWUP_SYSTEM_PROMPT = (
    "你是训练分析报告的『浅追问』助手。用户在看一份已生成的分析报告, 就报告内容向你提问。\n"
    "严格边界:\n"
    "- 你只解释这一份报告 (基于给定的报告快照: 分析上下文 + 结构化结论 + 叙述)。\n"
    "- 报告是某一时刻的快照。你不访问实时数据、不重新分析。若用户问的超出报告覆盖范围或需要新数据,"
    "明确告诉他『这需要重新分析』, 而不是自行推测新结论。\n"
    "- 只读: 你不修改任何训练数据, 也不修改目标配置。若用户想改目标, 引导他去『目标澄清』; 想补事实, 引导去『休整标注』或重新同步/上传。\n"
    "- 基于报告已有信息给带前提和置信度的回答; 数据不足时说明不确定, 不硬下结论。\n"
    "- 不生成完整训练计划, 不给精确重量/组数/配速处方, 不替代医疗建议。\n"
    "回答用中文, 简洁, 可用 markdown (标题/列表/加粗)。"
)


def answer_followup(report_snapshot: dict, history: list[dict], question: str, llm_key: str) -> dict:
    """就某份报告快照回答浅追问。

    report_snapshot: {context, structured, narrative} (只读快照)。
    history: 本报告已有问答 [{role, content}]。
    返回 {ok, answer} 或 {ok:False, message}。
    """
    if not settings.llm_base_url or not settings.llm_model:
        return {"ok": False, "message": "平台未配置 LLM (.env 的 LLM_BASE_URL / LLM_MODEL)"}
    if not llm_key:
        return {"ok": False, "message": "尚未配置 LLM Key, 请到「设置 → 凭证」填写"}
    snapshot_msg = {
        "role": "user",
        "content": "这是当前报告快照 (只读):\n" + json.dumps(report_snapshot, ensure_ascii=False, default=str),
    }
    messages = [
        {"role": "system", "content": FOLLOWUP_SYSTEM_PROMPT},
        snapshot_msg,
    ] + history + [{"role": "user", "content": question}]
    payload = {"model": settings.llm_model, "messages": messages, "temperature": 0.4}
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    try:
        resp = httpx.post(url, headers={"Authorization": f"Bearer {llm_key}"}, json=payload, timeout=60.0)
        resp.raise_for_status()
        answer = resp.json()["choices"][0]["message"]["content"]
    except httpx.TimeoutException:
        return {"ok": False, "message": "请求超时 (60s)"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"调用失败: {type(exc).__name__}"}
    return {"ok": True, "answer": answer}


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

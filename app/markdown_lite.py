"""极轻量 Markdown 渲染 (零依赖)。

安全优先: 先对全文做 HTML 转义, 再套一层白名单 markdown 规则。
因此 LLM/用户输入里的任何 <script>、on* 属性、原始标签都会被转义成纯文本,
渲染出的 HTML 只可能包含本模块显式生成的安全标签, 不存在 XSS 注入面。

支持的子集 (够覆盖 LLM 复盘叙述 / 对话回复):
- 段落 (空行分段)
- 标题 #, ##, ###  → <h4>/<h5>/<h6> (页面内不抢主标题层级)
- 无序列表 -, *      → <ul><li>
- 有序列表 1. 2.     → <ol><li>
- 行内: **粗**, *斜*, `代码`
- 单行内换行保留为 <br>

不支持 (故意): 链接/图片 (避免 javascript: 协议面)、表格、HTML 直通、代码块围栏。
"""
from __future__ import annotations

import html
import re

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_CODE = re.compile(r"`([^`]+?)`")


def _inline(text: str) -> str:
    """行内格式化。text 已是转义后的安全文本。"""
    text = _CODE.sub(r"<code>\1</code>", text)
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    text = _ITALIC.sub(r"<em>\1</em>", text)
    return text


def render(md: str | None) -> str:
    """把 markdown 文本渲染为安全 HTML 字符串。"""
    if not md:
        return ""
    # 1) 整体转义: 之后任何 < > & 都是纯文本, 杜绝标签注入
    escaped = html.escape(md, quote=False)
    lines = escaped.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    html_parts: list[str] = []
    para: list[str] = []          # 累积的段落行
    list_items: list[str] = []    # 累积的列表项
    list_kind: str | None = None  # 'ul' | 'ol'

    def flush_para() -> None:
        if para:
            html_parts.append("<p>" + "<br>".join(_inline(l) for l in para) + "</p>")
            para.clear()

    def flush_list() -> None:
        nonlocal list_kind
        if list_items:
            tag = list_kind or "ul"
            html_parts.append(
                f"<{tag}>" + "".join(f"<li>{_inline(it)}</li>" for it in list_items) + f"</{tag}>"
            )
            list_items.clear()
            list_kind = None

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped:  # 空行 → 结束当前段落/列表
            flush_para()
            flush_list()
            continue

        h = re.match(r"^(#{1,3})\s+(.*)$", stripped)
        if h:
            flush_para()
            flush_list()
            level = len(h.group(1)) + 3  # # → h4, ## → h5, ### → h6
            html_parts.append(f"<h{level}>{_inline(h.group(2).strip())}</h{level}>")
            continue

        ol = re.match(r"^\d+[.)]\s+(.*)$", stripped)
        if ol:
            flush_para()
            if list_kind == "ul":
                flush_list()
            list_kind = "ol"
            list_items.append(ol.group(1).strip())
            continue

        ul = re.match(r"^[-*]\s+(.*)$", stripped)
        if ul:
            flush_para()
            if list_kind == "ol":
                flush_list()
            list_kind = "ul"
            list_items.append(ul.group(1).strip())
            continue

        # 普通文本行 → 段落 (若正在列表中, 先收尾列表)
        flush_list()
        para.append(stripped)

    flush_para()
    flush_list()
    return "".join(html_parts)

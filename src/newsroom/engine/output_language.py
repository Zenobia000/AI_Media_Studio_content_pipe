"""A line at the end of the prompt of the steps that write Chinese for readers, asking for Traditional Chinese (Taiwan usage), for models that drift into Simplified.

The pack's prompts are in Traditional Chinese, and a hosted model writes its answers in the same script. A local model (Qwen on Ollama) often answers in Simplified
whatever the prompt says: in a run of 337 titles, 47% had Simplified-only characters on a site in Traditional. One plain instruction at the end of the system prompt took
a test of 12 articles from 59 such characters to 1. It is opt-in (LLM_TRADITIONAL=true): the original sent the prompt as it is, and the line is a different request
(so its answers are paid for again, not reused)."""

from __future__ import annotations

import os

NOTE = "\n\n輸出語言：titleZh、summaryZh、editorialJudgment、reasonZh、bodyZh、tags 與所有給讀者看的中文，一律使用繁體中文（臺灣用字與用語），不得出現簡體字。程式碼、專有名詞、引文原文照原樣保留。"
# the steps that write Chinese for readers (the report steps are named report_...)
PURPOSES = {"understand_article", "summarize_article", "mend_wording", "story_digest", "translate_body", "translate_quoted"}


def enabled() -> bool:
    return os.environ.get("LLM_TRADITIONAL") == "true"


def apply(opts: dict) -> dict:
    """The options of a call, with the line added when it is on and the step writes Chinese for readers: at the end of the system prompt, or of the user
    prompt for a step that has no system prompt (the title-and-summary step: it was left out, and its titles stayed in Simplified)."""
    purpose = opts.get("purpose") or ""
    if not enabled() or not (purpose in PURPOSES or purpose.startswith("report")):
        return opts
    if opts.get("system"):
        return {**opts, "system": opts["system"] + NOTE}
    if isinstance(opts.get("user"), str) and opts["user"]:
        return {**opts, "user": opts["user"] + NOTE}
    return opts

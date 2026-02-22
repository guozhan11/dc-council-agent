import json
import os
from typing import Any, Dict, List

from openai import OpenAI


def _build_sources_block(items: List[Dict[str, Any]]) -> str:
    # Number sources starting from 1 so citations are [1], [2], ...
    lines = []
    for i, it in enumerate(items, start=1):
        title = (it.get("title") or "").strip() or "Untitled"
        url = (it.get("url") or "").strip()
        source = (it.get("source") or "").strip()
        label = f"[{i}] {title}"
        if source:
            label += f" — {source}"
        if url:
            label += f" ({url})"
        lines.append(label)
    return "\n".join(lines)


def summarize_updates(
    items: List[Dict[str, Any]],
    *,
    model: str = "gpt-4.1-mini",
    max_bullets: int = 8,
) -> Dict[str, Any]:
    """
    Returns a dict like:
    {
      "headline": "...",
    "bullets": [{"text": "Lead clause — supporting detail", "sources": [1,3]}],
      "sources": [{"n": 1, "title": "...", "url": "...", "source": "..."}]
    }
    """

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    if not items:
        return {
            "headline": "No updates this week.",
            "bullets": [],
            "sources": [],
        }

    client = OpenAI(api_key=api_key)

    # Keep the input compact but informative.
    # The model will cite items by index (1-based).
    trimmed_items = []
    for it in items:
        trimmed_items.append(
            {
                "title": (it.get("title") or "")[:200],
                "source": (it.get("source") or "")[:80],
                "url": (it.get("url") or "")[:500],
                # Prefer the "content" / "summary" you already scraped
                "text": (it.get("text") or it.get("summary") or "")[:2000],
                # Optional fields if you have them
                "date": it.get("date"),
            }
        )

    sources_block = _build_sources_block(trimmed_items)

    prompt = f"""
You are summarizing a weekly policy/news digest about DC Council.
You MUST preserve traceability to sources.

You will be given N source items, numbered [1]..[N].
Write a concise weekly summary with up to {max_bullets} bullets.
The headline must be a short, specific title that captures the most important development of the week.
Each bullet must start with a short lead clause, followed by an em dash (" — ") and supporting detail.
Prioritize items most relevant to DC Council subscribers.

Rules:
- Every bullet MUST cite at least one source number in a "sources" list.
- Do NOT invent facts not supported by the items.
- Keep bullets readable for a newsletter.

Return ONLY valid JSON in this exact schema:

{{
  "headline": "string",
  "bullets": [
    {{
      "text": "string",
      "sources": [1, 2]
    }}
  ]
}}

Here are the sources (for citation only):
{sources_block}

Here are the items as JSON:
{json.dumps(trimmed_items, ensure_ascii=False)}
""".strip()

    # Responses API call (OpenAI Python SDK)
    # (Docs: API reference and structured outputs are supported in the platform docs.) :contentReference[oaicite:2]{index=2}
    resp = client.responses.create(
        model=model,
        input=prompt,
    )

    # SDK response format can vary by version; this is a robust way:
    output_text = ""
    for part in resp.output:
        if part.type == "message":
            for c in part.content:
                if c.type == "output_text":
                    output_text += c.text

    cleaned_text = output_text.strip()
    if cleaned_text.startswith("```"):
        lines = cleaned_text.splitlines()
        if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].startswith("```"):
            cleaned_text = "\n".join(lines[1:-1]).strip()

    try:
        summary = json.loads(cleaned_text)
    except Exception as e:
        raise RuntimeError(
            f"Model did not return valid JSON. Error: {e}\nRaw:\n{output_text}"
        )

    # Attach only cited sources so the template lists what was referenced.
    used_sources = set()
    for b in summary.get("bullets", []):
        for s in b.get("sources", []):
            if isinstance(s, int) and 1 <= s <= len(trimmed_items):
                used_sources.add(s)

    sources = []
    for i, it in enumerate(trimmed_items, start=1):
        if i not in used_sources:
            continue
        sources.append(
            {
                "n": i,
                "title": it.get("title") or "",
                "url": it.get("url") or "",
                "source": it.get("source") or "",
            }
        )

    summary["sources"] = sources
    return summary

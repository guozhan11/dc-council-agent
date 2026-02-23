import json
import os
import re
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


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _lead(text: str) -> str:
    return (text or "").split(" — ", 1)[0].strip()


def _is_near_duplicate(candidate: Dict[str, Any], kept: Dict[str, Any]) -> bool:
    cand_lead = _lead(str(candidate.get("text") or ""))
    kept_lead = _lead(str(kept.get("text") or ""))

    cand_tokens = _tokenize(cand_lead)
    kept_tokens = _tokenize(kept_lead)
    if not cand_tokens or not kept_tokens:
        return False

    intersection = len(cand_tokens & kept_tokens)
    union = len(cand_tokens | kept_tokens)
    jaccard = (intersection / union) if union else 0.0

    cand_sources = {s for s in candidate.get("sources", []) if isinstance(s, int)}
    kept_sources = {s for s in kept.get("sources", []) if isinstance(s, int)}
    source_overlap = 0.0
    if cand_sources and kept_sources:
        source_overlap = len(cand_sources & kept_sources) / min(len(cand_sources), len(kept_sources))

    return jaccard >= 0.62 or (jaccard >= 0.45 and source_overlap >= 0.8)


def _dedupe_bullets(bullets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    for bullet in bullets:
        if not bullet.get("text"):
            continue
        if any(_is_near_duplicate(bullet, existing) for existing in deduped):
            continue
        deduped.append(bullet)
    return deduped


def summarize_updates(
    items: List[Dict[str, Any]],
    *,
    model: str = "gpt-4.1-mini",
    max_bullets: int = 8,
    interests: str | None = None,
) -> Dict[str, Any]:
    """
    Returns a dict like:
    {
      "headline": "...",
            "interest_notice": "..." | null,
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

    interests_line = f"Subscriber interests: {interests}" if interests else "Subscriber interests: (not specified)"

    prompt = f"""
You are summarizing a weekly policy/news digest about DC Council.
You MUST preserve traceability to sources.

You will be given N source items, numbered [1]..[N].
Write a concise weekly summary with up to {max_bullets} bullets.
The headline must be a short, specific title that captures the most important development of the week.
Each bullet must be exactly two parts: a one-sentence summary, then an em dash (" — "), then 1-2 sentences of supporting detail.
Prioritize items most relevant to the subscriber's interests. If none are relevant, summarize the most important items overall.

Rules:
- Every bullet MUST cite at least one source number in a "sources" list.
- Do NOT invent facts not supported by the items.
- Keep bullets readable for a newsletter.
- Bullets must be non-overlapping: each bullet should cover a different development, not rephrase the same event.
- If there are fewer distinct developments, return fewer bullets (1-2 bullets is acceptable). Do not pad with repetitive bullets.

Return ONLY valid JSON in this exact schema:

{{
  "headline": "string",
    "interest_notice": "string or null",
  "bullets": [
    {{
      "text": "string",
      "sources": [1, 2]
    }}
  ]
}}

If no items are relevant to the subscriber's interests, set "interest_notice" to:
"No updates this week for your interests: {interests}. Showing general updates instead."
Use the interests text exactly as provided. Otherwise set "interest_notice" to null.

{interests_line}

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

    for bullet in summary.get("bullets", []):
        text = str(bullet.get("text") or "")
        text = re.sub(r"\s*\((?:Sources?|Source)\s*:[^)]*\)\s*$", "", text, flags=re.IGNORECASE)
        bullet["text"] = re.sub(r"\s+", " ", text).strip()

    summary["bullets"] = _dedupe_bullets(summary.get("bullets", []))[:max_bullets]

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

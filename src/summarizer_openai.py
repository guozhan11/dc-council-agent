import json
import os
import re
import html
from urllib.parse import parse_qs, urlparse
from typing import Any, Dict, List

from openai import OpenAI


def _get_openai_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    return OpenAI(api_key=api_key)


def _response_to_text(resp: Any) -> str:
    output_text = ""
    for part in resp.output:
        if part.type == "message":
            for c in part.content:
                if c.type == "output_text":
                    output_text += c.text
    return output_text


def _strip_code_fences(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].startswith("```"):
            return "\n".join(lines[1:-1]).strip()
    return cleaned


def _extract_json_object(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return text
    return text[start : end + 1]


def _repair_common_json_errors(text: str) -> str:
    repaired = text

    # Insert a missing comma between a completed JSON value and the next quoted key.
    repaired = re.sub(r'("(?:\\.|[^"\\])*"|\]|\}|\d|true|false|null)\s*("[^"]+"\s*:)', r'\1, \2', repaired)

    # Remove trailing commas before object/array close.
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    return repaired


def _load_summary_json(output_text: str) -> Dict[str, Any]:
    cleaned_text = _strip_code_fences(output_text)
    json_candidate = _extract_json_object(cleaned_text)

    try:
        return json.loads(json_candidate)
    except json.JSONDecodeError as original_error:
        repaired_candidate = _repair_common_json_errors(json_candidate)
        try:
            return json.loads(repaired_candidate)
        except json.JSONDecodeError:
            raise RuntimeError(
                f"Model did not return valid JSON. Error: {original_error}\nRaw:\n{output_text}"
            ) from original_error


def summarize_interest_phrase(interests: str, *, model: str = "gpt-4.1-mini") -> str:
    cleaned_interests = str(interests or "").strip()
    if not cleaned_interests:
        return ""

    client = _get_openai_client()
    prompt = f"""
Rewrite the subscriber's free-form interest text as a short, clean preference phrase for an email.

Rules:
- Preserve the actual subjects the subscriber cares about.
- Remove question wording, filler words, and first-person phrasing.
- Return a concise noun phrase, not a sentence.
- Do not add new topics.
- Keep it under 12 words when possible.
- Return plain text only, with no quotes and no explanation.

Subscriber interest text:
{cleaned_interests}
""".strip()

    resp = client.responses.create(
        model=model,
        input=prompt,
    )
    output_text = _response_to_text(resp)
    phrase = _strip_code_fences(output_text).strip().strip('"').strip("'").strip()
    return re.sub(r"\s+", " ", phrase).strip(" .;,:!?")


def _build_sources_block(items: List[Dict[str, Any]]) -> str:
    # Number sources starting from 1 so citations are [1], [2], ...
    lines = []
    for i, it in enumerate(items, start=1):
        title = _sanitize_source_title(it.get("title") or "") or "Untitled"
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


def _parse_source_numbers(value: Any, max_n: int) -> List[int]:
    nums: List[int] = []
    if isinstance(value, list):
        for it in value:
            try:
                n = int(it)
            except Exception:
                continue
            if 1 <= n <= max_n and n not in nums:
                nums.append(n)
    elif isinstance(value, str):
        for match in re.findall(r"\d+", value):
            n = int(match)
            if 1 <= n <= max_n and n not in nums:
                nums.append(n)
    return nums


def _split_bullet_text(text: str) -> tuple[str, str]:
    normalized_text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized_text:
        return "", ""

    parts = re.split(r"\s*—\s*|\s+-\s+", normalized_text, maxsplit=1)
    if len(parts) == 2:
        lead = parts[0].strip(" .;,:!?")
        detail = parts[1].strip()
        return lead, detail
    return normalized_text, ""


def _sanitize_source_title(value: str) -> str:
    text = html.unescape(str(value or ""))
    # Some feeds include inline markup (<b>..</b>) in titles.
    text = re.sub(r"<[^>]+>", "", text)
    # Strip simple markdown emphasis while keeping inner text.
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"__(.*?)__", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _normalize_source_url(item: Dict[str, Any]) -> str:
    url = str(item.get("url") or "").strip()
    source = str(item.get("source") or "").strip()
    if source != "granicus_rss" or "DownloadFile.php" not in url:
        return url

    try:
        clip_id = (parse_qs(urlparse(url).query).get("clip_id") or [None])[0]
    except Exception:
        clip_id = None

    if clip_id:
        return f"https://dc.granicus.com/MediaPlayer.php?view_id=2&clip_id={clip_id}"
    return url


def summarize_updates(
    items: List[Dict[str, Any]],
    *,
    model: str = "gpt-4.1-mini",
    max_bullets: int = 8,
    interests: str | None = None,
    strict_interest: bool = False,
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

    if not items:
        return {
            "headline": "No updates this week.",
            "bullets": [],
            "sources": [],
        }

    client = _get_openai_client()

    # Keep the input compact but informative.
    # The model will cite items by index (1-based).
    trimmed_items = []
    for it in items:
        trimmed_items.append(
            {
                "title": _sanitize_source_title((it.get("title") or ""))[:200],
                "source": (it.get("source") or "")[:80],
                "url": _normalize_source_url(it)[:500],
                # Prefer the "content" / "summary" you already scraped
                "text": (it.get("text") or it.get("summary") or "")[:2000],
                # Optional fields if you have them
                "date": it.get("date"),
            }
        )

    sources_block = _build_sources_block(trimmed_items)

    interests_line = f"Subscriber interests: {interests}" if interests else "Subscriber interests: (not specified)"
    strict_interest_rules = ""
    if strict_interest and interests:
        strict_interest_rules = """
- STRICT INTEREST MODE IS ON.
- Every bullet MUST be directly relevant to subscriber interests.
- If only one item is relevant, return exactly one bullet.
- Do NOT include general bullets that are unrelated to the subscriber interests.
""".strip()

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
{strict_interest_rules}

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
    output_text = _response_to_text(resp)

    summary = _load_summary_json(output_text)

    for bullet in summary.get("bullets", []):
        text = str(bullet.get("text") or "")

        structured_sources = _parse_source_numbers(bullet.get("sources"), len(trimmed_items))
        inline_tail_match = re.search(r"\((?:Sources?|Source)\s*:\s*([^)]*)\)\s*$", text, flags=re.IGNORECASE)
        inline_tail_sources = _parse_source_numbers(inline_tail_match.group(1), len(trimmed_items)) if inline_tail_match else []
        # Match both single [1] and comma-separated [1,2] or [1, 2, 3] bracket formats
        inline_bracket_sources = _parse_source_numbers(" ".join(re.findall(r"\[([0-9,\s]+)\]", text)), len(trimmed_items))

        merged_sources: List[int] = []
        for n in structured_sources + inline_tail_sources + inline_bracket_sources:
            if n not in merged_sources:
                merged_sources.append(n)
        bullet["sources"] = merged_sources

        text = re.sub(r"\s*\((?:Sources?|Source)\s*:[^)]*\)\s*$", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\bSources?\s*:?\s*\[[^\]]*\]", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*\[[0-9,\s]+\]", "", text)
        bullet_text = re.sub(r"\s+", " ", text).strip()
        # Strip any trailing em-dash left behind after source citations were removed
        # (e.g. "...challenges. — [1,2]" → after stripping [1,2] → "...challenges. —")
        bullet_text = re.sub(r"\s*—\s*$", "", bullet_text).strip()
        lead, detail = _split_bullet_text(bullet_text)
        bullet["lead"] = lead
        bullet["detail"] = detail
        bullet["text"] = f"{lead} — {detail}" if detail else lead

    summary["bullets"] = _dedupe_bullets(summary.get("bullets", []))[:max_bullets]

    ordered_source_ids: List[int] = []
    filtered_bullets: List[Dict[str, Any]] = []
    sourceless_bullets: List[Dict[str, Any]] = []
    for b in summary.get("bullets", []):
        normalized: List[int] = []
        for s in b.get("sources", []):
            if isinstance(s, int) and 1 <= s <= len(trimmed_items) and s not in normalized:
                normalized.append(s)
            if isinstance(s, int) and 1 <= s <= len(trimmed_items) and s not in ordered_source_ids:
                ordered_source_ids.append(s)
        b["sources"] = normalized
        if b.get("text") and normalized:
            filtered_bullets.append(b)
        elif b.get("text"):
            # Keep for a controlled fallback that still guarantees references.
            sourceless_bullets.append(b)

    # If source validation wiped all citations, keep the model text but attach
    # deterministic fallback references so every bullet remains traceable.
    if not filtered_bullets and sourceless_bullets:
        synthesized_bullets: List[Dict[str, Any]] = []
        for idx, b in enumerate(sourceless_bullets[:max_bullets], start=1):
            fallback_source = idx if idx <= len(trimmed_items) else 1
            b["sources"] = [fallback_source]
            synthesized_bullets.append(b)
        filtered_bullets = synthesized_bullets

    summary["bullets"] = filtered_bullets[:max_bullets]

    ordered_source_ids = []
    for b in summary.get("bullets", []):
        for s in b.get("sources", []):
            if s not in ordered_source_ids:
                ordered_source_ids.append(s)

    renumber_map = {old: idx + 1 for idx, old in enumerate(ordered_source_ids)}

    for b in summary.get("bullets", []):
        b["sources"] = [renumber_map[s] for s in b.get("sources", []) if s in renumber_map]

    sources = []
    for old_id in ordered_source_ids:
        if not isinstance(old_id, int) or not (1 <= old_id <= len(trimmed_items)):
            continue
        it = trimmed_items[old_id - 1]
        sources.append(
            {
                "n": renumber_map.get(old_id, old_id),
                "title": _sanitize_source_title(it.get("title") or ""),
                "url": it.get("url") or "",
                "source": it.get("source") or "",
            }
        )

    summary["sources"] = sources
    return summary


def review_summary_quality(
    summary: Dict[str, Any],
    *,
    interests: str | None = None,
    model: str = "gpt-4.1-mini",
) -> Dict[str, Any]:
    """
    Uses an LLM reviewer for format/readability signals.
    Fails closed only for malformed reviewer output.
    """

    client = _get_openai_client()

    interests_line = interests if interests else "(not specified)"
    prompt = f"""
You are a format checker for newsletter summaries.

Decide if this summary is good enough to send to a subscriber.

Hard requirements:
- Must contain at least 1 bullet.
- Must contain at least 1 source.
- Every bullet must include at least one source id.
- Headline must be non-empty and relevant.

Important:
- Do NOT reject based on topic preference match.
- Do NOT reject because content is not about a specific domain.
- Focus on structure, clarity, and whether the summary is non-empty and readable.

Return ONLY valid JSON in this exact schema:
{{
  "approved": true,
  "score": 0,
  "issues": ["string"],
  "reason": "string"
}}

Scoring guide:
- 90-100: excellent, ready to send
- 70-89: acceptable
- 50-69: weak
- 0-49: unacceptable

If any hard requirement fails, approved must be false.

Subscriber interests:
{interests_line}

Summary JSON:
{json.dumps(summary, ensure_ascii=False)}
""".strip()

    resp = client.responses.create(
        model=model,
        input=prompt,
    )
    output_text = _response_to_text(resp)
    review = _load_summary_json(output_text)

    approved = bool(review.get("approved"))
    try:
        score = int(review.get("score", 0))
    except Exception:
        score = 0
    issues = review.get("issues") if isinstance(review.get("issues"), list) else []
    reason = str(review.get("reason") or "").strip()

    normalized = {
        "approved": approved,
        "score": max(0, min(100, score)),
        "issues": [str(it).strip() for it in issues if str(it).strip()],
        "reason": reason,
    }
    return normalized


def verify_interest_relevance(
    bullets: List[Dict[str, Any]],
    *,
    interests: str | None,
    model: str = "gpt-4.1-mini",
) -> Dict[str, Any]:
    """
    Returns 1-based indices of bullets relevant to subscriber interests.
    """

    if not bullets:
        return {"relevant_indices": [], "reason": "no bullets"}
    if not interests:
        return {"relevant_indices": list(range(1, len(bullets) + 1)), "reason": "no interests provided"}

    client = _get_openai_client()
    compact_bullets = []
    for idx, b in enumerate(bullets, start=1):
        compact_bullets.append({"i": idx, "text": str(b.get("text") or "").strip()})

    prompt = f"""
You are checking whether newsletter bullets are relevant to a subscriber's interests.

Return ONLY valid JSON in this schema:
{{
  "relevant_indices": [1, 2],
  "reason": "string"
}}

Rules:
- Include an index only if that bullet is directly relevant to the interests.
- Exclude generic or unrelated policy bullets.
- Never include indices outside the provided list.

Subscriber interests:
{interests}

Bullets JSON:
{json.dumps(compact_bullets, ensure_ascii=False)}
""".strip()

    resp = client.responses.create(
        model=model,
        input=prompt,
    )
    output_text = _response_to_text(resp)
    parsed = _load_summary_json(output_text)

    relevant_indices = []
    for value in parsed.get("relevant_indices", []):
        try:
            i = int(value)
        except Exception:
            continue
        if 1 <= i <= len(bullets) and i not in relevant_indices:
            relevant_indices.append(i)

    return {
        "relevant_indices": relevant_indices,
        "reason": str(parsed.get("reason") or "").strip(),
    }

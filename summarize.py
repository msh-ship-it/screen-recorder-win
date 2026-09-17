"""Meeting summary ("post-meeting notes") from a timecoded transcript via Groq chat models."""
import json
import time

import requests

import config

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"

# Free-tier Groq allows ~8K tokens/minute for gpt-oss models, counting both the
# prompt and the requested completion. Cyrillic text runs ~3 chars/token, so
# ~10K chars of transcript per request leaves room for instructions and output.
MAX_CHUNK_CHARS = 10_000
MAX_RETRIES = 6
MAX_TOPICS = 40
MAX_OPEN_QUESTIONS = 30

SYSTEM_PROMPT = (
    "Ты — ассистент, который составляет протоколы встреч по расшифровке разговора. "
    "Пиши на русском языке. Опирайся только на то, что есть в расшифровке: "
    "не придумывай имена, решения и сроки. Если что-то не прозвучало — так и пиши: «не указано»."
)

DECISION_RULE = (
    "Решение или задача — это любая договорённость, поручение или просьба сделать что-то "
    "конкретное, которую не отвергли, даже если сказано неформально («сделай», «попробуй "
    "доделать», «мне надо, чтобы ты…», «я потом потестирую»). Неформальные договорённости "
    "помечай «(предварительно)». Констатации фактов, мнения и размышления вслух решениями "
    "не считаются. Имён в расшифровке может не быть и спикеры не размечены — тогда указывай "
    "ответственного по контексту («собеседник», «автор записи») или «не указано»."
)

FINAL_INSTRUCTIONS = f"""Составь протокол встречи строго по такой структуре (Markdown):

## Кратко
2–4 предложения: о чём был разговор и чем закончился.

## Что обсуждали
Маркированный список ключевых тем, без повторов.

## Договорённости и решения
Маркированный список. Каждый пункт в формате:
- Что решили — ответственный: <кто или «не указано»>; срок: <когда или «не указано»>
{DECISION_RULE} Если решений не было — один пункт «не было».

## Открытые вопросы
Что осталось нерешённым или требует уточнения. Если таких нет — напиши «нет».

## Следующие шаги
Кто что делает дальше, по порядку. Если не обсуждалось — напиши «не указано».
"""

CHUNK_INSTRUCTIONS = f"""Это фрагмент длинного разговора. Верни только JSON такого вида:
{{"topics": ["..."], "decisions": [{{"what": "...", "owner": "...", "deadline": "...", "timecode": "..."}}], "open_questions": ["..."]}}

Правила:
- topics — до 5 коротких тем фрагмента, без повторов.
- decisions — все решения из фрагмента. {DECISION_RULE} owner и deadline — как прозвучало, иначе «не указано»; timecode — таймкод реплики.
- open_questions — до 5 нерешённых вопросов.
- Повторяющиеся реплики не дублируй. Если чего-то нет — пустой список."""


class SummaryError(Exception):
    pass


def _chat(user_content: str, max_tokens: int, json_mode: bool = False) -> str:
    payload = {
        "model": config.GROQ_SUMMARY_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.2,
        "max_completion_tokens": max_tokens,
        "reasoning_effort": "low",
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    for _ in range(MAX_RETRIES):
        response = requests.post(
            GROQ_CHAT_URL,
            headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
            json=payload,
            timeout=300,
        )
        if response.status_code == 429:
            time.sleep(float(response.headers.get("retry-after", 20)) + 1)
            continue
        if response.status_code == 403:
            raise SummaryError("Groq недоступен из вашего региона (ошибка 403) — включите VPN и повторите")
        if response.status_code != 200:
            raise SummaryError(f"Groq API error {response.status_code}: {response.text[:500]}")
        content = (response.json()["choices"][0]["message"].get("content") or "").strip()
        if not content:
            raise SummaryError("Groq returned an empty summary")
        return content
    raise SummaryError("Groq rate limit: не удалось дождаться лимита, попробуй позже")


def _split(text: str, limit: int) -> list[str]:
    chunks, current, size = [], [], 0
    for line in text.splitlines():
        if current and size + len(line) > limit:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def _unique(items: list[str]) -> list[str]:
    seen, result = set(), []
    for item in items:
        key = item.strip().lower()
        if key and key not in seen:
            seen.add(key)
            result.append(item.strip())
    return result


def _chunk_notes(chunk: str) -> dict:
    raw = _chat(f"{CHUNK_INSTRUCTIONS}\n\nРасшифровка:\n{chunk}", max_tokens=1500, json_mode=True)
    try:
        notes = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SummaryError(f"Модель вернула некорректные заметки: {e}")
    return {
        "topics": [str(t) for t in notes.get("topics", [])],
        "decisions": [d for d in notes.get("decisions", []) if isinstance(d, dict) and d.get("what")],
        "open_questions": [str(q) for q in notes.get("open_questions", [])],
    }


def _merge_notes(all_notes: list[dict]) -> str:
    # Decisions are aggregated in code, never re-summarized, so none get dropped.
    topics = _unique([t for n in all_notes for t in n["topics"]])[:MAX_TOPICS]
    questions = _unique([q for n in all_notes for q in n["open_questions"]])[:MAX_OPEN_QUESTIONS]
    decisions, seen = [], set()
    for d in (d for n in all_notes for d in n["decisions"]):
        key = str(d["what"]).strip().lower()
        if key not in seen:
            seen.add(key)
            decisions.append(d)

    lines = ["Темы:"] + [f"- {t}" for t in topics]
    lines.append("")
    lines.append(
        "Решения (перенеси в протокол каждое разное решение; "
        "повторы одного и того же объедини в один пункт):"
    )
    if decisions:
        for d in decisions:
            lines.append(
                f"- [{d.get('timecode', '')}] {d['what']} — ответственный: "
                f"{d.get('owner') or 'не указано'}; срок: {d.get('deadline') or 'не указано'}"
            )
    else:
        lines.append("- не было")
    lines.append("")
    lines.append("Открытые вопросы:")
    lines += [f"- {q}" for q in questions] or ["- нет"]
    return "\n".join(lines)


def summarize_transcript(transcript: str) -> str:
    """Returns the meeting summary as Markdown."""
    if not config.GROQ_API_KEY:
        raise SummaryError("GROQ_API_KEY is not configured")

    if len(transcript) <= MAX_CHUNK_CHARS:
        return _chat(f"{FINAL_INSTRUCTIONS}\n\nРасшифровка:\n{transcript}", max_tokens=2500)

    all_notes = [_chunk_notes(chunk) for chunk in _split(transcript, MAX_CHUNK_CHARS)]
    merged = _merge_notes(all_notes)
    return _chat(f"{FINAL_INSTRUCTIONS}\n\nЗаметки по фрагментам разговора:\n{merged}", max_tokens=2500)

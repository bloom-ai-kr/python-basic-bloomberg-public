import json
import logging
import os
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency/runtime
    def load_dotenv(*args: Any, **kwargs: Any) -> bool:
        return False

try:
    import openai
except Exception:  # pragma: no cover - optional dependency/runtime
    openai = None

DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"
LOGGER = logging.getLogger(__name__)

POSITIVE_NEWS_KEYWORDS = (
    "수주",
    "계약",
    "협약",
    "투자",
    "출시",
    "허가",
    "승인",
    "확대",
    "증가",
    "흑자",
    "성장",
    "상승",
)
NEGATIVE_NEWS_KEYWORDS = (
    "유상증자",
    "감자",
    "적자",
    "감소",
    "하락",
    "중단",
    "철회",
    "리콜",
    "소송",
    "조사",
    "악화",
    "부진",
)

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")


def _fallback_response(risk_flag: str, error_detail: str | None = None) -> dict[str, Any]:
    response = {
        "answer": "현재 서비스가 원활하지 않습니다. 나중에 다시 시도해주세요.",
        "key_points": [],
        "risk_flags": [risk_flag],
        "unknowns": [],
        "source": "fallback",
    }
    if error_detail:
        response["error_detail"] = error_detail
    return response


def get_ai_runtime_status() -> dict[str, Any]:
    return {
        "provider": "openai" if os.getenv("OPENAI_API_KEY") else "local-placeholder",
        "model": os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
        "api_key_configured": bool(os.getenv("OPENAI_API_KEY")),
    }


def ask_analysis_copilot(context: dict[str, Any], question: str) -> dict[str, Any]:
    # If OpenAI key or client not available, return a safe fallback message
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key or openai is None:
        detail = "OPENAI_API_KEY 미설정" if not api_key else "openai 패키지 미설치"
        return _fallback_response("OpenAI 미설정 또는 클라이언트 미사용 폴백", detail)

    # Build a controlled prompt: emphasize using only provided context, do not re-query or
    # recalculate prices/indicators — rely on the analysis context passed in.
    model = os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
    system_prompt = (
        "당신은 주식 분석 앱의 보조 AI입니다. 아래에 제공된 분석 컨텍스트(요약, 지표, 최근 시세)\n"
        "그리고 사용자의 질문만을 사용해 답변하세요. 절대로 외부 API를 호출하거나 가격/지표를 새로 계산하지 마세요."
    )

    # Serialize a compact context for the model
    safe_context = {
        "summary": context.get("summary") or context.get("analysis_summary") or {},
        "analysis_context": context.get("analysis_context") or {},
    }

    user_prompt = (
        "분석 컨텍스트:\n" + json.dumps(safe_context, ensure_ascii=False) + "\n\n"
        + "질문:\n" + str(question)
    )
    try:
        # Support both new OpenAI client (`from openai import OpenAI`) and legacy `openai` module
        content = ""
        if hasattr(openai, "OpenAI"):
            # new-style client: use `responses.create` as in the provided example
            client = openai.OpenAI(api_key=api_key)
            response = client.responses.create(
                model=model,
                instructions=system_prompt,
                input=user_prompt,
                max_output_tokens=600,
                temperature=0.2,
            )
        else:
            # fall back to legacy module-style API
            try:
                openai.api_key = api_key
            except Exception:
                pass
            response = openai.ChatCompletion.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=600,
                temperature=0.2,
            )

        # Robustly extract text from different response shapes
        def _extract_text(resp: Any) -> str:
            if resp is None:
                return ""
            # new client may expose `output_text` shortcut
            try:
                out = getattr(resp, "output_text", None)
                if isinstance(out, str) and out.strip():
                    return out.strip()
            except Exception:
                pass
            # object with .choices
            choices = None
            if hasattr(resp, "choices"):
                choices = resp.choices
            elif isinstance(resp, dict):
                choices = resp.get("choices")

            if not choices:
                return ""

            first = choices[0]
            # dict-style
            if isinstance(first, dict):
                msg = first.get("message") or first.get("delta")
                if isinstance(msg, dict):
                    return (msg.get("content") or msg.get("text") or "").strip()
                return (first.get("text") or "").strip()

            # object-style
            try:
                return (first.message.content or "").strip()
            except Exception:
                try:
                    return (getattr(first, "text", "") or "").strip()
                except Exception:
                    return ""

        content = _extract_text(response)

        if not content:
            raise RuntimeError("Empty response from OpenAI")

        return {
            "answer": content,
            "key_points": [],
            "risk_flags": [],
            "unknowns": [],
            "source": "openai",
        }
    except Exception as exc:
        LOGGER.exception("OpenAI copilot request failed")
        return _fallback_response("OpenAI 호출 실패 폴백", str(exc))


def summarize_ops_context(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "answer": "10-8장에서 주문 상태와 보유현황을 읽어 AI 요약을 붙일 예정입니다.",
        "key_points": ["잔고와 주문 데이터를 요약하는 흐름 구현"],
        "risk_flags": ["현재는 스켈레톤 응답입니다."],
        "unknowns": [],
        "source": "skeleton",
    }


def summarize_news_briefing(company_name: str, articles: list[dict[str, Any]]) -> dict[str, Any]:
    if not articles:
        return {
            "summary": f"{company_name or '해당 종목'} 관련 최근 기사를 찾지 못했습니다.",
            "opportunities": [],
            "warnings": [],
            "needs_confirmation": [],
            "source": "rule-based",
        }

    latest_article = articles[0]
    opportunities: list[str] = []
    warnings: list[str] = []
    needs_confirmation: list[str] = []

    for article in articles:
        title = str(article.get("title") or "").strip()
        description = str(article.get("description") or "").strip()
        haystack = f"{title} {description}"

        if any(keyword in haystack for keyword in POSITIVE_NEWS_KEYWORDS):
            opportunities.append(title)
        if any(keyword in haystack for keyword in NEGATIVE_NEWS_KEYWORDS):
            warnings.append(title)

    if not opportunities:
        opportunities.append("최근 기사에서 즉시 해석 가능한 뚜렷한 호재 키워드는 제한적입니다.")

    if not warnings:
        warnings.append("기사 제목만으로는 즉시 경계할 단일 악재가 뚜렷하지 않습니다.")

    if len(articles) >= 5:
        needs_confirmation.append("기사 수가 많아 공시성 기사와 사업 기사 비중을 구분해서 보는 것이 좋습니다.")

    if any("유상증자" in (article.get("title") or "") + " " + (article.get("description") or "") for article in articles):
        needs_confirmation.append("유상증자 관련 기사는 자금 사용 목적과 기존 주주 희석 가능성을 함께 확인하세요.")

    return {
        "summary": (
            f"{company_name or '해당 종목'} 관련 최근 기사 {len(articles)}건을 확인했습니다. "
            f"가장 최근 기사는 '{latest_article.get('title') or '제목 없음'}'이며, "
            f"최근 보도 흐름은 공시와 사업 진행 상황을 함께 확인해야 하는 구간으로 보입니다."
        ),
        "opportunities": opportunities[:3],
        "warnings": warnings[:3],
        "needs_confirmation": needs_confirmation[:3],
        "source": "rule-based",
    }

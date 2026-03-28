import os
import json
import time

from google import genai
from google.genai import types
from config import GEMINI_API_KEY, logger

SYSTEM_PROMPT = """
당신은 무역, 구매, 영업 등 광범위한 실무를 총괄하는 최고급 이메일 비서입니다.

아래에 [새로 도착한 이메일 원문]과 [내 과거 이메일 요약 장부]가 함께 제공됩니다.

판단 기준:
1. 장부에 저장된 과거 기록들을 살펴보고, 새 이메일이 어떤 주제와 내용상 이어지는 핑퐁(답장)인지 종합적으로 판단하십시오.
2. 만약 핑퐁이라면: 이미 요약된 과거 내용은 무시하고, 새롭게 추가된 내용만 핵심 요약하십시오. thread_key는 장부에서 일치하는 주제명을 그대로 사용하십시오.
3. 만약 완전히 새로운 메일이라면: 전체 내용을 요약하고, thread_key로 이 메일의 핵심 주제를 간결하게 작성하십시오.
4. 내용 없이 정보성이 전혀 없는 단순 인사 메일("잘 받았습니다", "감사합니다" 등)의 경우에만 status를 '스킵'으로 분류하십시오.

[사용자 기피 학습 노트]에 등록된 패턴과 유사한 메일도 '스킵'으로 분류하십시오.

반드시 아래 JSON 형식만 반환하십시오:
{
    "status": "'알림' 또는 '스킵'",
    "is_thread": "true 또는 false (핑퐁 여부)",
    "thread_key": "연결된 주제명 또는 새 주제명 (간결하게)",
    "thread_index": "이 메일이 해당 스레드의 몇 번째인지 (숫자)",
    "summary": "요약 내용 (status가 '스킵'이면 빈 문자열)"
}

요약 작성 시: 마크다운(*, # 등) 절대 사용 금지. • 기호만 사용. 각 문단 사이 줄바꿈 1번.
"""

def process_email_with_ai(mail_data, thread_history_text):
    """
    V1.11.0: 파이썬은 우체부/서기 역할만 합니다.
    이메일 원본 + 장부 전체를 제미나이에게 던지고, 제미나이가 직접 판단합니다.
    """
    email_body = mail_data.get('body', '')
    if not email_body or email_body == "본문 추출 불가 메일" or not GEMINI_API_KEY:
        return _fallback_response()

    # 제미나이에게 던질 최종 텍스트: 이메일 원본 + 장부
    final_text = f"""[새로 도착한 이메일 원문]
발신자: {mail_data.get('sender', '알 수 없음')}
날짜: {mail_data.get('date', '알 수 없음')}
제목: {mail_data.get('subject', '제목 없음')}
본문:
{email_body}

[내 과거 이메일 요약 장부]
{thread_history_text}"""

    # 사용자 기피 학습 노트 주입
    dynamic_prompt = SYSTEM_PROMPT
    try:
        from feedback_manager import load_preferences
        preferences = load_preferences()
        if preferences:
            pref_text = "\n".join([f"{i+1}. {p}" for i, p in enumerate(preferences)])
            dynamic_prompt += f"\n\n[사용자 기피 학습 노트]\n아래 패턴과 유사한 메일은 '스킵'으로 분류:\n{pref_text}"
    except Exception:
        pass

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        logger.error(f"AI 클라이언트 초기화 실패: {e}")
        return _fallback_response()

    try:
        req_config = types.GenerateContentConfig(
            system_instruction=dynamic_prompt,
            response_mime_type="application/json"
        )
    except Exception as e:
        logger.error(f"AI 설정 오류: {e}")
        return _fallback_response()

    # 1순위: 최신 모델 / 2순위: 안정화 모델 순으로 폴백 체인을 구성합니다.
    model_chain = [
        "gemini-3-flash-preview",   # 1순위: 최신 모델
        "gemini-2.5-flash",         # 2순위: 2.5 안정화 모델 (503 폭주 시 자동 전환)
    ]

    max_retries = 3
    for model_name in model_chain:
        logger.info(f"AI 모델 [{model_name}]으로 분석을 시도합니다...")
        success = False
        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=final_text,
                    config=req_config
                )
                ai_result = json.loads(response.text)
                logger.info(f"AI 분석 완료 (모델: {model_name}): status={ai_result.get('status')}, thread_key={ai_result.get('thread_key')}, index={ai_result.get('thread_index')}")
                return ai_result

            except Exception as e:
                logger.error(f"AI 통신 오류 [{model_name}] (시도 {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    wait_time = 2 * (2 ** attempt)
                    logger.warning(f"{wait_time}초 후 재시도합니다.")
                    time.sleep(wait_time)

        logger.warning(f"모델 [{model_name}] 3회 모두 실패. 다음 모델로 전환합니다...")

    logger.error("모든 모델이 응답하지 않습니다. 비상 안내문으로 대체합니다.")
    return _fallback_response()


def _fallback_response():
    """
    AI 전체 모델 체인 실패 시 반환하는 비상 응답입니다.
    is_ai_error=True 플래그로 main.py가 재시도 대기열 처리를 할 수 있게 신호를 보냅니다.
    """
    return {
        "status": "알림",
        "is_ai_error": True,        # ← 재시도 대기열 판단용 신호
        "is_thread": False,
        "thread_key": "AI 오류",
        "thread_index": 1,
        "summary": "AI 서버 응답 오류로 이번 메일은 요약하지 못했습니다. 원본 이메일을 직접 확인해 주십시오."
    }

import os
import json
import time

# 1. 예전 패키지(google.generativeai) 대신 
# 사용자님께서 알려주신 따끈따끈한 최신 공식 라이브러리(google-genai)를 가져옵니다.
from google import genai
from google.genai import types

from config import GEMINI_API_KEY, logger

# 2. 비서에게 어떻게 요약해야 할지 가르치는 시스템 지시문입니다.
SYSTEM_PROMPT = """
당신은 무역, 구매, 영업, 영업관리 등 광범위한 실무를 총괄하는 최고급 이메일 비서입니다. 
제공되는 이메일 텍스트를 꼼꼼히 읽어보되, 이전에 주고받은 답변 내역(인용문)이 있다면 그 대화의 전체 문맥과 흐름까지 모두 파악하여 분석하십시오.
분석 후 반드시 아래 JSON 포맷 형식을 철저히 지켜서 결과를 반환하십시오:

{
    "category": "'영업', '구매', '내부보고', '무역',  '기타', '스킵' 이 6가지 단어 중 하나만 선택 (단, '수고하세요', '네 알겠습니다', '확인했습니다' 같은 극히 단순한 인사나, 내용이 거의 없는 단답형 메일, 또는 중요치 않게 중복되는 메일인 경우 반드시 '스킵'을 깐깐하게 선택할 것)",
    "summary": "과거 대화의 맥락을 파악하고, 글이 빽빽하지 않도록 각 주제 문단마다 반드시 줄바꿈(엔터)을 1번씩 넉넉하게 띄워 여백을 주고, 주요 핵심은 마크다운(*, # 등)을 절대 쓰지 말고 진짜 텍스트 기호(• 기호 등)만 써서 폰에서 완벽하게 읽히도록 요약할 것. (만약 category가 '스킵'이면 요약을 생략해도 무방함)"
}
"""

def process_email_with_ai(mail_data, thread_count, latest_summary=""):
    """
    핑퐁 카운트와 이전 요약본(latest_summary)을 토대로, 문맥을 놓치지 않는 지능형 AI 함수입니다.
    """
    email_body = mail_data.get('body', '')
    if not email_body or email_body == "본문 추출 불가 메일" or not GEMINI_API_KEY:
        return {
            "category": "기타",
            "summary": "메일 본문이 없거나 AI API 키 설정을 찾을 수 없어 분석할 수 없습니다."
        }

    # 비용 절감을 위한 핑퐁 다이나믹 프롬프트 작성!
    is_comprehensive = (thread_count == 1 or thread_count % 5 == 0)
    target_text = mail_data.get('body', '') if is_comprehensive else mail_data.get('new_body', '')
    
    if not target_text.strip():
        target_text = email_body
        
    if is_comprehensive:
        mission = "지금까지 핑퐁된 대화 맥락을 총정리해서, 이 대화의 기승전결(전체 흐름)을 종합적으로 세세하게 요약해 줄 것."
        final_text = target_text
    else:
        mission = "이전에 요약해둔 [이전까지의 대화 핵심 요약 리마인드] 내용을 읽고 문맥의 흐름을 파악한 뒤, 그것과 논리적으로 자연스럽게 이어지게끔 [방금 새로 도착한 메시지 원문]의 내용만 덧붙여서 아주 스마트하게 단문 요약할 것."
        if latest_summary:
            final_text = f"--- [이전까지의 대화 핵심 요약 리마인드] ---\n{latest_summary}\n\n--- [방금 새로 도착한 메시지 원문] ---\n{target_text}"
        else:
            final_text = target_text
        
    dynamic_prompt = SYSTEM_PROMPT.replace(
        "이전에 주고받은 답변 내역(인용문)이 있다면 그 대화의 전체 문맥과 흐름까지 모두 파악하여 분석하십시오.",
        mission
    )

    # 최신 패키지 코드 방식(genai.Client)에 맞춰 구글 서버 입장권을 제시합니다.
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        logger.error(f"최신 AI 클라이언트를 준비하는 중 문제가 발생했습니다: {e}")
        return _fallback_response()

    max_retries = 3
    base_waittime = 2

    # 표(JSON) 모양으로만 답을 주도록 단단히 교육하는 최신버전 전용 설정값입니다.
    try:
        req_config = types.GenerateContentConfig(
            system_instruction=dynamic_prompt,
            response_mime_type="application/json"
        )
    except Exception as e:
        logger.error(f"AI 설정(Config) 과정에서 오류가 발생했습니다: {e}")
        return _fallback_response()

    for attempt in range(max_retries):
        try:
            logger.info("최고급 머리를 가진 AI 비서(Gemini 3)에게 이메일 분석을 부탁하는 중입니다...")
            
            # 사용자님께서 콕 찝어주신 최신 모델명('gemini-3-flash-preview')을 장착합니다!
            response = client.models.generate_content(
                model="gemini-3-flash-preview",
                contents=final_text,
                config=req_config
            )

            # 성공하면 받은 텍스트를, 파이썬 서랍(사전, 딕셔너리) 구조로 바꿉니다.
            ai_result = json.loads(response.text)
            logger.info("AI(Gemini 3.0) 분석이 성공적으로 날카롭게 끝났습니다!")
            return ai_result

        except Exception as e:
            logger.error(f"저런! AI 통신 중 약간의 오류 방해가 있습니다 (시도 횟수: {attempt + 1}/{max_retries}): {e}")
            
            if attempt < max_retries - 1:
                # 에러가 나면 처음엔 2초, 다음엔 4초... 이렇게 슬근슬근 텀을 두며 커피를 마시고 재시도합니다. (똑똑한 백오프 로직)
                wait_time = base_waittime * (2 ** attempt)
                logger.warning(f"서버가 아직 자고 있나 봐요! {wait_time}초 동안 잠깐 커피 한 잔 마시고 다시 노크합니다.")
                time.sleep(wait_time)
            else:
                logger.error("AI 서버가 완전히 뻗은 것 같아 이번 분석은 임시 비상 안내문으로 돌려막습니다.")
                return _fallback_response()

def _fallback_response():
    """
    AI 서버가 폭발했을 때 파이프라인 전체가 죽지 않게 던져주는 구급상자입니다.
    """
    return {
        "category": "오류",
        "summary": "AI 서버(구글 허가 서버) 문제로 이번 메일은 임시로 요약하지 못했습니다.\n\n잠시 네트워크나 외부 API 상태가 안 좋으니, 나중에 원문 이메일을 직접 확인해주시기 바랍니다."
    }

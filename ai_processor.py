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
    "category": "'영업', '구매', '내부보고', '무역', '기타', '스킵' 중 하나를 선택. 메일 전체의 내용과 숨은 맥락을 읽어보고 가장 적합한 것을 유연하고 종합적으로 판단할 것. 비록 짧은 답장이나 전달(FW) 메일이더라도, 그 안에 업무 지시, 비용, 일정, 원가 분석, 문서 전달 등 비즈니스적 가치가 일부라도 포함되어 있다면 스킵하지 말고 적절한 카테고리에 배정할 것. 오직 내용 없이 정보성이 전혀 없는 리액션용 인사말(예: '잘 받았습니다', '감사합니다')인 경우에 한해서만 '스킵'을 선택할 것.",
    "summary": "과거 대화의 맥락을 파악하고, 글이 빽빽하지 않도록 각 주제 문단마다 반드시 줄바꿈(엔터)을 1번씩 넉넉하게 띄워 여백을 주고, 주요 핵심은 마크다운(*, # 등)을 쓰지 말고 진짜 텍스트 기호(• 기호 등)만 써서 폰에서 완벽하게 읽히도록 요약할 것. (만약 category가 '스킵'이면 요약을 생략해도 무방함)"
}
"""

def process_email_with_ai(mail_data, thread_count, summary_history=None):
    """
    RAG 누적 기억망(summary_history)을 토대로, 과거 중복 요약을 걸러내는 초거대 지능형 AI 함수입니다.
    """
    if summary_history is None:
        summary_history = []
        
    email_body = mail_data.get('body', '')
    if not email_body or email_body == "본문 추출 불가 메일" or not GEMINI_API_KEY:
        return {
            "category": "기타",
            "summary": "메일 본문이 없거나 AI API 키 설정을 찾을 수 없어 분석할 수 없습니다."
        }

    # 비교 대조법 프롬프트 작성! (파이썬 가위질을 없애고 100% 원문을 모두 던집니다)
    is_comprehensive = (thread_count == 1 or thread_count % 5 == 0)
    target_text = email_body
    
    if is_comprehensive:
        mission = "이 이메일 스레드(핑퐁)의 처음 발단부터 현재까지의 전체적인 맥락과 핵심을 종합적으로 세세하게 브리핑해 줄 것."
        final_text = target_text
    else:
        # 이전에 제미나이가 요약해줬던 내용들을 쫙 나열합니다.
        history_text = "\n\n".join([f"[{i+1}회차 요약] {s}" for i, s in enumerate(summary_history)])
        mission = "여기 내가 그동안 정리해 둔 [과거 누적 요약 기록장]이 있다. 그리고 저기 새롭게 답장(꼬리말)이 길게 섞여 붙어있는 [최신 이메일 원문 전체]가 있다.\n네가 할 일은 이 둘을 치밀하게 비교해서, 과거 기록장에 이미 나와 있는 옛날 대화 내용은 100% 무시하고, 상단에 **오직 새롭게 덧붙여진 '우두머리 최신 메시지(이름/시간/내용)' 단 1개만**을 찾아내서 명료하게 핵심만 단문 요약(새로운 내용 추가)하는 것이다.\n절대로 과거 요약을 반복 재탕해서는 안 된다!"
        if summary_history:
            final_text = f"--- [AI 과거 누적 요약 기록장 (반복 요약 금지! 암기할 것)] ---\n{history_text}\n\n--- [최신 이메일 원문 전체 (위에서 새 내용 1개만 찾아서 요약할 것)] ---\n{target_text}"
        else:
            final_text = target_text
        
    dynamic_prompt = SYSTEM_PROMPT.replace(
        "이전에 주고받은 답변 내역(인용문)이 있다면 그 대화의 전체 문맥과 흐름까지 모두 파악하여 분석하십시오.",
        mission
    )
    
    # [V1.10.0 신규 뇌수술] 사용자가 텔레그램 버튼으로 학습시킨 '싫어하는 이메일(메모장)' 교과서를 통째로 AI 전두엽 프롬프트에 구겨 넣습니다!!
    from feedback_manager import load_preferences
    preferences = load_preferences()
    if preferences:
        # 리스트에 있는 쓰레기 메일 특징들을 번호판 달고 한 문단으로 엮어줍니다.
        pref_text = "\n".join([f"{i+1}. {p}" for i, p in enumerate(preferences)])
        sys_appendix = f"\n\n🚨 [핵심: 사용자 맞춤 거부 학습 노트]\n주인님이 텔레그램을 통해 명시적으로 '이런 류의 내용은 쓸모없으니 스킵하라'고 내 머릿속에 등록해 둔 과거의 비선호 패턴들이야. 아래 목록의 내용/주제 형식과 조금이라도 유사한 뉘앙스의 메일이 새로 들어온다면 절대로 요약하지 말고, 반드시 '스킵' 카테고리로 쳐낼 것!!:\n{pref_text}"
        dynamic_prompt += sys_appendix

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

import os
import json
import time

from google import genai
from google.genai import types
from config import GEMINI_API_KEY, PROMPTS_DIR, logger

def load_prompt(filename):
    """V3.3 외부 텍스트(메모장) 프롬프트 파일을 안전하게 읽어오는 헬퍼 함수"""
    filepath = os.path.join(PROMPTS_DIR, filename)
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except Exception as e:
        logger.error(f"[치명적 오류] 프롬프트 파일({filename})을 찾을 수 없습니다: {e}")
        # 파일이 없을 경우 최소한의 안전장치(기본 자아)를 강제로 반환하여 시스템 다운 방지
        return "당신은 부장님을 보조하는 비서입니다. 친절하게 응답하십시오."

def process_email_with_ai(mail_data, thread_history_text, force_summarize=False):
    """
    V2.6: 비서의 투명성 강화.
    - 스킵할 경우 이유를 명시합니다.
    - force_summarize=True 일 경우 모든 스킵 규칙을 무시하고 강제로 요약합니다.
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

    if force_summarize:
        final_text += "\n\n⚠️ [중요 부장님 명령]: 위 메일이 스킵 기준에 해당하더라도, 이번만큼은 예외로 모든 규칙을 무시하고 반드시 '알림'으로 분류하여 상세히 요약하십시오."

    # [V3.3] 피아니의 자아(Persona)와 엄격한 이메일 요약 규정(Rules) 텍스트 파일 2개를 불러와 조립합니다.
    base_persona = load_prompt("peani_persona.txt")
    summary_rules = load_prompt("email_summary_rules.txt")
    dynamic_prompt = f"{base_persona}\n\n{summary_rules}"
    if not force_summarize:
        try:
            from feedback_manager import load_preferences, load_corrections
            
            # 1. 스킵 기피 패턴 주입
            preferences = load_preferences()
            if preferences:
                pref_text = "\n".join([f"{i+1}. {p}" for i, p in enumerate(preferences)])
                dynamic_prompt += f"\n\n[사용자 기피 학습 노트]\n아래 패턴과 유사한 메일은 '스킵'으로 분류:\n{pref_text}"
                
            # 2. [V3.2] 오답 노트 (요약 시 필수 교정 규칙) 주입
            corrections = load_corrections()
            if corrections:
                corr_text = "\n".join([f"- {c}" for c in corrections])
                dynamic_prompt += f"\n\n[사용자 교정 오답 노트 (가장 우선적으로 지킬 것)]\n이전 요약 오류를 수정한 규칙입니다. 요약 시 반드시 엄수하십시오:\n{corr_text}"
                
        except Exception as e:
            logger.error(f"학습 노트를 불러오는 중 오류 발생: {e}")
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
                logger.info(f"AI 분석 완료 (모델: {model_name}): status={ai_result.get('status')}, skip_reason={ai_result.get('skip_reason')}")
                return ai_result

            except Exception as e:
                logger.error(f"AI 통신 오류 [{model_name}] (시도 {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    wait_time = 2 * (2 ** attempt)
                    logger.warning(f"{wait_time}초 후 재시도합니다.")
                    time.sleep(wait_time)

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

def chat_with_secretary(user_message: str, replied_text: str = None) -> str:
    """
    V3.0 대화형 인공지능 비서 모드:
    사용자의 일상적인 말이나 질문에 대해, 부장님을 보좌하는 유능하고 친절한 비서의 자아(Persona)로 대답합니다.
    [V3.2] replied_text가 제공되면, 사용자가 봇의 지난 요약을 지적/피드백하는 상황으로 간주하여 교정 규칙을 추출합니다.
    """
    if not GEMINI_API_KEY:
        return "🚨 (시스템 오류) 제 두뇌(API 키)가 연결되어 있지 않습니다. .env를 확인해 주세요."

    # [V3.3] 채팅 시에는 피아니 페르소나 텍스트 파일만 깔끔하게 불러와서 뇌에 덮어씌웁니다.
    chat_prompt = load_prompt("peani_persona.txt")
    
    # [V3.7 듀얼 마스터] 평범한 일상 대화 시에도 자신이 할 수 있는 진짜 "기계적 명령어" 한계를 명확히 인식하도록 매뉴얼을 주입합니다.
    chat_prompt += "\n\n" + load_prompt("telegram_commands.txt")
    
    # [V4.2 코어 기능] 부장님 지시: 이전 기억(메모)들을 10건(최신 역순)으로 확장하여 머릿속에 '최근 수첩'을 꽂아줍니다!
    from memo_manager import get_recent_memos
    memo_prompt = load_prompt("memo_instruction.txt")
    recent_memos_text = get_recent_memos(limit=10)
    
    chat_prompt += "\n\n" + memo_prompt + f"\n\n[부장님의 공용 수첩(user_notes.json) 최근 10건 고유 ID 기록]\n{recent_memos_text}"

# [V3.6 버그 수정 및 리팩토링] 부장님이 답장을 보낸 경우, 미리 분리해 둔 특수 임무(reply_mission)를 뇌에 추가합니다.
    if replied_text:
        mission_text = load_prompt("reply_mission.txt")
        chat_prompt += "\n\n" + mission_text.format(replied_text=replied_text[:300])

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        req_config = types.GenerateContentConfig(
            system_instruction=chat_prompt,
        )
        # 빠른 응답을 위해 2.5-flash 모델을 메인으로 사용
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=user_message,
            config=req_config
        )
        return response.text
    except Exception as e:
        logger.error(f"대화 처리 중 오류: {e}")
        return "🚨 앗, 부장님! 방금 머리가 좀 아파서(서버 오류) 말씀을 제대로 못 들었습니다. 다시 말씀해 주시겠어요?"

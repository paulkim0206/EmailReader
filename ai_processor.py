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
        return "당신은 부장님을 보조하는 비서입니다. 친절하게 응답하십시오."

def load_ability(ability_name):
    """[V10.0] abilities 폴더 내의 전문 직무 프롬프트를 읽어옵니다."""
    filepath = os.path.join(PROMPTS_DIR, "abilities", f"{ability_name}.txt")
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except Exception as e:
        logger.warning(f"능력치 파일({ability_name}) 로드 실패: {e}")
        return ""

def process_email_with_ai(mail_data, thread_history_text):
    """
    V10.0: 모듈형 프롬프트 시스템 적용 버전.
    Persona + Summarizer(Ability) + Technical Expert(Ability)를 조합합니다.
    """
    email_body = mail_data.get('body', '')
    if not email_body or email_body == "본문 추출 불가 메일" or not GEMINI_API_KEY:
        return _fallback_response()

    # 1. 지능 조립: 자아 + 요약 능력 + 기술 지식
    base_persona = load_prompt("peani_persona.txt")
    summarizer_ability = load_ability("summarizer")
    tech_expert_ability = load_ability("technical_expert")
    
    dynamic_prompt = f"{base_persona}\n\n{summarizer_ability}\n\n{tech_expert_ability}"
    
    try:
        from feedback_manager import load_preferences, load_corrections
        preferences = load_preferences()
        if preferences:
            pref_text = "\n".join([f"{i+1}. {p}" for i, p in enumerate(preferences)])
            dynamic_prompt += f"\n\n[사용자 기피 학습 노트]\n아래 패턴과 유사한 메일은 '스킵'으로 분류:\n{pref_text}"

        corrections = load_corrections()
        if corrections:
            corr_text = "\n".join([f"- {c}" for c in corrections])
            dynamic_prompt += f"\n\n[사용자 교정 오답 노트]\n요약 시 반드시 엄수:\n{corr_text}"
    except Exception:
        pass

    # 시간 자각 지점 주입
    import datetime
    import pytz
    from config import USER_TIMEZONE
    try:
        tz = pytz.timezone(USER_TIMEZONE)
        now = datetime.datetime.now(tz)
    except Exception:
        now = datetime.datetime.now()
        
    current_time_info = f"\n\n[현재 시간 자각 지침]\n오늘은 {now.strftime('%Y-%m-%d (%A)')} 이며 시각은 {now.strftime('%H:%M:%S')} 입니다."
    dynamic_prompt += current_time_info

    # 제미나이 전송용 데이터 구성
    final_text = f"[새 메일 원문]\n발신: {mail_data.get('sender')}\n제목: {mail_data.get('subject')}\n본문: {email_body}\n\n[요약 장부]\n{thread_history_text}"

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        req_config = types.GenerateContentConfig(
            system_instruction=dynamic_prompt,
            response_mime_type="application/json"
        )
        
        model_chain = ["gemini-3-flash-preview", "gemini-2.5-flash"]
        for model_name in model_chain:
            try:
                response = client.models.generate_content(model=model_name, contents=final_text, config=req_config)
                return json.loads(response.text)
            except Exception as e:
                logger.error(f"AI 통신 오류 ({model_name}): {e}")
                time.sleep(2)
    except Exception:
        pass

    return _fallback_response()

def _fallback_response():
    return {
        "status": "알림",
        "is_ai_error": True,
        "is_thread": False,
        "thread_key": "AI 오류",
        "thread_index": 1,
        "summary": "AI 서버 응답 오류로 메일을 요약하지 못했습니다. 직접 확인해 주십시오."
    }

def chat_with_secretary(user_message: str, replied_text: str = None) -> str:
    """
    V10.0: 자아 + 비서 능력 + 명령어 매뉴얼 조합
    """
    if not GEMINI_API_KEY:
        return "🚨 제 두뇌(API 키)가 연결되어 있지 않습니다."

    # 1. 지능 조립: 자아 + 비서/명령어 대응 능력
    chat_prompt = load_prompt("peani_persona.txt")
    secretary_ability = load_ability("secretary")
    commands_manual = load_prompt("telegram_commands.txt")
    
    chat_prompt += f"\n\n{secretary_ability}\n\n{commands_manual}"
    
    # 최근 대화 맥락 주입
    try:
        from chat_manager import get_recent_chat_context
        chat_context = get_recent_chat_context(limit=20)
        chat_prompt += "\n\n" + chat_context
    except Exception:
        pass

    # 메모 현황 주입
    try:
        from memo_manager import get_active_memos_text
        recent_memos_text = get_active_memos_text()
        chat_prompt += f"\n\n[부장님 수첩 현황]\n{recent_memos_text}"
    except Exception:
        pass

    # 시간 주입
    import datetime
    import pytz
    from config import USER_TIMEZONE
    try:
        tz = pytz.timezone(USER_TIMEZONE)
        now = datetime.datetime.now(tz)
        chat_prompt += f"\n\n[현재 시각] {now.strftime('%Y-%m-%d %H:%M:%S')}"
    except Exception:
        pass

    if replied_text:
        mission_text = load_prompt("reply_mission.txt")
        chat_prompt += "\n\n" + mission_text.format(replied_text=replied_text[:300])

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=user_message,
            config=types.GenerateContentConfig(system_instruction=chat_prompt)
        )
        return response.text
    except Exception as e:
        return f"🚨 처리 중 오류가 발생했습니다: {str(e)[:80]}"

def generate_daily_report_ai(raw_summaries: list) -> dict:
    """
    V10.0: 자아 + 요약 능력 + 전략 기획 능력 조합
    """
    if not GEMINI_API_KEY or not raw_summaries:
        return {"report": "데이터 부족"}

    base_persona = load_prompt("peani_persona.txt")
    summarizer_ability = load_ability("summarizer")
    strategy_chief_ability = load_ability("strategy_chief")
    
    dynamic_prompt = f"{base_persona}\n\n{summarizer_ability}\n\n{strategy_chief_ability}"
    data_text = "\n".join([f"제목: {i['subject']} | 요약: {i['summary']}" for i in raw_summaries])

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=data_text,
            config=types.GenerateContentConfig(
                system_instruction=dynamic_prompt,
                response_mime_type="application/json"
            )
        )
        return json.loads(response.text)
    except Exception:
        return {"topics": [{"category": "오류", "items": ["보고서 생성 실패"]}]}

def generate_weekly_summary_ai(daily_reports: dict) -> dict:
    """
    V10.0: 자아 + 전략 기획 능력(분석 특화) 조합
    """
    if not GEMINI_API_KEY or not daily_reports:
        return {"summary": "데이터 부족"}

    base_persona = load_prompt("peani_persona.txt")
    strategy_chief_ability = load_ability("strategy_chief")
    
    dynamic_prompt = f"{base_persona}\n\n{strategy_chief_ability}"
    
    week_text = ""
    for day, data in daily_reports.items():
        if isinstance(data, dict) and "topics" in data:
            week_text += f"\n[{day}]\n" + "\n".join([f"- {t['category']}: {', '.join(t['items'])}" for t in data["topics"]])

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=week_text,
            config=types.GenerateContentConfig(
                system_instruction=dynamic_prompt,
                response_mime_type="application/json"
            )
        )
        return json.loads(response.text)
    except Exception:
        return {"weekly_summary": "분석 실패", "key_achievements": []}


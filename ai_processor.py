import os
import json
import time
import re
import datetime
import pytz

from google import genai
from google.genai import types
from config import GEMINI_API_KEY, PROMPTS_DIR, logger, PRIMARY_MODEL, BACKUP_MODEL, USER_TIMEZONE
# --- [V11.8] 지능형 성능 최적화: 전역 클라이언트 싱글톤 ---
_AI_CLIENT = None

def _get_ai_client():
    """API 클라이언트를 매번 새로 만들지 않고 한 번만 만들어 재사용하는 엔진입니다."""
    global _AI_CLIENT
    if _AI_CLIENT is None and GEMINI_API_KEY:
        try:
            from google import genai
            _AI_CLIENT = genai.Client(api_key=GEMINI_API_KEY)
        except Exception as e:
            logger.error(f"AI 클라이언트 생성 실패: {e}")
            return None
    return _AI_CLIENT

# --- [V11.7] 지능형 지침서(프롬프트) 전용 스마트 메모리 장부 ---
_PROMPT_CACHE = {} 

def _read_prompt_file(filename, subfolder=None):
    """파일의 수정 시간을 체크하여 똑똑하게(캐싱) 읽어오는 공용 헬퍼 함수"""
    if subfolder:
        filepath = os.path.join(PROMPTS_DIR, subfolder, filename)
    else:
        filepath = os.path.join(PROMPTS_DIR, filename)

    try:
        if not os.path.exists(filepath):
            return ""
            
        # 1. 파일의 '마지막 수정 시간'을 확인합니다.
        current_mtime = os.path.getmtime(filepath)
        
        # 2. 이미 메모리에 있고, 시간이 바뀌지 않았다면? (초고속 반환!)
        if filepath in _PROMPT_CACHE:
            cached = _PROMPT_CACHE[filepath]
            if cached['mtime'] == current_mtime:
                return cached['content']
        
        # 3. 처음 읽거나 내용이 바뀌었다면? (새로 읽고 메모리 업데이트)
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            _PROMPT_CACHE[filepath] = {
                'content': content,
                'mtime': current_mtime
            }
            # logger.info(f"💡 지침서 최신화 완료: {filename}")
            return content
            
    except Exception as e:
        logger.error(f"프롬프트 파일({filename}) 로드 실패: {e}")
        return ""

def _get_now_info():
    """현재 사용자 시간대의 시각 정보를 비서의 자아에 주입하기 위한 문자열 생성"""
    try:
        tz = pytz.timezone(USER_TIMEZONE)
        now = datetime.datetime.now(tz)
        return f"\n\n[현재 시간 자각 지침]\n오늘은 {now.strftime('%Y-%m-%d (%A)')} 이며 시각은 {now.strftime('%H:%M:%S')} 입니다."
    except Exception:
        return f"\n\n[현재 시간] {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

def _clean_ai_json(text):
    """AI 응답에서 불필요한 마크다운 기호(```json 등)를 제거하고 순수 JSON만 추출"""
    if not text: return ""
    return re.sub(r'```json\n?|```', '', text).strip()

# --- [기존 외부 호출 함수 리팩토링] ---

def load_prompt(filename):
    """외부 텍스트 프롬프트 파일을 읽어옵니다. (호환성 유지)"""
    return _read_prompt_file(filename)

def load_ability(ability_name):
    """abilities 폴더 내의 전문 직무 프롬프트를 읽어옵니다. (호환성 유지)"""
    return _read_prompt_file(f"{ability_name}.txt", subfolder="abilities")

def process_email_with_ai(mail_data, thread_history_text, force_summarize=False, retry_count=1):
    """
    [V11.5] 헬퍼 함수 기반 리팩토링 버전.
    [V11.2] retry_count에 따라 주력/백업 엔진을 지능적으로 선택합니다.
    """
    email_body = mail_data.get('body', '')
    if not email_body or email_body == "본문 추출 불가 메일" or not GEMINI_API_KEY:
        return _fallback_response()

    # 1. 지능 및 자아 조립
    dynamic_prompt = _read_prompt_file("peani_persona.txt")
    dynamic_prompt += f"\n\n{load_ability('summarizer')}\n\n{load_ability('technical_expert')}"
    
    try:
        from feedback_manager import load_preferences, load_corrections
        if not force_summarize:
            preferences = load_preferences()
            if preferences:
                pref_text = "\n".join([f"{i+1}. {p}" for i, p in enumerate(preferences)])
                dynamic_prompt += f"\n\n[사용자 기피 학습 노트]\n'스킵' 분류 기준:\n{pref_text}"
        else:
            dynamic_prompt += "\n\n[특별 지침] 반드시 요약하십시오."

        corrections = load_corrections()
        if corrections:
            corr_text = "\n".join([f"- {c}" for c in corrections])
            dynamic_prompt += f"\n\n[교정 오답 노트]\n최우선 산출 기준:\n{corr_text}"
    except Exception: pass

    # 시간 감각 주입
    dynamic_prompt += _get_now_info()

    # 데이터 구성
    final_text = f"[새 메일]\n발신: {mail_data.get('sender')}\n제목: {mail_data.get('subject')}\n본문: {email_body}\n\n[장부]\n{thread_history_text}"

    try:
        client = _get_ai_client()
        if not client: return _fallback_response()
        
        req_config = types.GenerateContentConfig(system_instruction=dynamic_prompt, response_mime_type="application/json")
        
        # 3+3 전략: 1~3회 주력, 4~6회 백업
        model_name = PRIMARY_MODEL if retry_count <= 3 else BACKUP_MODEL

        response = client.models.generate_content(model=model_name, contents=final_text, config=req_config)
        return json.loads(_clean_ai_json(response.text))
    except Exception as e:
        logger.error(f"AI 분석 중 오류 ({retry_count}회차): {e}")
        return _fallback_response()

def _fallback_response():
    return {
        "status": "알림", "is_ai_error": True, "is_thread": False, "thread_key": "AI 오류",
        "thread_index": 1, "summary": "AI 서버 응답 오류로 메일을 요약하지 못했습니다. 직접 확인해 주십시오."
    }

def chat_with_secretary(user_message: str, replied_text: str = None) -> str:
    """[V11.5] 채팅 환경 즉시 백업 및 헬퍼 통합 버전"""
    if not GEMINI_API_KEY: return "🚨 제 두뇌(API 키)가 연결되어 있지 않습니다."

    # 지능 조립
    chat_prompt = _read_prompt_file("peani_persona.txt")
    chat_prompt += f"\n\n{load_ability('secretary')}\n\n{_read_prompt_file('telegram_commands.txt')}"
    
    # 맥락/메모/시간 주입
    try:
        from chat_manager import get_recent_chat_context
        chat_prompt += "\n\n" + get_recent_chat_context(limit=30)
    except Exception: pass

    try:
        from memo_manager import get_active_memos_text
        chat_prompt += f"\n\n[부장님 수첩 현황]\n{get_active_memos_text()}"
    except Exception: pass

    chat_prompt += _get_now_info()

    if replied_text:
        chat_prompt += "\n\n" + _read_prompt_file("reply_mission.txt").format(replied_text=replied_text[:300])

    try:
        client = _get_ai_client()
        if not client: return "🚨 제 두뇌(API 키)가 연결되어 있지 않습니다."
        
        # [V11.8] 속도 복구를 위해 백업 절차 없이 즉시 주력 엔진만 호출합니다.
        response = client.models.generate_content(
            model=PRIMARY_MODEL, 
            contents=user_message,
            config=types.GenerateContentConfig(system_instruction=chat_prompt)
        )
        return response.text
        
    except Exception as e:
        logger.error(f"채팅 엔진 응답 실패: {e}")
        return "🚨 앗, 부장님! 방금 머리가 좀 아파서 말씀을 제대로 못 들었습니다. 다시 말씀해 주시겠어요?"

def generate_daily_report_ai(raw_summaries: list) -> dict:
    """[V11.5] 일일 보고서 생성 리팩토링"""
    if not GEMINI_API_KEY or not raw_summaries: return {"report": "데이터 부족"}

    dynamic_prompt = f"{_read_prompt_file('peani_persona.txt')}\n\n{load_ability('summarizer')}\n\n{load_ability('strategy_chief')}"
    data_text = "\n".join([f"제목: {i['subject']} | 요약: {i['summary']}" for i in raw_summaries])

    try:
        client = _get_ai_client()
        if not client: return {"topics": [{"category": "오류", "items": ["API 연결 실패"]}]}

        response = client.models.generate_content(
            model=PRIMARY_MODEL, contents=data_text,
            config=types.GenerateContentConfig(system_instruction=dynamic_prompt, response_mime_type="application/json")
        )
        return json.loads(_clean_ai_json(response.text))
    except Exception:
        return {"topics": [{"category": "오류", "items": ["보고서 생성 실패"]}]}

def generate_weekly_summary_ai(daily_reports: dict) -> dict:
    """[V11.5] 주간 보고서 생성 리팩토링"""
    if not GEMINI_API_KEY or not daily_reports: return {"summary": "데이터 부족"}

    dynamic_prompt = f"{_read_prompt_file('peani_persona.txt')}\n\n{load_ability('strategy_chief')}"
    week_text = ""
    for day, data in daily_reports.items():
        if isinstance(data, dict) and "topics" in data:
            week_text += f"\n[{day}]\n" + "\n".join([f"- {t['category']}: {', '.join(t['items'])}" for t in data["topics"]])

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model=PRIMARY_MODEL, contents=week_text,
            config=types.GenerateContentConfig(system_instruction=dynamic_prompt, response_mime_type="application/json")
        )
        return json.loads(_clean_ai_json(response.text))
    except Exception:
        return {"weekly_summary": "분석 실패", "key_achievements": []}

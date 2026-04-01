import os
import json
import time
import re
import datetime
import pytz

from google import genai
from google.genai import types
from config import GEMINI_API_KEY, PROMPTS_DIR, logger, AI_MODEL, USER_TIMEZONE
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
        return f"\n\n(참고: 서버시각 {now.strftime('%Y-%m-%d %H:%M:%S')})"
    except Exception:
        return f"\n\n(참고: 서버시각 {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')})"

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

    # [V12.9] 지능형 세이프가드: 과잉 방어 해제 (부장님의 정상 메일 소통 보장)
    # 1. 텍스트 내 유의미한 한국어/영어 단어가 있는지 먼저 확인 (화이트리스트)
    # 한글(가-힣) 또는 영단어([a-zA-Z]{3,})가 5개 이상 발견되면 정상 메일로 간주하고 무조건 통과!
    meaningful_words = re.findall(r'[가-힣]+|[a-zA-Z]{3,}', email_body)
    if len(meaningful_words) >= 5:
        pass # 정상 메일로 판단하여 아래 검사를 건너뜁니다.
    else:
        # 2. 깨진 글자(\ufffd) 비율 확인 (전체의 50% 이상일 때만 진짜 위험군으로 분류)
        replacement_count = email_body.count('\ufffd')
        if len(email_body) > 0 and (replacement_count / len(email_body)) > 0.5:
            return {
                "status": "알림", "is_ai_error": True, "summary": "⚠️ <b>[토큰 보호]</b> 본문 인코딩이 심하게 깨져(50% 이상) 판독이 어렵습니다. 원문을 직접 확인해 주세요."
            }
        
        # 3. 외계어 패턴(공백 없는 긴 Base64 등) 감지 (기존 100자 -> 300자로 대폭 완화)
        if re.search(r'[A-Za-z0-9+/]{300,}', email_body):
            return {
                "status": "알림", "is_ai_error": True, "summary": "⚠️ <b>[토큰 보호]</b> 해독되지 않은 대량의 데이터(Base64)가 감지되어 AI 분석을 차단했습니다. 직접 확인이 필요합니다."
            }

    # 1. 지능 및 자아 조립
    dynamic_prompt = _read_prompt_file("peani_persona.txt")
    dynamic_prompt += f"\n\n{load_ability('summarizer')}"
    
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

    # [V12.7] 부장님의 지식: 정석형 지능형 재시도 (Exponential Backoff 적용)
    max_retries = 3
    current_attempt = 1
    
    while current_attempt <= max_retries:
        try:
            client = _get_ai_client()
            if not client: return _fallback_response()
            
            req_config = types.GenerateContentConfig(system_instruction=dynamic_prompt, response_mime_type="application/json")
            
            # [V12.7] 단일 정예 엔진(AI_MODEL)으로 승부
            response = client.models.generate_content(model=AI_MODEL, contents=final_text, config=req_config)
            result = json.loads(_clean_ai_json(response.text))
            
            # 빈 요약 방지 로직 유지
            if result.get('status') == '알림' and not result.get('summary', '').strip():
                result['summary'] = "💡 [알림] 메인 본문이 너무 복합하거나 지연이 발생하여 요약을 구성하지 못했습니다. 원문을 직접 확인해 주십시오."
                
            return result

        except Exception as e:
            logger.warning(f"AI 분석 중 시도({current_attempt}/{max_retries}) 실패: {e}")
            if current_attempt < max_retries:
                # [V12.7] 정석 타이밍: 1회 실패 시 5초, 2회 실패 시 15초 대기
                wait_time = 5 if current_attempt == 1 else 15
                logger.info(f"지능형 재시도를 위해 {wait_time}초간 숨을 고릅니다...")
                time.sleep(wait_time)
            current_attempt += 1

    # 모든 시도(3회) 실패 시 최종 항복(1단계)
    return _fallback_response()

def _fallback_response():
    """[V12.7] 실시간 시도가 모두 실패했을 때 부장님께 드리는 전문적인 보고"""
    return {
        "status": "알림", "is_ai_error": True, "is_thread": False, "thread_key": "일시적 지연",
        "thread_index": 1, 
        "summary": "⚠️ <b>[피아니 일시 지연]</b> AI 서버 응답 지연으로 실시간 분석을 중단했습니다. 5분 뒤 배경에서 마지막 1회 추가 요약 시도를 진행하겠습니다."
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
            model=AI_MODEL, 
            contents=user_message,
            config=types.GenerateContentConfig(system_instruction=chat_prompt)
        )
        return response.text
        
    except Exception as e:
        logger.error(f"채팅 엔진 응답 실패: {e}")
        return "🚨 앗, 부장님! 방금 머리가 좀 아파서 말씀을 제대로 못 들었습니다. 다시 말씀해 주시겠어요?"

def generate_daily_report_ai(raw_summaries: list) -> dict:
    """[V11.8] 일일 보고서 전용 지침(daily_strategy)을 사용하여 고객사별 요약을 생성합니다."""
    if not GEMINI_API_KEY or not raw_summaries: return {"report": "데이터 부족"}

    # 일일 보고서 전용 지침으로 교체 (더 슬림하고 명확한 비즈니스 분석 수행)
    dynamic_prompt = f"{_read_prompt_file('peani_persona.txt')}\n\n{load_ability('daily_strategy')}"
    data_text = "\n".join([f"제목: {i['subject']} | 요약: {i['summary']}" for i in raw_summaries])

    try:
        client = _get_ai_client()
        if not client: return {"topics": [{"category": "오류", "items": ["API 연결 실패"]}]}

        response = client.models.generate_content(
            model=AI_MODEL, contents=data_text,
            config=types.GenerateContentConfig(system_instruction=dynamic_prompt, response_mime_type="application/json")
        )
        return json.loads(_clean_ai_json(response.text))
    except Exception:
        return {"topics": [{"category": "오류", "items": ["보고서 생성 실패"]}]}

def generate_weekly_summary_ai(daily_reports: dict) -> dict:
    """[V11.8] 주간 보고서 전용 지침(weekly_strategy)을 사용하여 한 주의 흐름을 통합 분석합니다."""
    if not GEMINI_API_KEY or not daily_reports: return {"summary": "데이터 부족"}

    # 주간 보고서 전용 지침으로 교체
    dynamic_prompt = f"{_read_prompt_file('peani_persona.txt')}\n\n{load_ability('weekly_strategy')}"
    week_text = ""
    for day, data in daily_reports.items():
        if isinstance(data, dict) and "topics" in data:
            week_text += f"\n[{day}]\n" + "\n".join([f"- {t['category']}: {', '.join(t['items'])}" for t in data["topics"]])

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model=AI_MODEL, contents=week_text,
            config=types.GenerateContentConfig(system_instruction=dynamic_prompt, response_mime_type="application/json")
        )
        return json.loads(_clean_ai_json(response.text))
    except Exception:
        return {"weekly_summary": "분석 실패", "key_achievements": []}

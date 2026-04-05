import os
import json
import time
import re
import datetime
import pytz

from google import genai
from google.genai import types
from config import GEMINI_API_KEY, PROMPTS_DIR, logger, AI_MODEL, USER_TIMEZONE
from token_manager import log_token
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

# [V12.13] 프롬프트 뇌(뇌세포) 일괄 이식: 기동 시 모든 지침서를 100% 메모리에 상주시킵니다.
_PROMPT_CACHE = {} 
_BASE_SUMMARIZER_PROMPT = "" # [V12.13] 요약 업무를 위해 미리 조립된 지침서 완제품

def load_all_prompts_to_memory():
    """
    [V12.13] 비서가 눈을 뜰 때(기동 시), 'prompts' 폴더 안의 모든 매뉴얼을 
    암기(메모리 캐싱)하도록 하여 분석 속도를 최고 속도로 끌어올립니다.
    """
    global _PROMPT_CACHE
    logger.info("🧠 프롬프트 지침서(매뉴얼) 일괄 암기를 시작합니다...")
    
    # 1. 대상 폴더 정의 (핵심 폴더 및 전문 직무 폴더)
    target_dirs = {
        "core": PROMPTS_DIR,
        "abilities": os.path.join(PROMPTS_DIR, "abilities")
    }

    count = 0
    for category, base_path in target_dirs.items():
        if not os.path.exists(base_path): continue
        
        for filename in os.listdir(base_path):
            # 오직 텍스트(.txt) 파일만 읽으며, 백업 폴더는 무시합니다.
            if filename.endswith(".txt"):
                filepath = os.path.join(base_path, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        # '핵심/파일명' 또는 '파일명' 형식으로 키를 생성하여 저장합니다.
                        key = f"{category}/{filename}" if category != "core" else filename
                        _PROMPT_CACHE[key] = f.read().strip()
                        count += 1
                except Exception as e:
                    logger.error(f"지침서({filename}) 암기 실패: {e}")

    logger.info(f"✅ 총 {count}개의 지침서를 완벽하게 암기했습니다. 이제 분석 시 디스크를 확인하지 않습니다.")
    
    # 2. [V12.13] 베이스 지침서 사전 조립 (Pre-assembly)
    # 메일 분석 시 매번 조립하지 않고, 미리 완성된 지침서를 메모리에 들고 있게 합니다.
    global _BASE_SUMMARIZER_PROMPT
    persona = _read_prompt_file("peani_persona.txt")
    summarizer = _read_prompt_file("summarizer.txt", subfolder="abilities")
    _BASE_SUMMARIZER_PROMPT = f"{persona}\n\n{summarizer}"
    logger.info("⚡ 요약 전문가용 베이스 지침서 사전 조립 완료!")

def _read_prompt_file(filename, subfolder=None):
    """
    [V12.13] 암기된 뇌(캐시)에서 매뉴얼을 즉시 꺼내오는 고속 헬퍼 함수입니다.
    """
    key = f"{subfolder}/{filename}" if subfolder else filename
    
    # 1. 이미 암기된 내용이 있다면 즉시 반환합니다.
    if key in _PROMPT_CACHE:
        return _PROMPT_CACHE[key]
    
    # 2. 혹시나 그새 새 파일이 생겼을 경우를 대비한 최소한의 방어책 (실시간 읽기 시도)
    try:
        filepath = os.path.join(PROMPTS_DIR, subfolder or "", filename)
        if os.path.exists(filepath):
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                _PROMPT_CACHE[key] = content # 다음을 위해 암기
                return content
    except Exception: pass
    
    return ""

def _get_now_info():
    """현재 사용자 시간대의 시각 정보를 비서의 자아에 주입하기 위한 문자열 생성"""
    try:
        tz = pytz.timezone(USER_TIMEZONE)
        now = datetime.datetime.now(tz)
        readable = now.strftime("오후 %I시 %M분" if now.hour >= 12 else "오전 %I시 %M분").replace(" 0", " ")
        return f"\n\n[현재 시각: {readable} ({now.strftime('%Y-%m-%d')}). 이 정보는 참고용이며 이 형식 그대로 출력하지 마십시오.]"
    except Exception:
        return f"\n\n[현재 시각 정보 없음]"

def _clean_ai_json(text):
    """AI 응답에서 불필요한 마크다운 기호(```json 등)를 제거하고 순수 JSON만 추출"""
    if not text: return ""
    # 1. ```json 또는 ``` 문구를 제거합니다. (대소문자 무관)
    cleaned = re.sub(r'```(?:json)?\n?|```', '', text, flags=re.IGNORECASE).strip()
    # 2. JSON 시작({)과 끝(}) 사이의 내용만 남깁니다. (찌꺼기 텍스트 방어)
    json_match = re.search(r'(\{.*\})', cleaned, re.DOTALL)
    return json_match.group(1) if json_match else cleaned

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

    # [V12.11] 최정예 세이프가드: 기계어 패턴(500자)만 철저히 차단 (부장님의 정석 지참)
    # 띄어쓰기 한 칸 없이 500자 이상의 영문/숫자/기호가 이어지면 '기계어'로 판단하여 차단합니다.
    if re.search(r'[A-Za-z0-9+/]{500,}', email_body):
        return {
            "status": "알림", "is_ai_error": True, "summary": "⚠️ <b>[토큰 보호]</b> 해독되지 않은 대량의 데이터(Base64)가 감지되어 AI 분석을 차단했습니다. 직접 확인이 필요합니다."
        }

    # 1. [V12.13] 이미 조립된 '완성형 지침서'를 즉시 가져옵니다.
    dynamic_prompt = _BASE_SUMMARIZER_PROMPT
    if not dynamic_prompt: # 만약 초기화 전이라면 실시간 조립
        dynamic_prompt = f"{_read_prompt_file('peani_persona.txt')}\n\n{load_ability('summarizer')}"
    
    try:
        from feedback_manager import load_preferences, load_corrections
        if not force_summarize:
            preferences = load_preferences()
            if preferences:
                pref_lines = []
                for i, p in enumerate(preferences):
                    if isinstance(p, dict):
                        # [V12.19] 지능형 규칙 추출: 이유(reason)를 최우선으로 제공
                        pref_lines.append(f"{i+1}. [유형/이유]: {p.get('reason')} (예시 제목: {p.get('subject')})")
                    else:
                        pref_lines.append(f"{i+1}. {p}")
                pref_text = "\n".join(pref_lines)
                dynamic_prompt += f"\n\n[사용자 기피 학습 노트]\n기본적으로 모든 메일을 상세히 요약하십시오. 단, 아래 제공되는 **[사용자 기피 학습 노트]**에 명시된 규칙이나 유형(유형/이유)에 해당하여 부장님이 명시적으로 요약을 원치 않는 경우에만 status를 '스킵'으로 분류하십시오. (단순 제목 일치뿐만 아니라 '성격'이 같으면 스킵하십시오.):\n{pref_text}"
        else:
            dynamic_prompt += "\n\n[특별 지침] 반드시 요약하십시오."

        corrections = load_corrections()
        if corrections:
            # [V12.16] AI 최적화 오답 복기 세트 구성 (대괄호와 해시태그 활용)
            corr_text = ""
            for i, c in enumerate(corrections, 1):
                if isinstance(c, dict):
                    corr_text += (
                        f"\n#### [과거 실수 복기 사례 #{i}]\n"
                        f"- [당시 오답]: {c.get('mistake', '알 수 없음')}\n"
                        f"- [부장님 지적]: {c.get('correction', '지시사항 준수 요청')}\n"
                        f"- [반성 및 신규 규칙]: {c.get('lesson', '규칙 미지정')}\n"
                        f"####"
                    )
                else:
                    # 기존 고전 데이터(문자열) 대응
                    corr_text += f"\n- {c}"
            
            dynamic_prompt += f"\n\n[📢 최우선 오답 노트 복기 지침]\n다음은 네가 과거에 실수하여 부장님께 혼났던 사례들이다. 이번 요약 시 절대 같은 실수를 반복하지 마라:\n{corr_text}"
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

            # --- [X-RAY DEBUG START] --- 
            try:
                debug_log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_payload_debug.txt")
                with open(debug_log_path, "a", encoding="utf-8") as f:
                    tz = pytz.timezone(USER_TIMEZONE)
                    now_str = datetime.datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
                    f.write(f"\n{'='*50}\n")
                    f.write(f"[X-RAY DEBUG: process_email_with_ai] {now_str}\n")
                    f.write(f"{'-'*50}\n")
                    f.write(f"[SYSTEM_INSTRUCTION]\n{dynamic_prompt}\n")
                    f.write(f"{'-'*50}\n")
                    f.write(f"[CONTENTS]\n{final_text}\n")
                    f.write(f"{'='*50}\n")
            except Exception as de:
                logger.error(f"X-레이 디버깅 기록 중 오류: {de}")
            # --- [X-RAY DEBUG END] ---

            # [V12.7] 단일 정예 엔진(AI_MODEL)으로 승부
            response = client.models.generate_content(model=AI_MODEL, contents=final_text, config=req_config)
            result = json.loads(_clean_ai_json(response.text))
            
            # [V12.25] 실시간 토큰 사용량 기록 (입력/출력)
            if response.usage_metadata:
                log_token("Mail_Summary", response.usage_metadata.prompt_token_count, response.usage_metadata.candidates_token_count)
            
            # [V12.17] client_name이 누락되었을 경우를 대비한 기본값 설정
            if 'client_name' not in result:
                result['client_name'] = "알 수 없음"
                
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

def extract_skip_rule_ai(subject: str, body: str) -> str:
    """
    [V12.19] 사용자가 '요약 제외'를 누른 이유를 AI가 스스로 분석하여 
    일반화된 '스킵 규칙(Rule)'을 한 문장으로 추출합니다.
    """
    if not subject and not body: return "내용 없음"
    
    prompt = (
        f"너는 부장님의 취향을 완벽히 파악하는 수석 비서다.\n"
        f"부장님이 아래 메일을 보시고 '요약할 필요 없다'며 제외(Skip)하셨다.\n"
        f"이 메일의 제목과 내용을 보고, 부장님이 이 메일을 제외하신 '본성(유형)'을 분석해라.\n\n"
        f"대상 메일:\n[제목]: {subject}\n[본문]: {body[:2000]}\n\n"
        f"반드시 아래와 같이 '[유형] 이유' 형식의 한 문장으로만 결론을 내라.\n"
        f"예: 🔴 [시스템 자동 회신] 특정 인물의 부재나 휴가 안내 등 업무 실체가 없는 자동 메일\n"
        f"예: 📊 [단순 데이터 공유] 매일 반복되는 원자재 및 부품의 재고 현황 단순 리스트"
    )
    
    # --- [X-RAY DEBUG START] ---
    try:
        debug_log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_payload_debug.txt")
        with open(debug_log_path, "a", encoding="utf-8") as f:
            tz = pytz.timezone(USER_TIMEZONE)
            now_str = datetime.datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"\n{'='*50}\n")
            f.write(f"[X-RAY DEBUG: extract_skip_rule_ai] {now_str}\n")
            f.write(f"{'-'*50}\n")
            f.write(f"[SYSTEM_INSTRUCTION]\n(없음)\n")
            f.write(f"{'-'*50}\n")
            f.write(f"[CONTENTS]\n{prompt}\n")
            f.write(f"{'='*50}\n")
    except Exception as de:
        logger.error(f"X-레이 디버깅 기록 중 오류: {de}")
    # --- [X-RAY DEBUG END] ---

    try:
        client = _get_ai_client()
        if not client: return "분석 실패(API 오류)"
        
        response = client.models.generate_content(model=AI_MODEL, contents=prompt)
        
        # [V12.25] 토큰 기록
        if response.usage_metadata:
            log_token("Skip_Rule_Analysis", response.usage_metadata.prompt_token_count, response.usage_metadata.candidates_token_count)
            
        rule = response.text.strip() if response.text else "유형 파악 불가"
        return rule
    except Exception as e:
        logger.error(f"스킵 규칙 추출 중 오류: {e}")
        return "분석 중 오류 발생"

def _fallback_response():
    """[V12.7] 실시간 시도가 모두 실패했을 때 부장님께 드리는 전문적인 보고"""
    return {
        "status": "알림", "is_ai_error": True, "is_thread": False, "thread_key": "일시적 지연",
        "thread_index": 1, 
        "summary": "⚠️ <b>[피아니 일시 지연]</b> AI 서버 응답 지연으로 실시간 분석을 중단했습니다. 5분 뒤 배경에서 마지막 1회 추가 요약 시도를 진행하겠습니다."
    }

def route_intent(user_message: str) -> str:
    """
    [V13.0] AI 기반 초경량 의도 분석 라우터.
    사용자의 메시지를 읽고 4가지 카테고리 중 하나로 정확히 분류합니다.
    분류 기준: MAIL_WORK, MEMO_WORK, REPORT_WORK, GENERAL_CHAT
    """
    if not GEMINI_API_KEY: return "GENERAL_CHAT"
    if not user_message: return "GENERAL_CHAT"
    
    # [QC] 단순히 '📝 메모보기' 버튼을 누른 경우는 굳이 AI를 거칠 필요 없이 토큰 0개로 즉시 라우팅합니다.
    if user_message.strip() == "📝 메모보기":
        return "MEMO_WORK"

    prompt = (
        "너는 부장님의 지시 의도를 정확히 파악하는 초고속 의도 분석 라우터(Router)다.\n"
        "다음 사용자의 메시지를 읽고, 오직 아래 4가지 영문 카테고리 이름 중 하나만 결과로 출력하라. (설명, 인사말 등 다른 말은 절대 금지)\n\n"
        "[카테고리]\n"
        "1. MAIL_WORK : 이메일 요약, 메일 수신 확인, 스킵 이유 등 메일과 관련된 질문\n"
        "2. MEMO_WORK : 수첩 목록 확인, 메모 내용 저장/삭제/수정, 특정 항목 완료 지시\n"
        "3. REPORT_WORK : 일일 보고서, 주간 보고서 생성 요청\n"
        "4. GENERAL_CHAT : 일상적인 인사, 안부, 궁금증, 잡담, 칭찬, 비서 자체와의 대화\n\n"
        f"대상 메시지: {user_message[:500]}\n"
        "정답 카테고리:"
    )

    try:
        from token_manager import log_token
        client = _get_ai_client()
        if not client: return "GENERAL_CHAT"

        # --- [X-RAY DEBUG START] ---
        try:
            debug_log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_payload_debug.txt")
            with open(debug_log_path, "a", encoding="utf-8") as f:
                tz = pytz.timezone(USER_TIMEZONE)
                now_str = datetime.datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"\n{'='*50}\n")
                f.write(f"[X-RAY DEBUG: route_intent] {now_str}\n")
                f.write(f"{'-'*50}\n")
                f.write(f"[SYSTEM_INSTRUCTION]\n(없음)\n")
                f.write(f"{'-'*50}\n")
                f.write(f"[CONTENTS]\n{prompt}\n")
                f.write(f"{'='*50}\n")
        except Exception as de:
            logger.error(f"X-레이 디버깅 기록 중 오류: {de}")
        # --- [X-RAY DEBUG END] ---

        # 가장 빠르고 일관된 출력을 위해 temperature 최소화 설정
        response = client.models.generate_content(
            model=AI_MODEL, 
            contents=prompt,
             config=types.GenerateContentConfig(temperature=0.0)
        )
        
        # [V12.25] 토큰 기록 (라우터 전용)
        if hasattr(response, 'usage_metadata') and response.usage_metadata:
            log_token("Intent_Router", response.usage_metadata.prompt_token_count, response.usage_metadata.candidates_token_count)

        result = response.text.strip().upper() if response.text else "GENERAL_CHAT"
        logger.info(f"[Intent Router] AI 판단 원본 결과: {result}")
        
        if "MAIL" in result: return "MAIL_WORK"
        elif "MEMO" in result: return "MEMO_WORK"
        elif "REPORT" in result: return "REPORT_WORK"
        else: return "GENERAL_CHAT"
        
    except Exception as e:
        logger.error(f"의도 분류 중 오류 (기본값 GENERAL_CHAT 설정): {e}")
        return "GENERAL_CHAT"

def chat_with_secretary(user_message: str, replied_text: str = None, include_history: bool = True, include_memos: bool = False, intent: str = "GENERAL_CHAT") -> str:
    """
    [V12.16] 초고성능 실시간 기억력 이식 (True Multi-turn API 적용)
    [V13.0] 프롬프트 기능 분할 수술 적용 (동적 어빌리티 로딩)
    """
    if not GEMINI_API_KEY: return "🚨 제 두뇌(API 키)가 연결되어 있지 않습니다."

    # 1. 수석 비서용 정체성 조립 (System Persona)
    chat_prompt = _read_prompt_file("peani_persona.txt")
    
    # [새로운 아키텍처] 의도(Intent)에 따라 필요한 '기능형 스위치 프롬프트'만 이식하여 토큰을 절약합니다!
    if intent == "MEMO_WORK":
        chat_prompt += f"\n\n{load_ability('memo_trigger')}"
    elif intent == "REPORT_WORK":
        chat_prompt += f"\n\n{load_ability('report_trigger')}"
        
    # 2. 고정 지식(수첩/시간) 주입
    if include_memos:
        try:
            from memo_manager import get_active_memos_text
            chat_prompt += f"\n\n[부장님 수첩 현황]\n{get_active_memos_text()}"
        except Exception: pass


    chat_prompt += _get_now_info()

    # 3. 답장(Reply) 시 맥락 강조 (부장님이 무엇에 대해 말씀하시는지 인지력 강화)
    if replied_text:
        chat_prompt += "\n\n" + _read_prompt_file("reply_mission.txt").format(replied_text=replied_text[:300])

    try:
        # [V12.16] 전략적 필터링: GPS 분석 등 단순 작업 시에는 14일치 방대한 기억을 불러오지 않습니다. (과부하 방지)
        history_raw = []
        if include_history:
            from chat_manager import get_recent_chat_history_raw
            # 초강력 인지능력: 최근 14일간의 모든 대화 맥락을 순식간에 복원합니다.
            history_raw = get_recent_chat_history_raw(days=14)
        else:
            logger.info("⚡ [초경량 인지] 14일 기억 복원을 건너뛰고 부장님의 명령에만 집중합니다.")
        
        # 제미나이의 '대화 흐름' 방식(user -> model)으로 데이터를 완벽히 변환합니다.
        contents = []
        for log in history_raw:
            role = "user" if log['role'] == 'user' else "model"
            contents.append(types.Content(role=role, parts=[types.Part.from_text(text=log['content'])]))
        
        # [치명적 버그 수정] 어떤 경우에도 부장님의 '현재 메시지'는 배열 맨 마지막에 추가되어야 합니다.
        contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_message)]))

        client = _get_ai_client()
        if not client: return "🚨 제 두뇌(API 키)가 연결되어 있지 않습니다."

        # --- [X-RAY DEBUG START] ---
        try:
            debug_log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_payload_debug.txt")
            with open(debug_log_path, "a", encoding="utf-8") as f:
                tz = pytz.timezone(USER_TIMEZONE)
                now_str = datetime.datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"\n{'='*50}\n")
                f.write(f"[X-RAY DEBUG: chat_with_secretary] {now_str}\n")
                f.write(f"{'-'*50}\n")
                f.write(f"[SYSTEM_INSTRUCTION]\n{chat_prompt}\n")
                f.write(f"{'-'*50}\n")
                f.write(f"[CONTENTS]\n{contents}\n")
                f.write(f"{'='*50}\n")
        except Exception as de:
            logger.error(f"X-레이 디버깅 기록 중 오류: {de}")
        # --- [X-RAY DEBUG END] ---

        # [혁신] 단일 메시지가 아닌 '누적된 대화 흐름(contents)' 전체를 바탕으로 응답을 생성합니다.
        response = client.models.generate_content(
            model=AI_MODEL, 
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=chat_prompt)
        )
        
        # [V12.25] 토큰 기록
        if response.usage_metadata:
            log_token("Secretary_Chat", response.usage_metadata.prompt_token_count, response.usage_metadata.candidates_token_count)
            
        # [V12.16] AI 응답이 비어있거나(None) 차단되었을 때를 대비한 최종 방어선
        return response.text or "🚨 앗, 부장님! 방금 머릿속에 안개가 낀 것처럼 답변이 떠오르지 않습니다. 다시 한번 말씀해 주시겠어요?"
        
    except Exception as e:
        logger.error(f"지능형 대화 엔진 오류: {e}")
        return "🚨 앗, 부장님! 방금 머릿속에 기억들이 꼬여서 잠시 멍해졌습니다. 다시 말씀해 주시겠어요?"

def generate_daily_report_ai(raw_summaries: list) -> dict:
    """[V11.8] 일일 보고서 전용 지침(daily_strategy)을 사용하여 고객사별 요약을 생성합니다."""
    if not GEMINI_API_KEY or not raw_summaries: return {"report": "데이터 부족"}

    # 일일 보고서 전용 지침으로 교체 (더 슬림하고 명확한 비즈니스 분석 수행)
    dynamic_prompt = f"{_read_prompt_file('peani_persona.txt')}\n\n{load_ability('daily_strategy')}"
    # [V12.17] 장부에 저장된 진짜 고객사명(client)을 제공하여 AI의 오판을 방지합니다.
    data_text = "\n".join([f"고객사: {i['client']} | 제목: {i['subject']} | 요약: {i['summary']}" for i in raw_summaries])

    try:
        client = _get_ai_client()
        if not client: return {"topics": [{"category": "오류", "items": ["API 연결 실패"]}]}

        # --- [X-RAY DEBUG START] ---
        try:
            debug_log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_payload_debug.txt")
            with open(debug_log_path, "a", encoding="utf-8") as f:
                tz = pytz.timezone(USER_TIMEZONE)
                now_str = datetime.datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"\n{'='*50}\n")
                f.write(f"[X-RAY DEBUG: generate_daily_report_ai] {now_str}\n")
                f.write(f"{'-'*50}\n")
                f.write(f"[SYSTEM_INSTRUCTION]\n{dynamic_prompt}\n")
                f.write(f"{'-'*50}\n")
                f.write(f"[CONTENTS]\n{data_text}\n")
                f.write(f"{'='*50}\n")
        except Exception as de:
            logger.error(f"X-레이 디버깅 기록 중 오류: {de}")
        # --- [X-RAY DEBUG END] ---

        response = client.models.generate_content(
            model=AI_MODEL, contents=data_text,
            config=types.GenerateContentConfig(system_instruction=dynamic_prompt, response_mime_type="application/json")
        )
        
        # [V12.25] 토큰 기록
        if response.usage_metadata:
            log_token("Daily_Report", response.usage_metadata.prompt_token_count, response.usage_metadata.candidates_token_count)
            
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
        client = _get_ai_client()
        if not client: return {"weekly_summary": "분석 실패", "key_achievements": []}
        
        # --- [X-RAY DEBUG START] ---
        try:
            debug_log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_payload_debug.txt")
            with open(debug_log_path, "a", encoding="utf-8") as f:
                tz = pytz.timezone(USER_TIMEZONE)
                now_str = datetime.datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"\n{'='*50}\n")
                f.write(f"[X-RAY DEBUG: generate_weekly_summary_ai] {now_str}\n")
                f.write(f"{'-'*50}\n")
                f.write(f"[SYSTEM_INSTRUCTION]\n{dynamic_prompt}\n")
                f.write(f"{'-'*50}\n")
                f.write(f"[CONTENTS]\n{week_text}\n")
                f.write(f"{'='*50}\n")
        except Exception as de:
            logger.error(f"X-레이 디버깅 기록 중 오류: {de}")
        # --- [X-RAY DEBUG END] ---

        response = client.models.generate_content(
            model=AI_MODEL, contents=week_text,
            config=types.GenerateContentConfig(system_instruction=dynamic_prompt, response_mime_type="application/json")
        )
        
        # [V12.25] 토큰 기록
        if response.usage_metadata:
            log_token("Weekly_Report", response.usage_metadata.prompt_token_count, response.usage_metadata.candidates_token_count)
            
        return json.loads(_clean_ai_json(response.text))
    except Exception as e:
        logger.error(f"주간 보고서 분석 중 오류: {e}")
        return {"weekly_summary": "분석 실패", "key_achievements": []}

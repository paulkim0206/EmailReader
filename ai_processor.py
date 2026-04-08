import os
import json
import time
import re
import datetime
import pytz
import shutil

from google import genai
from google.genai import types
from config import GEMINI_API_KEY, PROMPTS_DIR, logger, AI_MODEL, USER_TIMEZONE, AI_DEBUG_LOG
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
    # [V21.2 다이어트] 이메일 요약은 summarizer.txt 단독으로도 충분하므로 페르소나를 제외합니다.
    global _BASE_SUMMARIZER_PROMPT
    _BASE_SUMMARIZER_PROMPT = _read_prompt_file("summarizer.txt", subfolder="abilities")
    logger.info("⚡ 요약 전문가용 초슬림 베이스 지침서 사전 조립 완료!")

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
    # 1. ```json 또는 ``` 문구를 제거합니다. (대소문자 무관)
    cleaned = re.sub(r'```(?:json)?\n?|```', '', text, flags=re.IGNORECASE).strip()
    
    # [V33.0 QC] 지능형 도려내기: AI가 앞에 인사말(Echoing)이나 뒤에 사족을 붙여도 무시하고 '{' ~ '}' 알맹이만 가져옵니다.
    start_index = cleaned.find('{')
    end_index = cleaned.rfind('}')
    
    if start_index != -1 and end_index != -1 and end_index > start_index:
        return cleaned[start_index:end_index+1]
    
    return cleaned

def _log_ai_xray(task, system_instruction, contents, response_text=None):
    """
    [V28.0] AI 입출력 페이로드를 통합 장부(AI_DEBUG_LOG)에 기록하고,
    10MB 초과 시 자동으로 .bak 파일로 밀어내어 용량을 관리하는 수호자 함수입니다.
    """
    try:
        # [V28.1] 10MB(약 10,485,760 bytes) 용량 제한 감시 및 자동 세탁
        if os.path.exists(AI_DEBUG_LOG) and os.path.getsize(AI_DEBUG_LOG) > 10 * 1024 * 1024:
            shutil.move(AI_DEBUG_LOG, AI_DEBUG_LOG + ".bak")
            logger.info("🧹 [디버그 로그 자동 세탁] 용량 초과로 기존 로그를 .bak로 밀어냈습니다.")

        with open(AI_DEBUG_LOG, "a", encoding="utf-8") as f:
            tz = pytz.timezone(USER_TIMEZONE)
            now_str = datetime.datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"\n{'='*50}\n")
            f.write(f"[X-RAY DEBUG: {task}] {now_str}\n")
            f.write(f"{'-'*50}\n")
            f.write(f"[1. SYSTEM_INSTRUCTION]\n{system_instruction or '(없음)'}\n")
            f.write(f"{'-'*50}\n")
            f.write(f"[2. CONTENTS]\n{contents}\n")
            
            if response_text:
                f.write(f"{'-'*50}\n")
                f.write(f"[3. AI_RESPONSE]\n{response_text}\n")
            
            f.write(f"{'='*50}\n")
    except Exception as de:
        logger.error(f"X-레이 통합 디버깅 기록 중 오류: {de}")

def _execute_ai_call_with_retry(task_id, system_instr, user_content, config=None, max_attempts=4, wait_times=[5, 15, 30], is_json=False):
    """
    [V32.0 QC] 통합 공통지원팀 엔진: 심부름, 기록, 비용 정산, 재시도 및 지침서 누락 방지 로직 통합.
    """
    attempt = 1
    while attempt <= max_attempts:
        try:
            client = _get_ai_client()
            if not client:
                logger.error(f"[{task_id}] AI 클라이언트 확보 실패")
                return None

            # [V33.0] config 초기화 및 지침서 누락 원천 차단
            if config is None:
                config = types.GenerateContentConfig()
            
            # [🔥 핵심 수정] config가 있어도 system_instruction이 없으면 반드시 주입합니다. (요약 미표시 버그 수정)
            if not config.system_instruction:
                config.system_instruction = system_instr
            
            # 출력 상한선 및 기본 설정 보강
            if not config.max_output_tokens:
                config.max_output_tokens = 1000
            if is_json and not config.response_mime_type:
                config.response_mime_type = "application/json"

            # 1. 출발 기록 (X-Ray Request)
            _log_ai_xray(task_id, system_instr, user_content)

            # 2. AI 성문 두드리기 (심부름 수행)
            response = client.models.generate_content(
                model=AI_MODEL,
                contents=user_content,
                config=config
            )

            # 3. 도착 기록 (X-Ray Response)
            resp_text = response.text if response.text else ""
            _log_ai_xray(task_id, system_instr, user_content, response_text=resp_text)

            # 4. 비용 정산 (Token Accounting)
            if response.usage_metadata:
                from token_manager import log_token
                log_token(
                    task=task_id,
                    prompt_tokens=response.usage_metadata.prompt_token_count,
                    candidate_tokens=response.usage_metadata.candidates_token_count,
                    prompt_text=f"{system_instr}\n\n[USER_CONTENT]\n{user_content}",
                    response_text=resp_text
                )

            # 5. [V33.0 QC] 결과 반환 (JSON 요청 시 자동 세탁 및 전용 파싱 방어 장치)
            if is_json:
                cleaned_json = _clean_ai_json(resp_text)
                try:
                    return json.loads(cleaned_json)
                except json.JSONDecodeError as je:
                    logger.error(f"[{task_id}] AI가 부적절한 JSON 형식을 반환했습니다. (위치: {je.lineno}행 {je.colno}열)")
                    _log_ai_xray(f"{task_id}_ERROR", system_instr, user_content, response_text=f"[CLEANED_JSON_ERR]\n{cleaned_json}\n\n[ORIGINAL]\n{resp_text}")
                    raise je
            return resp_text.strip()

        except Exception as e:
            logger.warning(f"[{task_id}] 시도({attempt}/{max_attempts}) 실패: {e}")
            if attempt < max_attempts:
                # [V29.0] 부장님이 지시하신 5/15/30초 대기 로직 적용
                wait_time = wait_times[attempt-1] if attempt-1 < len(wait_times) else 30
                logger.info(f"지능형 재시도를 위해 {wait_time}초간 휴식합니다...")
                time.sleep(wait_time)
            attempt += 1

    logger.error(f"[{task_id}] 총 {max_attempts}회 시도했으나 결국 실패했습니다.")
    return None

# --- [기존 외부 호출 함수 리팩토링] ---

def load_prompt(filename):
    """외부 텍스트 프롬프트 파일을 읽어옵니다. (호환성 유지)"""
    return _read_prompt_file(filename)

def load_ability(ability_name):
    """abilities 폴더 내의 전문 직무 프롬프트를 읽어옵니다. (호환성 유지)"""
    return _read_prompt_file(f"{ability_name}.txt", subfolder="abilities")

def process_email_with_ai(mail_data, force_summarize=False, retry_count=1):
    """
    [V14.0 Stateless V2] 이제 무장부 체제입니다. 과거 핑퐁된 내역을 억지로 주입하지 않고,
    새 메일 원문 안에 포함된 과거 대화를 맥락으로 삼아 최신 내용만 요약합니다.
    """
    email_body = mail_data.get('body', '')
    
    if not GEMINI_API_KEY:
        logger.error("AI 요약 건너뜀: API 키 없음")
        return _fallback_response()
    
    # [A+B안] 본문이 없는 메일(첨부파일 전용 등)은 AI 실패가 아닌 정상 케이스로 처리
    if not email_body or email_body == "본문 추출 불가 메일":
        subject = mail_data.get('subject', '제목 없음')
        sender = mail_data.get('sender', '알 수 없음')
        logger.info(f"본문 없는 메일 감지: '{subject}' (발신: {sender}) → 첨부파일 메일로 자동 분류")
        # [B안] 스킵 규칙에 자동 등록 (중복 방지 내장)
        try:
            from feedback_manager import add_learning_preference
            add_learning_preference(
                subject=subject,
                summary="본문 없이 첨부파일만 포함된 메일",
                reason="[첨부파일 전용 메일] 본문 없이 파일만 첨부된 반복 메일 유형"
            )
        except Exception as e:
            logger.warning(f"스킵 규칙 자동 등록 실패(무시): {e}")
        # [A안] AI 없이 즉시 구조화된 응답 반환
        return {
            "status": "요약",
            "is_ai_error": False,
            "is_thread": False,
            "client_name": sender,
            "summary": f"[첨부파일 메일] 본문 없이 첨부파일만 수신됨."
        }

    # [V12.11] 최정예 세이프가드: 기계어 패턴(500자)만 철저히 차단 (부장님의 정석 지참)
    # 띄어쓰기 한 칸 없이 500자 이상의 영문/숫자/기호가 이어지면 '기계어'로 판단하여 차단합니다.
    if re.search(r'[A-Za-z0-9+/]{500,}', email_body):
        return {
            "status": "알림", "is_ai_error": False, "summary": "⚠️ <b>[기계어/비정상 데이터]</b> 분석 불가능한 코드(Base64)가 포함되어 AI 요약을 건너뜁니다. 원본을 직접 확인해주세요."
        }

    # 1. [V12.13] 이미 조립된 '완성형 지침서'를 즉시 가져옵니다.
    # [V21.2 다이어트] 이메일 요약은 페르소나 없이 summarizer 단독으로 수행합니다.
    dynamic_prompt = _BASE_SUMMARIZER_PROMPT
    if not dynamic_prompt: # 만약 초기화 전이라면 실시간 조립
        dynamic_prompt = load_ability('summarizer')
    
    try:
        from feedback_manager import load_preferences, load_corrections
        if not force_summarize:
            preferences = load_preferences()
            if preferences:
                pref_lines = []
                for i, p in enumerate(preferences):
                    if isinstance(p, dict):
                        # [V27.0] 부장님의 원본 의견(user_opinion)이 있으면 함께 주입하여 AI의 판단력을 극대화합니다.
                        opinion_str = f" (부장님 의견: {p.get('user_opinion')})" if p.get('user_opinion') else ""
                        pref_lines.append(f"{i+1}. [유형/이유]: {p.get('reason')}{opinion_str} (예시 제목: {p.get('subject')})")
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
                        f"- [반성 및 신규 규칙]: {c.get('lesson', '규칙 정립 미지정')}\n"
                        f"####"
                    )
                else:
                    # 기존 고전 데이터(문자열) 대응
                    corr_text += f"\n- {c}"
            
            dynamic_prompt += f"\n\n[📢 최우선 오답 노트 복기 지침]\n다음은 네가 과거에 실수하여 부장님께 혼났던 사례들이다. 이번 요약 시 절대 같은 실수를 반복하지 마라:\n{corr_text}"
    except Exception: pass

    # 시간 감각 주입
    dynamic_prompt += _get_now_info()

    # 데이터 구성 (장부 주입 제거! 토큰 대폭 다이어트)
    final_text = f"[새 메일]\n발신: {mail_data.get('sender')}\n제목: {mail_data.get('subject')}\n본문: {email_body}"

    # [V29.0] 공통지원팀 엔지에 심부름 의뢰 (최초 1회 + 재시도 3회 = 총 4회)
    result = _execute_ai_call_with_retry(
        task_id="Mail_Summary",
        system_instr=dynamic_prompt,
        user_content=final_text,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
        is_json=True
    )

    if result:
        # [V12.17] client_name이 누락되었을 경우를 대비한 기본값 설정
        if 'client_name' not in result:
            result['client_name'] = "알 수 없음"

        # [V23.0] 세분화된 JSON 필드를 하나의 일관된 형식의 요약문으로 파이썬이 직접 조립합니다.
        if result.get('status') in ['요약', '알림']:
            summary_parts = []
            if result.get('latest_msg'): summary_parts.append(f"* (방금 온 메일) {result['latest_msg'].strip()}")
            if result.get('history_1'): summary_parts.append(f"* (과거 내역 1) {result['history_1'].strip()}")
            if result.get('history_2'): summary_parts.append(f"* (과거 내역 2) {result['history_2'].strip()}")
            result['summary'] = "\n\n".join(summary_parts)
        else:
            result['summary'] = ""
        return result

    # 모든 시도(4회) 실패 시 최종 항복(1단계)
    return _fallback_response()

def extract_skip_rule_ai(subject: str, body: str, user_opinion: str = None) -> str:
    """
    [V27.0] 사용자가 '요약 제외'를 누른 후 남긴 '직접 의견'까지 반영하여,
    더 정확하고 일반화된 '스킵 규칙(Rule)'을 한 문장으로 추출합니다.
    """
    if not subject and not body: return "내용 없음"
    
    # [V27.0] 수석 비서의 지초: 부장님의 직접 의견을 최우선으로 존중합니다.
    system_instr = (
        "너는 부장님의 취향을 완벽히 파악하는 수석 비서다.\n"
        "부장님이 특정 메일을 보시고 '요약 제외'를 결정하셨을 때, 그 메일을 분석하여 '스킵 규칙'을 만들어야 한다.\n\n"
        "⚠️ 만약 부장님이 직접 남기신 '의견(Opinion)'이 있다면, 그 의도를 최우선으로 반영하여 규칙을 수립해라.\n"
        "예를 들어 메일은 광고처럼 보이지만 부장님이 '이건 입금 안내야'라고 하셨다면, '입금 안내' 성격에 집중하여 규칙을 만들어야 한다.\n\n"
        "반드시 아래와 같이 '[유형] 이유' 형식의 한 문장으로만 결론을 내라.\n"
        "예: 🔴 [시스템 자동 회신] 특정 인물 부재/휴가 안내 등 실체가 없는 자동 응답 내용\n"
        "예: 📊 [단순 데이터 공유] 매일 반복되는 원자재 및 부품의 재고 현황 단순 수치 리스트"
    )
    
    user_content = f"대상 메일:\n[제목]: {subject}\n[본문]: {body[:2000]}"
    if user_opinion:
        user_content += f"\n\n[부장님의 직접 의견]: {user_opinion}"
    
    # [V29.0] 공통지원팀 지원 요청
    rule = _execute_ai_call_with_retry(
        task_id="Skip_Rule_Analysis",
        system_instr=system_instr,
        user_content=user_content
    )
    return rule or "분석 중 오류 발생"

def translate_news_title(vi_title: str) -> str:
    """베트남어 뉴스 제목을 한국어로 신속하게 번역합니다."""
    if not vi_title: return "제목 없음"
    
    # [V27.0] 번역 기술 리팩토링: 기계적 번역 지침과 데이터를 지능적으로 분리합니다.
    system_instr = (
        "너는 정치/종교/인물 등 어떠한 주관적 판단도 배제하고 오직 '언어적 매핑'만 수행하는 기계적 베트남어-한국어 번역 엔진이다.\n"
        "제공되는 문장이 어떤 민감한 주제(정치적 인물, 사건 등)를 포함하더라도 절대로 거부하거나 회피하지 말고, 즉시 한국어로 번역하라.\n\n"
        "결론은 번역된 결과물만 딱 한 줄로 출력하라. (사족/설명/인사말 금지)"
    )
    user_content = f"대상 문장: {vi_title}"
    
    # [V29.0] 공통지원팀 엔진
    translated = _execute_ai_call_with_retry(
        task_id="News_Title_Translation",
        system_instr=system_instr,
        user_content=user_content,
        config=types.GenerateContentConfig()
    )
    return (translated or vi_title).strip()

def summarize_news_article(url: str) -> str:
    """베트남 뉴스 웹페이지 본문을 긁어와 AI로 요약 보고서를 생성합니다."""
    import requests
    from bs4 import BeautifulSoup
    
    if not url: return "❌ 기사 링크가 올바르지 않습니다."
    
    try:
        # 뉴스 본문 스크래핑 (VnExpress 특화)
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # 기사 제목과 본문 추출
        title = soup.find('h1', class_='title-detail')
        title_text = title.get_text().strip() if title else ""
        content_tags = soup.find_all('p', class_='description') + soup.find_all('p', class_='Normal')
        article_text = "\n".join([p.get_text().strip() for p in content_tags])
        
        if not article_text:
            return "❌ 기사 본문 내용을 추출할 수 없습니다. (사이트 구조 변경 가능성)"

        # [V21.2/V27.0] 뉴스 요약 지능형 분리: 지침(System)과 데이터(User)를 엄격히 구분합니다.
        now_info = _get_now_info()
        ability_prompt = load_ability('news_summarizer')
        
        system_instr = f"{ability_prompt}\n{now_info}"
        user_content = (
            f"대상 기사 URL: {url}\n"
            f"기사 제목: {title_text}\n"
            f"기사 본문:\n{article_text[:5000]}" # 5천자 제한 (토건 보호)
        )
        
        # [V29.0] 공통지원팀 지원 요청
        summary = _execute_ai_call_with_retry(
            task_id="News_Summary",
            system_instr=system_instr,
            user_content=user_content
        )
        if summary:
            return summary
        else:
            return "❌ 현재 구글 AI 서버 부하로 인해 기사 분석이 지연되고 있습니다. 잠시 후 다시 시도해주세요."
    except Exception as e:
        logger.error(f"뉴스 요약 스크래핑/전처리 오류: {e}")
        return "❌ 기사 본문을 분석하는 중에 오류가 발생했습니다."

def _fallback_response():
    """[V12.7] 실시간 시도가 모두 실패했을 때 부장님께 드리는 전문적인 보고"""
    return {
        "status": "알림", "is_ai_error": True, "is_thread": False,
        "summary": "⚠️ [피아니 일시 지연] AI 서버 응답 지연으로 실시간 분석을 중단했습니다. 5분 뒤 배경에서 마지막 1회 추가 요약 시도를 진행하겠습니다."
    }

def route_intent(user_message: str) -> str:
    """
    [V13.0] AI 기반 초경량 의도 분석 라우터.
    사용자의 메시지를 읽고 3가지 카테고리 중 하나로 정확히 분류합니다.
    분류 기준: MAIL_WORK, REPORT_WORK, GENERAL_CHAT
    """
    if not GEMINI_API_KEY: return "GENERAL_CHAT"
    if not user_message: return "GENERAL_CHAT"
    

    prompt = (
        "너는 부장님의 지시 의도를 정확히 파악하는 초고속 의도 분석 라우터(Router)다.\n"
        "다음 사용자의 메시지를 읽고, 오직 아래 3가지 영문 카테고리 이름 중 하나만 결과로 출력하라. (설명, 인사말 등 다른 말은 절대 금지)\n\n"
        "[카테고리]\n"
        "1. MAIL_WORK : 이메일 요약, 메일 수신 확인, 스킵 이유 등 메일과 관련된 질문\n"
        "2. REPORT_WORK : 일일 보고서, 주간 보고서 생성 요청\n"
        "3. GENERAL_CHAT : 일상적인 인사, 안부, 궁금증, 잡담, 칭찬, 비서 자체와의 대화\n\n"
        f"대상 메시지: {user_message[:500]}\n"
        "정답 카테고리:"
    )

    # [V29.0] 공통지원팀 지원 요청
    result = _execute_ai_call_with_retry(
        task_id="Intent_Router",
        system_instr="(없음)", # 지침이 프롬프트 본문에 포함됨
        user_content=prompt,
        config=types.GenerateContentConfig(temperature=0.0)
    )

    if result:
        result = result.upper()
        logger.info(f"[Intent Router] AI 판단 원본 결과: {result}")
        if "MAIL" in result: return "MAIL_WORK"
        elif "REPORT" in result: return "REPORT_WORK"
    
    return "GENERAL_CHAT"

def chat_with_secretary(user_message: str, replied_text: str = None, include_history: bool = True, intent: str = "GENERAL_CHAT") -> str:
    """
    [V12.16] 초고성능 실시간 기억력 이식 (True Multi-turn API 적용)
    [V13.0] 프롬프트 기능 분할 수술 적용 (동적 어빌리티 로딩)
    """
    if not GEMINI_API_KEY: return "🚨 제 두뇌(API 키)가 연결되어 있지 않습니다."

    # [V21.5/V21.6 다이어트 및 정리] 필요한 프롬프트만 전략적으로 결합합니다.
    if intent == "REPORT_WORK":
        # 보고서 업무 지시: 정체성(Persona)마저 완전히 걷어내고 오직 트리거만 로드 (V21.5)
        chat_prompt = load_ability('report_trigger')
    else:
        # 그 외 모든 대화: 뼈대(Persona) + 대화 정체성(Chat Persona) 장착 (V21.6)
        chat_prompt = _read_prompt_file("peani_persona.txt")
        chat_prompt += f"\n\n{load_ability('chat_persona')}"
        
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

        # [V29.0] 공통지원팀 지원 요청 (최고 성능 대화 엔진 연결)
        ai_reply = _execute_ai_call_with_retry(
            task_id="Secretary_Chat",
            system_instr=chat_prompt,
            user_content=contents
        )
        
        if ai_reply:
            return ai_reply
        else:
            return "🚨 앗, 부장님! 방금 머릿속에 안개가 낀 것처럼 답변이 떠오르지 않습니다. 다시 한번 말씀해 주시겠어요?"

    except Exception as e:
        logger.error(f"지능형 대화 엔진 오류: {e}")
        return "🚨 앗, 부장님! 방금 머릿속에 기억들이 꼬여서 잠시 멍해졌습니다. 다시 말씀해 주시겠어요?"

def generate_daily_report_ai(raw_summaries: list) -> dict:
    """[V11.8] 일일 보고서 전용 지침(daily_strategy)을 사용하여 고객사별 요약을 생성합니다."""
    if not GEMINI_API_KEY or not raw_summaries: return {"report": "데이터 부족"}

    # [V11.8/V21.2 다이어트] 일일 보고서는 전용 지침(daily_strategy) 단독으로 수행 (페르소나 제외)
    dynamic_prompt = load_ability('daily_strategy')
    # [V12.17] 장부에 저장된 진짜 고객사명(client)을 제공하여 AI의 오판을 방지합니다.
    data_text = "\n".join([f"고객사: {i['client']} | 제목: {i['subject']} | 요약: {i['summary']}" for i in raw_summaries])

    # [V29.0] 공통지원팀 지원 요청
    result = _execute_ai_call_with_retry(
        task_id="Daily_Report",
        system_instr=dynamic_prompt,
        user_content=data_text,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
        is_json=True
    )
    return result or {"topics": [{"category": "오류", "items": ["보고서 생성 실패"]}]}



import json
import os
import threading
from config import USER_PREFERENCES_FILE, USER_CORRECTIONS_FILE, logger

# [V12.13] 인메모리 싱글톤 캐시: AI의 학습 내용을 메모리에 상주시킵니다.
_PREFERENCES_CACHE = None
_CORRECTIONS_CACHE = None
_PREF_LOCK = threading.Lock() # [QC] 학습 노트용 문잠금 장치
_CORR_LOCK = threading.Lock() # [QC] 오답 노트용 문잠금 장치

def load_preferences():
    """AI가 기피해야 할 [학습 노트]를 메모리에서 즉시 꺼내거나, 처음이면 파일에서 읽어옵니다."""
    global _PREFERENCES_CACHE
    
    with _PREF_LOCK:
        if _PREFERENCES_CACHE is not None:
            return _PREFERENCES_CACHE
            
        if not os.path.exists(USER_PREFERENCES_FILE):
            _PREFERENCES_CACHE = []
            return _PREFERENCES_CACHE
        
        try:
            with open(USER_PREFERENCES_FILE, "r", encoding="utf-8") as f:
                _PREFERENCES_CACHE = json.load(f)
                return _PREFERENCES_CACHE
        except Exception as e:
            logger.error(f"비서의 스킵 학습 노트를 읽는데 실패했습니다: {e}")
            return []

def save_preferences(pref_list):
    """학습한 내용을 메모리에 반영하고, 창고(SSD)에도 실시간 동기화합니다."""
    global _PREFERENCES_CACHE
    
    with _PREF_LOCK:
        _PREFERENCES_CACHE = pref_list
        try:
            with open(USER_PREFERENCES_FILE, "w", encoding="utf-8") as f:
                json.dump(pref_list, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logger.error(f"스킵 학습 노트를 디스크에 적는데 실패했습니다: {e}")

def add_learning_preference(subject, summary):
    """
    텔레그램에서 👎 싫어요(학습) 버튼을 눌렀을 때 호출됩니다.
    해당 메일의 제목과 요약 내용을 한 문장으로 예쁘게 묶어서 교과서에 추가합니다.
    """
    if not subject:
        subject = "제목 없음"
    if not summary:
        summary = "내용 없음"
        
    pattern = f"[제목]: {subject} / [내용 특징]: {summary}"
    
    # load_preferences() 내부에서 이미 Lock을 사용하므로 여기서는 별도 처리 불필요
    current_list = load_preferences()
    
    # 이미 학습한 내용이라면 중복 저장하지 않음
    if pattern in current_list:
        return False, "아하! 이 형식은 제가 예전에 이미 학습해서 암기하고 있는 패턴입니다."
        
    current_list.append(pattern)
    save_preferences(current_list)
    
    logger.info(f"🧠 [AI 학습 완료] 사용자가 기피하는 새로운 이메일 패턴을 장부에 박제했습니다: {subject}")
    return True, "패턴 머릿속에 입력 완료!"

# --- V3.2 오답 노트(Corrections) 관리 기능 ---

def load_corrections():
    """AI가 요약 시 명심해야 할 '오답 노트'를 메모리에서 즉시 읽어옵니다."""
    global _CORRECTIONS_CACHE
    
    with _CORR_LOCK:
        if _CORRECTIONS_CACHE is not None:
            return _CORRECTIONS_CACHE
            
        if not os.path.exists(USER_CORRECTIONS_FILE):
            _CORRECTIONS_CACHE = []
            return _CORRECTIONS_CACHE
        
        try:
            with open(USER_CORRECTIONS_FILE, "r", encoding="utf-8") as f:
                _CORRECTIONS_CACHE = json.load(f)
                return _CORRECTIONS_CACHE
        except Exception as e:
            logger.error(f"오답 노트를 읽어오는데 실패했습니다: {e}")
            return []

def save_corrections(corr_list):
    """새로운 오답 규칙을 메모리에 반영하고 SSD에 안전하게 저장합니다."""
    global _CORRECTIONS_CACHE
    
    with _CORR_LOCK:
        _CORRECTIONS_CACHE = corr_list
        try:
            with open(USER_CORRECTIONS_FILE, "w", encoding="utf-8") as f:
                json.dump(corr_list, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logger.error(f"오답 노트를 디스크에 적는데 실패했습니다: {e}")

def add_correction(rule_text, original_summary=None):
    """
    [V12.16] 상황 기반 오답 학습 체계 구축
    텔레그램 답장을 통해 AI가 스스로 추출한 [학습 규칙]과 당시의 [오답 원문]을 한 세트로 저장합니다.
    """
    import datetime
    import pytz
    from config import USER_TIMEZONE
    
    if not rule_text:
        return False, "학습할 내용이 없습니다."
        
    current_list = load_corrections()
    
    # [V12.16] AI 최적화 구조로 데이터 포장
    tz = pytz.timezone(USER_TIMEZONE)
    new_case = {
        "mistake": original_summary or "과거 데이터(상황 미기록)",
        "correction": "부장님의 지시사항 및 피드백 기반",
        "lesson": rule_text,
        "recorded_at": datetime.datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    }
    
    # 중복 체크 (규칙 내용 기준)
    for existing in current_list:
        if isinstance(existing, dict) and existing.get("lesson") == rule_text:
            return False, "이미 알고 있는 규칙입니다."
        elif isinstance(existing, str) and existing == rule_text:
            return False, "이미 알고 있는 고전 규칙입니다."
            
    current_list.append(new_case)
    save_corrections(current_list)
    
    logger.info(f"🧠 [지능형 오답 노트 저장] 상황 기반 교정 규칙이 장부에 등록되었습니다: {rule_text}")
    return True, "오답 노트 세트 등록 완료!"

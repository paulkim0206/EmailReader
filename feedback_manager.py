import json
import os
from config import USER_PREFERENCES_FILE, USER_CORRECTIONS_FILE, logger

def load_preferences():
    """AI가 기피해야 할 [학습 노트]를 하드디스크에서 꺼내어 리스트로 반환합니다."""
    if not os.path.exists(USER_PREFERENCES_FILE):
        return []
    
    try:
        with open(USER_PREFERENCES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"비서의 스킵 학습 노트를 읽는데 실패했습니다: {e}")
        return []

def save_preferences(pref_list):
    """(제한 없음!) 주인이 싫어하는 새로운 메일 패턴을 장부에 추가하여 저장합니다."""
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
    """AI가 요약 시 명심해야 할 '오답 노트'를 읽어옵니다."""
    if not os.path.exists(USER_CORRECTIONS_FILE):
        return []
    
    try:
        with open(USER_CORRECTIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"오답 노트를 읽어오는데 실패했습니다: {e}")
        return []

def save_corrections(corr_list):
    """사용자가 지적한 새로운 오답 규칙을 장부에 저장합니다."""
    try:
        with open(USER_CORRECTIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(corr_list, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"오답 노트를 디스크에 적는데 실패했습니다: {e}")

def add_correction(rule_text):
    """
    텔레그램 답장을 통해 AI가 스스로 추출한 [학습 규칙] 하나를 추가합니다.
    """
    if not rule_text:
        return False, "학습할 내용이 없습니다."
        
    current_list = load_corrections()
    
    if rule_text in current_list:
        return False, "이미 알고 있는 규칙입니다."
        
    current_list.append(rule_text)
    save_corrections(current_list)
    
    logger.info(f"🧠 [오답 노트 저장 완료] 새로운 교정 규칙이 장부에 등록되었습니다: {rule_text}")
    return True, "오답 노트 등록 완료!"

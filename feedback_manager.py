import json
import os
from config import USER_PREFERENCES_FILE, logger

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

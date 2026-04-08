import json
import os
import re
import shutil
from config import logger

def sanitize_text(text):
    """
    [V33.0 QC] 텍스트 내의 줄바꿈, 탭, 제어 문자 등을 공백으로 치환하여 
    JSON 형식이 파괴되는 것을 방지합니다.
    """
    if not text:
        return ""
    # 모든 종류의 공백 문자(줄바꿈 포함)를 단일 공백으로 치환
    clean_text = re.sub(r'\s+', ' ', str(text)).strip()
    return clean_text

def safe_json_dump(data, file_path, indent=4):
    """
    [V33.0 QC] 원자적 저장(Atomic Write) 기술을 사용하여 장부 오염을 원천 차단합니다.
    임시 파일에 먼저 기록한 후, 성공 시에만 기존 파일을 교체합니다.
    """
    temp_path = f"{file_path}.tmp"
    try:
        # 1. 임시 파일에 데이터 기록
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
        
        # 2. 파일 교체 (Atomic Replace)
        # os.replace는 덮어쓰기를 지원하며 원자성이 보장됩니다.
        os.replace(temp_path, file_path)
    except Exception as e:
        # 실패 시 임시 파일 삭제
        if os.path.exists(temp_path):
            try: os.remove(temp_path)
            except: pass
        logger.error(f"장부 안전 저장 실패 ({file_path}): {e}")
        raise e

import json
import os
import re
from config import BLACKLIST_FILE, logger

def load_blacklist():
    """차단된 발송자들의 장부를 로컬 창고에서 꺼내어 파이썬 세트로 읽어 들입니다."""
    if not os.path.exists(BLACKLIST_FILE):
        return set() # 장부가 없으면 아무도 차단되지 않은 깨끗한 상태
    
    try:
        with open(BLACKLIST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data)
    except Exception as e:
        logger.error(f"블랙리스트 장부를 읽는데 실패했습니다: {e}")
        return set()

def save_blacklist(blacklist_set):
    """추가된 녀석들을 포함하여 새 장부 세트를 디스크에 덮어씁니다."""
    try:
        with open(BLACKLIST_FILE, "w", encoding="utf-8") as f:
            json.dump(list(blacklist_set), f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"블랙리스트 장부를 디스크에 적는데 실패했습니다: {e}")

def extract_pure_email(sender_str):
    """
    이메일 헤더는 종종 "홍길동 <spammer@spam.com>" 이렇게 꼬리표가 붙어옵니다.
    여느 사람처럼 "< >" 껍데기 안에 있는 진짜 '스팸 이메일 주소'만 눈치껏 강제 적출해내는 가위질입니다.
    """
    if not sender_str:
        return ""
    match = re.search(r'<(.+?)>', sender_str)
    if match:
        return match.group(1).strip().lower()
    # 꺾쇠가 없는 "spammer@spam.com" 순수 텍스트 형태라면 그대로 반환
    return sender_str.strip().lower()

def add_to_blacklist(sender_str):
    """텔레그램 봇 버튼에서 호출할 때 사용! 순수 이메일만 추출해 블랙 장부에 도장을 찍습니다."""
    email_address = extract_pure_email(sender_str)
    if not email_address:
        return False, "이메일 주소를 뽑아낼 수 없습니다."
        
    current_list = load_blacklist()
    if email_address in current_list:
        return False, f"이미 블랙리스트에 등록된 놈입니다: {email_address}"
        
    current_list.add(email_address)
    save_blacklist(current_list)
    logger.info(f"🚫 [성공] 스팸 거머리 1명을 영구 차단했습니다: {email_address}")
    return True, email_address

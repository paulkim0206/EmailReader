import json
import os
import datetime
import pytz
import threading
from config import TOKEN_USAGE_FILE, USER_TIMEZONE, logger

# 동시에 여러 공정이 기록을 시도할 때 파일이 깨지지 않게 방어하는 잠금 장치입니다.
_TOKEN_LOCK = threading.Lock()

def log_token(task, prompt_tokens, candidate_tokens):
    """
    [V12.25] AI 사용 시 발생하는 입/출력 토큰을 부장님 전용 장부에 기록합니다.
    """
    try:
        # [V12.27] 부장님의 정석 지침: 프로젝트 표준(pytz + USER_TIMEZONE) 방식으로 복구
        # 이제 지역을 어디로 바꿔도 설정된 타임존에 맞춰 자동으로 기록됩니다.
        tz = pytz.timezone(USER_TIMEZONE)
        now = datetime.datetime.now(tz)
        
        # 장부에 기록될 한 줄의 데이터 구성
        entry = {
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
            "task": task,
            "input_tokens": prompt_tokens or 0,
            "output_tokens": candidate_tokens or 0,
            "total_tokens": (prompt_tokens or 0) + (candidate_tokens or 0)
        }

        with _TOKEN_LOCK:
            # 1. 기존 장부가 있는지 확인하고 읽어옵니다.
            data = []
            if os.path.exists(TOKEN_USAGE_FILE):
                try:
                    with open(TOKEN_USAGE_FILE, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    # 파일이 깨졌거나 비어있으면 새로 시작합니다.
                    data = []
            
            # 2. 새 기록을 추가합니다.
            data.append(entry)
            
            # 3. 장부를 다시 저장합니다. (가독성을 위해 예쁘게 들여쓰기)
            with open(TOKEN_USAGE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
        logger.info(f"🪙 토큰 기록 완료: {task} (In: {prompt_tokens}, Out: {candidate_tokens})")

    except Exception as e:
        logger.error(f"토큰 장부 기록 중 에러 발생: {e}")

def get_daily_token_usage(target_date=None):
    """오늘(또는 특정 날짜)의 총 토큰 사용량을 계산하여 반환합니다."""
    if not target_date:
        tz = pytz.timezone(USER_TIMEZONE)
        target_date = datetime.datetime.now(tz).strftime("%Y-%m-%d")
    
    total_in = 0
    total_out = 0
    count = 0
    
    if os.path.exists(TOKEN_USAGE_FILE):
        try:
            with open(TOKEN_USAGE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for entry in data:
                    if entry.get("date") == target_date:
                        total_in += entry.get("input_tokens", 0)
                        total_out += entry.get("output_tokens", 0)
                        count += 1
        except Exception:
            pass
            
    return {
        "date": target_date,
        "total_input": total_in,
        "total_output": total_out,
        "total_sum": total_in + total_out,
        "request_count": count
    }

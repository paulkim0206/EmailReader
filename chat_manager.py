import json
import os
import datetime
import threading
from config import CHAT_HISTORY_FILE, logger
from utils import safe_json_dump

# 처음 이 모듈이 불려올 때, 장기 기억 장부(JSON)가 없으면 즉시 신설합니다.
os.makedirs(os.path.dirname(CHAT_HISTORY_FILE), exist_ok=True)
if not os.path.exists(CHAT_HISTORY_FILE):
    with open(CHAT_HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump([], f)

# [V12.13] 인메모리 싱글톤 캐시: 부장님과의 소중한 대화를 메모리(책상 위)에 올려두어 응답 속도를 1,000배 높입니다.
_CHAT_LOGS_CACHE = None
_CHAT_LOCK = threading.RLock() # [QC] 대화 기록용 문잠금 장치

def _load_chat_logs():
    """
    [V12.13] 대화 기록을 메모리로 불러오는 내부 전용 헬퍼 함수입니다.
    """
    global _CHAT_LOGS_CACHE
    
    with _CHAT_LOCK:
        if _CHAT_LOGS_CACHE is not None:
            return _CHAT_LOGS_CACHE
            
        if os.path.exists(CHAT_HISTORY_FILE):
            try:
                with open(CHAT_HISTORY_FILE, 'r', encoding='utf-8') as f:
                    _CHAT_LOGS_CACHE = json.load(f)
                    return _CHAT_LOGS_CACHE
            except Exception as e:
                logger.error(f"대화 기록 파일 읽기 중 오류: {e}")
                
        _CHAT_LOGS_CACHE = []
        return _CHAT_LOGS_CACHE

def save_chat_log(role: str, content: str):
    """
    부장님의 질문과 피아니의 답변을 메모리에 즉시 반영하고, 창고(SSD)에도 실시간 동기화합니다.
    """
    global _CHAT_LOGS_CACHE
    
    with _CHAT_LOCK:
        try:
            # 1. 메모리(캐시) 주머니 준비
            logs = _load_chat_logs()
            
            # 2. 새로운 대화 한 마디 추가
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            new_entry = {
                "timestamp": now_str,
                "role": role,
                "content": content
            }
            logs.append(new_entry)
            
            # 3. 메모리와 파일(창고) 실시간 동기화
            safe_json_dump(logs, CHAT_HISTORY_FILE, indent=2)
                
        except Exception as e:
            logger.error(f"🚨 대화 기록부 동기화 중 오류 발생: {e}")

def get_recent_chat_context(limit: int = 20) -> str:
    """
    메모리(캐시)에서 가장 최근 대화 맥락을 순식간에 훑어서 가져옵니다.
    """
    try:
        # 1. 창고(SSD)가 아닌 책상 위(메모리)에서 바로 꺼냅니다.
        logs = _load_chat_logs()
        
        if not logs:
            return ""
            
        recent_logs = logs[-limit:]
        
        context_str = "\n[최근 대화 맥락 (당신이 방금 전 부장님과 나눈 이야기)]\n"
        for log in recent_logs:
            speaker = "기록된 나(피아니)" if log['role'] == 'assistant' else "부장님"
            context_str += f"- {log['timestamp']} ({speaker}): {log['content']}\n"
            
        return context_str
    except Exception as e:
        logger.error(f"🚨 대화 맥락 불러오기 실패: {e}")
        return ""
def get_recent_chat_history_raw(days: int = 14, max_entries: int = 100) -> list:
    """
    [V12.16] 최근 N일(기본 14일) 이내의 대화만 똑똑하게 골라냅니다.
    아무리 대화가 많아도 최대 100개까지만 가져와서 토큰을 보호합니다.
    """
    try:
        logs = _load_chat_logs()
        if not logs: return []
        
        # 1. 14일 전 기점 계산
        cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
        
        # 2. 날짜 필터링 (장기 아카이브에서 2주일치만 쑥!)
        recent_logs = []
        for log in reversed(logs):
            try:
                log_time = datetime.datetime.strptime(log['timestamp'], "%Y-%m-%d %H:%M:%S")
                if log_time >= cutoff:
                    recent_logs.insert(0, log) # 역순으로 찾아내어 다시 정순으로 조립
                else:
                    # 14일보다 오래된 데이터가 나오면 중단 (시간 효율)
                    break
            except Exception: continue
            
            # 3. 개수 안전장치 (부장님의 지갑 보호)
            if len(recent_logs) >= max_entries:
                break
                
        return recent_logs
    except Exception as e:
        logger.error(f"🚨 14일 기억력 필터링 중 오류: {e}")
        return []

def get_chat_status() -> dict:
    """
    [V13.1] 현재 대화 장부의 상태(건수, 파일 용량, 최근 기록 시각)를 반환합니다.
    """
    try:
        logs = _load_chat_logs()
        count = len(logs)
        size_bytes = os.path.getsize(CHAT_HISTORY_FILE) if os.path.exists(CHAT_HISTORY_FILE) else 0
        size_kb = round(size_bytes / 1024, 1)
        last_time = logs[-1]['timestamp'] if logs else "기록 없음"
        return {"count": count, "size_kb": size_kb, "last_time": last_time}
    except Exception as e:
        logger.error(f"대화 장부 상태 조회 중 오류: {e}")
        return {"count": 0, "size_kb": 0, "last_time": "오류"}

def clear_chat_history() -> bool:
    """
    [V13.1] 대화 장부(JSON)를 완전히 비우고, 메모리 캐시도 즉시 리로드합니다.
    """
    global _CHAT_LOGS_CACHE
    try:
        with _CHAT_LOCK:
            safe_json_dump([], CHAT_HISTORY_FILE)
            _CHAT_LOGS_CACHE = []  # 메모리 캐시도 즉시 비움
        logger.info("대화 장부 초기화 및 캐시 리로드 완료.")
        return True
    except Exception as e:
        logger.error(f"대화 장부 초기화 중 오류: {e}")
        return False

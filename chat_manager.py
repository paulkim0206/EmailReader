import json
import os
import datetime
from config import CHAT_HISTORY_FILE, logger

# 처음 이 모듈이 불려올 때, 장기 기억 장부(JSON)가 없으면 즉시 신설합니다.
os.makedirs(os.path.dirname(CHAT_HISTORY_FILE), exist_ok=True)
if not os.path.exists(CHAT_HISTORY_FILE):
    with open(CHAT_HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump([], f)

# [V12.13] 인메모리 싱글톤 캐시: 부장님과의 소중한 대화를 메모리(책상 위)에 올려두어 응답 속도를 1,000배 높입니다.
_CHAT_LOGS_CACHE = None

def _load_chat_logs():
    """
    [V12.13] 대화 기록을 메모리로 불러오는 내부 전용 헬퍼 함수입니다.
    """
    global _CHAT_LOGS_CACHE
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
        with open(CHAT_HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)
            
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

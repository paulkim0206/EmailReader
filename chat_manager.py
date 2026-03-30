import json
import os
import datetime
from config import CHAT_HISTORY_FILE, logger

# 처음 이 모듈이 불려올 때, 장기 기억 장부(JSON)가 없으면 즉시 신설합니다.
os.makedirs(os.path.dirname(CHAT_HISTORY_FILE), exist_ok=True)
if not os.path.exists(CHAT_HISTORY_FILE):
    with open(CHAT_HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump([], f)

def save_chat_log(role: str, content: str):
    """
    [V5.0 장기 기억 시스템]
    부장님의 질문(User)과 피아니의 답변(Assistant)을 타임스탬프와 함께 '영구적'으로 기록합니다.
    데이터가 무거워져도 일단 무제한으로 쌓아서 1년 치 기억의 토대를 만듭니다.
    """
    try:
        # 1. 기존 장부 읽어오기
        with open(CHAT_HISTORY_FILE, 'r', encoding='utf-8') as f:
            logs = json.load(f)
        
        # 2. 새로운 대화 한 마디 추가
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        new_entry = {
            "timestamp": now_str,
            "role": role, # 'user' 또는 'assistant'
            "content": content
        }
        logs.append(new_entry)
        
        # 3. 다시 안전하게 덮어쓰기
        with open(CHAT_HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)
            
    except Exception as e:
        logger.error(f"🚨 장기 기억 장부(chat_history) 기록 중 오류 발생: {e}")

def get_recent_chat_context(limit: int = 20) -> str:
    """
    [V5.0 핵심 브레인]
    피아니가 대답하기 직전, 최근 20~30마디 정도의 대화 흐름을 쓱 훑어보고 옵니다.
    이게 있어야 부장님이 아까 했던 말에 대해 "그건 어때?"라고 물어도 알아듣습니다.
    """
    try:
        if not os.path.exists(CHAT_HISTORY_FILE):
            return ""
            
        with open(CHAT_HISTORY_FILE, 'r', encoding='utf-8') as f:
            logs = json.load(f)
            
        if not logs:
            return ""
            
        # 가장 최근 메시지들만 추출 (limit 개수만큼)
        recent_logs = logs[-limit:]
        
        context_str = "\n[최근 대화 맥락 (당신이 방금 전 부장님과 나눈 이야기)]\n"
        for log in recent_logs:
            speaker = "기록된 나(피아니)" if log['role'] == 'assistant' else "부장님"
            context_str += f"- {log['timestamp']} ({speaker}): {log['content']}\n"
            
        return context_str
    except Exception as e:
        logger.error(f"🚨 대화 맥락 불러오기 실패: {e}")
        return ""

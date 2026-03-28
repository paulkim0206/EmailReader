import json
import os
import re
import datetime
from config import THREAD_CACHE_FILE, THREAD_MAX_SIZE, THREAD_TIMEOUT_DAYS, logger

def load_threads():
    if os.path.exists(THREAD_CACHE_FILE):
        try:
            with open(THREAD_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_threads(threads):
    try:
        # 한도 초과 시 오래된(Last Date) 순으로 잘라내는 자동 청소(LRU) 로직
        if len(threads) > THREAD_MAX_SIZE:
            sorted_keys = sorted(threads.keys(), key=lambda k: threads[k].get("last_date", ""), reverse=True)
            threads = {k: threads[k] for k in sorted_keys[:THREAD_MAX_SIZE]}
            
        with open(THREAD_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(threads, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"핑퐁 메모리 장부 저장 실패: {e}")

def get_base_subject(subject):
    """'Re:', 'Fwd:' 같은 이메일 꼬리표를 기계적으로 무시하고 순수 제모만 적출합니다."""
    if not subject:
        return ""
    cleaned = re.sub(r'(?i)^(re|fwd|fw|답장|전달)\s*:\s*', '', subject).strip()
    # 꼬리가 여러 개 달릴 수 있으니 두어 번 더 청소합니다 (예: Re: Fwd: Re: 제목)
    for _ in range(3):
        cleaned = re.sub(r'(?i)^(re|fwd|fw|답장|전달)\s*:\s*', '', cleaned).strip()
    return cleaned

def get_or_create_thread(subject):
    """제목을 바탕으로 장부에서 스레드 방을 찾아오고 핑퐁 카운트를 계산합니다."""
    threads = load_threads()
    base_subj = get_base_subject(subject)
    now_str = datetime.datetime.now().isoformat()
    
    if base_subj in threads:
        t_data = threads[base_subj]
        last_date = datetime.datetime.fromisoformat(t_data.get("last_date", now_str))
        
        # 90일 지났으면 타임아웃 리셋! (3개월 망각 조작)
        if (datetime.datetime.now() - last_date).days >= THREAD_TIMEOUT_DAYS:
            logger.info(f"오래된 대화방 부활! ({base_subj}) 카운트 리셋!")
            t_data["count"] = 1
            t_data["msg_id"] = None
        else:
            t_data["count"] = t_data.get("count", 0) + 1
            
        t_data["last_date"] = now_str
    else:
        # 완전히 새로운 대화 시작
        threads[base_subj] = {
            "count": 1,
            "msg_id": None,
            "last_date": now_str,
            "summary_history": []
        }
        t_data = threads[base_subj]
        
    save_threads(threads) # [치명적 버그 수정] 지금까지 장부에 적어만 놓고 디스크 저장을 누락했던 코드 추가!
    return base_subj, t_data

def update_thread_data(base_subj, msg_id=None, latest_summary=None):
    """텔레그램 말풍선 번호와, AI가 방금 만들어낸 요약본을 누적 장부에 확정 저장합니다."""
    threads = load_threads()
    
    # [치명적 버그 수정] 혹시라도 메모리에서 증발했다면 다시 빈 방을 무조건 만들어 줍니다.
    if base_subj not in threads:
        threads[base_subj] = {
            "count": 1,
            "msg_id": None,
            "last_date": datetime.datetime.now().isoformat(),
            "summary_history": []
        }
        
    if msg_id is not None:
        threads[base_subj]["msg_id"] = msg_id
    if latest_summary is not None:
        if "summary_history" not in threads[base_subj]:
            threads[base_subj]["summary_history"] = []
        threads[base_subj]["summary_history"].append(latest_summary)
        
    save_threads(threads)

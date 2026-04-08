import json
import os
import datetime
import threading
from config import THREAD_CACHE_FILE, THREAD_MAX_SIZE, logger

THREAD_HISTORY_LIMIT = 5      # 스레드당 최대 보관 요약 개수
THREAD_TIMEOUT_DAYS = 30      # 30일 이상 소식 없으면 방 삭제

# [V12.13] 인메모리 싱글톤 캐시: 메모리에 장부를 딱 한 권만 펼쳐두어 하드디스크 부하를 90% 줄입니다.
_THREADS_CACHE = None
_THREAD_LOCK = threading.RLock() # [QC] 연속 대화 장부용 문잠금 장치

def load_threads():
    global _THREADS_CACHE
    
    with _THREAD_LOCK:
        # 1. 이미 메모리(책상 위)에 펼쳐져 있다면 바로 반환합니다. (초고속!)
        if _THREADS_CACHE is not None:
            return _THREADS_CACHE

        # 2. 처음 실행되었거나 메모리가 비었다면 파일(창고)에서 가져옵니다.
        if os.path.exists(THREAD_CACHE_FILE):
            try:
                with open(THREAD_CACHE_FILE, "r", encoding="utf-8") as f:
                    _THREADS_CACHE = json.load(f)
                    return _THREADS_CACHE
            except Exception as e:
                logger.error(f"장부 파일 읽기 중 오류 발생: {e}")
        
        # 3. 파일도 없고 메모리도 처음이라면 빈 장부를 만듭니다.
        _THREADS_CACHE = {}
        return _THREADS_CACHE

def save_threads(threads):
    global _THREADS_CACHE
    
    with _THREAD_LOCK:
        try:
            # 0. 메모리(캐시) 업데이트
            _THREADS_CACHE = threads
            
            # 1. 전체 주제 방이 너무 많아지면 가장 오래된 방부터 정리합니다.
            if len(threads) > THREAD_MAX_SIZE:
                sorted_keys = sorted(
                    threads.keys(),
                    key=lambda k: threads[k].get("last_date", ""),
                    reverse=True
                )
                threads = {k: threads[k] for k in sorted_keys[:THREAD_MAX_SIZE]}
            from utils import safe_json_dump
            safe_json_dump(threads, THREAD_CACHE_FILE, indent=4)
        except Exception as e:
            logger.error(f"장부 저장 실패: {e}")


def get_next_thread_index(thread_key):
    pass # Deleted

def save_summary_entry(uid, subject, summary, msg_id=None, client_name=None):
    """
    [V15.0 Flat DB] 메일 고유 번호(uid)를 키로 삼아 1차원 평면(Flat) 구조로 요약본을 저장합니다.
    """
    if not uid:
        logger.warning("UID가 누락되어 장부에 저장하지 않습니다.")
        return
        
    threads = load_threads()
    now_str = datetime.datetime.now().isoformat()
    uid_str = str(uid)

    threads[uid_str] = {
        "subject": subject,        # 구 thread_key 호환
        "client_name": client_name or "알 수 없음",
        "date": now_str[:10],      # YYYY-MM-DD
        "summary": summary,
        "msg_id": msg_id,
        "for_report": False,       # 보고서 등록 토글
        "last_date": now_str       # 정렬/삭제용 타임스탬프
    }

    # 오래된 데이터 자동 청소 (최신 1000개만 유지)
    if len(threads) > THREAD_MAX_SIZE:
        sorted_keys = sorted(
            threads.keys(),
            key=lambda k: threads[k].get("last_date", ""),
            reverse=True
        )
        threads = {k: threads[k] for k in sorted_keys[:THREAD_MAX_SIZE]}

    save_threads(threads)
    logger.info(f"장부 저장 (Flat DB) 완료: UID={uid_str}")

def toggle_report_pin_by_uid(uid, status=True):
    """
    [V15.0 Flat DB] UID로 즉시 검색하여 핀셋(보고서 대상) 상태를 토글합니다.
    """
    threads = load_threads()
    uid_str = str(uid)
    if uid_str in threads:
        threads[uid_str]["for_report"] = status
        save_threads(threads)
        logger.info(f"보고서 마킹 완료: UID={uid_str} -> {status}")
        return True
    return False

def get_summaries_all_by_date(target_date: str) -> list:
    """
    [V15.0 Flat DB] 장부를 단일 루프로 훑어 특정 날짜의 핀이 꽂힌 요약본을 반환합니다.
    """
    threads = load_threads()
    results = []
    
    for uid_str, data in threads.items():
        if isinstance(data, dict):
            if data.get("date") == target_date and data.get("for_report", False):
                results.append({
                    "client": data.get("client_name", "알 수 없음"),
                    "subject": data.get("subject", "제목 없음"),
                    "summary": data.get("summary", "")
                })
    return results

def find_entry_by_uid(uid):
    """
    [V15.0] 이전 호환성 및 메모/학습 조회용으로 즉시 반환합니다.
    """
    if not uid: return None
    threads = load_threads()
    return threads.get(str(uid))

def get_thread_msg_id(subject):
    """더 이상 텔레그램 쓰레드 엮기를 사용하지 않으므로 None을 반환하여 기능을 무력화합니다."""
    return None


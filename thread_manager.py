import json
import os
import datetime
from config import THREAD_CACHE_FILE, THREAD_MAX_SIZE, logger

THREAD_HISTORY_LIMIT = 5      # 스레드당 최대 보관 요약 개수
THREAD_TIMEOUT_DAYS = 30      # 30일 이상 소식 없으면 방 삭제

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
        # 전체 주제 방이 너무 많아지면 가장 오래된 방부터 정리합니다.
        if len(threads) > THREAD_MAX_SIZE:
            sorted_keys = sorted(
                threads.keys(),
                key=lambda k: threads[k].get("last_date", ""),
                reverse=True
            )
            threads = {k: threads[k] for k in sorted_keys[:THREAD_MAX_SIZE]}
        with open(THREAD_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(threads, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"장부 저장 실패: {e}")

def format_threads_for_prompt():
    """
    제미나이에게 던질 장부를 준비합니다.
    - 30일 이상 소식 없는 오래된 방은 자동 삭제합니다.
    - 각 방의 요약 기록에 #인덱스 번호를 붙여서 텍스트로 포맷합니다.
    - 각 방에서는 최신 5개까지만 전달합니다.
    """
    threads = load_threads()
    now = datetime.datetime.now()
    cleaned = {}

    for key, data in threads.items():
        last_date_str = data.get("last_date", "")
        try:
            last_date = datetime.datetime.fromisoformat(last_date_str)
            if (now - last_date).days >= THREAD_TIMEOUT_DAYS:
                logger.info(f"30일 타임아웃: '{key}' 방을 장부에서 삭제합니다.")
                continue  # 30일 초과 방은 제외 (다음 저장 시 실제로 삭제됨)
        except Exception:
            pass
        cleaned[key] = data

    # 변경사항이 있으면 저장
    if len(cleaned) != len(threads):
        save_threads(cleaned)

    if not cleaned:
        return "없음"  # 장부가 텅 비었을 때

    lines = []
    for thread_key, data in cleaned.items():
        history = data.get("summary_history", [])
        # 최신 5개만 잘라서 포맷
        recent = history[-THREAD_HISTORY_LIMIT:]
        formatted_entries = []
        for i, entry in enumerate(recent):
            if isinstance(entry, dict):
                idx = entry.get("index", i + 1)
                date = entry.get("date", "날짜 미상")
                summary = entry.get("summary", "")
            else:
                # 기존 구형 문자열 포맷 호환 처리
                idx = i + 1
                date = "날짜 미상"
                summary = entry
            formatted_entries.append(f"  #{idx} [{date}]: {summary}")

        lines.append(f"[주제: {thread_key}]")
        lines.extend(formatted_entries)

    return "\n".join(lines)

def save_thread_entry(thread_key, thread_index, summary, msg_id=None):
    """
    제미나이가 판단한 결과(thread_key, thread_index, summary)를 장부에 저장합니다.
    파이썬은 아무 판단 없이 제미나이가 시키는 대로만 적습니다.
    """
    threads = load_threads()
    now_str = datetime.datetime.now().isoformat()

    new_entry = {
        "index": thread_index,
        "date": now_str[:10],  # YYYY-MM-DD
        "summary": summary,
        "for_report": False    # [V11.9] 기본은 미포함 상태이며, 부장님이 버튼을 누를 때 True로 바뀝니다.
    }

    if thread_key not in threads:
        threads[thread_key] = {
            "msg_id": None,
            "last_date": now_str,
            "summary_history": []
        }

    threads[thread_key]["last_date"] = now_str
    if msg_id is not None:
        threads[thread_key]["msg_id"] = msg_id

    threads[thread_key]["summary_history"].append(new_entry)

    # 스레드별 최신 5개 초과분 자동 삭제 (가비지 컬렉터)
    history = threads[thread_key]["summary_history"]
    if len(history) > THREAD_HISTORY_LIMIT:
        threads[thread_key]["summary_history"] = history[-THREAD_HISTORY_LIMIT:]
        logger.info(f"가비지 컬렉터: '{thread_key}' 방의 오래된 요약본을 정리했습니다.")

    save_threads(threads)
    logger.info(f"장부 저장 완료: '{thread_key}' #{thread_index}")

def mark_as_report_target(thread_key, thread_index, status=True):
    """
    [V11.9] 특정 메일 요약을 일일/주간 보고서 대상으로 마킹하거나 해제합니다.
    """
    threads = load_threads()
    if thread_key not in threads:
        return False
    
    found = False
    for entry in threads[thread_key].get("summary_history", []):
        if entry.get("index") == thread_index:
            entry["for_report"] = status
            found = True
            break
            
    if found:
        save_threads(threads)
        logger.info(f"보고서 마킹 완료: '{thread_key}' #{thread_index} -> {status}")
        return True
    return False

def get_thread_msg_id(thread_key):
    """텔레그램 핑퐁 말풍선 연결을 위해 저장된 메시지 ID를 가져옵니다."""
    threads = load_threads()
    if thread_key in threads:
        return threads[thread_key].get("msg_id")
    return None

def get_summaries_all_by_date(target_date: str) -> list:
    """
    [V9.0 리포트 전용] 장부(thread_memory.json)를 샅샅이 뒤져
    특정 날짜(YYYY-MM-DD)와 일치하는 모든 요약본을 수집하여 리스트로 반환합니다.
    """
    threads = load_threads()
    results = []
    
    for thread_key, data in threads.items():
        history = data.get("summary_history", [])
        for entry in history:
            if isinstance(entry, dict):
                # [V11.9] 특정 날짜와 일치 '하면서' 부장님이 보고서용으로 선정한(for_report=True) 것만 수집
                if entry.get("date") == target_date:
                    if entry.get("for_report", False): # 핀 버튼 누른 것만 필터링
                        results.append({
                            "subject": thread_key,
                            "summary": entry.get("summary", "")
                        })
            else:
                # 구형 데이터(단순 문자열)는 무시 (새로운 시스템 체제 전환 중)
                continue
                    
    return results


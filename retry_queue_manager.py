import json
import os
import datetime
from config import RETRY_QUEUE_FILE, RETRY_WAIT_MINUTES, logger


def load_retry_queue():
    if os.path.exists(RETRY_QUEUE_FILE):
        try:
            with open(RETRY_QUEUE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_retry_queue(queue):
    try:
        with open(RETRY_QUEUE_FILE, "w", encoding="utf-8") as f:
            json.dump(queue, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"재시도 대기열 저장 실패: {e}")


def add_to_retry_queue(mail_data: dict):
    """
    AI 6회 전부 실패했을 때 이메일을 재시도 대기열에 조용히 등록합니다.
    5분 후 재시도 예약 시각을 함께 기록합니다.
    """
    queue = load_retry_queue()
    uid = mail_data.get("uid", "")

    # 이미 대기열에 있으면 중복 등록 방지
    if any(item.get("uid") == uid for item in queue):
        return

    retry_after = (
        datetime.datetime.now() + datetime.timedelta(minutes=RETRY_WAIT_MINUTES)
    ).isoformat()

    queue.append({
        "uid": uid,
        "mail_data": mail_data,
        "retry_after": retry_after,
        "retry_count": 1  # [V11.2] 최초 실패 시 1회차로 등록
    })
    save_retry_queue(queue)
    logger.info(f"재시도 대기열 등록 완료: '{mail_data.get('subject', '')}' → {RETRY_WAIT_MINUTES}분 후 재시도 예정")


def get_pending_retries():
    """
    5분 대기 시간이 지난 항목만 꺼내서 반환합니다.
    """
    queue = load_retry_queue()
    now = datetime.datetime.now()
    pending = []
    for item in queue:
        try:
            retry_after = datetime.datetime.fromisoformat(item["retry_after"])
            if now >= retry_after:
                pending.append(item)
        except Exception:
            pass
    return pending


def update_retry_status(uid: str, delay_minutes: int):
    """
    [V11.2] 재시도 실패 시 회차를 1 늘리고, 다음 시도 시각을 설정합니다.
    """
    queue = load_retry_queue()
    for item in queue:
        if item.get("uid") == uid:
            item["retry_count"] = item.get("retry_count", 1) + 1
            item["retry_after"] = (
                datetime.datetime.now() + datetime.timedelta(minutes=delay_minutes)
            ).isoformat()
            break
    save_retry_queue(queue)
    logger.info(f"재시도 상태 업데이트: UID {uid} → {delay_minutes}분 뒤 {item['retry_count']}회차 시도 예정")


def remove_from_retry_queue(uid: str):
    """
    재시도가 완료되었을 때 (성공/실패 무관) 대기열에서 삭제합니다.
    """
    queue = load_retry_queue()
    queue = [item for item in queue if item.get("uid") != uid]
    save_retry_queue(queue)
    logger.info(f"재시도 대기열에서 삭제 완료: UID {uid}")

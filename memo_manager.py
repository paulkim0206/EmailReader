import json
import os
import datetime
import threading
from config import USER_NOTES_FILE, logger

os.makedirs(os.path.dirname(USER_NOTES_FILE), exist_ok=True)
if not os.path.exists(USER_NOTES_FILE):
    with open(USER_NOTES_FILE, 'w', encoding='utf-8') as f:
        json.dump([], f)

# [V12.13] 인메모리 싱글톤 캐시: 부장님의 수첩을 메모리에 상주시킵니다.
_NOTES_CACHE = None
_NOTES_LOCK = threading.RLock() # [QC] 수첩용 문잠금 장치

def _load_notes():
    """수첩 내용을 메모리에서 즉시 꺼내거나, 처음이면 파일에서 읽어옵니다."""
    global _NOTES_CACHE
    
    with _NOTES_LOCK:
        if _NOTES_CACHE is not None:
            return _NOTES_CACHE
            
        try:
            with open(USER_NOTES_FILE, 'r', encoding='utf-8') as f:
                _NOTES_CACHE = json.load(f)
                return _NOTES_CACHE
        except Exception as e:
            logger.error(f"수첩 파일을 읽는데 실패했습니다: {e}")
            _NOTES_CACHE = []
            return _NOTES_CACHE

def _save_notes(notes):
    """수첩의 변경사항을 메모리에 반영하고 SSD에 실시간 동기화합니다."""
    global _NOTES_CACHE
    
    with _NOTES_LOCK:
        _NOTES_CACHE = notes
        try:
            with open(USER_NOTES_FILE, 'w', encoding='utf-8') as f:
                json.dump(notes, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"수첩 파일을 저장하는데 실패했습니다: {e}")

def save_memo(content: str) -> bool:
    try:
        notes = _load_notes()
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # [V4.2] 고유 ID 자동 발급 장치 (가장 큰 번호 찾아서 +1, 영구 결번 유지)
        new_id = max([n.get('id', 0) for n in notes], default=0) + 1
        
        # 'status' 필드 추가: active(활성) / deleted(완료/삭제 처리됨)
        new_note = {"id": new_id, "timestamp": now_str, "content": content, "status": "active"}
        notes.append(new_note)
        _save_notes(notes)
        logger.info(f"✅ 메모 등록 성공 [ID:{new_id}]")
        return True
    except Exception as e:
        logger.error(f"🚨 메모 등록 실패: {e}")
        return False

def get_recent_memos(limit: int = 10) -> str:
    """[V4.2] 부장님 지시: 가장 최근 메모 10개를 역계산(역순)하여 불러옵니다."""
    try:
        notes = _load_notes()
        if not notes: return "(현재 수첩 메모가 완전히 텅 비어 있습니다.)"
            
        recent_notes = list(reversed(notes[-limit:]))
        result = ""
        for note in recent_notes:
            status_mark = ""
            if note.get('status') == 'deleted':
                status_mark = " (✅완료/삭제 처리됨)"
            result += f"- [번호:{note.get('id', '?')}] ({note['timestamp']}) {note['content']}{status_mark}\n"
        return result.strip()
    except Exception as e:
        return f"🚨 수첩 읽기 실패: {e}"

def delete_memo(memo_id: int) -> bool:
    """[V4.2 업데이트] 물리적 삭제 대신 '완료/삭제' 상태로 영구 보존(논리적 삭제)하여 고유번호(결번)를 지킵니다."""
    try:
        notes = _load_notes()
        deleted = False
        for n in notes:
            if n.get('id') == memo_id:
                n['status'] = 'deleted'
                n['timestamp'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S (완료처리)")
                deleted = True
                break
        
        if deleted:
            _save_notes(notes)
            return True
        return False
    except Exception:
        return False

def update_memo(memo_id: int, new_content: str) -> bool:
    """[V4.2] 특정 ID를 찾아내서 새로운 문구로 덮어씁니다."""
    try:
        notes = _load_notes()
        updated = False
        for n in notes:
            # 삭제/완료된 메모라도 부장님이 강제로 수정하면 다시 되살려줍니다(활성화)
            if n.get('id') == memo_id:
                n['content'] = new_content
                n['status'] = 'active'
                n['timestamp'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S (수정됨)")
                updated = True
                break
        if updated:
            _save_notes(notes)
            return True
        return False
    except Exception:
        return False

def get_active_memos_text() -> str:
    """[V8.0] 부장님이 아직 끝내지 않은(active) 메모만 전체를 텍스트로 뽑아냅니다. AI 브리핑용입니다."""
    try:
        notes = _load_notes()
        active_notes = [n for n in notes if n.get('status') == 'active']
        
        if not active_notes:
            return "(현재 완료되지 않은 메모가 하나도 없습니다. 수첩이 깨끗하네요! ✨)"
            
        result = "[부장님의 미완료 수첩 목록]\n"
        for note in active_notes:
            result += f"- {note.get('id', '?')}번: {note['content']} (등록: {note['timestamp']})\n"
        return result.strip()
    except Exception as e:
        return f"🚨 미완료 수첩 읽기 실패: {e}"

def get_all_memos() -> str:
    """`/notelist` 명령어 발동 시, 전체 장부를 텍스트로 뽑아냅니다."""
    notes = _load_notes()
    if not notes: return "수첩이 비어있습니다."
    
    lines = []
    for n in notes:
        prefix = "✅ [완료/취소 선 쫙-긋기] " if n.get('status') == 'deleted' else ""
        lines.append(f"[{n.get('id', '?')}번] {n['timestamp']}\n내용: {prefix}{n['content']}\n")
    return "\n".join(lines)

import json
import os
import datetime
from config import USER_NOTES_FILE, logger

os.makedirs(os.path.dirname(USER_NOTES_FILE), exist_ok=True)
if not os.path.exists(USER_NOTES_FILE):
    with open(USER_NOTES_FILE, 'w', encoding='utf-8') as f:
        json.dump([], f)

def _load_notes():
    with open(USER_NOTES_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def _save_notes(notes):
    with open(USER_NOTES_FILE, 'w', encoding='utf-8') as f:
        json.dump(notes, f, ensure_ascii=False, indent=2)

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

def get_all_memos() -> str:
    """`/수첩` 명령어 발동 시, 전체 장부를 텍스트로 뽑아냅니다."""
    notes = _load_notes()
    if not notes: return "수첩이 비어있습니다."
    
    lines = []
    for n in notes:
        prefix = "✅ [완료/취소 선 쫙-긋기] " if n.get('status') == 'deleted' else ""
        lines.append(f"[{n.get('id', '?')}번] {n['timestamp']}\n내용: {prefix}{n['content']}\n")
    return "\n".join(lines)

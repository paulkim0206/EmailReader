import json
import os
import datetime
import threading
from config import USER_NOTES_FILE, USER_NOTES_BACKUP_FILE, logger

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
            # [V12.29] 서버가 켜질 때마다(재시작 시) 자동으로 완료된 메모를 백업으로 일괄 이사시킵니다.
            _auto_archive_deleted_memos()
            
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

def _save_backup_note(note):
    """[V12.28] 완료된 메모의 원본 전체를 별도의 백업 파일에 안전하게 보관합니다."""
    try:
        backup_data = []
        if os.path.exists(USER_NOTES_BACKUP_FILE):
            try:
                with open(USER_NOTES_BACKUP_FILE, 'r', encoding='utf-8') as f:
                    backup_data = json.load(f)
            except Exception: pass
        
        # 중복 방지를 위해 이미 백업에 있는지 확인 (ID 기준)
        if any(b.get('id') == note.get('id') for b in backup_data):
            return
            
        backup_data.append(note)
        with open(USER_NOTES_BACKUP_FILE, 'w', encoding='utf-8') as f:
            json.dump(backup_data, f, ensure_ascii=False, indent=2)
        logger.info(f"📁 메모 백업 성공 [ID:{note.get('id')}]")
    except Exception as e:
        logger.error(f"🚨 메모 백업 중 오류: {e}")

def _auto_archive_deleted_memos():
    """
    [V12.29] 서버 재시작 시 실행되는 '일괄 강제 이사 청소기'입니다.
    status가 'deleted'인 메모들을 백업 파일로 옮기고 원본 데이터에서는 내용을 비웁니다.
    """
    try:
        if not os.path.exists(USER_NOTES_FILE): return
        
        with open(USER_NOTES_FILE, 'r', encoding='utf-8') as f:
            notes = json.load(f)
            
        is_changed = False
        for n in notes:
            # 낮 시간 동안 'deleted'로 표시된 항목들을 찾습니다.
            if n.get('status') == 'deleted':
                # 1. 백업 파일로 원본 내용 복사
                _save_backup_note(n)
                
                # 2. 원본에서는 내용을 비우고 'archived' 상태로 변경
                n['content'] = "--- [완료됨/백업됨] ---"
                n['status'] = 'archived'
                n['timestamp'] = n['timestamp'] + " (이사완료)"
                is_changed = True
        
        if is_changed:
            with open(USER_NOTES_FILE, 'w', encoding='utf-8') as f:
                json.dump(notes, f, ensure_ascii=False, indent=2)
            logger.info("🧹 새벽(재시작) 일괄 강제 이사 청소를 완료했습니다!")
            
    except Exception as e:
        logger.error(f"일괄 자동 이사 중 오류 발생: {e}")

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
    """[V12.29] 부장님 지시: 낮 시간에는 '내용 유지'하며 'deleted' 상태로만 표시합니다."""
    try:
        notes = _load_notes()
        deleted = False
        for n in notes:
            if n.get('id') == memo_id and n.get('status') == 'active':
                n['status'] = 'deleted'
                n['timestamp'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S (완료처리)")
                deleted = True
                break
        
        if deleted:
            _save_notes(notes)
            return True
        return False
    except Exception as e:
        logger.error(f"메모 완료 처리 중 오류: {e}")
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
    """`/notelist` 명령어 발동 시, 현재 장부를 텍스트로 뽑아냅니다. (이사 완료된 항목은 내용 제외)"""
    notes = _load_notes()
    if not notes: return "수첩이 비어있습니다."
    
    lines = []
    for n in notes:
        status = n.get('status')
        if status == 'archived':
            lines.append(f"[{n.get('id', '?')}번] (✅ 완료/백업됨)")
        elif status == 'deleted':
             lines.append(f"[{n.get('id', '?')}번] {n['timestamp']}\n내용: (✅ 임시 완료됨/이사대기) {n['content']}\n")
        else:
            lines.append(f"[{n.get('id', '?')}번] {n['timestamp']}\n내용: {n['content']}\n")
    return "\n".join(lines)

def get_backup_memos_text() -> str:
    """[V12.29] 백업 파일(user_notes_backup.json)에 격격된 전체 메모를 텍스트로 전환합니다."""
    try:
        if not os.path.exists(USER_NOTES_BACKUP_FILE):
             return "(백업 파일이 아직 생성되지 않았습니다.)"
             
        with open(USER_NOTES_BACKUP_FILE, 'r', encoding='utf-8') as f:
            backup_data = json.load(f)
            
        if not backup_data:
            return "(백업된 메모가 하나도 없습니다.)"
            
        lines = ["====== [부장님의 백업 수첩 원본 보관소] ======\n"]
        for n in backup_data:
            lines.append(f"[{n.get('id', '?')}번] {n['timestamp']}\n내용: {n['content']}\n")
            
        return "\n".join(lines)
    except Exception as e:
        return f"🚨 백업 수첩 읽기 실패: {e}"

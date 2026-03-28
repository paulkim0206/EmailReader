import os
from config import SAVE_DIRECTORY_PATH, logger

def create_and_save_report(mail_data, ai_result):
    """
    사용자가 텔레그램 방에서 버튼이나 '/save' 명령어를 사용했을 때 작동합니다.
    분석이 끝난 메일 내용을 글씨 깨짐이 없는 깔끔한 마크다운(.md) 파일로 보관해줍니다.
    """
    
    if not os.path.exists(SAVE_DIRECTORY_PATH):
        try:
            os.makedirs(SAVE_DIRECTORY_PATH)
            logger.info(f"문서를 보관할 튼튼한 금고(새 폴더)를 만들었습니다: {SAVE_DIRECTORY_PATH}")
        except Exception as e:
            logger.error(f"저장 폴더를 만드는 데 문제가 생겼습니다: {e}")
            return False, "폴더 생성 실패"

    raw_subject = mail_data.get('subject', '제목없음')
    safe_title = "".join(c for c in raw_subject if c.isalnum() or c in (' ', '_', '-')).strip()
    
    short_date = mail_data.get('date', '날짜미상')[:10].replace(" ", "").replace("-", "") 
    category = ai_result.get('thread_key', '미분류')
    
    file_name = f"{short_date}_{category}_{safe_title[:20]}.md"
    file_path = os.path.join(SAVE_DIRECTORY_PATH, file_name)

    try:
        content = f"# 📧 이메일 인공지능 분석 보고서: {raw_subject}\n\n"
        
        content += "## 1. 메일 기본 정보\n"
        content += f"- **보낸 사람:** {mail_data.get('sender', '알 수 없음')}\n"
        content += f"- **수신 일시:** {mail_data.get('date', '알 수 없음')}\n\n"
        
        content += "## 2. 전체 흐름 요약\n"
        content += f"{ai_result.get('summary', '요약 내용이 없습니다.')}\n\n"
        
        content += "## 3. 원본 이메일 내용\n"
        content += "*(아래는 이메일 전체 원본 텍스트입니다.)*\n\n"
        content += "---\n\n"
        content += f"{mail_data.get('body', '본문 내용 없음')}\n"
        
        # UTF-8 인코딩을 적용해 한글이 절대 깨지지 않게 씁니다.
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
            
        logger.info(f"마크다운(.md) 보고서가 완벽하고 깔끔하게 저장되었습니다: {file_path}")
        
        return True, file_path

    except Exception as e:
        logger.error(f"마크다운 보고서를 작성하다 오류가 발생했습니다: {e}")
        return False, f"문서 생성 에러: {e}"

import os
from config import SAVE_DIRECTORY_PATH, logger

def create_and_save_report(mail_data, ai_result):
    """
    사용자가 텔레그램 방에서 버튼이나 '/save' 명령어를 사용했을 때 작동합니다.
    분석이 끝난 메일 내용을 아주 깔끔하고 보기 편한 프리미엄 HTML(.html) 파일로 보관해줍니다.
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
    
    # 확장자를 .html로 변경합니다.
    file_name = f"{short_date}_{category}_{safe_title[:20]}.html"
    file_path = os.path.join(SAVE_DIRECTORY_PATH, file_name)

    try:
        # 1. 분리된 HTML 템플릿 파일을 읽어옵니다. (디자인 유지보수 용이성 확보)
        template_path = os.path.join(os.path.dirname(__file__), "prompts", "templates", "email_raw.html")
        
        # 만약 템플릿 파일이 없으면 최소한의 기본 포맷이라도 출력하도록 방어합니다.
        if os.path.exists(template_path):
            with open(template_path, "r", encoding="utf-8") as f:
                html_template = f.read()
        else:
            logger.warning("HTML 템플릿 파일을 찾을 수 없어 기본 모드로 전환합니다.")
            html_template = "<html><body><h1>{{SUBJECT}}</h1><hr><pre>{{BODY}}</pre></body></html>"

        # 2. 부장님이 주신 데이터로 템플릿의 빈칸(Placeholder)을 채웁니다.
        html_content = html_template.replace("{{SUBJECT}}", raw_subject)
        html_content = html_content.replace("{{SENDER}}", mail_data.get('sender', '알 수 없음'))
        html_content = html_content.replace("{{DATE}}", mail_data.get('date', '알 수 없음'))
        html_content = html_content.replace("{{BODY}}", mail_data.get('body', '본문 내용 없음'))

        # 3. UTF-8 인코딩을 적용해 한글이 깨지지 않게 저장합니다.
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(html_content)
            
        logger.info(f"아웃룩 클래식 스타일 원본 문서 저장 완료: {file_path}")
        return True, file_path

    except Exception as e:
        logger.error(f"HTML 보고서를 작성하다 오류가 발생했습니다: {e}")
        return False, f"문서 생성 에러: {e}"

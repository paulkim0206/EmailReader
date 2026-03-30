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
        # 프리미엄 HTML 템플릿을 작성합니다.
        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>이메일 분석 보고서: {raw_subject}</title>
    <style>
        body {{ font-family: 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif; line-height: 1.6; color: #333; max-width: 900px; margin: 40px auto; padding: 20px; background-color: #f8f9fa; }}
        .header {{ background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%); color: white; padding: 30px; border-radius: 12px 12px 0 0; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
        .section {{ background: white; padding: 30px; border-radius: 0 0 12px 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); margin-bottom: 20px; }}
        h1 {{ margin: 0; font-size: 24px; }}
        h2 {{ color: #2a5298; border-left: 5px solid #2a5298; padding-left: 15px; margin-top: 30px; font-size: 20px; }}
        .meta-info {{ background: #f1f3f5; padding: 15px; border-radius: 8px; margin-top: 15px; font-size: 14px; }}
        .summary {{ background: #e7f3ff; padding: 20px; border-radius: 8px; border-left: 5px solid #007bff; font-size: 16px; white-space: pre-wrap; }}
        .body-raw {{ background: #fdfdfd; padding: 20px; border: 1px solid #dee2e6; border-radius: 8px; white-space: pre-wrap; font-family: 'Consolas', monospace; color: #495057; font-size: 13px; }}
        .footer {{ font-size: 12px; color: #868e96; text-align: center; margin-top: 30px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>📧 이메일 분석 보고서</h1>
        <div class="meta-info">
            <strong>📌 제목:</strong> {raw_subject}<br>
            <strong>👤 발신자:</strong> {mail_data.get('sender', '알 수 없음')}<br>
            <strong>🕒 날짜:</strong> {mail_data.get('date', '알 수 없음')}
        </div>
    </div>
    
    <div class="section">
        <h2>📋 인공지능 요약 내용</h2>
        <div class="summary">{ai_result.get('summary', '요약 내용이 없습니다.').replace('\n', '<br>')}</div>
        
        <h2>📜 이메일 원본 텍스트</h2>
        <div class="body-raw">{mail_data.get('body', '본문 내용 없음')}</div>
    </div>
    
    <div class="footer">본 보고서는 지능형 이메일 비서(V2.5)에 의해 생성되었습니다.</div>
</body>
</html>
"""
        # UTF-8 인코딩을 적용해 한글이 절대 깨지지 않게 씁니다.
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(html_content)
            
        logger.info(f"프리미엄 HTML 보고서가 완벽하게 저장되었습니다: {file_path}")
        return True, file_path

    except Exception as e:
        logger.error(f"HTML 보고서를 작성하다 오류가 발생했습니다: {e}")
        return False, f"문서 생성 에러: {e}"

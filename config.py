import os
import logging
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

# 환경 변수 확인 및 할당
IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.gmail.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", 993))
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# 결과물 저장용 로컬 경로 (기본값 설정)
SAVE_DIRECTORY_PATH = os.getenv("SAVE_DIRECTORY_PATH", "C:/Email_Reports")

# 프로젝트 최상위 경로
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 전역 로거(Logger) 설정 함수
def setup_logger():
    # 로거 인스턴스 생성
    logger = logging.getLogger("EmailAssistantLogger")
    logger.setLevel(logging.INFO)

    # 핸들러 중복 추가 방지
    if not logger.handlers:
        # 1. 콘솔 출력 시스템
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)

        # 2. 파일 출력 시스템 (app.log)
        log_file_path = os.path.join(BASE_DIR, "app.log")
        file_handler = logging.FileHandler(log_file_path, encoding='utf-8')
        file_handler.setLevel(logging.INFO)

        # 3. 로거 포맷 지정 (시간 - 로거명 - 로그레벨 - 메시지)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        console_handler.setFormatter(formatter)
        file_handler.setFormatter(formatter)

        logger.addHandler(console_handler)
        logger.addHandler(file_handler)

    return logger

# 프로젝트 전체에서 재사용할 logger 객체
logger = setup_logger()

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

# [V11.2] 부장님의 지침에 따른 AI 엔진 서열 정리 (3.0 주력 / 2.5 예비)
PRIMARY_MODEL = "gemini-3-flash-preview"
BACKUP_MODEL = "gemini-2.5-flash"

# 프로젝트 최상위 경로
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# [V7.0] 사용자 맞춤형 타임존 설정 및 영구 저장 파일 경로
DATA_DIR = os.path.join(BASE_DIR, "data")
LOGS_DIR = os.path.join(BASE_DIR, "logs")

# 폴더 자동 생성 (없으면 만들기)
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)

TIMEZONE_FILE = os.path.join(DATA_DIR, "timezone.json")

def get_current_timezone():
    # 1. 부장님이 명령어로 바꾼 설정 파일이 있는지 먼저 확인 (최우선)
    if os.path.exists(TIMEZONE_FILE):
        try:
            import json
            with open(TIMEZONE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("timezone", "Asia/Ho_Chi_Minh")
        except Exception:
            pass
    # 2. 파일이 없으면 .env 설정 또는 베트남(호치민) 기본값 사용
    return os.getenv("USER_TIMEZONE", "Asia/Ho_Chi_Minh")

USER_TIMEZONE = get_current_timezone()

# 결과물 저장용 로컬 경로 (루트 폴더 내 Email_Reports)
SAVE_DIRECTORY_PATH = os.getenv("SAVE_DIRECTORY_PATH", os.path.join(BASE_DIR, "Email_Reports"))

# 텔레그램 메일 핑퐁(스레드) 추적 장부 설정
THREAD_CACHE_FILE = os.path.join(DATA_DIR, "thread_memory.json")
THREAD_MAX_SIZE = 2000     # 최대 스레드 기억 용량 (방 개수 기준)
THREAD_TIMEOUT_DAYS = 30   # 30일 이상 소식 없는 방은 자동 삭제

# [V4.2] 공용 수첩(JSON) 및 대화 히스토리 파일 경로
USER_NOTES_FILE = os.path.join(DATA_DIR, "user_notes.json")
CHAT_HISTORY_FILE = os.path.join(DATA_DIR, "chat_history.json")

# AI 서버 장애 시 재시도 대기열 파일 경로 및 대기 시간 설정
RETRY_QUEUE_FILE = os.path.join(DATA_DIR, "retry_queue.json")
RETRY_WAIT_MINUTES = 5  # AI 6회 실패 후 재시도까지 기다리는 시간 (분)

# 메일 고유 번호 저장용 (중복 방지)
PROCESSED_UIDS_FILE = os.path.join(DATA_DIR, "processed_uids.json")

# AI 제미나이 맞춤형 진화용 '기피 메일 학습 노트' 경로 세팅
USER_PREFERENCES_FILE = os.path.join(DATA_DIR, "user_preferences.json")

# [V3.2] AI 요약 교정용 '오답 노트' 오프라인 기록용 경로 세팅
USER_CORRECTIONS_FILE = os.path.join(DATA_DIR, "user_corrections.json")

# [V3.3] 피아니 페르소나 및 외부 프롬프트 텍스트가 담긴 폴더 경로 세팅
PROMPTS_DIR = os.path.join(BASE_DIR, "prompts")

# [V9.0] 주 단위 통합 비즈니스 리포트 저장 폴더 세팅
REPORTS_DIR = os.path.join(DATA_DIR, "reports")

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

        # 2. 파일 출력 시스템 (logs/app.log)
        log_file_path = os.path.join(LOGS_DIR, "app.log")
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

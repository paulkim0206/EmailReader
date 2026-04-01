import imaplib
import email
from email.header import decode_header
from email.utils import parsedate_to_datetime
import datetime
import json
import os
import re
from bs4 import BeautifulSoup
from config import IMAP_SERVER, IMAP_PORT, EMAIL_ADDRESS, EMAIL_PASSWORD, logger, BASE_DIR, PROCESSED_UIDS_FILE

# 이미 처리된 메일 번호들을 안전하게 저장해둘 메모장(파일)의 경로입니다.
UID_FILE = PROCESSED_UIDS_FILE

def load_processed_uids():
    """이미 처리된 이메일의 고유 번호 목록을 불러오는 함수입니다."""
    if os.path.exists(UID_FILE):
        try:
            with open(UID_FILE, "r", encoding="utf-8") as f:
                # 파일에 저장된 리스트를 가져와서 중복을 알아서 지워주도록 묶음(set)으로 만듭니다.
                return set(json.load(f))
        except Exception as e:
            logger.error(f"메일 고유 번호 파일 읽기 오류: {e}")
            return set()
    return set()

def save_processed_uid(uid):
    """새롭게 성공적으로 처리한 이메일 고유 번호를 안전하게 저장하는 엑셀 다이어리 같은 역할입니다."""
    uids = load_processed_uids()
    uids.add(uid)
    try:
        with open(UID_FILE, "w", encoding="utf-8") as f:
            json.dump(list(uids), f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"메일 고유 번호 저장 오류: {e}")

def get_text_from_email(msg):
    """
    이메일 본문에서 컴퓨터가 넣은 겉모양(디자인)을 모두 벗겨내고
    오직 '순수한 글자'만 깔끔하게 뽑아내는 아주 중요한 함수입니다.
    """
    text_content = ""
    html_content = ""

    # 메일이 텍스트, عکس, 첨부파일 등으로 쪼개진 혼합형인지 확인합니다.
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))

            # 첨부파일 등은 무시하고 본문 메시지만 찾습니다.
            if "attachment" not in content_disposition:
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        # 한글이 외계어로 깨지지 않게 예쁘게 풀어주는 번역 과정을 거칩니다.
                        decoded_text = decode_payload(payload, part.get_content_charset())
                        if content_type == "text/plain": # 순수한 글자 형태
                            text_content += decoded_text
                        elif content_type == "text/html": # 웹페이지 구조(HTML) 형태
                            html_content += decoded_text
                except Exception as e:
                    logger.error(f"메일 본문 해석 중 작은 문제 발생: {e}")
    else:
        # 이메일이 하나의 덩어리로 왔을 때의 처리입니다.
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                decoded_text = decode_payload(payload, msg.get_content_charset())
                if msg.get_content_type() == "text/plain":
                    text_content = decoded_text
                elif msg.get_content_type() == "text/html":
                    html_content = decoded_text
        except Exception as e:
            logger.error(f"메일 본문 해석 중 작은 문제 발생: {e}")

    # [V12.4] 수술 결과: 표준적인 multipart 구조가 깨졌거나 본문을 못 찾았을 때를 대비한 '강제 해독' 로직 보강
    if not text_content.strip() and not html_content.strip():
        try:
            # 이메일 라이브러리가 본문을 못 찾았을 때, 전체 데이터 덩어리를 하나로 보고 다시 시도합니다.
            payload = msg.get_payload(decode=True)
            if payload:
                text_content = decode_payload(payload, msg.get_content_charset())
        except Exception: pass

    # 일반 텍스트가 있으면 그것을 최우선으로 사용합니다.
    if text_content.strip():
        return text_content.strip()
    
    # 일반 텍스트가 없고 HTML 글씨만 있다면, 불필요한 태그를 청소하여 글자만 추출합니다.
    elif html_content.strip():
        # BeautifulSoup 라이브러리 미설치 환경 대비 (순수 정규식으로 HTML 태그 싹둑 제거)
        clean_text = re.sub(r'<[^>]+>', ' ', html_content)
        # 과도한 공백 및 이스케이프 문자 정리
        clean_text = re.sub(r'\s+', ' ', clean_text).replace('&nbsp;', ' ')
        return clean_text.strip()
    
    return "본문 추출 불가 메일"

def decode_payload(payload, charset):
    """
    [V12.4] 중국(gb18030, gbk) 및 아시아권(big5, euc-jp) 특수 인코딩 지원 화끈하게 확장!
    한국어(utf-8, cp949 등)가 깨지지 않게 단단히 막아주는 보안관 역할입니다.
    """
    charsets_to_try = [charset, 'utf-8', 'gb18030', 'gbk', 'euc-kr', 'cp949', 'big5', 'euc-jp', 'iso-8859-1']
    for cs in charsets_to_try:
        if cs:
            try:
                # 이 언어 방식이 맞는지 하나하나 시도해 봅니다.
                return payload.decode(cs)
            except (UnicodeDecodeError, LookupError):
                continue
    # 모든 방식이 실패하면 억지로라도 표준 글자로 바꿉니다 (글자가 조금 깨질지언정 에러로 컴퓨터가 멈추지 않게 보호합니다).
    return payload.decode('utf-8', errors='replace')

def decode_email_header(raw_header):
    """이메일 제목이나 보낸 사람이 특수 문자로 꼬여있을 때, 이를 사람이 읽을 수 있게 풀어주는 역할을 합니다."""
    if not raw_header:
        return ""
    decoded_parts = decode_header(raw_header)
    result = ""
    for decoded_string, charset in decoded_parts:
        if isinstance(decoded_string, bytes):
            try:
                if charset:
                    result += decoded_string.decode(charset)
                else:
                    result += decoded_string.decode("utf-8")
            except Exception:
                result += decoded_string.decode("utf-8", errors="replace")
        else:
            result += decoded_string
    return result

def format_to_vietnam_time(raw_date_str):
    """
    뒤죽박죽인 전 세계 이메일 발송 시간(+0000, +0900 등)을 
    사용자님이 계신 '베트남 표준시(GMT+7)'로 깔끔하게 통일시켜 줍니다!
    """
    if not raw_date_str:
        return "시간 정보 없음"
    try:
        # 이메일 표준 시간(텍스트)을 진짜 파이썬 시계 객체로 변환합니다.
        dt = parsedate_to_datetime(raw_date_str)
        # 베트남 시차(UTC+7)를 설정합니다.
        vn_tz = datetime.timezone(datetime.timedelta(hours=7))
        vn_dt = dt.astimezone(vn_tz)
        # 예쁘게 출력: "2026-03-28 11:46 (베트남 시간)"
        return vn_dt.strftime("%Y-%m-%d %H:%M (베트남 시간)")
    except Exception as e:
        logger.error(f"시간 포맷 변환 실패, 원본 유지. 오류: {e}")
        return decode_email_header(raw_date_str)



def fetch_unseen_emails():
    """
    메일 서버에 '암호화된 안전한 통로'로 접속하여 '읽지 않은 메일'만 조심스럽게 가져오는 메인 함수입니다.
    이때 서버의 메일 상태를 멋대로 바꾸지 않도록 극도로 주의를 기울입니다. (보안 1원칙)
    """
    logger.info("메일 서버 안전 접속 시도 (해킹 방지를 위한 SSL 암호화)...")
    try:
        # 이메일 서버와 안전하게 대화할 수 있는 전용 통신망(SSL)을 엽니다.
        # [V1.12.1] 봇이 영원히 멈추는(프리징) 현상 방지를 위해 15초 타임아웃을 걸어둡니다.
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT, timeout=15)
        mail.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        mail.select("inbox")
        logger.info("메일 서버 안전하게 접속 성공 완료!")

        # 'UNSEEN' 즉, 우리가 평소 안 읽은 상태로 둔 이메일 상자만 정당하게 검색하되,
        # 회사 그룹웨어 서버 특성에 맞춰 고유 번호(UID) 형식으로 바로 다이렉트 검색합니다.
        status, response = mail.uid('SEARCH', 'UNSEEN')
        if status != "OK":
            logger.error("메일 검색에 실패하였습니다. 서버가 바쁠 수 있습니다.")
            return []

        uids = response[0].split()
        processed_uids = load_processed_uids() # 컴퓨터가 두 번 일하는 걸 방지하기 위해 그동안 작업한 목록을 꺼냅니다.
        fetched_emails = []

        for uid_bytes in uids:
            # 안전하게 뽑아낸 메일 고유 번호입니다.
            uid = uid_bytes.decode('utf-8')

            # 어제나 아까 이미 요약해 드린 메일이라면 스킵합니다. (중복 방어)
            if uid in processed_uids:
                continue

            # 제일 중요한 부분입니다: (BODY.PEEK[]) 옵션을 써서 컴퓨터가 마음대로 
            # 메일을 "읽음!" 으로 처리해버리는 대형 사고를 원천 차단합니다. 
            # 그룹웨어 특성에 맞게 uid 명령어를 사용하여 서버와 직접 대화합니다.
            status, msg_data = mail.uid('FETCH', uid, "(BODY.PEEK[])")
            if status != "OK" or not msg_data or not msg_data[0] or not isinstance(msg_data[0], tuple):
                continue

            raw_email = msg_data[0][1]
            if not isinstance(raw_email, bytes):
                continue
            msg = email.message_from_bytes(raw_email)

            subject = decode_email_header(msg.get("Subject"))
            sender = decode_email_header(msg.get("From"))
            date = format_to_vietnam_time(msg.get("Date")) # 베트남 시간으로 강제 변환!
            body = get_text_from_email(msg)

            # 분석을 위해 예쁘게 한 바구니에 담아 놓습니다. (가위질 제거! 100% 원문만 보냅니다)
            fetched_emails.append({
                "uid": uid,
                "subject": subject,
                "sender": sender,
                "date": date,
                "body": body
            })
            
            logger.info(f"새로운 메일을 안전하게 읽어왔습니다: {subject}")

        return fetched_emails

    except Exception as e:
        logger.error(f"메일 수신 중 돌발 상황이 발생했습니다: {e}")
        return []
def fetch_raw_eml(uid):
    """
    [V12.8] 부장님의 리소스 절약 지침: 최종 실패 시에만 서버에서 원본 데이터를 가져옵니다.
    """
    logger.info(f"긴급 원본 패치 시작 (UID: {uid})...")
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT, timeout=15)
        mail.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        mail.select("inbox")
        
        # PEEK 옵션을 써서 서버의 메일 상태(읽음 표시)를 건드리지 않고 원본 데이터만 가져옵니다.
        status, msg_data = mail.uid('FETCH', uid, "(BODY.PEEK[])")
        mail.logout()
        
        if status == "OK" and msg_data and msg_data[0]:
            raw_email = msg_data[0][1]
            if isinstance(raw_email, bytes):
                return raw_email
        return None
    except Exception as e:
        logger.error(f"원본 메일 패치 중 오류 발생: {e}")
        return None

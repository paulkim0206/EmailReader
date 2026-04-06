import imaplib
import email
import threading
from email.header import decode_header
from email.utils import parsedate_to_datetime
import datetime
import json
import os
import re
from bs4 import BeautifulSoup
from config import IMAP_SERVER, IMAP_PORT, EMAIL_ADDRESS, EMAIL_PASSWORD, logger, BASE_DIR, PROCESSED_UIDS_FILE, UID_FILE_JSON, USER_TIMEZONE
import pytz

# 이미 처리된 메일 번호들을 안전하게 저장해둘 메모장(파일)의 경로입니다.
UID_FILE = PROCESSED_UIDS_FILE

# [V12.13] 중복 체크 초고속화: 수만 개의 메일 번호도 0.0001초 만에 찾아내는 인메모리 세트 주머니입니다.
_PROCESSED_UIDS_CACHE = None
_UID_LOCK = threading.RLock() # [V12.16] 재진입 가능 도어락(RLock)으로 교체하여 데드락(끼임 현상) 해결

def _migrate_from_json():
    """
    [일회성 임시 이사반] 기존 JSON 장부를 새 TXT 장부로 안전하게 옮깁니다.
    부장님의 소중한 데이터 유실을 0%로 만들기 위한 자동화 로직입니다.
    """
    if os.path.exists(PROCESSED_UIDS_FILE):
        return # 이미 이사 완료됨
        
    if os.path.exists(UID_FILE_JSON):
        try:
            logger.info("🚚 [이사팀 출동] 기존 JSON 장부를 발견했습니다. 새 장부(.txt)로 이사를 시작합니다...")
            with open(UID_FILE_JSON, "r", encoding="utf-8") as f:
                old_data = json.load(f) # 기존 데이터를 리스트로 읽어옴
            
            # 새 장부(.txt) 개설 및 데이터 복사
            with open(PROCESSED_UIDS_FILE, "w", encoding="utf-8") as f:
                for uid in old_data:
                    f.write(f"{uid}\n") # 한 줄에 하나씩 기록
            
            # 예전 장부 보관 (삭제하지 않고 .bak으로 개명)
            os.rename(UID_FILE_JSON, UID_FILE_JSON + ".bak")
            logger.info(f"✅ [이사 완료] {len(old_data)}개의 UID를 무사히 옮겼습니다. 예전 파일은 .bak로 보관했습니다.")
        except Exception as e:
            logger.error(f"🚨 [이사 실패] 이사 도중 문제가 생겼습니다: {e}")

def load_processed_uids():
    global _PROCESSED_UIDS_CACHE
    
    with _UID_LOCK: # [순서 정하기] 문 잠그고 입장
        # 1. 이미 내 머릿속(메모리)에 번호들이 다 있다면 바로 반환합니다.
        if _PROCESSED_UIDS_CACHE is not None:
            return _PROCESSED_UIDS_CACHE

        # 2. [V12.16] 자동 이사 서비스 호출 (필요한 경우에만 1회 실행)
        _migrate_from_json()

        # 3. 처음 실행되었다면 파일(금고)에서 번호 목록을 꺼내옵니다.
        if os.path.exists(PROCESSED_UIDS_FILE):
            try:
                with open(PROCESSED_UIDS_FILE, "r", encoding="utf-8") as f:
                    # [V12.16] 한 줄에 하나씩 적힌 텍스트를 읽어 세트(set) 주머니에 담습니다.
                    _PROCESSED_UIDS_CACHE = set(f.read().splitlines())
                    return _PROCESSED_UIDS_CACHE
            except Exception as e:
                logger.error(f"메일 고유 번호 파일 읽기 오류: {e}")
        
        # 4. 금고가 비었다면 빈 주머니를 만듭니다.
        _PROCESSED_UIDS_CACHE = set()
        return _PROCESSED_UIDS_CACHE

def save_processed_uid(uid):
    global _PROCESSED_UIDS_CACHE
    
    with _UID_LOCK: # [순서 정하기] 문 잠그고 입장
        # 0. 메모리 주머니 업데이트
        uids = load_processed_uids()
        uid_str = str(uid)
        
        if uid_str in uids:
            return # 이미 있다면 중복 기록 방지

        uids.add(uid_str)
        
        try:
            # [V12.16] 혁신: 파일 전체를 다시 쓰지 않고, 맨 뒤에 '한 줄만 추가(Append)'합니다.
            with open(PROCESSED_UIDS_FILE, "a", encoding="utf-8") as f:
                f.write(f"{uid_str}\n")
            logger.info(f"메일 번호 고속 기록 완료 (UID: {uid_str})")
        except Exception as e:
            logger.error(f"메일 고유 번호 저장 오류: {e}")

def _repair_base64_payload(raw_txt):
    """
    [V12.24] 자가 치유 엔진의 핵심: 
    본문에 숨겨진 Base64 블록을 찾아 강제로 해독하고, 말이 되는 내용이라면 원본을 대체합니다.
    """
    import base64
    # 40자 이상의 공백 없는 영문/숫자/기호 블록을 찾습니다.
    b64_pattern = re.compile(r'[A-Za-z0-9+/=]{40,}')
    
    def decode_and_verify(match):
        blob = match.group(0)
        try:
            # 1. 암호 덩어리 사이의 미세한 공백/줄바꿈 제거
            clean_blob = re.sub(r'\s+', '', blob)
            # 2. 강제 해독 시도
            decoded = base64.b64decode(clean_blob).decode('utf-8', errors='ignore')
            
            # 3. 해독된 내용이 전형적인 메일 단어를 포함하는지 '지능형 검증'
            meaningful_keywords = ["dear", "hi", "good", "hope", "shipping", "invoice", "date", "regard", "thanks"]
            if len(decoded) > 10 and any(word in decoded.lower() for word in meaningful_keywords):
                return f"\n[해독 성공]\n{decoded}\n[/해독 완료]\n"
        except Exception:
            pass
        return blob # 해독 실패 시 원본 유지
        
    return b64_pattern.sub(decode_and_verify, raw_txt)

def get_text_from_email(msg):
    """
    이메일 본문에서 컴퓨터가 넣은 겉모양(디자인)을 모두 벗겨내고
    오직 '순수한 글자'만 깔끔하게 뽑아내는 아주 중요한 함수입니다.
    [V12.22] 껍데기 필터링 강화: "HTML로 보세요" 같은 안내문에 속지 않도록 개선되었습니다.
    """
    text_content = ""
    html_content = ""

    # 메일이 텍스트, 그림, 첨부파일 등으로 쪼개진 혼합형인지 확인합니다.
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))

            # 첨부파일 등은 무시하고 본문 메시지만 찾습니다.
            if "attachment" not in content_disposition:
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        decoded_text = decode_payload(payload, part.get_content_charset())
                        if content_type == "text/plain": # 순수한 글자 형태
                            text_content += decoded_text
                        elif content_type == "text/html": # 웹페이지 구조(HTML) 형태
                            html_content += decoded_text
                except Exception as e:
                    logger.error(f"메일 본문 해석 중 작은 문제 발생: {e}")

    # [V12.24] 고도화된 자가 치유 (심폐소생술 로직)
    # 1. 메일 파서가 내용을 하나도 못 찾았거나(형식 파괴), 본문에 외계어(Content-Type 등)가 섞여 있을 때 작동합니다.
    if not text_content.strip() and not html_content.strip():
        logger.warning("🚑 [긴급] 메일 형식이 손상되어 수동 파싱(Scraping)을 시도합니다.")
        raw_payload = msg.get_payload()
        if isinstance(raw_payload, list):
            # 조각조각 난 경우 다 합칩니다.
            raw_text = "\n".join([str(p) for p in raw_payload])
        else:
            raw_text = str(raw_payload)
        
        # 외계어(Base64 블록)를 찾아 강제로 해독 시도
        text_content = _repair_base64_payload(raw_text)
        
    # 만약 본문은 읽었지만 여전히 암호 덩어리(Base64)가 섞여 있다면 한 번 더 걸러줍니다.
    if "RGVhci" in text_content or len(re.findall(r'[A-Za-z0-9+/=]{100,}', text_content)) > 0:
        text_content = _repair_base64_payload(text_content)

    # [QC] 알맹이가 없는 '유령 안내문'인지 체크하는 내부 도구입니다.
    def is_just_placeholder(txt):
        if not txt: return True
        txt = txt.strip().lower()
        # 안내문의 전형적인 키워드들 (한글, 영어, 중국어 포함)
        placeholders = [
            "html 형식", "html viewer", "multi-part message", "mime format",
            "html-compatible", "browser", "화면이 정상적으로 보이지", "본 메일은 html"
        ]
        # 글자 수가 너무 짧으면서(100자 이하) 위 키워드 중 하나라도 포함되어 있다면 유령 문구로 간주!
        if len(txt) < 150:
            for p in placeholders:
                if p in txt: return True
        return False

    # 1. 일반 텍스트가 있지만, 그게 만약 '유령 안내문'이라면 과감히 버리고 HTML에 기대를 겁니다.
    final_text = ""
    if text_content.strip() and not is_just_placeholder(text_content):
        final_text = text_content.strip()
    
    # 2. 텍스트가 없거나 유령 문구뿐이라면, HTML을 정성껏 뜯어서 내용을 보충합니다.
    if not final_text and html_content.strip():
        try:
            # [V12.22] 정교한 수술: BeautifulSoup을 사용하여 스타일, 스크립트 등 불순물을 제거합니다.
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_content, 'lxml') # 또는 'html.parser'
            
            # 스타일과 스크립트 태그는 내용을 포함해서 아예 제거!
            for script_or_style in soup(["script", "style"]):
                script_or_style.decompose()

            # 줄바꿈과 공백을 적절히 섞어서 깨끗한 글자만 추출합니다.
            clean_text = soup.get_text(separator=' ', strip=True)
            # 과도한 공백 및 이스케이프 문자 정리
            final_text = re.sub(r'\s+', ' ', clean_text).replace('&nbsp;', ' ')
        except Exception as e:
            # 만약 수술 중 에러가 나면, 예비용 정규식(칼)을 사용하여 억지로라도 뜯어냅니다.
            logger.warning(f"BeautifulSoup 고속 추출 실패로 정규식 대체: {e}")
            clean_text = re.sub(r'<[^>]+>', ' ', html_content)
            final_text = re.sub(r'\s+', ' ', clean_text).strip()
    
    # 혹시 남아있을지도 모르는 원본 데이터 찌꺼기(--None 등) 정교하게 청소
    final_text = re.sub(r'--\w+|Content-Type:.*|Content-Transfer-Encoding:.*', '', final_text)
    
    # [방어막 1단계] 기계어로 된 폭탄 스팸메일 대비: 최대 글자 수 강제 제한 (5천 자)
    if final_text and len(final_text) > 5000:
        logger.warning(f"⚠️ 본문 텍스트가 비정상적으로 깁니다 ({len(final_text)}자). 토큰 폭탄 방지를 위해 강제 절단합니다.")
        final_text = final_text[:5000] + "\n\n... (원문이 너무 길어 이하 생략됨)"
        
    return final_text if final_text.strip() else "본문 추출 불가 메일"

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

def _parse_email_message(msg, uid):
    """
    [V12.23] 모든 메일을 해석하는 공용 도구함입니다. 
    메일 제목, 보낸 사람, 시간, 본문 추출 로직을 하나로 합쳤습니다.
    """
    subject = decode_email_header(msg.get("Subject"))
    sender = decode_email_header(msg.get("From"))
    date = format_to_vietnam_time(msg.get("Date"))
    body = get_text_from_email(msg)
    
    return {
        "uid": uid,
        "subject": subject,
        "sender": sender,
        "date": date,
        "body": body
    }

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



def fetch_recent_emails():
    """
    [V12.23] 부장님 지적 반영: 'UNSEEN'이 아닌 'SINCE' 날짜 기준으로 최근 메일을 가져옵니다.
    메일 서버에 안전하게 접속하여 어제부터 오늘까지의 메일을 봇의 장부와 대조합니다.
    """
    import time
    max_retries = 3
    retry_delay = 1
    
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"메일 서버 안전 접속 시도 ({attempt}/{max_retries})...")
            # [V12.23] context manager(with)를 사용하여 접속 종료를 자동화합니다.
            with imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT, timeout=15) as mail:
                mail.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
                mail.select("inbox")
                logger.info("메일 서버 안전하게 접속 성공 완료!")

                # 날짜 기준(SINCE)으로 어제부터 온 모든 메일을 가져와 장부와 대조합니다.
                tz = pytz.timezone(USER_TIMEZONE)
                since_date = (datetime.datetime.now(tz) - datetime.timedelta(days=1)).strftime("%d-%b-%Y")
                
                status, response = mail.uid('SEARCH', 'SINCE', since_date)
                if status != "OK":
                    logger.error(f"메일 검색({since_date})에 실패하였습니다.")
                    return []

                uids = response[0].split()
                processed_uids = load_processed_uids() 
                fetched_emails = []

                for uid_bytes in uids:
                    uid = uid_bytes.decode('utf-8')
                    if uid in processed_uids:
                        continue

                    # PEEK 옵션으로 메일 읽음 처리 방지
                    status, msg_data = mail.uid('FETCH', uid, "(BODY.PEEK[])")
                    if status != "OK" or not msg_data or not msg_data[0] or not isinstance(msg_data[0], tuple):
                        continue

                    raw_email = msg_data[0][1]
                    msg = email.message_from_bytes(raw_email)
                    
                    # [V12.23] 통합된 파싱 도구를 사용합니다.
                    fetched_emails.append(_parse_email_message(msg, uid))
                    
                return fetched_emails

        except Exception as e:
            if attempt < max_retries:
                logger.warning(f"메일 접속 장애 발생 ({attempt}/{max_retries}). {retry_delay}초 후 다시 시도: {e}")
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                logger.error(f"최종 3회 접속 시도 모두 실패: {e}")
                return []

        except Exception as e:
            if attempt < max_retries:
                logger.warning(f"메일 접속 장애 발생 ({attempt}/{max_retries}). {retry_delay}초 후 다시 시도합니다: {e}")
                time.sleep(retry_delay)
                retry_delay *= 2 # 지능형 대기: 기다리는 시간을 2배씩 늘려 서버 부하를 줄입니다.
            else:
                logger.error(f"최종 3회 접속 시도 모두 실패: {e}")
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
                logger.info(f"성공: 원본 메일 패치 완료 ({len(raw_email)} bytes)")
                return raw_email
        
        logger.error(f"실패: 서버에서 원본 데이터를 찾지 못했습니다. (UID: {uid}, Status: {status})")
        return None
    except Exception as e:
        logger.error(f"원본 메일 패치 중 오류 발생: {e}")
        return None
def fetch_parsed_mail(uid):
    """
    [V12.23] 통합 수술 결과: _parse_email_message를 사용하여 신속하게 특정 메일을 복구합니다.
    """
    logger.info(f"실시간 메일 데이터 복구 시작 (UID: {uid})...")
    try:
        with imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT, timeout=15) as mail:
            mail.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            mail.select("inbox")
            status, msg_data = mail.uid('FETCH', uid, "(BODY.PEEK[])")
            
            if status == "OK" and msg_data and msg_data[0] and isinstance(msg_data[0], tuple):
                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)
                
                # [V12.23] 통합된 파싱 도구로 깔끔하게 반환!
                result = _parse_email_message(msg, uid)
                logger.info(f"성공: 메일 데이터 복구 완료 ({result['subject']})")
                return result
        
        logger.error(f"실패: 서버에서 해당 메일 데이터를 찾지 못했습니다. (UID: {uid})")
        return None
    except Exception as e:
        logger.error(f"메일 데이터 복구 중 오류 발생: {e}")
        return None

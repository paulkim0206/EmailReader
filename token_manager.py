import json
import os
import datetime
import pytz
import threading
from config import TOKEN_USAGE_FILE, USER_TIMEZONE, logger

# 동시에 여러 공정이 기록을 시도할 때 파일이 깨지지 않게 방어하는 잠금 장치입니다.
_TOKEN_LOCK = threading.Lock()

def log_token(task, prompt_tokens, candidate_tokens, prompt_text=None, response_text=None):
    """
    [V12.25] AI 사용 시 발생하는 입/출력 토큰을 부장님 전용 장부에 기록합니다.
    [V17.2] 1만 토큰 초과 시 🚨 경보 발령 및 정밀 진단 리포트를 생성합니다.
    """
    try:
        from config import TOKEN_ALERT_THRESHOLD, HIGH_TOKEN_REPORTS_DIR
        tz = pytz.timezone(USER_TIMEZONE)
        now = datetime.datetime.now(tz)
        total_tokens = (prompt_tokens or 0) + (candidate_tokens or 0)
        
        # 장부에 기록될 한 줄의 데이터 구성
        entry = {
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
            "task": task,
            "input_tokens": prompt_tokens or 0,
            "output_tokens": candidate_tokens or 0,
            "total_tokens": total_tokens
        }

        with _TOKEN_LOCK:
            data = []
            if os.path.exists(TOKEN_USAGE_FILE):
                try:
                    with open(TOKEN_USAGE_FILE, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    data = []
            
            data.append(entry)
            with open(TOKEN_USAGE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
        logger.info(f"🪙 토큰 기록 완료: {task} (Total: {total_tokens})")

        # [V17.2] 1만 토큰 초과 시 정밀 진단 리포트 생성
        is_high_token = total_tokens >= TOKEN_ALERT_THRESHOLD
        if is_high_token and prompt_text:
            from config import HIGH_TOKEN_LOG_FILE
            try:
                # [V17.5] 개별 파일이 아닌 하나의 통합 파일에 '누적 기록' 합니다.
                with open(HIGH_TOKEN_LOG_FILE, "a", encoding="utf-8") as rf:
                    rf.write(f"\n{'='*60}\n")
                    rf.write(f"⚠️ [비상: 고비용 AI 호출 기록] {now.strftime('%Y-%m-%d %H:%M:%S')}\n")
                    rf.write(f"작업명: {task} | 사용량: {total_tokens:,} (In: {prompt_tokens:,}, Out: {candidate_tokens:,})\n")
                    rf.write(f"{'-'*60}\n")
                    rf.write(f"[1. AI 지침 및 입력 원문]\n{prompt_text}\n\n")
                    rf.write(f"[2. AI 답변 원문]\n{response_text}\n")
                    rf.write(f"{'='*60}\n")
                logger.warning(f"🚨 통합 고비용 장부에 기록 완료: {HIGH_TOKEN_LOG_FILE}")
            except Exception as re:
                logger.error(f"통합 장부 기록 중 오류: {re}")

        # [V12.30] 실시간 텔레그램 토큰 사용량 직통 알림 발송 (동기식)
        try:
            from urllib import request as url_req
            from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
            
            task_kr = {
                "Mail_Summary": "📧 새 이메일 요약",
                "Intent_Router": "🕵️ 의도 분석 라우터",
                "Secretary_Chat": "🤖 비서와의 지능형 대화",
                "Skip_Rule_Analysis": "🏠 스킵 규칙 추출",
                "Daily_Report": "📅 일일 비즈니스 보고 생성",
                "Weekly_Report": "📊 주간 통합 보고 생성",
                "News_Summary": "📰 뉴스 속보 요약",
                "News_Title_Translation": "🌐 뉴스 제목 번역"
            }.get(task, task)
            
            # [V18.9] 의도 분석 라우터 및 뉴스 번역은 알림을 발송하지 않습니다.
            if task in ["Intent_Router", "News_Title_Translation"]:
                return

            # [V17.5] 텔레그램 경보 메시지 페이로드 구성
            msg_data = {"chat_id": TELEGRAM_CHAT_ID, "parse_mode": "HTML"}
            
            if is_high_token:
                msg_data["text"] = (
                    f"🚨 <b>[토큰 레드라인 경보]</b> 🚨\n\n"
                    f"🎯 <b>위험 작업:</b> {task_kr}\n"
                    f"🔥 <b>총 사용량: {total_tokens:,} 토큰</b>\n"
                    f"📥 입력: {prompt_tokens:,} / 📤 출력: {candidate_tokens:,}\n\n"
                    f"📢 <b>부장님!</b> 단일 호출 비용이 1만 토큰을 초과했습니다.\n"
                    f"아래 [다운로드] 버튼을 눌러 통합 진단 장부를 확인하시거나, "
                    f"비정상 동작이면 <b>/shutdown</b> 명령어로 전원을 차단하십시오! 🔌"
                )
                # [V17.5] 인라인 버튼 추가 (JSON 형식)
                msg_data["reply_markup"] = {
                    "inline_keyboard": [[
                        {"text": "📄 통합 진단 장부 다운로드", "callback_data": "token_log_download"}
                    ]]
                }
            else:
                msg_data["text"] = (
                    f"🎯 <b>작업:</b> {task_kr}\n"
                    f"📥 입력: {prompt_tokens or 0} / 📤 출력: {candidate_tokens or 0} 토큰"
                )
            
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = json.dumps(msg_data).encode('utf-8')
            req = url_req.Request(url, data=payload, headers={'Content-Type': 'application/json'})
            url_req.urlopen(req, timeout=3)
        except Exception as te:
            logger.error(f"실시간 토큰 텔레그램 알림 전송 실패: {te}")

    except Exception as e:
        logger.error(f"토큰 장부 기록 중 에러 발생: {e}")

def get_daily_token_usage(target_date=None):
    """오늘(또는 특정 날짜)의 총 토큰 사용량을 계산하여 반환합니다."""
    if not target_date:
        tz = pytz.timezone(USER_TIMEZONE)
        target_date = datetime.datetime.now(tz).strftime("%Y-%m-%d")
    
    total_in = 0
    total_out = 0
    count = 0
    
    if os.path.exists(TOKEN_USAGE_FILE):
        try:
            with open(TOKEN_USAGE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for entry in data:
                    if entry.get("date") == target_date:
                        total_in += entry.get("input_tokens", 0)
                        total_out += entry.get("output_tokens", 0)
                        count += 1
        except Exception:
            pass
            
    return {
        "date": target_date,
        "total_input": total_in,
        "total_output": total_out,
        "total_sum": total_in + total_out,
        "request_count": count
    }

def get_daily_token_report_message(target_date=None, is_realtime=False):
    """
    [V16.6] 부장님을 위한 '실속형 데일리 토큰 정산서'를 생성합니다.
    [V19.5] 실시간 조회 모드 지원: 당일 00:00 ~ 현재 시각 범위를 명시합니다.
    """
    tz = pytz.timezone(USER_TIMEZONE)
    now = datetime.datetime.now(tz)
    
    if not target_date:
        target_date = now.strftime("%Y-%m-%d")
    
    # 1. 카테고리별 합산 준비
    usage_by_task = {}
    total_in = 0
    total_out = 0
    total_requests = 0
    
    # 한글 이름 매핑 (V18.2 번역기 추가 등 반영)
    task_map = {
        "Mail_Summary": "📧 새 이메일 요약",
        "Intent_Router": "🕵️ 의도 분석 라우터",
        "Secretary_Chat": "🤖 비서와의 지능형 대화",
        "Skip_Rule_Analysis": "🏠 스킵(제외) 규칙 추출",
        "Daily_Report": "📅 일일 비즈니스 보고서 생성",
        "Weekly_Report": "📊 주간 통합 보고서 생성",
        "News_Summary": "📰 뉴스 속보 요약",
        "News_Title_Translation": "🌐 뉴스 제목 번역",
        "Unknown": "❓ 기타 작업"
    }

    if os.path.exists(TOKEN_USAGE_FILE):
        try:
            with _TOKEN_LOCK:
                with open(TOKEN_USAGE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for entry in data:
                        if entry.get("date") == target_date:
                            task = entry.get("task", "Unknown")
                            t_in = entry.get("prompt", 0) # V19.5: 필드명 맞춤
                            t_out = entry.get("candidate", 0)
                            
                            if task not in usage_by_task:
                                usage_by_task[task] = {"in": 0, "out": 0}
                            
                            usage_by_task[task]["in"] += t_in
                            usage_by_task[task]["out"] += t_out
                            total_in += t_in
                            total_out += t_out
                            total_requests += 1
        except Exception as e:
            logger.error(f"토큰 장부 합산 중 오류: {e}")
            return None

    if total_requests == 0:
        return f"📊 <b>[{target_date}]</b>\n아직 집계된 토큰 사용 내역이 없습니다."

    # 2. 메시지 조립
    title = "🪙 <b>[피아니] 실시간 토큰 정산 보고</b>" if is_realtime else "🪙 <b>[피아니] 오늘의 AI 토큰 정산서</b>"
    msg = f"{title}\n"
    
    # [V19.5] 정밀한 시간 범위 명시
    if is_realtime:
        current_time = now.strftime("%H:%M")
        msg += f"📅 <b>집계 기간:</b> {target_date} (00:00 ~ {current_time} 현재)\n\n"
    else:
        msg += f"📅 <b>기준일:</b> {target_date}\n\n"
        
    msg += f"📊 <b>전체 이용 현황</b>\n"
    msg += f"- 요청 건수: {total_requests}건\n"
    msg += f"- 총 사용량: <b>{total_in + total_out:,}</b> 💡 (In: {total_in:,} / Out: {total_out:,})\n\n"
    msg += f"📂 <b>카테고리별 지출 상세</b>\n"
    
    # 지출이 많은 순서대로 정렬
    sorted_tasks = sorted(usage_by_task.items(), key=lambda x: (x[1]["in"] + x[1]["out"]), reverse=True)
    
    for task_id, tokens in sorted_tasks:
        name = task_map.get(task_id, task_id)
        msg += f"- {name}: <b>{tokens['in'] + tokens['out']:,}</b> 🪙 (In: {tokens['in']:,} / Out: {tokens['out']:,})\n"
        
    footer = "\n✅ 실시간 집계 결과입니다. 부장님!" if is_realtime else "\n✅ 부장님, 오늘도 알뜰하고 똑똑하게 AI를 운용하셨습니다! 👍"
    msg += footer
    
    return msg

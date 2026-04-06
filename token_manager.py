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
        report_path = ""
        if is_high_token and prompt_text:
            filename = f"report_{now.strftime('%Y%m%d_%H%M%S')}_{task}.txt"
            report_path = os.path.join(HIGH_TOKEN_REPORTS_DIR, filename)
            try:
                with open(report_path, "w", encoding="utf-8") as rf:
                    rf.write(f"⚠️ [고비용 AI 호출 정밀 진단 리포트] ⚠️\n")
                    rf.write(f"일시: {now.strftime('%Y-%m-%d %H:%M:%S')}\n")
                    rf.write(f"작업명: {task}\n")
                    rf.write(f"사용량: {total_tokens} (In: {prompt_tokens}, Out: {candidate_tokens})\n")
                    rf.write(f"{'='*50}\n\n")
                    rf.write(f"[1. AI 지침 및 입력 원문]\n{prompt_text}\n\n")
                    rf.write(f"{'-'*50}\n\n")
                    rf.write(f"[2. AI 답변 원문]\n{response_text}\n")
                logger.warning(f"🚨 고비용 리포트 생성 완료: {report_path}")
            except Exception as re:
                logger.error(f"리포트 생성 중 오류: {re}")

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
                "News_Summary": "📰 뉴스 속보 요약"
            }.get(task, task)
            
            if is_high_token:
                msg = (
                    f"🚨 <b>[토큰 사용량 레드라인 경보]</b> 🚨\n\n"
                    f"🎯 <b>위험 작업:</b> {task_kr}\n"
                    f"🔥 <b>총 사용량: {total_tokens:,} 토큰</b>\n"
                    f"📥 입력: {prompt_tokens:,} / 📤 출력: {candidate_tokens:,}\n\n"
                    f"📢 <b>부장님!</b> 단일 호출 비용이 설정치({TOKEN_ALERT_THRESHOLD})를 초과했습니다.\n"
                    f"방금 생성된 <code>{os.path.basename(report_path)}</code> 리포트를 확인하시거나, "
                    f"비정상 동작으로 보이면 <b>/shutdown</b> 명령어로 서버를 멈춰주세요! 🔌"
                )
            else:
                msg = f"🪙 <b>[실시간 토큰 알림]</b>\n\n🎯 <b>작업:</b> {task_kr}\n📥 <b>입력:</b> {prompt_tokens or 0} 토큰\n📤 <b>출력:</b> {candidate_tokens or 0} 토큰"
            
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}).encode('utf-8')
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

def get_daily_token_report_message(target_date=None):
    """
    [V16.6] 부장님을 위한 '실속형 데일리 토큰 정산서'를 생성합니다.
    카테고리별로 (In: XXX / Out: YYY) 상세 내역을 포함합니다.
    """
    if not target_date:
        tz = pytz.timezone(USER_TIMEZONE)
        target_date = datetime.datetime.now(tz).strftime("%Y-%m-%d")
    
    # 1. 카테고리별 합산 준비
    usage_by_task = {}
    total_in = 0
    total_out = 0
    total_requests = 0
    
    # 한글 이름 매핑
    task_map = {
        "Mail_Summary": "📧 새 이메일 요약",
        "Intent_Router": "🕵️ 의도 분석 라우터",
        "Secretary_Chat": "🤖 비서와의 지능형 대화",
        "Skip_Rule_Analysis": "🏠 스킵(제외) 규칙 추출",
        "Daily_Report": "📅 일일 비즈니스 보고서 생성",
        "Weekly_Report": "📊 주간 통합 보고서 생성",
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
                            t_in = entry.get("input_tokens", 0)
                            t_out = entry.get("output_tokens", 0)
                            
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
        return None

    # 2. 메시지 조립
    msg = f"🪙 <b>[피아니] 오늘의 AI 토큰 정산서</b>\n"
    msg += f"📅 <b>기준일:</b> {target_date}\n\n"
    msg += f"📊 <b>전체 이용 현황</b>\n"
    msg += f"- 요청 건수: {total_requests}건\n"
    msg += f"- 총 사용량: <b>{total_in + total_out:,}</b> 💡 (In: {total_in:,} / Out: {total_out:,})\n\n"
    msg += f"📂 <b>카테고리별 지출 상세</b>\n"
    
    # 지출이 많은 순서대로 정렬해서 보여드립니다.
    sorted_tasks = sorted(usage_by_task.items(), key=lambda x: (x[1]["in"] + x[1]["out"]), reverse=True)
    
    for task_id, tokens in sorted_tasks:
        name = task_map.get(task_id, task_id)
        msg += f"- {name}: <b>{tokens['in'] + tokens['out']:,}</b> 🪙 (In: {tokens['in']:,} / Out: {tokens['out']:,})\n"
        
    msg += f"\n✅ 부장님, 오늘도 알뜰하고 똑똑하게 AI를 운용하셨습니다! 👍"
    
    return msg

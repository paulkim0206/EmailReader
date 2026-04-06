import os
import json
import datetime
import pytz
from config import REPORTS_DIR, logger, USER_TIMEZONE

def get_weekly_report_path(date_obj):
    """
    주어진 날짜가 속한 주차(ISO Week)의 연도 폴더와 파일 경로를 반환합니다.
    예: data/reports/2026/2026_W14.json
    """
    year, week_num, _ = date_obj.isocalendar()
    year_dir = os.path.join(REPORTS_DIR, str(year))
    os.makedirs(year_dir, exist_ok=True)
    filename = f"{year}_W{week_num:02d}.json"
    return os.path.join(year_dir, filename)

def load_weekly_report(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"주간 보고서 로드 실패: {e}")
    return {}

def save_weekly_report(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"주간 보고서 저장 실패: {e}")

def update_daily_report(date_str=None):
    """
    [V12.14] 현지 시각(USER_TIMEZONE) 기준으로 '어제' 날짜를 정확히 계산하여 
    주간 통합 장부에 기록합니다. (서버 시각 불일치 해결)
    """
    if date_str is None:
        # [핵심 수정] 시스템 시계 대신 설정된 타임존 기준의 어제 날짜를 구합니다.
        tz = pytz.timezone(USER_TIMEZONE)
        today = datetime.datetime.now(tz).date()
        target_date_obj = today - datetime.timedelta(days=1)
        date_str = target_date_obj.isoformat()
        logger.info(f"📅 [자동 스케줄] 현지 시각 기준 어제({date_str}) 보고서 작성을 시작합니다.")
    else:
        target_date_obj = datetime.date.fromisoformat(date_str)
        logger.info(f"🎯 [수동 요청] 지정된 날짜({date_str}) 보고서 작성을 시작합니다.")

    # 1. [V16.5] 지능형 장고 뒤지기: 이미 가공된 보고서가 있다면 AI 비용을 아끼고 즉시 배송합니다.
    report_path = get_weekly_report_path(target_date_obj)
    weekly_data = load_weekly_report(report_path)
    weekday_name = target_date_obj.strftime("%A")
    
    if weekday_name in weekly_data:
        logger.info(f"💾 [{date_str}] 이미 완성된 보고서가 장고에 있습니다. 즉시 배달합니다.")
        return weekly_data[weekday_name].get("data")

    # 2. 원천 데이터(Thread Memory) 추출 (완성본이 없을 때만 요리 시작)
    from thread_manager import get_summaries_all_by_date
    raw_summaries = get_summaries_all_by_date(date_str)
    
    if not raw_summaries:
        logger.warning(f"⚠️ [{date_str}] 요약할 데이터도 없고 기존 보고서도 없습니다.")
        return None

    # 2. AI 분석 (30자 주제별 요약)
    from ai_processor import generate_daily_report_ai
    report_json = generate_daily_report_ai(raw_summaries)

    # [V18.4] 방어 로직: 보고서가 오류 데이터(NameError 등)를 포함하고 있다면 저장하지 않습니다.
    if not report_json or (isinstance(report_json, dict) and any(t.get("category") == "오류" for t in report_json.get("topics", []))):
        logger.warning(f"⚠️ [{date_str}] 생성된 보고서가 유효하지 않거나 오류를 포함하고 있어 저장을 건너뜁니다.")
        return report_json

    # 3. 주간 통합 파일 업데이트
    report_path = get_weekly_report_path(target_date_obj)
    weekly_data = load_weekly_report(report_path)
    
    # 요일 이름 (Monday, Tuesday...)
    weekday_name = target_date_obj.strftime("%A")
    weekly_data[weekday_name] = {
        "date": date_str,
        "data": report_json
    }
    
    save_weekly_report(report_path, weekly_data)
    logger.info(f"✅ [{date_str}] 보고서가 성공적으로 주간 장고에 기록되었습니다.")
    
    return report_json

def generate_weekly_summary():
    """
    [V13.2] AI 없이 파이썬만으로 월~토 일일 보고서를 고객별로 재분류합니다.
    토큰 0원, 팩트만 전달합니다.
    """
    tz = pytz.timezone(USER_TIMEZONE)
    today = datetime.datetime.now(tz).date()
    reference_date = today - datetime.timedelta(days=1)

    report_path = get_weekly_report_path(reference_date)
    weekly_data = load_weekly_report(report_path)

    if not weekly_data:
        logger.warning(f"⚠️ 주간 보고서를 위한 데이터가 없습니다. (참조 파일: {os.path.basename(report_path)})")
        return None

    # 요일 한글 매핑
    day_labels = {
        "Monday": "월", "Tuesday": "화", "Wednesday": "수",
        "Thursday": "목", "Friday": "금", "Saturday": "토"
    }

    # 고객별 집계 딕셔너리
    client_summary = {}

    for day_en, label in day_labels.items():
        if day_en not in weekly_data:
            continue
        data = weekly_data[day_en].get("data", {})
        if not isinstance(data, dict):
            continue

        # client_reports 구조만 처리 (현재 표준)
        for cr in data.get("client_reports", []):
            client = cr.get("client", "알 수 없음").strip()
            if not client:
                continue
            if client not in client_summary:
                client_summary[client] = []
            for s in cr.get("summaries", []):
                client_summary[client].append(f"[{label}] {s}")

    if not client_summary:
        logger.warning("주간 데이터를 집계했으나 client_reports 항목이 없습니다.")
        return None

    # 주차 레이블 계산
    year, week_num, _ = reference_date.isocalendar()
    # 해당 주의 월요일/토요일 계산
    monday = reference_date - datetime.timedelta(days=reference_date.weekday())
    saturday = monday + datetime.timedelta(days=5)
    week_label = f"{year} W{week_num:02d} ({monday.strftime('%m-%d')} ~ {saturday.strftime('%m-%d')})"

    result = {
        "week_label": week_label,
        "client_summary": client_summary,
        "total_items": sum(len(v) for v in client_summary.values())
    }

    # WeeklySummary로 저장 (온디맨드 재호출 방지용)
    weekly_data["WeeklySummary"] = {
        "generated_at": datetime.datetime.now().isoformat(),
        "content": result
    }
    save_weekly_report(report_path, weekly_data)
    logger.info(f"주간 집계 보고서 생성 완료: {week_label} / 고객 {len(client_summary)}사 / 총 {result['total_items']}건")

    return result


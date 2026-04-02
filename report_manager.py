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

    # 1. 원천 데이터(Thread Memory) 추출
    from thread_manager import get_summaries_all_by_date
    raw_summaries = get_summaries_all_by_date(date_str)
    
    if not raw_summaries:
        logger.warning(f"⚠️ [{date_str}] 요약할 메일 데이터가 없습니다. (핀 버튼으로 선택된 메일이 있는지 확인해 주세요.)")
        return None

    # 2. AI 분석 (30자 주제별 요약)
    from ai_processor import generate_daily_report_ai
    report_json = generate_daily_report_ai(raw_summaries)

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
    logger.info(f"[{date_str}] 일일 보고서가 주간 장부({os.path.basename(report_path)})에 기록되었습니다.")
    
    return report_json

def generate_weekly_summary():
    """
    [V12.14] 한 주의 흐름을 분석하며, 날짜 계산 시 현지 시각을 기준으로 합니다.
    """
    # [핵심 수정] 현지 시각 기준으로 어제(일요일) 데이터를 참조하여 주차 파일을 결정합니다.
    tz = pytz.timezone(USER_TIMEZONE)
    today = datetime.datetime.now(tz).date()
    reference_date = today - datetime.timedelta(days=1)
    
    report_path = get_weekly_report_path(reference_date)
    weekly_data = load_weekly_report(report_path)
    
    if not weekly_data:
        logger.warning(f"⚠️ 주간 보고서를 위한 데이터가 없습니다. (참조 파일: {os.path.basename(report_path)})")
        return None

    # 월~토요일 데이터만 필터링 (부장님 지침: 6일치만 담을 것)
    daily_reports_for_ai = {}
    for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]:
        if day in weekly_data:
            daily_reports_for_ai[day] = weekly_data[day]["data"]

    if not daily_reports_for_ai:
        return None

    # 1. AI 분석 (주간 통합 및 중복 제거)
    from ai_processor import generate_weekly_summary_ai
    summary_json = generate_weekly_summary_ai(daily_reports_for_ai)

    # 2. 결과 저장
    weekly_data["WeeklySummary"] = {
        "generated_at": datetime.datetime.now().isoformat(),
        "content": summary_json
    }
    save_weekly_report(report_path, weekly_data)
    logger.info(f"주간 통합 보고서 생성이 완료되었습니다: {os.path.basename(report_path)}")
    
    return summary_json

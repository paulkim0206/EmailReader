import os
import json
import datetime
from config import REPORTS_DIR, logger

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
    [V9.0] 특정 날짜(기본값: 어제)의 메일을 요약하여 주간 통합 장부에 기록합니다.
    """
    if date_str is None:
        # 어제 날짜 구하기 (오전 6시 보고용)
        target_date_obj = datetime.date.today() - datetime.timedelta(days=1)
        date_str = target_date_obj.isoformat()
    else:
        target_date_obj = datetime.date.fromisoformat(date_str)

    # 1. 원천 데이터(Thread Memory) 추출
    from thread_manager import get_summaries_all_by_date
    raw_summaries = get_summaries_all_by_date(date_str)
    
    if not raw_summaries:
        logger.info(f"[{date_str}] 요약할 메일 데이터가 없습니다.")
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
    [V9.0] 이번 주(월~토) 일일 보고서들을 취합하여 주간 통합 리포트를 생성합니다.
    """
    # [V11.4] 월요일 아침에 실행되므로, '어제(일요일)' 기준의 주차 파일을 가져와야 지난주(월~토) 데이터가 잡힙니다.
    reference_date = datetime.date.today() - datetime.timedelta(days=1)
    report_path = get_weekly_report_path(reference_date)
    weekly_data = load_weekly_report(report_path)
    
    if not weekly_data:
        logger.warning("주간 보고서를 위한 일일 데이터가 하나도 없습니다.")
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

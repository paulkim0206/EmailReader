import os
import requests
import xml.etree.ElementTree as ET
from config import RSS_URLS, PROCESSED_RSS_FILE, logger

# [V17.0] 베트남 뉴스 RSS 관리자 (RSS Manager)
# 신규 기사를 탐지하고 중복을 방지하는 기능을 담당합니다.

def load_processed_rss_links():
    """이미 처리된 뉴스 링크들을 장부에서 불러옵니다."""
    if not os.path.exists(PROCESSED_RSS_FILE):
        return set()
    try:
        with open(PROCESSED_RSS_FILE, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    except Exception as e:
        logger.error(f"RSS 장부 로드 중 오류: {e}")
        return set()

def save_processed_rss_link(link):
    """새로 확인한 뉴스 링크를 장부에 기록합니다."""
    try:
        with open(PROCESSED_RSS_FILE, "a", encoding="utf-8") as f:
            f.write(f"{link}\n")
    except Exception as e:
        logger.error(f"RSS 장부 기록 중 오류: {e}")

def fetch_new_rss_items():
    """설정된 모든 RSS URL에서 새로운 기사들을 가져옵니다."""
    processed_links = load_processed_rss_links()
    new_items = []
    
    # 봇이 처음 실행될 때(장부가 아예 없거나 비어있을 때) 과거 뉴스가 쏟아지는 것을 방지
    is_initial_run = not os.path.exists(PROCESSED_RSS_FILE) or len(processed_links) == 0

    for url in RSS_URLS:
        try:
            # 베트남 서버에서 봇을 거부할 수 있으므로 브라우저인 척 헤더 추가
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            
            # XML 파싱
            root = ET.fromstring(response.content)
            
            for item in root.findall('.//item'):
                title_node = item.find('title')
                link_node = item.find('link')
                pub_date_node = item.find('pubDate')
                description_node = item.find('description')

                title = title_node.text if title_node is not None else "제목 없음"
                link = link_node.text if link_node is not None else ""
                pub_date = pub_date_node.text if pub_date_node is not None else ""
                description = description_node.text if description_node is not None else ""
                
                if not link: continue # 링크가 없으면 무시

                if link not in processed_links:
                    if is_initial_run:
                        # 첫 실행 시에는 장부에만 적고 알림은 주지 않음 (폭탄 방지)
                        save_processed_rss_link(link)
                        processed_links.add(link)
                    else:
                        new_items.append({
                            "title": title,
                            "link": link,
                            "pub_date": pub_date,
                            "description": description
                        })
        except Exception as e:
            logger.error(f"RSS fetch 중 오류 ({url}): {e}")

    # 처음 장부를 만드는 중이라면 "준비 완료"로 간주하고 비운 채로 리턴
    if is_initial_run:
        logger.info("🔍 RSS 뉴스 감시를 위해 초기 장부 작성을 완료했습니다. 다음 감시부터 알림이 시작됩니다.")
        return []

    return new_items

import re
import os

filepath = r'C:\Users\MSI\.gemini\antigravity\brain\fa3d1bc6-3440-4c73-8f79-8382d278e613\.system_generated\steps\686\content.md'
if not os.path.exists(filepath):
    print("File not found")
    exit()

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# Extract items
items = re.findall(r'<item>([\s\S]*?)</item>', content)

for item in items:
    title_match = re.search(r'<title>(.*?)</title>', item)
    date_match = re.search(r'<pubDate>(.*?)</pubDate>', item)
    if title_match and date_match:
        # Clean up title (HTML entities etc. if any)
        title = title_match.group(1).replace('<![CDATA[', '').replace(']]>', '')
        date = date_match.group(1)
        print(f"[{date}] {title}")

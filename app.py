import os
import requests
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree
from google import genai
from linebot import LineBotApi
from linebot.models import TextSendMessage

# 1. 設定環境變數
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
LINE_USER_ID = os.environ.get('LINE_USER_ID')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

# 自訂搜尋關鍵字與時間範圍
KEYWORDS = "基隆"  # 可依需求修改關鍵字，多個關鍵字可用逗號分隔，如 "基隆, 台電"
SEARCH_HOURS = 24

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)

def shorten_url(url):
    """將長網址縮短"""
    try:
        api_url = f"http://tinyurl.com/api-create.php?url={requests.utils.quote(url)}"
        res = requests.get(api_url, timeout=5)
        if res.status_code == 200:
            return res.text
    except Exception:
        pass
    return url

def fetch_google_news(keywords_str, hours):
    """爬取近指定小時內的新聞"""
    keyword_groups = [g.strip() for g in keywords_str.replace('，', ',').split(',') if g.strip()]
    all_news = []
    
    now_utc = datetime.now(timezone.utc)
    time_limit_utc = now_utc - timedelta(hours=int(hours))
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    for group in keyword_groups:
        url = f'https://news.google.com/rss/search?q={requests.utils.quote(group.replace(" ", " AND "))}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant'
        try:
            res = requests.get(url, timeout=15, headers=headers)
            if res.status_code == 200:
                tree = ElementTree.fromstring(res.content)
                for item in tree.findall('.//item'):
                    title = item.find('title').text if item.find('title') is not None else ''
                    pub_date_str = item.find('pubDate').text
                    
                    pub_date_dt = parsedate_to_datetime(pub_date_str)
                    pub_date_tw = pub_date_dt.astimezone(timezone(timedelta(hours=8)))
                    
                    if pub_date_dt > time_limit_utc:
                        all_news.append({
                            'title': title,
                            'time': pub_date_tw.strftime('%m/%d %H:%M'),
                            'source': item.find('source').text if item.find('source') is not None else '網路',
                            'link': item.find('link').text,
                            'timestamp': pub_date_tw
                        })
        except Exception as e:
            print(f"DEBUG: 解析 RSS 失敗: {e}")
            continue

    # 去重複
    unique_news = []
    seen_titles = set()
    for item in all_news:
        if item['title'] not in seen_titles:
            seen_titles.add(item['title'])
            unique_news.append(item)
            
    return sorted(unique_news, key=lambda x: x['timestamp'], reverse=True)

def generate_ai_summary(news_list):
    """將所有新聞打包交給 AI 生成整體總結報告（單次 API 呼叫）"""
    if not GEMINI_API_KEY:
        return "（未設定 GEMINI_API_KEY，無法生成總結）"
    if not news_list:
        return "近時間內無相關新聞，無需總結。"

    # 彙整所有新聞清單
    raw_news_text = ""
    for idx, item in enumerate(news_list, 1):
        raw_news_text += f"{idx}. [{item['source']}] {item['title']} ({item['time']})\n"

    prompt = f"""
以下是過去 {SEARCH_HOURS} 小時內爬取到的新聞標題列表：

{raw_news_text}

請扮演專業資訊分析師，針對上述新聞進行整體綜合總結：
1. **重點速覽**：歸納出 3~5 個核心主題/事件重點（條列說明）。
2. **輿情與關注焦點**：是否有需要特別留意、追蹤或高關注度的突發/重要議題。
3. **結論簡評**：用 1-2 句話做整體總結。

要求：文字精煉、客觀，直接輸出總結報告內容即可。
"""

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model='gemini-3.0-flash',
            contents=prompt,
        )
        return response.text.strip()
    except Exception as e:
        print(f"DEBUG: AI 生成失敗: {e}")
        return "（AI 總結生成失敗）"

def main():
    try:
        print("開始爬取新聞...")
        news = fetch_google_news(KEYWORDS, SEARCH_HOURS)
        print(f"共爬到 {len(news)} 則新聞。")
        
        now_tw = datetime.now(timezone.utc) + timedelta(hours=8)
        now_str = now_tw.strftime("%Y-%m-%d %H:%M")

        if not news:
            message = f"【近 {SEARCH_HOURS} 小時新聞總結報告】\n搜尋時間：{now_str}\n\n❌ 查無相關新聞。"
        else:
            print("正在呼叫 AI 進行總結...")
            ai_summary = generate_ai_summary(news)
            
            # 建立附帶原始新聞列表的推播內容
            news_links_block = "\n".join([
                f"{i}. [{item['source']}] {item['title']}\n   {shorten_url(item['link'])}"
                for i, item in enumerate(news[:10], 1)  # 附錄最多展示前 10 則
            ])
            
            message = f"【近 {SEARCH_HOURS} 小時新聞總結報告】\n時間：{now_str}\n✅ 共彙整 {len(news)} 則新聞\n\n"
            message += f"🤖 【AI 綜合總結】\n{ai_summary}\n\n"
            message += f"-------------------------\n📌 【新聞附錄 (前10則)】\n{news_links_block}"

        # 訊息截斷機制（符合 LINE 5000 字限制）
        if len(message) > 4000:
            message = message[:3900] + "\n\n...(訊息過長，已自動截斷)"

        # 發送推播
        line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=message))
        print("推播發送成功！")

    except Exception as e:
        print(f"執行失敗: {e}")

if __name__ == '__main__':
    main()

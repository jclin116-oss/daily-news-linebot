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
KEYWORDS = "台電"  # 可依需求修改關鍵字，多個關鍵字可用逗號分隔，如 "基隆, 台電"
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
    """分批處理新聞並依「各縣市」分類整理總結"""
    if not GEMINI_API_KEY:
        return "（未設定 GEMINI_API_KEY，無法生成總結）"
    if not news_list:
        return "近時間內無相關新聞，無需總結。"

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        chunk_size = 20
        chunks = [news_list[i:i + chunk_size] for i in range(0, len(news_list), chunk_size)]
        
        partial_summaries = []

        # 第一階段：分批提煉
        for idx, chunk in enumerate(chunks, 1):
            raw_text = ""
            for n_idx, item in enumerate(chunk, 1):
                raw_text += f"{n_idx}. [{item['source']}] {item['title']} ({item['time']})\n"
            
            prompt = f"請針對以下新聞進行提煉，初步將新聞依涉及的縣市或區域整理（無 markdown 符號）：\n\n{raw_text}"
            
            res = client.models.generate_content(
                model='gemini-3.6-flash',
                contents=prompt,
            )
            partial_summaries.append(res.text.strip())

        # 第二階段：依縣市彙整最終報告
        combined_text = "\n\n".join(partial_summaries)
        final_prompt = f"""
以下是過去 {SEARCH_HOURS} 小時內共 {len(news_list)} 則新聞的提煉摘要：

{combined_text}

請扮演專業資訊分析師，將上述新聞**完全依照「縣市」分類**整理成綜合報告。

格式嚴格要求：
1. 嚴禁使用任何 Markdown 符號（絕對不要出現 #、*、**、` 等）。
2. 不要輸出任何字數統計註記（如 (46字)）。
3. 全文直接按「【縣市名稱】」分類條列重點（若為中央政策或全國性新聞，可獨立歸類在【全國／中央政策】）。

排版範例：

【基隆市】
・ 事件一簡述與進展...
・ 事件二簡述與進展...

【新北市】
・ 事件一簡述與進展...

【全國／中央政策】（若無可省略）
・ 政策或議題簡述...
"""

        final_res = client.models.generate_content(
            model='gemini-3.6-flash',
            contents=final_prompt,
        )
        return final_res.text.strip()

    except Exception as e:
        print(f"DEBUG: AI 生成失敗: {e}")
        return f"（AI 總結生成失敗，原因：{e}）"

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

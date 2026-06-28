import os
import feedparser
import requests
from bs4 import BeautifulSoup
import google.generativeai as genai
import time
import json
import wave
import subprocess # 👈 NEW: MP3圧縮ツールを呼び出すための部品を追加

# ==========================================
# ⚙️ 設定エリア
# ==========================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
WEBHOOK_TEXT = os.environ.get("WEBHOOK_TEXT")
WEBHOOK_AUDIO = os.environ.get("WEBHOOK_AUDIO")

if not GEMINI_API_KEY or not WEBHOOK_TEXT:
    print("❌ エラー: APIキーやWebhook URLが設定されていません。")
    exit(1)

genai.configure(api_key=GEMINI_API_KEY)

def send_to_discord(webhook_url, text):
    for i in range(0, len(text), 1900):
        requests.post(webhook_url, json={"content": text[i:i+1900]})
        time.sleep(1)

def scrape_text(url):
    try:
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.content, "html.parser")
        return " ".join([p.text for p in soup.find_all("p")])[:2000] 
    except:
        return "本文取得失敗"

def call_gemini_with_fallback(prompt):
    models_to_try = ["gemini-3.1-flash-lite", "gemini-2.5-flash", "gemini-2.5-flash-lite"]
    for model_name in models_to_try:
        try:
            print(f"🔄 {model_name} で分析を実行中...")
            current_model = genai.GenerativeModel(model_name)
            response = current_model.generate_content(prompt)
            return response.text
        except Exception as e:
            print(f"⚠️ {model_name} でエラー発生: {e}")
            time.sleep(2)
    return None

def text_to_speech_voicevox(text, output_filename="radio.wav", speaker=2):
    """安定化のため一文ずつ分割し、最後にMP3に圧縮して容量制限を突破する"""
    print("🎙️ 音声を生成中...（安定化のため一文ずつ分割処理します）")
    
    clean_text = text.replace("*", "").replace("#", "")
    clean_text = clean_text.replace("。", "。\n").replace("！", "！\n").replace("？", "？\n")
    lines = [line.strip() for line in clean_text.split('\n') if line.strip()]
    wav_files = []
    
    try:
        for i, line in enumerate(lines):
            query_res = requests.post(f"http://127.0.0.1:50021/audio_query", params={"text": line, "speaker": speaker})
            if query_res.status_code != 200:
                continue
            
            synth_res = requests.post(f"http://127.0.0.1:50021/synthesis", params={"speaker": speaker}, json=query_res.json())
            if synth_res.status_code == 200:
                tmp_name = f"tmp_{i}.wav"
                with open(tmp_name, "wb") as f:
                    f.write(synth_res.content)
                wav_files.append(tmp_name)
        
        if not wav_files:
            print("❌ 音声ファイルの生成に失敗しました。")
            return None

        # 1. 数十個のWAVを1つの巨大なWAVに結合
        with wave.open(wav_files[0], 'rb') as w_in:
            params = w_in.getparams()
            with wave.open(output_filename, 'wb') as w_out:
                w_out.setparams(params)
                for wf in wav_files:
                    with wave.open(wf, 'rb') as w:
                        w_out.writeframes(w.readframes(w.getnframes()))
        
        # 2. 巨大なWAVを軽量なMP3に圧縮（Discordの制限回避）
        mp3_filename = "radio.mp3"
        print("🗜️ WAVからMP3へ圧縮中...（Discordアップロード用）")
        subprocess.run(["ffmpeg", "-i", output_filename, "-b:a", "128k", mp3_filename, "-y"], check=True)
        
        return mp3_filename # 成功時はMP3のファイル名を返す

    except Exception as e:
        print(f"⚠️ 音声生成/変換エラー: {e}")
        return None

def send_audio_to_discord(webhook_url, text_msg, filename):
    print(f"📤 Discordへ音声ファイル（{filename}）を送信中...")
    mime_type = "audio/mpeg" if filename.endswith(".mp3") else "audio/wav"
    
    with open(filename, "rb") as f:
        res = requests.post(webhook_url, data={"content": text_msg}, files={"file": (filename, f, mime_type)})
        if res.status_code >= 400:
            print(f"❌ Discord送信エラー ({res.status_code}): {res.text}")
        time.sleep(1)

def main():
    print("🚀 ニュース取得開始...")
    feed = feedparser.parse("https://news.yahoo.co.jp/rss/topics/domestic.xml")
    
    articles_for_prompt = ""
    original_links = []
    
    for i, entry in enumerate(feed.entries[:5]):
        text = scrape_text(entry.link)
        articles_for_prompt += f"【ID: {i}】\nTitle: {entry.title}\nContent: {text}\n---\n"
        original_links.append({"title": entry.title, "link": entry.link})
        time.sleep(1)

    prompt = f"""
    以下の5つのニュース記事を一括で処理してください。出力は必ず以下の形式の純粋なJSON配列のみとし、Markdown記法(```json など)は一切含めないでください。
    [
        {{
            "id": 0,
            "summary": "事象の事実ベースの要約（約200字）",
            "analysis": "なぜ重要か、今後の推論・考察（約300字）"
        }}
    ]
    【ニュース記事】
    {articles_for_prompt}
    """

    ai_result_text = call_gemini_with_fallback(prompt)
    if not ai_result_text:
        return

    try:
        clean_json_str = ai_result_text.strip().lstrip("```json").rstrip("```").strip()
        analyzed_data = json.loads(clean_json_str)
    except json.JSONDecodeError:
        print("❌ JSON解析失敗")
        return

    # ① テキストメッセージ送信
    text_msg = "🌤️ **本日の主要ニュース（要約と考察）**\n\n"
    for i, link_data in enumerate(original_links):
         text_msg += f"{i+1}. [{link_data['title']}](<{link_data['link']}>)\n"
    text_msg += "\n━━━━━━━━━━━━━━━━━━\n\n"

    for data in analyzed_data:
        idx = data['id']
        title = original_links[idx]['title']
        text_msg += f"📰 **【{idx+1}】{title}**\n**📌 要約:**\n{data['summary']}\n\n**💡 考察:**\n{data['analysis']}\n\n---\n"
    send_to_discord(WEBHOOK_TEXT, text_msg)

    # ② 音声メッセージ作成と送信
    print("🎙️ ラジオ台本構築中...")
    
    # 🎤 ここから手毬の原稿 🎤
    audio_msg = "📻 **【読み上げ原稿】ニュースラジオ**\n\nおはようございます。初星学園の、月村手毬です。本日の主要ニュースをお伝えします。ラインナップはこちらの5本です。\n\n"
    for i, link_data in enumerate(original_links):
        audio_msg += f"ニュースその{i+1}。{link_data['title']}。\n"
    
    audio_msg += "\nそれでは、一つ一つのニュースについて詳しく見て、考えていきましょう。\n\n"
    for data in analyzed_data:
        idx = data['id']
        title = original_links[idx]['title']
        audio_msg += f"まずは、「{title}」のニュースです。\n{data['summary']}\n\nこの件に関してですが、\n{data['analysis']}\n\n"
        
    audio_msg += "本日のニュースは以上となります。少しでもあなたの力になれたなら、光栄です。月村手毬がお送りしました。それでは、いってらっしゃいませ。"

    # --- 音声化（一文分割＆MP3圧縮処理） ---
    final_audio_file = text_to_speech_voicevox(audio_msg, speaker=2)

    if final_audio_file:
        send_audio_to_discord(WEBHOOK_AUDIO, "📻 **本日のニュースラジオ、月村手毬です！**", final_audio_file)
        send_to_discord(WEBHOOK_AUDIO, audio_msg) 
    else:
        send_to_discord(WEBHOOK_AUDIO, audio_msg) 

    print("✅ 全ての処理とDiscord送信が完了しました！")

if __name__ == "__main__":
    main()

import os
import feedparser
import requests
from bs4 import BeautifulSoup
import google.generativeai as genai
import time
import json
import wave
import subprocess
import traceback # 👈 NEW: エラー詳細を取得する部品

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

def text_to_speech_voicevox(text, output_filename="radio.wav", speaker=20):
    """【手毬チューニング版】もち子さんを使用し、速度・ピッチ・抑揚を調整"""
    print("🎙️ 音声を生成中...（月村手毬SSチューニング版）")
    
    clean_text = text.replace("*", "").replace("#", "").replace('"', '').replace("'", "")
    
    # 📝 読み間違いの強制修正（カタカナで直接指示）
    clean_text = clean_text.replace("月村手毬", "ツキムラテマリ")
    clean_text = clean_text.replace("初星学園", "ハツボシガクエン")
    
    clean_text = clean_text.replace("。", "。\n").replace("！", "！\n").replace("？", "？\n")
    lines = [line.strip() for line in clean_text.split('\n') if line.strip()]
    wav_files = []
    
    try:
        for i, line in enumerate(lines):
            # もち子さん(ID: 20)を指定して設計図をリクエスト
            query_res = requests.post(f"http://127.0.0.1:50021/audio_query", params={"text": line, "speaker": speaker})
            if query_res.status_code != 200: continue
            
            # ⚙️ VOICEVOXの設計図を「提供された動画の音声」に完全同期させる
            query_data = query_res.json()
            query_data["speedScale"] = 0.78       # 速度: 0.78（動画特有の「ぽつりぽつり」とした余裕のあるテンポ）
            query_data["pitchScale"] = -0.02      # 音高: -0.02（声質を濁さず、ごく僅かに明るさだけを抑える）
            query_data["intonationScale"] = 0.45  # 抑揚: 0.45（ロボットにならないギリギリのラインまで感情を削る）
            
            synth_res = requests.post(f"http://127.0.0.1:50021/synthesis", params={"speaker": speaker}, json=query_data)
            if synth_res.status_code == 200:
                tmp_name = f"tmp_{i}.wav"
                with open(tmp_name, "wb") as f:
                    f.write(synth_res.content)
                wav_files.append(tmp_name)
        
        if not wav_files:
            return "ERROR: VOICEVOXから有効な音声が1つも返ってきませんでした。"

        valid_wavs = []
        for wf in wav_files:
            try:
                with wave.open(wf, 'rb') as w:
                    valid_wavs.append(wf)
            except wave.Error:
                continue
                
        if not valid_wavs:
            return "ERROR: 生成されたWAVファイルが全て破損していました。"

        with wave.open(valid_wavs[0], 'rb') as w_in:
            params = w_in.getparams()
            with wave.open(output_filename, 'wb') as w_out:
                w_out.setparams(params)
                for wf in valid_wavs:
                    with wave.open(wf, 'rb') as w:
                        w_out.writeframes(w.readframes(w.getnframes()))
        
        mp3_filename = "radio.mp3"
        print("🗜️ WAVからMP3へ圧縮中...")
        subprocess.run(["ffmpeg", "-i", output_filename, "-b:a", "128k", mp3_filename, "-y"], check=True)
        
        return mp3_filename 

    except Exception as e:
        error_trace = traceback.format_exc()
        print(f"⚠️ 音声処理エラー:\n{error_trace}")
        return f"ERROR: 音声処理中にPythonエラーが発生しました。\n{e}"

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
            "summary": "【要約】事象の要約（約200字）。※重要ルール：事実ベースの出来事は文末を「～そうです。」とし、記者や関係者の推論・意見が含まれる部分は文末を「～そうですね。」としてください。",
            "analysis": "【考察】なぜ重要か、今後の推論（約300字）。※重要ルール：断定表現（～だ。～である。～です。）は一切禁止し、少しぶっきらぼうな口調で「～そうですね。」「～じゃないですかね。」「～みたいですね。」などの推論の言い回しで結んでください。"
        }}
    ]

    【ニュース記事】
    {articles_for_prompt}
    """

    ai_result_text = call_gemini_with_fallback(prompt)
    if not ai_result_text: return

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
    
    audio_msg = "📻 **ニュース**\n\nおはようございます。……プロデューサー、ちゃんと起きましたか？月村手毬です。本日の主要ニュースをお伝えします。ラインナップはこちらの5本です。\n\n"
    for i, link_data in enumerate(original_links):
        audio_msg += f"ニュースその{i+1}。{link_data['title']}。\n"
    
    audio_msg += "\n多いですね……プロデューサー……。それでは、一つ一つのニュースについて詳しく見て、考えていきましょう。\n\n"
    for data in analyzed_data:
        idx = data['id']
        title = original_links[idx]['title']
        audio_msg += f"まずは、「{title}」のニュースです。\n{data['summary']}\n\nこの件に関してですが、\n{data['analysis']}\n\n"
        
    audio_msg += "本日のニュースは以上です。……今日も一日、よそ見しないで、私だけを見ていればいいんです。私はレッスンに行ってきます。"

    # --- 音声化処理 ---
    final_audio_result = text_to_speech_voicevox(audio_msg, speaker=20)

    # 挙動変化：成功時とエラー時でDiscordへの送信内容を明確に分ける
    if final_audio_result and not final_audio_result.startswith("ERROR:"):
        send_audio_to_discord(WEBHOOK_AUDIO, "📻 **本日のニュースラジオ、月村手毬です！**", final_audio_result)
        send_to_discord(WEBHOOK_AUDIO, audio_msg) 
    else:
        # エラーが発生した場合は、Discordに直接エラー原因を通知する
        error_reason = final_audio_result if final_audio_result else "原因不明のエラー"
        send_to_discord(WEBHOOK_AUDIO, f"⚠️ **【システム警告】音声の生成に失敗しました。**\n以下の原因により、テキストのみお送りします。\n```\n{error_reason}\n```")
        send_to_discord(WEBHOOK_AUDIO, audio_msg) 

    print("✅ 全ての処理が完了しました！")

if __name__ == "__main__":
    main()

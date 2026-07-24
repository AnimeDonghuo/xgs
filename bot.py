import os
import re
import json
import threading
import requests
import yt_dlp
from http.server import SimpleHTTPRequestHandler, HTTPServer
from bs4 import BeautifulSoup
from pyrogram import Client, filters
from pyrogram.types import Message

# --- Configuration (Loaded from Environment Variables for Security on Koyeb) ---
API_ID = int(os.environ.get("API_ID", "26826540"))
API_HASH = os.environ.get("API_HASH", "32d454f51fc7b3b3c7d51c4f80f628b5")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_bot_token_here")

app = Client("xgshort_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- Koyeb Health Check Server ---
def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    class HealthCheckHandler(SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/':
                self.send_response(200)
                self.send_header("Content-type", "text/plain")
                self.end_headers()
                self.wfile.write(b"Bot health check: OK")
            else:
                self.send_response(404)
                self.end_headers()
                
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

# Start health check server in background thread to satisfy Koyeb
threading.Thread(target=run_health_server, daemon=True).start()

# --- Helper Functions for Parsing ---

def find_urls_in_dict(d, found=None):
    if found is None:
        found = []
    if isinstance(d, dict):
        for k, v in d.items():
            if isinstance(v, str):
                if v.startswith('http') and (v.endswith('.mp4') or '.m3u8' in v or 'video' in k or 'play_url' in k):
                    found.append(v)
            else:
                find_urls_in_dict(v, found)
    elif isinstance(d, list):
        for item in d:
            find_urls_in_dict(item, found)
    return found

def find_episodes_in_dict(d):
    if isinstance(d, dict):
        for k, v in d.items():
            if k == 'episodes' and isinstance(v, list):
                return v
            res = find_episodes_in_dict(v)
            if res is not None:
                return res
    elif isinstance(d, list):
        for item in d:
            res = find_episodes_in_dict(item)
            if res is not None:
                return res
    return None

def find_series_title_in_dict(d):
    if isinstance(d, dict):
        for k, v in d.items():
            if k in ['seriesName', 'series_title', 'series_name', 'name'] and isinstance(v, str):
                return v
            res = find_series_title_in_dict(v)
            if res is not None:
                return res
    elif isinstance(d, list):
        for item in d:
            res = find_series_title_in_dict(item)
            if res is not None:
                return res
    return None

def get_direct_video_url(url):
    if '.mp4' in url or '.m3u8' in url:
        return url
    headers = {
        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
    }
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            next_script = soup.find('script', id='__NEXT_DATA__')
            if next_script:
                data = json.loads(next_script.string)
                urls = find_urls_in_dict(data)
                if urls:
                    return urls[0]
            urls = re.findall(r'https?://[^\s"\']+\.(?:mp4|m3u8)[^\s"\']*', response.text)
            if urls:
                return urls[0]
    except Exception:
        pass
    return url

def parse_xgshort_series(url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
    }
    try:
        response = requests.get(url, headers=headers, timeout=15)
    except Exception:
        return None

    if response.status_code != 200:
        return None

    soup = BeautifulSoup(response.text, 'html.parser')
    series_title = "Short Drama"
    title_tag = soup.find('title')
    if title_tag:
        series_title = title_tag.string.strip()

    episodes = []
    next_script = soup.find('script', id='__NEXT_DATA__')
    if next_script:
        try:
            data = json.loads(next_script.string)
            episodes_list = find_episodes_in_dict(data)
            series_title_extracted = find_series_title_in_dict(data)
            if series_title_extracted:
                series_title = series_title_extracted

            if episodes_list:
                for idx, ep in enumerate(episodes_list):
                    eid = ep.get('eid') or ep.get('id') or ep.get('episode_id')
                    title = ep.get('title') or ep.get('name') or f"Episode {idx+1}"
                    video_url = ep.get('video_url') or ep.get('play_url') or ep.get('url')

                    if not video_url and eid:
                        base_url = url.split('?')[0]
                        video_url = f"{base_url}?eid={eid}"

                    if video_url:
                        episodes.append({
                            'title': title,
                            'url': video_url
                        })
        except Exception:
            pass

    # Fallback if dictionary structural parsing fails
    if not episodes:
        base_url = url.split('?')[0]
        eids = re.findall(r'eid=([a-zA-Z0-9]+)', response.text)
        unique_eids = list(dict.fromkeys(eids))
        for idx, eid in enumerate(unique_eids):
            episodes.append({
                'title': f"Episode {idx+1}",
                'url': f"{base_url}?eid={eid}"
            })

    return {
        'title': series_title,
        'episodes': episodes
    }

# --- Downloading Engine ---

def download_and_save(video_url, output_path):
    """Downloads streaming link using yt-dlp to temporary local storage."""
    ydl_opts = {
        'outtmpl': output_path,
        'format': 'bestvideo+bestaudio/best',
        'merge_output_format': 'mp4',
        'quiet': True,
        'no_warnings': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([video_url])

# --- Telegram Bot Commands ---

@app.on_message(filters.command("start"))
async def start_cmd(client, message: Message):
    await message.reply_text(
        "Welcome! Send `/dl <url>` to download a single episode, "
        "or `/batch <url>` to download the entire series sequentially."
    )

@app.on_message(filters.command("dl"))
async def download_single(client, message: Message):
    if len(message.command) < 2:
        await message.reply_text("Please provide a link. Usage: `/dl <link>`")
        return

    target_url = message.command[1]
    status = await message.reply_text("Retrieving single episode data...")

    try:
        direct_url = get_direct_video_url(target_url)
        output_filename = "episode.mp4"
        
        await status.edit_text("Downloading video...")
        download_and_save(direct_url, output_filename)

        if os.path.exists(output_filename):
            await status.edit_text("Uploading file to Telegram...")
            await message.reply_video(
                video=output_filename,
                caption="Here is your requested episode."
            )
            os.remove(output_filename) # Free Koyeb storage immediately
            await status.delete()
        else:
            await status.edit_text("Could not download the episode. Check if the link is correct.")
    except Exception as e:
        await status.edit_text(f"An error occurred: {str(e)}")
        if os.path.exists("episode.mp4"):
            os.remove("episode.mp4")

@app.on_message(filters.command("batch"))
async def download_batch(client, message: Message):
    if len(message.command) < 2:
        await message.reply_text("Please provide a series link. Usage: `/batch <link>`")
        return

    series_url = message.command[1]
    status = await message.reply_text("Analyzing series configuration...")

    series_data = parse_xgshort_series(series_url)
    if not series_data or not series_data.get('episodes'):
        await status.edit_text("Could not extract series structure. Ensure the link points to a valid series page.")
        return

    episodes = series_data['episodes']
    title = series_data['title']
    total_eps = len(episodes)

    await status.edit_text(f"Found series: **{title}**\nTotal: {total_eps} episodes.\nBeginning download process...")

    for i, ep in enumerate(episodes, start=1):
        ep_title = ep['title']
        ep_url = ep['url']
        
        step_msg = await message.reply_text(f"Processing ({i}/{total_eps}): {ep_title}")
        temp_file = f"temp_ep_{i}.mp4"

        try:
            direct_url = get_direct_video_url(ep_url)
            download_and_save(direct_url, temp_file)

            if os.path.exists(temp_file):
                await step_msg.edit_text(f"Uploading ({i}/{total_eps}): {ep_title}...")
                await message.reply_video(
                    video=temp_file,
                    caption=f"Series: {title}\n{ep_title}"
                )
                os.remove(temp_file) # Crucial to free disk space immediately
                await step_msg.delete()
            else:
                await step_msg.edit_text(f"Failed to download: {ep_title}")
        except Exception as e:
            await step_msg.edit_text(f"Failed to process {ep_title}: {str(e)}")
            if os.path.exists(temp_file):
                os.remove(temp_file)

    await status.reply_text("Batch processing completed.")

app.run()

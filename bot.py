import os
import re
import json
import threading
import requests
import yt_dlp
from urllib.parse import urljoin
from http.server import SimpleHTTPRequestHandler, HTTPServer
from bs4 import BeautifulSoup
from pyrogram import Client, filters
from pyrogram.types import Message

# --- Configuration ---
API_ID = int(os.environ.get("API_ID", "1234567"))
API_HASH = os.environ.get("API_HASH", "your_api_hash_here")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_bot_token_here")

# Proxy Configuration (Optional: set PROXY in Koyeb Environment Variables)
# e.g., PROXY = "http://username:password@ip:port" or "socks5://ip:port"
PROXY = os.environ.get("PROXY", "")

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

threading.Thread(target=run_health_server, daemon=True).start()

# --- Helper Functions ---

def get_requests_proxies():
    if PROXY:
        return {
            "http": PROXY,
            "https": PROXY
        }
    return None

def find_urls_in_dict_recursively(d, base_url, found=None):
    if found is None:
        found = []
    if isinstance(d, dict):
        for k, v in d.items():
            k_lower = k.lower()
            if isinstance(v, str):
                if v.startswith('http://') or v.startswith('https://') or v.startswith('/'):
                    if any(x in k_lower for x in ['video', 'play', 'src', 'stream', 'source', 'url', 'm3u8', 'mp4', 'file']):
                        abs_url = urljoin(base_url, v)
                        if not any(abs_url.lower().endswith(ext) or ext + '?' in abs_url.lower() for ext in ['.png', '.jpg', '.jpeg', '.webp', '.svg', '.gif', '.css', '.js']):
                            found.append((abs_url, k))
            else:
                find_urls_in_dict_recursively(v, base_url, found)
    elif isinstance(d, list):
        for item in d:
            find_urls_in_dict_recursively(item, base_url, found)
    return found

def get_direct_video_url(url, debug_info=None):
    if '.mp4' in url or '.m3u8' in url:
        return url, "Direct Input Link"
        
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://m.xgshort.com/',
        'Origin': 'https://m.xgshort.com'
    }
    
    proxies = get_requests_proxies()
    
    try:
        # Disable default automatic redirects to catch anti-bot redirects to t.me
        response = requests.get(url, headers=headers, timeout=10, allow_redirects=False, proxies=proxies)
        
        if debug_info is not None:
            debug_info['status_code'] = response.status_code
            
        # Manually trace redirections to catch blocks early
        redirect_count = 0
        while response.status_code in [301, 302, 303, 307, 308] and redirect_count < 5:
            redirect_url = response.headers.get('Location', '')
            if not redirect_url:
                break
                
            redirect_url = urljoin(url, redirect_url)
            
            # If redirected to Telegram, stop the request and report the datacenter block
            if 't.me' in redirect_url:
                return None, (
                    f"Anti-bot redirect detected! The website redirected the bot to their Telegram channel ({redirect_url}). "
                    "This occurs when the website blocks hosting/datacenter IP ranges (like Koyeb). "
                    "Setting up a residential or clean HTTP proxy under the 'PROXY' environment variable on Koyeb is recommended to bypass this."
                )
                
            url = redirect_url
            response = requests.get(url, headers=headers, timeout=10, allow_redirects=False, proxies=proxies)
            redirect_count += 1
            
        if debug_info is not None:
            debug_info['status_code'] = response.status_code
            debug_info['html_preview'] = response.text[:400]
            
        if response.status_code != 200:
            return None, f"HTTP Status {response.status_code}"
            
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Strategy 1: Check native HTML5 <video> tags
        video_tag = soup.find('video')
        if video_tag:
            src = video_tag.get('src')
            if src:
                return urljoin(url, src), "HTML5 Video Tag"
                
        # Strategy 2: Check standard <source> elements
        source_tag = soup.find('source')
        if source_tag:
            src = source_tag.get('src')
            if src:
                return urljoin(url, src), "HTML5 Source Tag"
                
        # Strategy 3: Check NextJS React hydrated structures
        next_script = soup.find('script', id='__NEXT_DATA__')
        if next_script:
            try:
                data = json.loads(next_script.string)
                found_pairs = find_urls_in_dict_recursively(data, url)
                if found_pairs:
                    return found_pairs[0][0], f"NextJS State Key: {found_pairs[0][1]}"
            except Exception:
                pass
                
        # Strategy 4: Raw text javascript variables pattern scanner
        for script in soup.find_all('script'):
            if script.string:
                patterns = [
                    r'["\'](?:video|play|src|source|stream|url)["\']\s*[:=]\s*["\'](https?://[^"\']+|/[^"\']+)["\']',
                    r'(?:video|play|src|source|stream|url)\s*=\s*["\'](https?://[^"\']+|/[^"\']+)["\']'
                ]
                for pattern in patterns:
                    matches = re.findall(pattern, script.string, re.IGNORECASE)
                    for match in matches:
                        abs_url = urljoin(url, match)
                        if not any(abs_url.lower().endswith(ext) or ext + '?' in abs_url.lower() for ext in ['.png', '.jpg', '.jpeg', '.webp', '.svg', '.gif', '.css', '.js']):
                            return abs_url, "Script Variable Extract"
                            
    except Exception as e:
        return None, f"Network Exception: {str(e)}"
        
    return None, "Unable to extract playable media stream"

def parse_xgshort_series(url, debug_info=None):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://m.xgshort.com/',
        'Origin': 'https://m.xgshort.com'
    }
    proxies = get_requests_proxies()
    try:
        response = requests.get(url, headers=headers, timeout=10, allow_redirects=False, proxies=proxies)
        
        # Trace redirect to prevent timeouts on parse
        redirect_count = 0
        while response.status_code in [301, 302, 303, 307, 308] and redirect_count < 5:
            redirect_url = response.headers.get('Location', '')
            if not redirect_url:
                break
            redirect_url = urljoin(url, redirect_url)
            if 't.me' in redirect_url:
                if debug_info is not None:
                    debug_info['status_code'] = response.status_code
                    debug_info['html_preview'] = f"Anti-bot redirect to Telegram ({redirect_url}) detected. Please set up a PROXY environment variable on Koyeb."
                return None
            url = redirect_url
            response = requests.get(url, headers=headers, timeout=10, allow_redirects=False, proxies=proxies)
            redirect_count += 1

        if debug_info is not None:
            debug_info['status_code'] = response.status_code
            debug_info['html_preview'] = response.text[:400]
    except Exception as e:
        if debug_info is not None:
            debug_info['status_code'] = 'Exception'
            debug_info['html_preview'] = str(e)
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

    if not episodes:
        base_url = url.split('?')[0]
        eids = re.findall(r'eid=([a-zA-Z0-9_-]+)', response.text)
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

# --- Downloading Engine ---

def download_and_save(video_url, output_path):
    ydl_opts = {
        'outtmpl': output_path,
        'format': 'bestvideo+bestaudio/best',
        'merge_output_format': 'mp4',
        'quiet': True,
        'no_warnings': True,
    }
    if PROXY:
        ydl_opts['proxy'] = PROXY
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
    status = await message.reply_text("Analyzing web page elements...")

    debug_info = {}
    direct_url, method = get_direct_video_url(target_url, debug_info)

    if not direct_url:
        sc = debug_info.get('status_code', 'Unknown')
        preview = debug_info.get('html_preview', 'No content received').replace('<', '&lt;').replace('>', '&gt;')
        err_msg = (
            f"❌ **Failed to extract video stream!**\n\n"
            f"**Reason:** {method}\n"
            f"**HTTP Status Code:** {sc}\n\n"
            f"**HTML Preview:**\n`{preview}`"
        )
        await status.edit_text(err_msg)
        return

    output_filename = "episode.mp4"
    await status.edit_text(f"Detected stream via **{method}**.\nDownloading media file...")

    try:
        download_and_save(direct_url, output_filename)

        if os.path.exists(output_filename):
            await status.edit_text("Uploading file to Telegram...")
            await message.reply_video(
                video=output_filename,
                caption=f"Source: [Link]({target_url})\nMethod: {method}"
            )
            os.remove(output_filename) 
            await status.delete()
        else:
            await status.edit_text("Download completed but physical file was not created.")
    except Exception as e:
        await status.edit_text(f"An error occurred during download: {str(e)}")
        if os.path.exists(output_filename):
            os.remove(output_filename)

@app.on_message(filters.command("batch"))
async def download_batch(client, message: Message):
    if len(message.command) < 2:
        await message.reply_text("Please provide a series link. Usage: `/batch <link>`")
        return

    series_url = message.command[1]
    status = await message.reply_text("Analyzing series configuration...")

    debug_info = {}
    series_data = parse_xgshort_series(series_url, debug_info)
    
    if not series_data or not series_data.get('episodes'):
        sc = debug_info.get('status_code', 'Unknown')
        preview = debug_info.get('html_preview', 'No content received').replace('<', '&lt;').replace('>', '&gt;')
        err_msg = (
            f"❌ **Could not extract series structure.**\n\n"
            f"**HTTP Status Code:** {sc}\n\n"
            f"**HTML Preview:**\n`{preview}`"
        )
        await status.edit_text(err_msg)
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
            ep_debug = {}
            direct_url, method = get_direct_video_url(ep_url, ep_debug)
            
            if not direct_url:
                await step_msg.edit_text(f"❌ Failed to extract stream link for {ep_title}. skipping.")
                continue

            download_and_save(direct_url, temp_file)

            if os.path.exists(temp_file):
                await step_msg.edit_text(f"Uploading ({i}/{total_eps}): {ep_title}...")
                await message.reply_video(
                    video=temp_file,
                    caption=f"Series: {title}\n{ep_title}"
                )
                os.remove(temp_file) 
                await step_msg.delete()
            else:
                await step_msg.edit_text(f"Failed to compile: {ep_title}")
        except Exception as e:
            await step_msg.edit_text(f"Failed to process {ep_title}: {str(e)}")
            if os.path.exists(temp_file):
                os.remove(temp_file)

    await status.reply_text("Batch processing completed.")

app.run()

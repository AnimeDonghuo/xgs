import os
import re
import json
import asyncio
import threading
import yt_dlp
from urllib.parse import urljoin
from http.server import SimpleHTTPRequestHandler, HTTPServer
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from pyrogram import Client, filters
from pyrogram.types import Message

# --- Configuration ---
API_ID = int(os.environ.get("API_ID", "1234567"))
API_HASH = os.environ.get("API_HASH", "your_api_hash_here")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_bot_token_here")

# Optional Proxy Configuration
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

def get_playwright_proxy():
    if PROXY:
        return {
            "server": PROXY
        }
    return None

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

# --- Async Extraction Engine using Playwright ---

async def get_direct_video_url_via_playwright(url, debug_info=None):
    """Launches headless Chromium asynchronously, runs JS, and captures the video stream URL."""
    if '.mp4' in url or '.m3u8' in url:
        return url, "Direct Input Link"
        
    video_url = None
    proxy_config = get_playwright_proxy()
    
    async with async_playwright() as p:
        launch_kwargs = {
            "headless": True,
            "args": ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu']
        }
        if proxy_config:
            launch_kwargs["proxy"] = proxy_config
            
        try:
            browser = await p.chromium.launch(**launch_kwargs)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
            )
            page = await context.new_page()
            
            # Intercept background network responses to catch dynamic video stream links
            async def handle_response(response):
                nonlocal video_url
                r_url = response.url
                if any(x in r_url.lower() for x in ['.m3u8', '.mp4', 'stream', 'video_url', 'play_url']):
                    if not any(ext in r_url.lower() for ext in ['.png', '.jpg', '.jpeg', '.webp', '.svg', '.gif', '.css', '.js']):
                        video_url = r_url
                        
            page.on("response", handle_response)
            
            try:
                response = await page.goto(url, wait_until="networkidle", timeout=25000)
                if response and response.status in [301, 302, 307, 308]:
                    loc = await response.header_value('location')
                    if loc and 't.me' in loc:
                        await browser.close()
                        return None, "Anti-bot redirect to Telegram. Datacenter IP is blocked. Proxy is required."
                
                # Wait for the player scripts to execute and load media sources
                await page.wait_for_timeout(5000)
            except Exception as e:
                if debug_info is not None:
                    debug_info['html_preview'] = f"Navigation timeout/error: {str(e)}"
                    
            # Fallback 1: Extract from DOM <video> element
            if not video_url:
                try:
                    video_src = await page.eval_on_selector("video", "el => el.src")
                    if video_src and not video_src.startswith("blob:"):
                        video_url = video_src
                except Exception:
                    pass
                    
            # Fallback 2: Extract from <video><source> tag
            if not video_url:
                try:
                    video_src = await page.eval_on_selector("video source", "el => el.src")
                    if video_src and not video_src.startswith("blob:"):
                        video_url = video_src
                except Exception:
                    pass
                    
            if debug_info is not None and not video_url:
                debug_info['html_preview'] = (await page.content())[:400]
                
            await browser.close()
        except Exception as e:
            return None, f"Playwright Engine Error: {str(e)}"
            
    if video_url:
        return video_url, "Playwright Browser Emulation"
    return None, "No active media stream found on page"

async def parse_xgshort_series_via_playwright(url, debug_info=None):
    """Loads the SPA series page and extracts dynamic list of episodes."""
    episodes = []
    series_title = "Short Drama"
    proxy_config = get_playwright_proxy()
    
    async with async_playwright() as p:
        launch_kwargs = {
            "headless": True,
            "args": ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu']
        }
        if proxy_config:
            launch_kwargs["proxy"] = proxy_config
            
        try:
            browser = await p.chromium.launch(**launch_kwargs)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
            )
            page = await context.new_page()
            
            # Intercept API responses containing episode list JSON data
            async def handle_response(response):
                nonlocal episodes, series_title
                try:
                    if "application/json" in response.headers.get("content-type", ""):
                        data = await response.json()
                        found_eps = find_episodes_in_dict(data)
                        found_title = find_series_title_in_dict(data)
                        if found_title:
                            series_title = found_title
                        if found_eps:
                            for idx, ep in enumerate(found_eps):
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

            page.on("response", handle_response)
            
            try:
                await page.goto(url, wait_until="networkidle", timeout=25000)
                await page.wait_for_timeout(5000) # Wait for network execution
            except Exception:
                pass
                
            # Fallback: Scrape the parsed DOM links containing 'eid='
            if not episodes:
                try:
                    html_content = await page.content()
                    soup = BeautifulSoup(html_content, 'html.parser')
                    title_tag = soup.find('title')
                    if title_tag:
                        series_title = title_tag.string.strip()
                        
                    links = await page.eval_on_selector_all("a", "elements => elements.map(el => el.href)")
                    unique_links = list(dict.fromkeys(links))
                    idx = 1
                    for link in unique_links:
                        if 'eid=' in link:
                            episodes.append({
                                'title': f"Episode {idx}",
                                'url': link
                            })
                            idx += 1
                except Exception:
                    pass
                    
            await browser.close()
        except Exception as e:
            if debug_info is not None:
                debug_info['html_preview'] = f"Playwright Error: {str(e)}"
                
    return {
        'title': series_title,
        'episodes': episodes
    }

# --- Downloading Engine ---

def download_and_save(video_url, output_path):
    """Synchronous downloader called via background worker to avoid blocking the event loop."""
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

async def download_and_save_async(video_url, output_path):
    """Wraps synchronous yt-dlp call in a non-blocking asyncio thread worker."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, download_and_save, video_url, output_path)

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
    status = await message.reply_text("Opening headless browser asynchronously...")

    debug_info = {}
    direct_url, method = await get_direct_video_url_via_playwright(target_url, debug_info)

    if not direct_url:
        preview = debug_info.get('html_preview', 'No content captured').replace('<', '&lt;').replace('>', '&gt;')
        err_msg = (
            f"❌ **Failed to extract video stream!**\n\n"
            f"**Reason:** {method}\n\n"
            f"**Debug Log:**\n`{preview}`"
        )
        await status.edit_text(err_msg)
        return

    output_filename = "episode.mp4"
    await status.edit_text(f"Detected stream via **{method}**.\nDownloading media file in background...")

    try:
        await download_and_save_async(direct_url, output_filename)

        if os.path.exists(output_filename):
            await status.edit_text("Uploading file to Telegram...")
            await message.reply_video(
                video=output_filename,
                caption=f"Source: [Link]({target_url})\nMethod: {method}"
            )
            os.remove(output_filename) 
            await status.delete()
        else:
            await status.edit_text("Download succeeded but output file was not created.")
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
    status = await message.reply_text("Opening headless browser to read series data...")

    debug_info = {}
    series_data = await parse_xgshort_series_via_playwright(series_url, debug_info)
    
    if not series_data or not series_data.get('episodes'):
        preview = debug_info.get('html_preview', 'No content captured').replace('<', '&lt;').replace('>', '&gt;')
        err_msg = (
            f"❌ **Could not extract series structure.**\n\n"
            f"**Debug Log:**\n`{preview}`"
        )
        await status.edit_text(err_msg)
        return

    episodes = series_data['episodes']
    title = series_data['title']
    total_eps = len(episodes)

    await status.edit_text(f"Found series: **{title}**\nTotal: {total_eps} episodes.\nBeginning sequential download...")

    for i, ep in enumerate(episodes, start=1):
        ep_title = ep['title']
        ep_url = ep['url']
        
        step_msg = await message.reply_text(f"Processing ({i}/{total_eps}): {ep_title}")
        temp_file = f"temp_ep_{i}.mp4"

        try:
            ep_debug = {}
            direct_url, method = await get_direct_video_url_via_playwright(ep_url, ep_debug)
            
            if not direct_url:
                await step_msg.edit_text(f"❌ Failed to extract stream link for {ep_title}. Skipping.")
                continue

            await download_and_save_async(direct_url, temp_file)

            if os.path.exists(temp_file):
                await step_msg.edit_text(f"Uploading ({i}/{total_eps}): {ep_title}...")
                await message.reply_video(
                    video=temp_file,
                    caption=f"Series: {title}\n{ep_title}"
                )
                os.remove(temp_file) 
                await step_msg.delete()
            else:
                await step_msg.edit_text(f"Failed to download: {ep_title}")
        except Exception as e:
            await step_msg.edit_text(f"Failed to process {ep_title}: {str(e)}")
            if os.path.exists(temp_file):
                os.remove(temp_file)

    await status.reply_text("Batch processing completed.")

app.run()

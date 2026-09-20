Import os
import re
import glob
import time
import shutil
import asyncio
import requests
import subprocess
from collections import defaultdict
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
from magnet2torrent import Magnet2Torrent
from natsort import natsorted

FILEMIRAGE_API_TOKEN = os.getenv("FILEMIRAGE_TOKEN", "9QQH-DGES-CWQZ-FXNV")
LINK_URL = os.getenv("LINK_URL", "https://fhpsbwpqtteuchtgyqlj.supabase.co/functions/v1/page-download/eb711bc9-9eab-4a10-b985-bf2f27bc2d58")

# STEP 1: PROCESS LINKS
async def process_links():
    print("🧲 Processing links (Converting magnets & Queuing direct links)...", flush=True)
    os.makedirs("downloads", exist_ok=True)
    os.makedirs("torrents", exist_ok=True)
    
    try:
        ks = requests.get(LINK_URL, timeout=10).text
        if "STOP.ALL.TORRENTS" in ks:
            print("🛑 Global kill switch active.", flush=True)
            return False
            
        direct_links = []
        for i, link in enumerate(ks.splitlines()):
            link = link.strip()
            if link and not link.startswith('#') and not link.endswith(' NO'):
                if link.startswith('magnet:'):
                    print(f"📥 Converting magnet: {link[:60]}...", flush=True)
                    try:
                        m2t = Magnet2Torrent(link)
                        filename, torrent_data = await asyncio.wait_for(m2t.retrieve_torrent(), timeout=30)
                        torrent_path = os.path.join("torrents", f"{filename}.torrent")
                        with open(torrent_path, "wb") as f:
                            f.write(torrent_data)
                        print(f"✅ Saved torrent: {torrent_path}", flush=True)
                    except Exception as e:
                        print(f"⚠️ Magnet conversion failed ({e}). Queuing raw magnet!", flush=True)
                        direct_links.append(link)
                elif link.startswith('http'):
                    if link.endswith('.torrent'):
                        try:
                            tor_data = requests.get(link, timeout=15).content
                            parsed = urlparse(link)
                            filename = os.path.basename(parsed.path) or f"download_{i}.torrent"
                            with open(os.path.join('torrents', filename), 'wb') as tf:
                                tf.write(tor_data)
                            print(f"✅ Downloaded .torrent file: {filename}", flush=True)
                        except Exception as e:
                            print(f"❌ Failed torrent download ({e}): {link}", flush=True)
                    else:
                        direct_links.append(link)
                        print(f"🔗 Queued direct HTTP download: {link}", flush=True)
                        
        if direct_links:
            with open('direct_links.txt', 'w') as f:
                f.write('\n'.join(direct_links))
        return True
    except Exception as e:
        print(f"❌ Error processing links: {e}", flush=True)
        return False

# STEP 2: ARIA2 DOWNLOADS
def get_best_trackers():
    fallback_trackers = [
        "udp://tracker.openbittorrent.com:80/announce",
        "udp://tracker.opentrackr.org:1337/announce",
        "udp://tracker.torrent.eu.org:451/announce",
        "udp://exodus.desync.com:6969/announce",
        "udp://open.stealth.si:80/announce"
    ]
    try:
        url = "https://raw.githubusercontent.com/ngosang/trackerslist/master/trackers_best.txt"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            fetched = [line.strip() for line in res.text.splitlines() if line.strip()]
            if fetched:
                return ",".join(fetched)
    except Exception:
        pass
    return ",".join(fallback_trackers)

async def download_target(target, sem, trackers, stuck_timeout=20, max_retries=5):
    async with sem:
        for attempt in range(1, max_retries + 1):
            print(f"📥 [Attempt {attempt}/{max_retries}] Starting: {target[:80]}", flush=True)
            cmd = [
                "aria2c", "--console-log-level=notice", "--summary-interval=2",
                "--dir=downloads", "--seed-time=0", "--file-allocation=none",
                "--enable-dht=true", "--enable-peer-exchange=true", "--follow-torrent=mem",
                "-s16", "-x16", "--min-split-size=1M", "--max-connection-per-server=16",
                "--bt-max-peers=128", "--bt-tracker-connect-timeout=5", "--bt-tracker-timeout=5",
                f"--bt-tracker={trackers}", target
            ]
            
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            download_started = False
            start_time = time.time()
            
            while proc.returncode is None:
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=2.0)
                    if not line: break
                    line_str = line.decode('utf-8', errors='ignore').strip()
                    if line_str and not download_started and re.search(r'\[#\w+\s+([0-9\.]+)([KMGTP]?i?B)/', line_str):
                        download_started = True
                        print(f"🚀 Download started for: {os.path.basename(target)}", flush=True)
                    if not download_started and (time.time() - start_time) > stuck_timeout:
                        try: proc.kill()
                        except Exception: pass
                        await proc.wait()
                        break
                except asyncio.TimeoutError:
                    if not download_started and (time.time() - start_time) > stuck_timeout:
                        try: proc.kill()
                        except Exception: pass
                        await proc.wait()
                        break

            await proc.wait()
            if proc.returncode == 0:
                print(f"✅ Downloaded: {target[:80]}", flush=True)
                return target

async def run_downloads():
    print("🚀 Starting aria2c downloads...", flush=True)
    targets = natsorted(glob.glob("torrents/*.torrent"))
    if os.path.exists("direct_links.txt"):
        with open("direct_links.txt", "r") as f:
            targets.extend(natsorted([line.strip() for line in f if line.strip()]))
            
    if not targets:
        print("⚠️ No torrents or links found to download.", flush=True)
        return

    trackers = get_best_trackers()
    sem = asyncio.Semaphore(16)
    tasks = [download_target(t, sem, trackers) for t in targets]
    await asyncio.gather(*tasks)

# STEP 3: NATSORT SMART ZIPPING
def get_dir_size(p):
    return sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fn in os.walk(p) for f in fn)

def create_7z_group(group_name, file_paths, base_dir, max_bytes_limit):
    group_dir = os.path.join(base_dir, group_name)
    os.makedirs(group_dir, exist_ok=True)
    for p in file_paths:
        dst = os.path.join(group_dir, os.path.basename(p))
        if p != dst and not os.path.exists(dst):
            shutil.move(p, dst)
    
    zip_name = f"{group_name}.zip"
    orig = os.getcwd()
    os.chdir(base_dir)
    cmd = ["7z", "a", "-mx0", "-mmt=on", zip_name, group_name]
    if get_dir_size(group_dir) > max_bytes_limit:
        cmd.insert(2, "-v5900m")
    subprocess.run(cmd, check=True)
    os.chdir(orig)
    shutil.rmtree(group_dir)

def zip_files():
    print("📦 Running Multi-Threaded Smart Auto-Group Zipping...", flush=True)
    folder = "downloads"
    video_ext = ('.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v')
    max_bytes = 10000 * 1024 * 1024

    all_videos = natsorted([os.path.join(r, f) for r, _, files in os.walk(folder) for f in files if f.lower().endswith(video_ext)])
    series_regex = re.compile(r'(?i)(?:^(.*?)[.\s_-]+)?S(\d{1,2})(?:[EX\-]|\b)')
    series_groups = defaultdict(list)
    unmatched = []

    for vid_path in all_videos:
        match = series_regex.search(os.path.basename(vid_path)) or series_regex.search(os.path.basename(os.path.dirname(vid_path)))
        if match:
            raw_title, s_num = match.group(1) or "Season", match.group(2)
            group_key = f"{raw_title.strip('. -_').lower()}_S{s_num}"
            series_groups[group_key].append(vid_path)
        else:
            unmatched.append(vid_path)

    for group_key in natsorted(series_groups.keys()):
        vids = natsorted(series_groups[group_key])
        if len(vids) > 3:
            first_stem = os.path.splitext(os.path.basename(vids[0]))[0]
            create_7z_group(first_stem, vids, folder, max_bytes)
        else:
            unmatched.extend(vids)

    unmatched = natsorted(unmatched)
    if len(unmatched) > 3:
        first_stem = os.path.splitext(os.path.basename(unmatched[0]))[0]
        create_7z_group(f"Batch_{first_stem}", unmatched, folder, max_bytes)

# STEP 4: FILEMIRAGE UPLOAD
def upload_single_file(file_path, server):
    filename = os.path.basename(file_path)
    print(f"⬆️ Uploading: {filename}", flush=True)
    curl_cmd = [
        "curl", "-X", "POST", f"{server}/upload.php",
        "-H", f"Authorization: Bearer {FILEMIRAGE_API_TOKEN}",
        "-F", f"file=@{file_path}", "--max-time", "3600"
    ]
    res = subprocess.run(curl_cmd, capture_output=True, text=True)
    if res.returncode == 0:
        print(f"✅ Finished uploading {filename}", flush=True)

def run_uploads():
    print("📤 Running Uploads to Filemirage...", flush=True)
    try:
        srv_res = requests.get("https://filemirage.com/api/servers", timeout=10).json()
        server = srv_res['data']['server']
    except Exception as e:
        print(f"Failed server fetch: {e}")
        return

    upload_queue = []
    for root, _, files in os.walk('downloads'):
        for f in files:
            if not any(ext in f for ext in [".!qB", ".part", ".aria2"]):
                upload_queue.append(os.path.join(root, f))
                
    upload_queue = natsorted(upload_queue)
    if upload_queue:
        with ThreadPoolExecutor(max_workers=4) as executor:
            executor.map(lambda f: upload_single_file(f, server), upload_queue)

if __name__ == "__main__":
    if asyncio.run(process_links()):
        asyncio.run(run_downloads())
        zip_files()
        run_uploads()


How to build this as android app on expo.dev

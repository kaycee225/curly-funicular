import os
import re
import glob
import time
import shutil
import asyncio
import requests
import subprocess
from datetime import datetime
from collections import defaultdict
from urllib.parse import urlparse, quote
from concurrent.futures import ThreadPoolExecutor
from magnet2torrent import Magnet2Torrent
from natsort import natsorted

# ENVIRONMENT VARIABLES
BUZZHEAVIER_ACCOUNT_ID = os.getenv("BUZZHEAVIER_ACCOUNT_ID", os.getenv("BUZZHEAVIER_TOKEN", "FINS3KA5OL68CPW5MEYD"))

# ADD YOUR PARENT ID / DIRECTORY ID HERE 👇
BUZZHEAVIER_FOLDER_ID = os.getenv("BUZZHEAVIER_FOLDER_ID", "roj8pv25ry0k")

LINK_URL = os.getenv("LINK_URL", "https://fhpsbwpqtteuchtgyqlj.supabase.co/functions/v1/page-download/eb711bc9-9eab-4a10-b985-bf2f27bc2d58")

video_exts = ('.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v')
ignored_exts = ('.txt', '.nfo', '.jpg', '.jpeg', '.png')

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
        "udp://tracker.torrent.eu.org:451/announce"
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

def cleanup_target_downloads(target):
    base_name = os.path.splitext(os.path.basename(target))[0]
    for p in glob.glob(os.path.join("downloads", f"{base_name}*")):
        try:
            if os.path.isfile(p) or os.path.islink(p):
                os.remove(p)
            elif os.path.isdir(p):
                shutil.rmtree(p)
        except Exception:
            pass

async def download_target(target, sem, trackers, stuck_timeout=20, max_retries=5):
    async with sem:
        fname = os.path.basename(target)
        for attempt in range(1, max_retries + 1):
            cmd = [
                "aria2c", "--console-log-level=notice", "--summary-interval=2",
                "--dir=downloads", "--seed-time=0", "--file-allocation=none",
                "--enable-dht=true", "--enable-peer-exchange=true", "--follow-torrent=mem",
                "-s16", "-x16", "--min-split-size=1M", "--max-connection-per-server=16",
                "--bt-max-peers=128", "--bt-tracker-connect-timeout=5", "--bt-tracker-timeout=5",
                f"--bt-tracker={trackers}", target
            ]
            
            proc = await asyncio.create_subprocess_exec(
                *cmd, 
                stdout=asyncio.subprocess.PIPE, 
                stderr=asyncio.subprocess.STDOUT
            )
            download_started = False
            start_time = time.time()
            
            while proc.returncode is None:
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=2.0)
                    if not line: break
                    line_str = line.decode('utf-8', errors='ignore').strip()
                    
                    if line_str:
                        match = re.search(r'\[#\w+\s+([0-9\.]+)\s*([KMGTP]?i?B)/', line_str)
                        if match:
                            downloaded_val = float(match.group(1))
                            if downloaded_val > 0 and not download_started:
                                download_started = True
                                print(f"\n🚀 Download active: {fname} (Attempt {attempt}/{max_retries})", flush=True)

                        if download_started and (line_str.startswith('[#') or 'ETA:' in line_str):
                            print(f"📊 [{fname[:25]}] {line_str}", flush=True)
                            
                    if download_started:
                        start_time = time.time()
                        
                    if not download_started and (time.time() - start_time) > stuck_timeout:
                        try: proc.kill()
                        except Exception: pass
                        await proc.wait()
                        cleanup_target_downloads(target)
                        break
                except asyncio.TimeoutError:
                    if not download_started and (time.time() - start_time) > stuck_timeout:
                        try: proc.kill()
                        except Exception: pass
                        await proc.wait()
                        cleanup_target_downloads(target)
                        break

            await proc.wait()
            if proc.returncode == 0 and download_started:
                print(f"✅ Download complete: {fname}", flush=True)
                return target
            else:
                cleanup_target_downloads(target)

async def run_downloads():
    print("🚀 Starting aria2c downloads...", flush=True)
    targets = natsorted(glob.glob("torrents/*.torrent"))
    if os.path.exists("direct_links.txt"):
        with open("direct_links.txt", "r") as f:
            targets.extend(natsorted([line.strip() for line in f if line.strip()]))
            
    if not targets:
        return

    trackers = get_best_trackers()
    sem = asyncio.Semaphore(16)
    tasks = [download_target(t, sem, trackers) for t in targets]
    await asyncio.gather(*tasks)

# STEP 3: USER-PROVIDED AUTO-GROUP ZIPPING
def zip_and_organize():
    print("📦 Running Multi-Threaded Smart Auto-Group Zipping & Splitting...", flush=True)
    folder = "downloads"
    video_ext = ('.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v')
    media_ext = video_ext + ('.srt', '.ass', '.vtt', '.sub')
    max_bytes = 10000 * 1024 * 1024

    def get_dir_size(p):
        return sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fn in os.walk(p) for f in fn)

    def create_7z_group(group_name, file_paths, base_dir, max_bytes_limit):
        group_dir = os.path.join(base_dir, group_name)
        os.makedirs(group_dir, exist_ok=True)
        
        files_to_move = list(file_paths)
        for p in file_paths:
            base_stem = os.path.splitext(p)[0]
            for sub_ext in ('.srt', '.ass', '.vtt', '.sub'):
                sub_file = base_stem + sub_ext
                if os.path.exists(sub_file) and sub_file not in files_to_move:
                    files_to_move.append(sub_file)

        for p in files_to_move:
            dst = os.path.join(group_dir, os.path.basename(p))
            if p != dst and not os.path.exists(dst):
                shutil.move(p, dst)
        
        folder_size = get_dir_size(group_dir)
        zip_name = f"{group_name}.zip"
        orig = os.getcwd()
        os.chdir(base_dir)
        cmd = ["7z", "a", "-mx0", "-mmt=on", zip_name, group_name]
        if folder_size > max_bytes_limit:
            cmd.insert(2, "-v5900m")
        subprocess.run(cmd, check=True)
        os.chdir(orig)
        shutil.rmtree(group_dir)

    if os.path.exists(folder):
        for item in natsorted(os.listdir(folder)):
            item_path = os.path.join(folder, item)
            if os.path.isdir(item_path):
                vids = natsorted([
                    os.path.join(r, f) for r, _, files in os.walk(item_path)
                    for f in files if f.lower().endswith(video_ext)
                ])
                if len(vids) > 3:
                    print(f"📦 Zipping folder: {item}", flush=True)
                    folder_size = get_dir_size(item_path)
                    orig = os.getcwd()
                    os.chdir(folder)
                    zip_name = f"{item}.zip"
                    cmd = ["7z", "a", "-mx0", "-mmt=on", zip_name, item]
                    if folder_size > max_bytes:
                        cmd.insert(2, "-v5900m")
                    subprocess.run(cmd, check=True)
                    os.chdir(orig)
                    shutil.rmtree(item_path)

    all_videos = []
    for r, _, files in os.walk(folder):
        for f in files:
            if f.lower().endswith(video_ext):
                all_videos.append(os.path.join(r, f))

    all_videos = natsorted(all_videos)

    series_regex = re.compile(r'(?i)(?:^(.*?)[.\s_-]+)?S(\d{1,2})(?:[EX\-]|\b)')
    
    series_groups = defaultdict(list)
    unmatched_videos = []

    for vid_path in all_videos:
        vid_name = os.path.basename(vid_path)
        parent_name = os.path.basename(os.path.dirname(vid_path))
        match = series_regex.search(vid_name) or series_regex.search(parent_name)
        
        if match:
            raw_title = match.group(1) or "Season"
            s_num = match.group(2)
            group_key = f"{raw_title.strip('. -_').lower()}_S{s_num}"
            series_groups[group_key].append(vid_path)
        else:
            unmatched_videos.append(vid_path)

    for group_key in natsorted(series_groups.keys()):
        vids = natsorted(series_groups[group_key])
        if len(vids) > 3:
            first_stem = os.path.splitext(os.path.basename(vids[0]))[0]
            create_7z_group(first_stem, vids, folder, max_bytes)
        else:
            unmatched_videos.extend(vids)

    unmatched_videos = natsorted(unmatched_videos)

    if len(unmatched_videos) > 3:
        first_stem = os.path.splitext(os.path.basename(unmatched_videos[0]))[0]
        # 'Batch_' prefix removed to adhere to the naming rule
        create_7z_group(first_stem, unmatched_videos, folder, max_bytes)

    for r, dirs, files in os.walk(folder, topdown=False):
        if r == folder: continue
        for f in natsorted(files):
            if f.lower().endswith(media_ext):
                src = os.path.join(r, f)
                dst = os.path.join(folder, f)
                if not os.path.exists(dst): 
                    shutil.move(src, dst)
        shutil.rmtree(r, ignore_errors=True)

# STEP 4: BUZZHEAVIER UPLOAD
def get_buzzheavier_root_id():
    if BUZZHEAVIER_FOLDER_ID and BUZZHEAVIER_FOLDER_ID != "YOUR_DIRECTORY_ID_HERE":
        return BUZZHEAVIER_FOLDER_ID
    if not BUZZHEAVIER_ACCOUNT_ID:
        return ""
    try:
        url = "https://buzzheavier.com/api/fs"
        headers = {"Authorization": f"Bearer {BUZZHEAVIER_ACCOUNT_ID}"}
        res = requests.get(url, headers=headers, timeout=10).json()
        if "id" in res:
            return res["id"]
        elif isinstance(res.get("data"), dict) and "id" in res["data"]:
            return res["data"]["id"]
    except Exception as e:
        print(f"⚠️ Failed fetching Buzzheavier root directory: {e}", flush=True)
    return ""

def upload_single_file(file_path, folder_id):
    filename = os.path.basename(file_path)
    size_mb = os.path.getsize(file_path) / (1024 * 1024)
    print(f"\n⬆️ Uploading to Buzzheavier: {filename} ({size_mb:.2f} MB)", flush=True)

    encoded_filename = quote(filename)
    if folder_id:
        upload_url = f"https://w.buzzheavier.com/{folder_id}/{encoded_filename}"
    else:
        upload_url = f"https://w.buzzheavier.com/{encoded_filename}"

    curl_cmd = ["curl", "-#o", "-", "-T", file_path]
    if BUZZHEAVIER_ACCOUNT_ID:
        curl_cmd.extend(["-H", f"Authorization: Bearer {BUZZHEAVIER_ACCOUNT_ID}"])
    curl_cmd.append(upload_url)

    res = subprocess.run(curl_cmd)
    if res.returncode == 0:
        print(f"\n✅ Finished uploading: {filename}", flush=True)
    else:
        print(f"\n❌ Upload failed for {filename} (curl exit code: {res.returncode})", flush=True)

def run_uploads():
    print("📤 Running Uploads to Buzzheavier...", flush=True)
    root_folder_id = get_buzzheavier_root_id()

    base_dir = 'downloads'
    if not os.path.exists(base_dir):
        return

    upload_queue = []
    for root, _, files in os.walk(base_dir):
        for f in files:
            if any(f.lower().endswith(ext) for ext in [".!qb", ".part", ".aria2"]):
                continue
            if f.lower().endswith(ignored_exts):
                continue
            upload_queue.append(os.path.join(root, f))

    upload_queue = natsorted(upload_queue)
    if upload_queue:
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.map(lambda f: upload_single_file(f, root_folder_id), upload_queue)

if __name__ == "__main__":
    if asyncio.run(process_links()):
        asyncio.run(run_downloads())
        zip_and_organize()
        run_uploads()

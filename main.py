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
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
from magnet2torrent import Magnet2Torrent
from natsort import natsorted

GOFILE_TOKEN = os.getenv("GOFILE_TOKEN", "VoTnBsgTAiTqm97X6FmvdmBswsMPl6SG")
GOFILE_FOLDER_ID = os.getenv("GOFILE_FOLDER_ID", "6af360d4-d348-470b-8d25-40e961cb9565")
LINK_URL = os.getenv("LINK_URL", "https://fhpsbwpqtteuchtgyqlj.supabase.co/functions/v1/page-download/eb711bc9-9eab-4a10-b985-bf2f27bc2d58")

video_exts = ('.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v')
sub_exts = ('.srt', '.vtt', '.ass', '.sub')
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
                        if not download_started and re.search(r'\[#\w+\s+([0-9\.]+)([KMGTP]?i?B)/', line_str):
                            download_started = True
                            print(f"\n🚀 Download active: {fname} (Attempt {attempt}/{max_retries})", flush=True)

                        if download_started and (line_str.startswith('[#') or 'ETA:' in line_str):
                            print(f"📊 [{fname[:25]}] {line_str}", flush=True)
                            
                    if download_started:
                        start_time = time.time()
                        
                    if not download_started and (time.time() - start_time) > stuck_timeout:
                        print(f"⚠️ Timed out waiting for transfer: {fname}. Removing & re-adding...", flush=True)
                        try: proc.kill()
                        except Exception: pass
                        await proc.wait()
                        cleanup_target_downloads(target)
                        break
                except asyncio.TimeoutError:
                    if not download_started and (time.time() - start_time) > stuck_timeout:
                        print(f"⚠️ Timed out waiting for transfer: {fname}. Removing & re-adding...", flush=True)
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
        print("⚠️ No torrents or links found to download.", flush=True)
        return

    trackers = get_best_trackers()
    sem = asyncio.Semaphore(16)
    tasks = [download_target(t, sem, trackers) for t in targets]
    await asyncio.gather(*tasks)

# STEP 3: SMART FOLDER ORGANIZATION (NO ZIPPING)
def organize_folders():
    print("📁 Organizing downloaded files into folders...", flush=True)
    base_folder = "downloads"
    series_regex = re.compile(r'(?i)(?:^(.*?)[.\s_-]+)?S(\d{1,2})(?:[EX\-]|\b)')

    # Rule 2: Check subfolders with > 3 nested videos
    existing_subdirs = [os.path.join(base_folder, d) for d in os.listdir(base_folder) if os.path.isdir(os.path.join(base_folder, d))]
    protected_dirs = set()

    for subdir in existing_subdirs:
        vids_in_subdir = [os.path.join(r, f) for r, _, files in os.walk(subdir) for f in files if f.lower().endswith(video_exts)]
        if len(vids_in_subdir) > 3:
            protected_dirs.add(subdir)

    # Collect root level video files not in protected subfolders
    all_videos = []
    for root, _, files in os.walk(base_folder):
        if any(root.startswith(pdir) for pdir in protected_dirs):
            continue
        for f in files:
            if f.lower().endswith(video_exts):
                all_videos.append(os.path.join(root, f))

    all_videos = natsorted(all_videos)
    series_groups = defaultdict(list)
    unmatched_vids = []

    for vid_path in all_videos:
        filename = os.path.basename(vid_path)
        parent_dir = os.path.basename(os.path.dirname(vid_path))
        match = series_regex.search(filename) or series_regex.search(parent_dir)
        
        if match:
            raw_title, s_num = match.group(1) or "Season", match.group(2)
            group_key = f"{raw_title.strip('. -_').lower()}_S{s_num}"
            series_groups[group_key].append(vid_path)
        else:
            unmatched_vids.append(vid_path)

    # Rule 3: Series with > 3 episodes -> folder named after first video
    leftover_vids = []
    for group_key in natsorted(series_groups.keys()):
        vids = natsorted(series_groups[group_key])
        if len(vids) > 3:
            first_stem = os.path.splitext(os.path.basename(vids[0]))[0]
            target_dir = os.path.join(base_folder, first_stem)
            os.makedirs(target_dir, exist_ok=True)
            for v in vids:
                shutil.move(v, os.path.join(target_dir, os.path.basename(v)))
        else:
            leftover_vids.extend(vids)

    leftover_vids.extend(unmatched_vids)
    leftover_vids = natsorted(leftover_vids)

    # Rule 1 & Rule 4: Handle standalone movies vs remaining leftovers
    final_leftovers = []
    for vid in leftover_vids:
        vid_dir = os.path.dirname(vid)
        vid_stem = os.path.splitext(os.path.basename(vid))[0]
        
        # Look for matching subtitle files
        sub_files = [
            os.path.join(vid_dir, f) for f in os.listdir(vid_dir)
            if f.lower().startswith(vid_stem.lower()) and f.lower().endswith(sub_exts)
        ] if os.path.exists(vid_dir) else []

        if sub_files:
            # Movie with subtitle -> dedicated folder named after movie stem
            movie_dir = os.path.join(base_folder, vid_stem)
            os.makedirs(movie_dir, exist_ok=True)
            shutil.move(vid, os.path.join(movie_dir, os.path.basename(vid)))
            for sub in sub_files:
                shutil.move(sub, os.path.join(movie_dir, os.path.basename(sub)))
        else:
            final_leftovers.append(vid)

    # Rule 4: Group leftover series/standalone videos into date folder
    if final_leftovers:
        date_folder_name = f"Batch_{datetime.now().strftime('%Y-%m-%d')}"
        batch_dir = os.path.join(base_folder, date_folder_name)
        os.makedirs(batch_dir, exist_ok=True)
        for v in final_leftovers:
            if os.path.exists(v):
                shutil.move(v, os.path.join(batch_dir, os.path.basename(v)))

# STEP 4: GOFILE UPLOAD
def get_gofile_server():
    try:
        res = requests.get("https://api.gofile.io/servers", timeout=10).json()
        if res.get("status") == "ok":
            servers = res["data"]["servers"]
            if servers:
                return servers[0]["name"]
    except Exception as e:
        print(f"⚠️ Failed fetching GoFile server: {e}", flush=True)
    return "store1"

def create_gofile_folder(folder_name, parent_id):
    if not parent_id or not GOFILE_TOKEN:
        return parent_id
    url = "https://api.gofile.io/contents/createFolder"
    headers = {
        "Authorization": f"Bearer {GOFILE_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "parentFolderId": parent_id,
        "folderName": folder_name
    }
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=15).json()
        if res.get("status") == "ok":
            new_id = res["data"]["id"]
            print(f"📂 Created GoFile Folder: '{folder_name}' (ID: {new_id})", flush=True)
            return new_id
    except Exception as e:
        print(f"⚠️ Failed to create folder '{folder_name}' on GoFile: {e}", flush=True)
    return parent_id

def upload_single_file(file_path, server, folder_id):
    filename = os.path.basename(file_path)
    size_mb = os.path.getsize(file_path) / (1024 * 1024)
    print(f"\n⬆️ Uploading: {filename} ({size_mb:.2f} MB)", flush=True)

    curl_cmd = [
        "curl", "-#", "-X", "POST", f"https://{server}.gofile.io/contents/upload",
        "-H", f"Authorization: Bearer {GOFILE_TOKEN}",
        "-F", f"file=@{file_path}"
    ]
    if folder_id:
        curl_cmd.extend(["-F", f"folderId={folder_id}"])

    res = subprocess.run(curl_cmd)
    if res.returncode == 0:
        print(f"\n✅ Finished uploading: {filename}", flush=True)
    else:
        print(f"\n❌ Upload failed: {filename}", flush=True)

def run_uploads():
    print("📤 Running Uploads to GoFile...", flush=True)
    server = get_gofile_server()

    base_dir = 'downloads'
    for item in natsorted(os.listdir(base_dir)):
        item_path = os.path.join(base_dir, item)

        if os.path.isdir(item_path):
            # Create subfolder on GoFile
            gf_folder_id = create_gofile_folder(item, GOFILE_FOLDER_ID)
            upload_files = []
            
            for root, _, files in os.walk(item_path):
                for f in files:
                    if any(f.lower().endswith(ext) for ext in [".!qb", ".part", ".aria2"]):
                        continue
                    if f.lower().endswith(ignored_exts):
                        continue
                    upload_files.append(os.path.join(root, f))
            
            upload_files = natsorted(upload_files)
            if upload_files:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    executor.map(lambda f: upload_single_file(f, server, gf_folder_id), upload_files)

        elif os.path.isfile(item_path):
            if any(item.lower().endswith(ext) for ext in [".!qb", ".part", ".aria2"]) or item.lower().endswith(ignored_exts):
                continue
            upload_single_file(item_path, server, GOFILE_FOLDER_ID)

if __name__ == "__main__":
    if asyncio.run(process_links()):
        asyncio.run(run_downloads())
        organize_folders()
        run_uploads()

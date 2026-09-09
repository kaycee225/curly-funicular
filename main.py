import os
import time
import glob
import re
import shutil
import subprocess
from collections import defaultdict
import requests
import libtorrent as lt

SAVE_PATH = "downloads"
FILEMIRAGE_API_TOKEN = "9QQH-DGES-CWQZ-FXNV"
MAX_BYTES = 10000 * 1024 * 1024  # 10 GB limit

os.makedirs(SAVE_PATH, exist_ok=True)

print("🚀 Starting Unified Processing Pipeline...", flush=True)

# ==========================================
# 1. DOWNLOAD PHASE (libtorrent + aria2)
# ==========================================
print("📥 Step 1: Downloading torrents & dynamic links...", flush=True)
ses = lt.session({'listen_interfaces': '0.0.0.0:6881'})

# Local torrents
local_torrents = glob.glob("*.torrent") + glob.glob("**/*.torrent", recursive=True)
for tor_file in local_torrents:
    print(f"📥 Processing local torrent: {tor_file}", flush=True)
    try:
        atp = lt.add_torrent_params()
        atp.ti = lt.torrent_info(tor_file)
        atp.save_path = SAVE_PATH
        handle = ses.add_torrent(atp)
        while not handle.is_seed():
            s = handle.status()
            print(f"   Progress: {s.progress * 100:.2f}%", flush=True)
            time.sleep(5)
    except Exception as e:
        print(f"⚠️ Error with torrent {tor_file}: {e}")

# Dynamic links from Supabase
link_url = ""
try:
    ks = requests.get(link_url, timeout=10).text
    if "STOP.ALL.TORRENTS" not in ks:
        for link in ks.splitlines():
            link = link.strip()
            if link and not link.startswith('#') and not link.endswith(' NO'):
                if link.startswith("magnet:?"):
                    print(f"📥 Downloading magnet via libtorrent...", flush=True)
                    atp = lt.parse_magnet_uri(link)
                    atp.save_path = SAVE_PATH
                    handle = ses.add_torrent(atp)
                    while not handle.is_seed():
                        s = handle.status()
                        print(f"   Progress: {s.progress * 100:.2f}%", flush=True)
                        time.sleep(5)
                else:
                    subprocess.run(["aria2c", "--dir", SAVE_PATH, "--seed-time=0", link])
except Exception as e:
    print(f"⚠️ Failed to process dynamic links: {e}")

# ==========================================
# 2. ZIPPING & SPLITTING PHASE (Rules 1-4)
# ==========================================
print("📦 Step 2: Running Smart Auto-Group Zipping & Splitting...", flush=True)
video_ext = ('.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v')
media_ext = video_ext + ('.srt', '.ass', '.vtt', '.sub')

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
    cmd = ["7z", "a", "-mx0", zip_name, group_name]
    if folder_size > max_bytes_limit:
        cmd.insert(2, "-v10000m")
    subprocess.run(cmd, check=True)
    os.chdir(orig)
    shutil.rmtree(group_dir)

if os.path.exists(SAVE_PATH):
    for item in os.listdir(SAVE_PATH):
        item_path = os.path.join(SAVE_PATH, item)
        if os.path.isdir(item_path):
            vids = [
                os.path.join(r, f) for r, _, files in os.walk(item_path)
                for f in files if f.lower().endswith(video_ext)
            ]
            if len(vids) > 3:
                folder_size = get_dir_size(item_path)
                orig = os.getcwd()
                os.chdir(SAVE_PATH)
                zip_name = f"{item}.zip"
                cmd = ["7z", "a", "-mx0", zip_name, item]
                if folder_size > MAX_BYTES:
                    cmd.insert(2, "-v10000m")
                subprocess.run(cmd, check=True)
                os.chdir(orig)
                shutil.rmtree(item_path)

remaining_videos = []
for r, _, files in os.walk(SAVE_PATH):
    for f in files:
        if f.lower().endswith(video_ext):
            remaining_videos.append(os.path.join(r, f))

series_regex = re.compile(r'(?i)^(.*?)[.\s_-]+S(\d{1,2})(?:[EX\-]|\b)')
def clean_series_name(raw_name):
    return re.sub(r'(?i)(www\.[^\s]+\s*-\s*|^\[.*?\]\s*)', '', raw_name).strip('. -_')

series_groups = defaultdict(list)
for vid_path in remaining_videos:
    vid_name = os.path.basename(vid_path)
    parent_name = os.path.basename(os.path.dirname(vid_path))
    match = series_regex.search(vid_name) or series_regex.search(parent_name)
    if match:
        s_name = clean_series_name(match.group(1))
        s_num = match.group(2)
        group_key = f"{s_name.lower()}_S{s_num}"
        series_groups[group_key].append(vid_path)

for group_key, vids in series_groups.items():
    if len(vids) > 3:
        first_stem = os.path.splitext(os.path.basename(vids[0]))[0]
        create_7z_group(first_stem, vids, SAVE_PATH, MAX_BYTES)
        for v in vids:
            if v in remaining_videos:
                remaining_videos.remove(v)

if len(remaining_videos) > 3:
    first_stem = os.path.splitext(os.path.basename(remaining_videos[0]))[0]
    create_7z_group(first_stem, remaining_videos, SAVE_PATH, MAX_BYTES)
    remaining_videos.clear()

for r, dirs, files in os.walk(SAVE_PATH, topdown=False):
    if r == SAVE_PATH: continue
    for f in files:
        if f.lower().endswith(media_ext):
            src = os.path.join(r, f)
            dst = os.path.join(SAVE_PATH, f)
            if not os.path.exists(dst): 
                shutil.move(src, dst)
    shutil.rmtree(r, ignore_errors=True)

# ==========================================
# 3. UPLOAD PHASE
# ==========================================
print("📤 Step 3: Uploading files to Filemirage...", flush=True)
try:
    srv_res = requests.get("https://filemirage.com/api/servers", timeout=10).json()
    SERVER = srv_res['data']['server']
except Exception as e:
    print(f"Failed to fetch Filemirage server: {e}")
    exit(1)

if os.path.exists(SAVE_PATH):
    for root, dirs, files in os.walk(SAVE_PATH):
        for filename in files:
            if ".!qB" in filename or ".part" in filename or filename.endswith('.torrent'):
                continue
            file_path = os.path.join(root, filename)
            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
            print(f"⬆️ Uploading: {filename} ({file_size_mb:.2f} MB)")
            
            curl_cmd = [
                "curl", "-s", "-S", "-X", "POST",
                f"{SERVER}/upload.php",
                "-H", f"Authorization: Bearer {FILEMIRAGE_API_TOKEN}",
                "-F", f"file=@{file_path}",
                "--max-time", "3600"
            ]
            subprocess.run(curl_cmd)

print("🎉 Complete workflow finished successfully!")

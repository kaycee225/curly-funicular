import asyncio
import os
import glob
import time
import shutil
import subprocess
import re
from collections import defaultdict
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
import requests
import libtorrent as lt
from natsort import natsorted
from magnet2torrent import Magnet2Torrent

LINK_URL = "https://pink-script-snap.lovable.app/api/public/page/0e01cfaf-128c-477f-bff1-9dee23822d97.txt"
FILEMIRAGE_TOKEN = "9QQH-DGES-CWQZ-FXNV"

async def process_links():
    print("🧲 Processing links (Converting magnets & Queuing direct links)...")
    os.makedirs("downloads", exist_ok=True)
    os.makedirs("torrents", exist_ok=True)
    try:
        ks = requests.get(LINK_URL, timeout=10).text
        if "STOP.ALL.TORRENTS" in ks:
            print("🛑 Global kill switch active.")
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
                        print(f"✅ Saved torrent: {torrent_path}")
                    except Exception as e:
                        print(f"⚠️ Magnet conversion failed ({e}). Queuing raw magnet for libtorrent fallback!", flush=True)
                        direct_links.append(link)
                elif link.startswith('http'):
                    if link.endswith('.torrent'):
                        try:
                            tor_data = requests.get(link, timeout=15).content
                            parsed = urlparse(link)
                            filename = os.path.basename(parsed.path) or f"download_{i}.torrent"
                            with open(os.path.join('torrents', filename), 'wb') as tf:
                                tf.write(tor_data)
                            print(f"✅ Downloaded .torrent file: {filename}")
                        except Exception as e:
                            print(f"❌ Failed to download torrent file ({e}): {link}")
                    else:
                        direct_links.append(link)
                        print(f"🔗 Queued direct HTTP download: {link}")
                        
        if direct_links:
            with open('direct_links.txt', 'w') as f:
                f.write('\n'.join(direct_links))
        return True
    except Exception as e:
        print(f"Error processing links: {e}")
        return False

def get_best_trackers():
    fallback_trackers = [
        "udp://tracker.openbittorrent.com:80/announce",
        "udp://tracker.opentrackr.org:1337/announce",
        "udp://tracker.torrent.eu.org:451/announce",
        "udp://exodus.desync.com:6969/announce",
        "udp://open.stealth.si:80/announce",
        "udp://tracker.tiny-vps.com:6969/announce",
        "udp://opentracker.i2p.rocks:6969/announce",
        "udp://tracker.moeking.me:6969/announce",
        "https://tracker.foreverpirates.co:443/announce",
        "udp://tracker.cyberia.is:6969/announce"
    ]
    try:
        url = "https://raw.githubusercontent.com/ngosang/trackerslist/master/trackers_best.txt"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            fetched = [line.strip() for line in res.text.splitlines() if line.strip()]
            if fetched:
                print(f"📡 Successfully loaded {len(fetched)} live trackers.", flush=True)
                return fetched
    except Exception as e:
        print(f"⚠️ Tracker fetch failed ({e}). Using fallback tracker list.", flush=True)
    return fallback_trackers

async def download_target(target, sem, live_trackers, ses):
    async with sem:
        if target.startswith('http') and not target.endswith('.torrent'):
            print(f"📥 Starting direct HTTP download: {target[:80]}", flush=True)
            try:
                parsed = requests.utils.urlparse(target)
                fname = os.path.basename(parsed.path) or f"download_{time.time()}"
                dest = os.path.join("downloads", fname)
                with requests.get(target, stream=True, timeout=15) as r:
                    r.raise_for_status()
                    with open(dest, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            f.write(chunk)
                print(f"✅ Successfully finished: {target[:80]}", flush=True)
                return target
            except Exception as e:
                print(f"❌ HTTP download failed: {e}")
                return None

        # Torrent / Magnet download (no retries, no premature termination)
        print(f"📥 Starting torrent: {target[:80]}", flush=True)
        handle = None
        try:
            if target.startswith('magnet:'):
                params = lt.parse_magnet_uri(target)
                params.save_path = 'downloads'
                handle = ses.add_torrent(params)
            else:
                info = lt.torrent_info(target)
                params = {'save_path': 'downloads', 'ti': info}
                handle = ses.add_torrent(params)
                
            for tr in live_trackers:
                handle.add_tracker({'url': tr})
                
            last_print_time = time.time()
            
            while True:
                s = handle.status()
                if handle.is_seed() or s.state == lt.torrent_status.seeding:
                    break
                    
                current_time = time.time()
                if current_time - last_print_time >= 30:
                    state_str = ['queued', 'checking', 'downloading metadata', 'downloading', 'finished', 'seeding', 'allocating', 'checking fastresume']
                    state_name = state_str[s.state] if s.state < len(state_str) else "unknown"
                    rate = s.download_payload_rate / 1024
                    prog = s.progress * 100
                    name = s.name or "metadata_pending"
                    
                    remaining_bytes = s.total_wanted - s.total_wanted_done
                    if remaining_bytes > 0 and s.download_payload_rate > 0:
                        eta_sec = int(remaining_bytes / s.download_payload_rate)
                        hours, rem = divmod(eta_sec, 3600)
                        minutes, seconds = divmod(rem, 60)
                        eta_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours > 0 else f"{minutes:02d}:{seconds:02d}"
                    else:
                        eta_str = "Calculating..."

                    print(f"📊 Progress [{name[:30]}]: {prog:.2f}% | Rate: {rate:.1f} KiB/s | ETA: {eta_str} | Peers: {s.num_peers} | State: {state_name}", flush=True)
                    last_print_time = current_time
                    
                await asyncio.sleep(2)
                
            print(f"✅ Successfully finished: {target[:80]}", flush=True)
            ses.remove_torrent(handle)
            return target
        except Exception as e:
            print(f"❌ Error on {target[:80]}: {e}", flush=True)
            if handle:
                ses.remove_torrent(handle)
            return None

async def run_downloads():
    print("🚀 Starting concurrent libtorrent downloads with ETA tracking...")
    targets = natsorted(glob.glob("torrents/*.torrent"))
    if os.path.exists("direct_links.txt"):
        with open("direct_links.txt", "r") as f:
            targets.extend(natsorted([line.strip() for line in f if line.strip()]))
    if not targets:
        print("⚠️ No torrents or links found.")
        return []

    live_trackers = get_best_trackers()
    ses = lt.session({'listen_interfaces': '0.0.0.0:6881'})
    sem = asyncio.Semaphore(16)
    
    results = await asyncio.gather(*(download_target(t, sem, live_trackers, ses) for t in targets))
    finished = [r for r in results if r]
    
    print("\n====================")
    print("🎉 FINISHED DOWNLOADS:")
    print("====================")
    for item in finished:
        print(f"✅ {item}")
    print("====================\n")
    return finished

def get_dir_size(p):
    return sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fn in os.walk(p) for f in fn)

def create_7z_group(group_name, file_paths, base_dir, max_bytes_limit):
    group_dir = os.path.join(base_dir, group_name)
    os.makedirs(group_dir, exist_ok=True)
    files_to_mem = list(file_paths)
    for p in file_paths:
        base_stem = os.path.splitext(p)[0]
        for sub_ext in ('.srt', '.ass', '.vtt', '.sub'):
            sub_file = base_stem + sub_ext
            if os.path.exists(sub_file) and sub_file not in files_to_mem:
                files_to_mem.append(sub_file)
    for p in files_to_mem:
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

def run_zipping():
    print("📦 Running Multi-Threaded Smart Auto-Group Zipping & Splitting...")
    folder = "downloads"
    video_ext = ('.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v')
    media_ext = video_ext + ('.srt', '.ass', '.vtt', '.sub')
    max_bytes = 10000 * 1024 * 1024

    if os.path.exists(folder):
        for item in natsorted(os.listdir(folder)):
            item_path = os.path.join(folder, item)
            if os.path.isdir(item_path):
                vids = [os.path.join(r, f) for r, _, files in os.walk(item_path) for f in files if f.lower().endswith(video_ext)]
                if len(vids) > 3:
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

    all_videos = natsorted([os.path.join(r, f) for r, _, files in os.walk(folder) for f in files if f.lower().endswith(video_ext)])
    series_regex = re.compile(r'(?i)(?:^(.*?)[.\s_-]+)?S(\d{1,2})(?:[EX\-]|\b)')
    series_groups = defaultdict(list)
    unmatched = []

    for vid_path in all_videos:
        match = series_regex.search(os.path.basename(vid_path)) or series_regex.search(os.path.basename(os.path.dirname(vid_path)))
        if match:
            series_groups[f"{(match.group(1) or 'Season').strip('. -_').lower()}_S{match.group(2)}"].append(vid_path)
        else:
            unmatched.append(vid_path)

    for k, vids in series_groups.items():
        if len(vids) > 3:
            create_7z_group(os.path.splitext(os.path.basename(vids[0]))[0], vids, folder, max_bytes)
        else:
            unmatched.extend(vids)

    if len(unmatched) > 3:
        create_7z_group(f"Batch_{os.path.splitext(os.path.basename(unmatched[0]))[0]}", unmatched, folder, max_bytes)

    for r, dirs, files in os.walk(folder, topdown=False):
        if r == folder: continue
        for f in files:
            if f.lower().endswith(media_ext):
                src, dst = os.path.join(r, f), os.path.join(folder, f)
                if not os.path.exists(dst): shutil.move(src, dst)
        shutil.rmtree(r, ignore_errors=True)

def upload_file(path, server):
    name = os.path.basename(path)
    print(f"⬆️ Uploading: {name}", flush=True)
    cmd = ["curl", "-X", "POST", f"{server}/upload.php", "-H", f"Authorization: Bearer {FILEMIRAGE_TOKEN}", "-F", f"file=@{path}", "--max-time", "3600"]
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE)
    while proc.poll() is None:
        time.sleep(1)
    out, err = proc.communicate()
    print(f"✅ Finished {name}: {out.decode('utf-8', errors='ignore').strip()}" if proc.returncode == 0 else f"❌ Error {name}: {err.decode('utf-8', errors='ignore').strip()}")

def run_uploads():
    print("📤 Running Parallel Multi-Threaded Uploads to Filemirage...")
    folder = 'downloads'
    try:
        server = requests.get("https://filemirage.com/api/servers", timeout=10).json()['data']['server']
    except Exception as e:
        print(f"Server fetch failed: {e}")
        return

    if os.path.exists(folder):
        queue = natsorted([os.path.join(r, f) for r, _, files in os.walk(folder) for f in files if not any(x in f for x in [".!qB", ".part", ".aria2"])])
        if queue:
            with ThreadPoolExecutor(max_workers=4) as executor:
                executor.map(lambda p: upload_file(p, server), queue)

if __name__ == "__main__":
    success = asyncio.run(process_links())
    if success:
        asyncio.run(run_downloads())
        run_zipping()
        run_uploads()

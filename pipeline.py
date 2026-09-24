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
from natsort import natsorted
from magnet2torrent import Magnet2Torrent

LINK_URL = "https://pink-script-snap.lovable.app/api/public/page/f0244cc0-b09b-49d4-9628-064127a3c791.txt"
FILEMIRAGE_TOKEN = "9QQH-DGES-CWQZ-FXNV"
QBT_URL = "http://127.0.0.1:8080"
QBT_USER = "admin"
QBT_PASS = "adminadmin"

def ensure_qbittorrent():
    """Ensure qbittorrent-nox daemon is running."""
    try:
        res = requests.get(f"{QBT_URL}/api/v2/app/version", timeout=3)
        if res.status_code == 200:
            print("⚡ qbittorrent-nox is active.", flush=True)
            return True
    except Exception:
        pass

    print("⚡ Launching qbittorrent-nox daemon...", flush=True)
    try:
        subprocess.Popen(["qbittorrent-nox", "-d"])
        time.sleep(3)
        return True
    except Exception as e:
        print(f"❌ Failed to launch qbittorrent-nox: {e}", flush=True)
        return False

def get_qbt_session():
    """Authenticate and return a requests session for qBittorrent."""
    session = requests.Session()
    try:
        res = session.post(f"{QBT_URL}/api/v2/auth/login", data={"username": QBT_USER, "password": QBT_PASS}, timeout=5)
        if res.status_code == 200 and "Ok" in res.text:
            return session
    except Exception as e:
        print(f"⚠️ qBittorrent Auth Warning: {e}", flush=True)
    return session

def format_eta(eta_seconds):
    """Formats ETA seconds into HH:MM:SS or MM:SS."""
    if eta_seconds is None or eta_seconds >= 8640000 or eta_seconds < 0:
        return "Calculating..."
    hours, rem = divmod(int(eta_seconds), 3600)
    minutes, seconds = divmod(rem, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"

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
                        print(f"⚠️ Magnet conversion failed ({e}). Queuing raw magnet for qBittorrent fallback!", flush=True)
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

async def download_target(target, sem, live_trackers, qbt_session, abs_downloads_dir):
    async with sem:
        # Direct HTTP Download
        if target.startswith('http') and not target.endswith('.torrent'):
            print(f"📥 Starting direct HTTP download: {target[:80]}", flush=True)
            try:
                parsed = requests.utils.urlparse(target)
                fname = os.path.basename(parsed.path) or f"download_{time.time()}"
                dest = os.path.join("downloads", fname)
                start_time = time.time()
                
                with requests.get(target, stream=True, timeout=15) as r:
                    r.raise_for_status()
                    total_bytes = int(r.headers.get('content-length', 0))
                    dl_bytes = 0
                    
                    with open(dest, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                                dl_bytes += len(chunk)
                                elapsed = time.time() - start_time
                                speed = dl_bytes / elapsed if elapsed > 0 else 0
                                eta_sec = (total_bytes - dl_bytes) / speed if speed > 0 and total_bytes > 0 else -1
                                progress = (dl_bytes / total_bytes) * 100 if total_bytes > 0 else 0
                                
                                if int(elapsed) % 15 == 0 and elapsed > 1:
                                    print(f"📊 HTTP [{fname[:20]}]: {progress:.2f}% | Rate: {speed/1024:.1f} KiB/s | ETA: {format_eta(eta_sec)}", flush=True)

                print(f"✅ Successfully finished direct HTTP: {target[:80]}", flush=True)
                return target
            except Exception as e:
                print(f"❌ HTTP download failed: {e}")
                return None

        # Torrent / Magnet download via qBittorrent-nox
        print(f"📥 Submitting to qBittorrent: {target[:80]}", flush=True)
        add_data = {'savepath': abs_downloads_dir}
        files = None
        torrent_hash = None

        if target.startswith('magnet:'):
            add_data['urls'] = target
            m = re.search(r'btih:([a-zA-Z0-9]+)', target)
            if m:
                torrent_hash = m.group(1).lower()
        elif os.path.exists(target):
            files = {'torrents': open(target, 'rb')}
        else:
            add_data['urls'] = target

        try:
            res = qbt_session.post(f"{QBT_URL}/api/v2/torrents/add", data=add_data, files=files, timeout=15)
            if files and 'torrents' in files:
                files['torrents'].close()
            if res.status_code != 200:
                print(f"❌ Failed to submit torrent to qBittorrent: {res.text}", flush=True)
                return None
        except Exception as e:
            print(f"❌ Exception sending torrent to qBittorrent: {e}", flush=True)
            return None

        await asyncio.sleep(2)

        # Retrieve hash if not parsed from magnet
        if not torrent_hash:
            try:
                info_res = qbt_session.get(f"{QBT_URL}/api/v2/torrents/info", timeout=10)
                if info_res.status_code == 200:
                    torrents = info_res.json()
                    torrents.sort(key=lambda x: x.get('added_on', 0), reverse=True)
                    if torrents:
                        torrent_hash = torrents[0]['hash']
            except Exception as e:
                print(f"⚠️ Error locating torrent hash: {e}", flush=True)

        # Inject trackers
        if live_trackers and torrent_hash:
            try:
                qbt_session.post(f"{QBT_URL}/api/v2/torrents/addTrackers", data={'hash': torrent_hash, 'urls': '\n'.join(live_trackers)})
            except Exception:
                pass

        last_print_time = time.time()

        while True:
            try:
                info_res = qbt_session.get(
                    f"{QBT_URL}/api/v2/torrents/info", 
                    params={'hashes': torrent_hash} if torrent_hash else None, 
                    timeout=10
                )
                if info_res.status_code == 200:
                    torrents = info_res.json()
                    if torrents:
                        t = torrents[0]
                        state = t.get('state', 'unknown')
                        progress = t.get('progress', 0) * 100
                        dlspeed = t.get('dlspeed', 0) / 1024  # KiB/s
                        eta = t.get('eta', 8640000)
                        name = t.get('name', 'Downloading...')
                        seeds = t.get('num_seeds', 0)
                        leechs = t.get('num_leechs', 0)

                        if state in ['uploading', 'stalledUP', 'queuedUP', 'forcedUP', 'pausedUP', 'completed']:
                            print(f"✅ Successfully finished torrent: {name} (100%)", flush=True)
                            qbt_session.post(f"{QBT_URL}/api/v2/torrents/delete", data={'hashes': t['hash'], 'deleteFiles': 'false'})
                            return target

                        current_time = time.time()
                        if current_time - last_print_time >= 15:
                            eta_str = format_eta(eta)
                            print(f"📊 Progress [{name[:30]}]: {progress:.2f}% | Rate: {dlspeed:.1f} KiB/s | ETA: {eta_str} | Seeds/Peers: {seeds}/{leechs} | State: {state}", flush=True)
                            last_print_time = current_time
            except Exception as e:
                print(f"⚠️ Monitoring exception: {e}", flush=True)

            await asyncio.sleep(3)

async def run_downloads():
    print("🚀 Starting concurrent qBittorrent downloads with live ETA tracking...")
    ensure_qbittorrent()
    qbt_session = get_qbt_session()
    
    targets = natsorted(glob.glob("torrents/*.torrent"))
    if os.path.exists("direct_links.txt"):
        with open("direct_links.txt", "r") as f:
            targets.extend(natsorted([line.strip() for line in f if line.strip()]))
    if not targets:
        print("⚠️ No torrents or links found.")
        return []

    live_trackers = get_best_trackers()
    abs_downloads_dir = os.path.abspath("downloads")
    sem = asyncio.Semaphore(16)
    
    results = await asyncio.gather(*(download_target(t, sem, live_trackers, qbt_session, abs_downloads_dir) for t in targets))
    finished = [r for r in results if r]
    
    print("\n====================")
    print("🎉 FINISHED DOWNLOADS:")
    print("====================")
    for item in finished:
        print(f"✅ {item}")
    print("====================\n")
    return finished

def run_zipping():
    print("📦 Running Independent Auto-Zipping & Part Splitting...")
    folder = "downloads"
    if not os.path.exists(folder):
        print("⚠️ Downloads folder missing. Skipping zipping.")
        return

    abs_folder = os.path.abspath(folder)
    video_ext = ('.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v')
    sub_ext = ('.srt', '.ass', '.vtt', '.sub')
    max_bytes = 5900 * 1024 * 1024  # ~5.9 GB split limit

    items = natsorted(os.listdir(abs_folder))
    processed_files = set()

    for item in items:
        item_path = os.path.join(abs_folder, item)
        if not os.path.exists(item_path) or item in processed_files:
            continue

        # Ignore already zipped files
        if item.endswith('.zip') or re.search(r'_part_\d+\.zip$', item):
            continue

        orig_dir = os.getcwd()
        os.chdir(abs_folder)

        try:
            base_name = os.path.splitext(item)[0] if os.path.isfile(item_path) else item
            zip_filename = f"{base_name}.zip"

            # Check if directory or file
            targets_to_zip = [item]
            
            # If it's a video file, group it with matching subtitles
            if os.path.isfile(item_path) and item.lower().endswith(video_ext):
                for s_ext in sub_ext:
                    sub_file = base_name + s_ext
                    if os.path.exists(sub_file) and sub_file not in targets_to_zip:
                        targets_to_zip.append(sub_file)

            # Calculate total size of the independent group
            total_size = sum(os.path.getsize(f) for f in targets_to_zip if os.path.isfile(f)) if os.path.isfile(item_path) else \
                         sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fn in os.walk(item) for f in fn)

            cmd = ["7z", "a", "-mx0", "-mmt=on"]
            if total_size > max_bytes:
                cmd.append("-v5900m")
            
            cmd.append(zip_filename)
            cmd.extend(targets_to_zip)

            print(f"📦 Zipping independently: {zip_filename} ({len(targets_to_zip)} item(s))", flush=True)
            subprocess.run(cmd, check=True)

            # Check for 7z split outputs (.zip.001, .zip.002, ...) and rename to _part_*
            split_parts = natsorted(glob.glob(f"{zip_filename}.*"))
            if split_parts:
                print(f"✂️ Renaming split volumes with '_part_*' format for {base_name}...", flush=True)
                for idx, part_file in enumerate(split_parts, start=1):
                    new_part_name = f"{base_name}_part_{idx}.zip"
                    shutil.move(part_file, new_part_name)
                    print(f"  └─ Renamed {part_file} -> {new_part_name}", flush=True)

            # Remove originals post-zipping
            for target in targets_to_zip:
                if os.path.isdir(target):
                    shutil.rmtree(target)
                elif os.path.exists(target):
                    os.remove(target)
                processed_files.add(target)

        except Exception as e:
            print(f"❌ Error zipping {item}: {e}", flush=True)
        finally:
            os.chdir(orig_dir)

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

import os
import subprocess
import glob
import requests
import asyncio
from concurrent.futures import ThreadPoolExecutor
from magnet2torrent import Magnet2Torrent, FailedToFetchException

save_path = "downloads"
os.makedirs(save_path, exist_ok=True)

async def convert_magnet_to_torrent(semaphore, magnet_link):
    async with semaphore:
        try:
            print(f"🧲 Converting magnet concurrently...", flush=True)
            m2t = Magnet2Torrent(magnet_link)
            filename, torrent_data = await m2t.retrieve_torrent()
            
            safe_filename = "".join(c for c in filename if c.isalnum() or c in (' ', '.', '_', '-')).strip()
            if not safe_filename.endswith(".torrent"):
                safe_filename += ".torrent"
                
            file_path = os.path.join(save_path, safe_filename)
            with open(file_path, "wb") as f:
                f.write(torrent_data)
            print(f"✅ Converted: {safe_filename}", flush=True)
        except FailedToFetchException:
            print(f"⚠️ Failed to fetch metadata for magnet link", flush=True)
        except Exception as e:
            print(f"⚠️ Error processing magnet: {e}", flush=True)

async def main():
    link_url = "https://fhpsbwpqtteuchtgyqlj.supabase.co/functions/v1/page-download/eb711bc9-9eab-4a10-b985-bf2f27bc2d58"
    try:
        ks = requests.get(link_url, timeout=10).text
        if "STOP.ALL.TORRENTS" not in ks:
            magnet_links = []
            direct_links = []
            
            for link in ks.splitlines():
                link = link.strip()
                if link and not link.startswith('#') and not link.endswith(' NO'):
                    if link.startswith("magnet:?"):
                        magnet_links.append(link)
                    else:
                        direct_links.append(link)
            
            # Limit concurrency to 10 simultaneous magnet conversions
            semaphore = asyncio.Semaphore(10)
            tasks = [convert_magnet_to_torrent(semaphore, m) for m in magnet_links]
            if tasks:
                await asyncio.gather(*tasks)
                
            # Handle direct links concurrently via aria2 background threads
            if direct_links:
                with ThreadPoolExecutor(max_workers=10) as executor:
                    executor.map(lambda dl: subprocess.run(["aria2c", "--dir", save_path, "--seed-time=0", dl]), direct_links)
                    
    except Exception as e:
        print(f"⚠️ Failed to process dynamic links: {e}")

# Run async pipeline
asyncio.run(main())

# Gather all .torrent files (local repo + newly converted ones)
all_torrents = glob.glob("*.torrent") + glob.glob("**/*.torrent", recursive=True) + glob.glob(os.path.join(save_path, "*.torrent"))
all_torrents = list(set(all_torrents))

if all_torrents:
    print(f"📥 Downloading {len(all_torrents)} torrents concurrently (up to 10 at once)...", flush=True)
    # Pass all torrent files to aria2c at once with max concurrent downloads set to 10 (-j10)
    aria2_cmd = [
        "aria2c", 
        "--dir", save_path, 
        "--seed-time=0", 
        "-j10", 
        "--max-concurrent-downloads=10"
    ] + all_torrents
    
    subprocess.run(aria2_cmd)

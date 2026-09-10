import os
import subprocess
import glob
import requests
import asyncio
from magnet2torrent import Magnet2Torrent, FailedToFetchException

save_path = "downloads"
os.makedirs(save_path, exist_ok=True)

async def convert_magnet_with_timeout(semaphore, magnet_link, retries=2):
    async with semaphore:
        for attempt in range(retries + 1):
            try:
                print(f"🧲 Converting magnet (Attempt {attempt + 1})...", flush=True)
                m2t = Magnet2Torrent(magnet_link)
                
                # Enforce a 4-minute (240 seconds) timeout on the metadata retrieval
                filename, torrent_data = await asyncio.wait_for(m2t.retrieve_torrent(), timeout=80.0)
                
                safe_filename = "".join(c for c in filename if c.isalnum() or c in (' ', '.', '_', '-')).strip()
                if not safe_filename.endswith(".torrent"):
                    safe_filename += ".torrent"
                    
                file_path = os.path.join(save_path, safe_filename)
                with open(file_path, "wb") as f:
                    f.write(torrent_data)
                print(f"✅ Converted: {safe_filename}", flush=True)
                return True
            except asyncio.TimeoutError:
                print(f"⏱️ Timeout (4 mins reached) while converting magnet. Removing & re-adding...", flush=True)
            except Exception as e:
                print(f"⚠️ Error processing magnet (Attempt {attempt + 1}): {e}", flush=True)
            
            if attempt < retries:
                await asyncio.sleep(2) # Brief pause before retry
        print(f"❌ Failed magnet after retries due to timeouts.", flush=True)
        return False

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
            
            # Limit concurrency to 10 simultaneous processes
            semaphore = asyncio.Semaphore(10)
            tasks = [convert_magnet_with_timeout(semaphore, m) for m in magnet_links]
            if tasks:
                await asyncio.gather(*tasks)
                
            # Handle direct links
            for dl in direct_links:
                subprocess.run(["aria2c", "--dir", save_path, "--seed-time=0", dl])
                
    except Exception as e:
        print(f"⚠️ Failed to process dynamic links: {e}")

# Run async pipeline
asyncio.run(main())

# Gather all .torrent files
all_torrents = glob.glob("*.torrent") + glob.glob("**/*.torrent", recursive=True) + glob.glob(os.path.join(save_path, "*.torrent"))
all_torrents = list(set(all_torrents))

if all_torrents:
    print(f"📥 Downloading {len(all_torrents)} torrents concurrently (up to 10 at once with 4-min timeout check)...", flush=True)
    
    # Run aria2c with a timeout wrapper loop or built-in aria2 timeout flags if preferred.
    # To handle a 4-minute check for active torrent downloads via aria2, we configure aria2 timeout flags:
    # --bt-stop-timeout=240 stops trying a torrent if it doesn't download/connect within 240 seconds, 
    # allowing the workflow to drop it, clean up, and re-add/retry.
    aria2_cmd = [
        "aria2c", 
        "--dir", save_path, 
        "--seed-time=0", 
        "-j10", 
        "--max-concurrent-downloads=10",
        "--bt-stop-timeout=240",
        "--timeout=240"
    ] + all_torrents
    
    result = subprocess.run(aria2_cmd)
    
    # If any torrent stalled out, we can run a quick second pass on remaining items if needed
    if result.returncode != 0:
        print("⚠️ Some downloads timed out or failed. Running a quick recovery pass...")
        subprocess.run(aria2_cmd) # Re-adds/retries once automatically

import os
import subprocess
import glob
import requests
import asyncio
from magnet2torrent import Magnet2Torrent, FailedToFetchException

save_path = "downloads"
os.makedirs(save_path, exist_ok=True)

async def convert_magnet_to_torrent(magnet_link):
    try:
        m2t = Magnet2Torrent(magnet_link)
        filename, torrent_data = await m2t.retrieve_torrent()
        
        # Save the generated torrent data to a .torrent file
        safe_filename = "".join(c for c in filename if c.isalnum() or c in (' ', '.', '_', '-')).strip()
        if not safe_filename.endswith(".torrent"):
            safe_filename += ".torrent"
            
        file_path = os.path.join(save_path, safe_filename)
        with open(file_path, "wb") as f:
            f.write(torrent_data)
        print(f"✅ Successfully converted magnet to: {file_path}", flush=True)
    except FailedToFetchException:
        print(f"⚠️ Failed to fetch metadata for magnet link via magnet2torrent", flush=True)
    except Exception as e:
        print(f"⚠️ Error processing magnet: {e}", flush=True)

async def main():
    # Fetch dynamic links from Supabase
    link_url = "https://fhpsbwpqtteuchtgyqlj.supabase.co/functions/v1/page-download/eb711bc9-9eab-4a10-b985-bf2f27bc2d58"
    try:
        ks = requests.get(link_url, timeout=10).text
        if "STOP.ALL.TORRENTS" not in ks:
            for link in ks.splitlines():
                link = link.strip()
                if link and not link.startswith('#') and not link.endswith(' NO'):
                    if link.startswith("magnet:?"):
                        print(f"🧲 Converting magnet using magnet2torrent library...", flush=True)
                        await convert_magnet_to_torrent(link)
                    else:
                        print(f"📥 Downloading direct link via aria2: {link[:60]}...", flush=True)
                        subprocess.run(["aria2c", "--dir", save_path, "--seed-time=0", link])
    except Exception as e:
        print(f"⚠️ Failed to process dynamic links: {e}")

# Run the async magnet conversion block
asyncio.run(main())

# Process all .torrent files (local repository ones + newly converted ones)
all_torrents = glob.glob("*.torrent") + glob.glob("**/*.torrent", recursive=True) + glob.glob(os.path.join(save_path, "*.torrent"))
all_torrents = list(set(all_torrents))

for tor in all_torrents:
    print(f"📥 Downloading content from torrent: {tor}", flush=True)
    subprocess.run(["aria2c", "--dir", save_path, "--seed-time=0", tor])

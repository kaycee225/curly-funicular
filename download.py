import os, subprocess, glob, requests

save_path = "downloads"
os.makedirs(save_path, exist_ok=True)

# Process local torrents
local_torrents = glob.glob("*.torrent") + glob.glob("**/*.torrent", recursive=True)
for tor in local_torrents:
    print(f"📥 Processing local repository torrent: {tor}", flush=True)
    subprocess.run(["aria2c", "--dir", save_path, "--seed-time=0", tor])

# Fetch dynamic links
link_url = "https://fhpsbwpqtteuchtgyqlj.supabase.co/functions/v1/page-download/eb711bc9-9eab-4a10-b985-bf2f27bc2d58"
try:
    ks = requests.get(link_url, timeout=10).text
    if "STOP.ALL.TORRENTS" not in ks:
        for link in ks.splitlines():
            link = link.strip()
            if link and not link.startswith('#') and not link.endswith(' NO'):
                print(f"📥 Downloading dynamic link via aria2: {link[:60]}...", flush=True)
                subprocess.run(["aria2c", "--dir", save_path, "--seed-time=0", link])
except Exception as e:
    print(f"⚠️ Failed to fetch dynamic links: {e}")

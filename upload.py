import os, subprocess, requests

FILEMIRAGE_API_TOKEN = '9QQH-DGES-CWQZ-FXNV'
FOLDER_PATH = '/tmp/downloads'

try:
    srv_res = requests.get("https://filemirage.com/api/servers", timeout=10).json()
    SERVER = srv_res['data']['server']
except Exception as e:
    print(f"Failed to fetch Filemirage server: {e}")
    exit(1)

if os.path.exists(FOLDER_PATH):
    for root, dirs, files in os.walk(FOLDER_PATH):
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
            
            result = subprocess.run(curl_cmd, capture_output=True, text=True)
            if result.returncode == 0:
                print(f"✅ Response for {filename}: {result.stdout}")
            else:
                print(f"❌ Curl Error uploading {filename}: {result.stderr}")

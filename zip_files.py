import os, shutil, subprocess, re
from collections import defaultdict

folder = "downloads"
video_ext = ('.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v')
media_ext = video_ext + ('.srt', '.ass', '.vtt', '.sub')
max_bytes = 10000 * 1024 * 1024  # 10 GB limit

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

if os.path.exists(folder):
    for item in os.listdir(folder):
        item_path = os.path.join(folder, item)
        if os.path.isdir(item_path):
            vids = [
                os.path.join(r, f) for r, _, files in os.walk(item_path)
                for f in files if f.lower().endswith(video_ext)
            ]
            if len(vids) > 3:
                print(f"📦 RULE 4 (Folder > 3 videos): Zipping {item}")
                folder_size = get_dir_size(item_path)
                orig = os.getcwd()
                os.chdir(folder)
                zip_name = f"{item}.zip"
                cmd = ["7z", "a", "-mx0", zip_name, item]
                if folder_size > max_bytes:
                    cmd.insert(2, "-v10000m")
                subprocess.run(cmd, check=True)
                os.chdir(orig)
                shutil.rmtree(item_path)

remaining_videos = []
for r, _, files in os.walk(folder):
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
        print(f"📦 RULE 2 (Series > 3 eps): Grouping {len(vids)} episodes into {first_stem}.zip")
        create_7z_group(first_stem, vids, folder, max_bytes)
        for v in vids:
            if v in remaining_videos:
                remaining_videos.remove(v)

if len(remaining_videos) > 3:
    first_stem = os.path.splitext(os.path.basename(remaining_videos[0]))[0]
    print(f"📦 RULE 3 (Leftovers > 3 videos): Grouping {len(remaining_videos)} videos into {first_stem}.zip")
    create_7z_group(first_stem, remaining_videos, folder, max_bytes)
    remaining_videos.clear()

for r, dirs, files in os.walk(folder, topdown=False):
    if r == folder: continue
    for f in files:
        if f.lower().endswith(media_ext):
            src = os.path.join(r, f)
            dst = os.path.join(folder, f)
            if not os.path.exists(dst): 
                shutil.move(src, dst)
    shutil.rmtree(r, ignore_errors=True)

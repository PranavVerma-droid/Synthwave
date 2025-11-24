#!/usr/bin/env python3
"""
YouTube Music Playlist Downloader - Professional Self-Hosted Application
A modern web interface for managing and downloading YouTube music playlists with scheduled automation
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from flask_socketio import SocketIO, emit
import os
import json
import subprocess
import threading
import time
from pathlib import Path
import re
from datetime import datetime
import queue
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import logging

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)
socketio = SocketIO(app, cors_allowed_origins="*")

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize scheduler
scheduler = BackgroundScheduler()
scheduler.start()

# Configuration
SCRIPT_DIR = Path(__file__).parent.absolute()
CONFIG_FILE = SCRIPT_DIR / "config.json"
DEFAULT_CONFIG = {
    "BASE_FOLDER": "/music",
    "DOWNLOADER_PATH": "/binary/yt-dlp",
    "RECORD_FILE_NAME": ".downloaded_videos.txt",
    "PARALLEL_LIMIT": 4,
    "PLAYLIST_M3U_FOLDER": "/playlists",
    "MUSIC_MOUNT_PATH": "/music",
    "PLAYLISTS": [],
    "CRON_ENABLED": False,
    "CRON_SCHEDULE": {
        "minute": "0",
        "hour": "2",
        "day": "*",
        "month": "*",
        "day_of_week": "*"
    },
    "LAST_RUN": None,
    "NEXT_RUN": None,
    "TIMEOUT_METADATA": 120,
    "TIMEOUT_DOWNLOAD": 600,
    "MAX_RETRIES": 3
}

# Global state
download_queue = queue.Queue()
download_status = {
    "is_running": False,
    "current_playlist": None,
    "current_song": None,
    "progress": 0,
    "total": 0,
    "logs": []
}


def load_config():
    """Load configuration from JSON file"""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    return DEFAULT_CONFIG.copy()


def save_config(config):
    """Save configuration to JSON file"""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)


def log_message(message, level="info"):
    """Add log message and emit to web clients"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = {
        "timestamp": timestamp,
        "level": level,
        "message": message
    }
    download_status["logs"].append(log_entry)
    # Keep only last 1000 logs
    if len(download_status["logs"]) > 1000:
        download_status["logs"] = download_status["logs"][-1000:]
    
    socketio.emit('log', log_entry)


def get_video_id(url):
    """Extract video ID from YouTube URL"""
    if "youtu.be/" in url:
        match = re.search(r'youtu\.be/([^?&]+)', url)
        if match:
            return match.group(1)
    elif "youtube.com/watch" in url:
        match = re.search(r'[?&]v=([^&]+)', url)
        if match:
            return match.group(1)
    return None


def extract_playlist_id(url):
    """Extract playlist ID from URL"""
    match = re.search(r'list=([^&]+)', url)
    if match:
        return match.group(1)
    return None


def is_album_url(url):
    """Check if URL is an album"""
    return "/album/" in url or "OLAK5uy_" in url


def song_exists(video_id, base_folder):
    """Check if song already exists"""
    try:
        result = subprocess.run(
            ["find", base_folder, "-type", "f", "-name", f"*{video_id}.mp3"],
            capture_output=True,
            text=True,
            timeout=10
        )
        return bool(result.stdout.strip())
    except:
        return False


def find_song_by_id(video_id, base_folder):
    """Find song file by video ID"""
    try:
        result = subprocess.run(
            ["find", base_folder, "-type", "f", "-name", f"*{video_id}.mp3"],
            capture_output=True,
            text=True,
            timeout=10
        )
        files = result.stdout.strip().split('\n')
        return files[0] if files and files[0] else None
    except:
        return None


def update_song_metadata(song_file, album_name, track_number):
    """Update song metadata using ffmpeg"""
    try:
        temp_file = f"{song_file}.tmp.mp3"
        result = subprocess.run([
            "ffmpeg", "-i", song_file,
            "-metadata", f"album={album_name}",
            "-metadata", f"track={track_number}",
            "-c", "copy", "-y", temp_file
        ], capture_output=True, timeout=30)
        
        if result.returncode == 0:
            os.replace(temp_file, song_file)
            return True
        else:
            if os.path.exists(temp_file):
                os.remove(temp_file)
            return False
    except Exception as e:
        log_message(f"Failed to update metadata: {str(e)}", "error")
        return False


def run_command_with_retry(cmd, timeout=60, max_retries=3, retry_delay=5):
    """Run command with retry logic"""
    for attempt in range(max_retries):
        try:
            log_message(f"Running command (attempt {attempt + 1}/{max_retries})...", "info")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return result
        except subprocess.TimeoutExpired:
            if attempt < max_retries - 1:
                log_message(f"Command timed out, retrying in {retry_delay}s...", "warning")
                time.sleep(retry_delay)
            else:
                log_message(f"Command timed out after {max_retries} attempts", "error")
                raise
        except Exception as e:
            if attempt < max_retries - 1:
                log_message(f"Command failed: {str(e)}, retrying in {retry_delay}s...", "warning")
                time.sleep(retry_delay)
            else:
                raise
    return None


def download_song(video_url, video_id, target_folder, album_name=None, track_number=None, config=None):
    """Download a single song"""
    if config is None:
        config = load_config()
    
    downloader = config["DOWNLOADER_PATH"]
    timeout_download = config.get("TIMEOUT_DOWNLOAD", 600)
    max_retries = config.get("MAX_RETRIES", 3)
    
    output_template = f"{target_folder}/%(artist)s - %(title)s - {video_id}.%(ext)s"
    
    cmd = [
        downloader, "-o", output_template,
        "--format", "bestaudio[ext=m4a]/best",
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        "--embed-thumbnail",
        "--convert-thumbnail", "png",
        "--add-metadata",
        "--parse-metadata", "%(title)s:%(meta_title)s",
        "--parse-metadata", "%(artist)s:%(meta_artist)s",
        "--no-overwrites",
        video_url
    ]
    
    if album_name:
        cmd.extend(["--parse-metadata", f"{album_name}:%(meta_album)s"])
        if track_number:
            cmd.extend(["--parse-metadata", f"{track_number}:%(meta_track)s"])
            cmd.extend(["--ppa", "EmbedThumbnail+ffmpeg_o:-c:v png -vf crop=\"'if(gt(ih,iw),iw,ih)':'if(gt(iw,ih),ih,iw)'\""])
    else:
        cmd.extend(["--parse-metadata", "Unsorted Songs:%(meta_album)s"])
        cmd.extend(["--ppa", "EmbedThumbnail+ffmpeg_o:-c:v png -vf crop=\"'if(gt(ih,iw),iw,ih)':'if(gt(iw,ih),ih,iw)'\""])
    
    try:
        result = run_command_with_retry(cmd, timeout=timeout_download, max_retries=max_retries)
        return result and result.returncode == 0
    except Exception as e:
        log_message(f"Download failed: {str(e)}", "error")
        return False


def download_album_artwork(album_url, album_folder, config=None):
    """Download album artwork"""
    if config is None:
        config = load_config()
    
    downloader = config["DOWNLOADER_PATH"]
    timeout_metadata = config.get("TIMEOUT_METADATA", 120)
    
    artwork_file = os.path.join(album_folder, "folder.png")
    
    if os.path.exists(artwork_file):
        log_message("Album artwork already exists", "info")
        return True
    
    # Clean up existing artwork
    for file in Path(album_folder).glob("folder.*"):
        file.unlink()
    
    try:
        cmd = [
            downloader,
            "--write-thumbnail",
            "--convert-thumbnails", "png",
            "--skip-download",
            "-o", f"{album_folder}/folder.%(ext)s",
            album_url
        ]
        
        result = subprocess.run(cmd, capture_output=True, timeout=timeout_metadata)
        
        if result.returncode == 0:
            # Clean up non-png files
            for file in Path(album_folder).glob("folder.*"):
                if file.suffix != ".png":
                    file.unlink()
            
            # Crop to square if ImageMagick is available
            try:
                subprocess.run([
                    "convert", artwork_file,
                    "-gravity", "center",
                    "-crop", "1:1",
                    "+repage",
                    f"{artwork_file}.tmp"
                ], timeout=30)
                
                if os.path.exists(f"{artwork_file}.tmp"):
                    os.replace(f"{artwork_file}.tmp", artwork_file)
                    log_message("Album artwork cropped to square", "success")
            except:
                log_message("Keeping original aspect ratio", "info")
            
            return True
    except Exception as e:
        log_message(f"Failed to download album artwork: {str(e)}", "error")
    
    return False


def generate_m3u_playlist(playlist_name, playlist_url, song_list, config):
    """Generate M3U playlist file"""
    if is_album_url(playlist_url):
        log_message(f"Skipping M3U generation for album: {playlist_name}", "info")
        return
    
    log_message(f"Generating M3U playlist for: {playlist_name}", "info")
    
    playlist_id = extract_playlist_id(playlist_url)
    if not playlist_id:
        log_message(f"Could not extract playlist ID from URL: {playlist_url}", "warning")
        return
    
    m3u_folder = Path(config["PLAYLIST_M3U_FOLDER"])
    m3u_folder.mkdir(parents=True, exist_ok=True)
    
    m3u_file = m3u_folder / f"{playlist_id}.m3u"
    
    base_folder = config["BASE_FOLDER"]
    music_mount = config["MUSIC_MOUNT_PATH"]
    
    with open(m3u_file, 'w') as f:
        f.write(f'#GONIC-NAME:"{playlist_name}"\n')
        f.write('#GONIC-COMMENT:""\n')
        f.write('#GONIC-IS-PUBLIC:"false"\n')
        
        for song in song_list:
            video_id = song.get('video_id')
            if not video_id:
                continue
            
            found_file = find_song_by_id(video_id, base_folder)
            if found_file and os.path.exists(found_file):
                relative_path = found_file.replace(base_folder, music_mount)
                f.write(f"{relative_path}\n")
    
    log_message(f"Created M3U playlist: {m3u_file.name}", "success")


def process_playlist(playlist_url, config):
    """Process a single playlist or album"""
    downloader = config["DOWNLOADER_PATH"]
    base_folder = config["BASE_FOLDER"]
    timeout_metadata = config.get("TIMEOUT_METADATA", 120)
    max_retries = config.get("MAX_RETRIES", 3)
    
    log_message(f"Processing: {playlist_url}", "info")
    
    # Get playlist name with retry
    try:
        log_message("Fetching playlist metadata...", "info")
        result = run_command_with_retry(
            [downloader, "--print", "%(playlist_title)s", playlist_url],
            timeout=timeout_metadata,
            max_retries=max_retries
        )
        
        if result and result.stdout:
            playlist_name = result.stdout.strip().split('\n')[0]
        else:
            log_message("Failed to fetch playlist title (empty response)", "error")
            return
            
    except subprocess.TimeoutExpired:
        log_message(f"Failed to fetch playlist title: timeout after {timeout_metadata}s", "error")
        return
    except Exception as e:
        log_message(f"Failed to fetch playlist title: {str(e)}", "error")
        return
    
    if not playlist_name:
        log_message("Failed to fetch playlist title, skipping...", "error")
        return
    
    # Clean playlist name
    if is_album_url(playlist_url):
        playlist_name = re.sub(r'^Album - ', '', playlist_name)
    
    playlist_name = re.sub(r'[^\w\s._-]', '', playlist_name)
    playlist_name = re.sub(r'\s+', ' ', playlist_name).strip()
    
    download_status["current_playlist"] = playlist_name
    
    # Emit initial status
    socketio.emit('progress', {
        'current': 0,
        'total': 0,
        'playlist': playlist_name,
        'song': 'Fetching song list...'
    })
    
    # Determine target folder
    if is_album_url(playlist_url):
        target_folder = os.path.join(base_folder, playlist_name)
    else:
        target_folder = os.path.join(base_folder, "Unsorted Songs")
    
    os.makedirs(target_folder, exist_ok=True)
    
    log_message(f"{'Album' if is_album_url(playlist_url) else 'Playlist'}: '{playlist_name}'", "info")
    log_message(f"Folder: {target_folder}", "info")
    
    # Get song list with retry
    try:
        log_message("Fetching song list...", "info")
        result = run_command_with_retry(
            [downloader, "--flat-playlist", "--print", "%(playlist_index)s:%(title)s:%(id)s", playlist_url],
            timeout=timeout_metadata,
            max_retries=max_retries
        )
        
        if result and result.stdout:
            song_lines = result.stdout.strip().split('\n')
        else:
            log_message("Failed to retrieve song list (empty response)", "error")
            return
            
    except subprocess.TimeoutExpired:
        log_message(f"Failed to retrieve song list: timeout after {timeout_metadata}s", "error")
        return
    except Exception as e:
        log_message(f"Failed to retrieve song list: {str(e)}", "error")
        return
    
    if not song_lines or not song_lines[0]:
        log_message("Failed to retrieve song list, skipping...", "error")
        return
    
    song_list = []
    for line in song_lines:
        parts = line.split(':', 2)
        if len(parts) == 3:
            song_list.append({
                'index': parts[0],
                'title': parts[1],
                'video_id': parts[2]
            })
    
    total_songs = len(song_list)
    log_message(f"Found {total_songs} songs", "success")
    
    download_status["total"] = total_songs
    download_status["progress"] = 0
    
    # Emit updated total
    socketio.emit('progress', {
        'current': 0,
        'total': total_songs,
        'playlist': playlist_name,
        'song': 'Starting download...'
    })
    
    record_file = os.path.join(base_folder, config["RECORD_FILE_NAME"])
    
    # Process each song
    for idx, song in enumerate(song_list, 1):
        video_id = song['video_id']
        title = song['title']
        
        download_status["current_song"] = f"{title} ({idx}/{total_songs})"
        download_status["progress"] = idx
        socketio.emit('progress', {
            'current': idx,
            'total': total_songs,
            'playlist': playlist_name,
            'song': title
        })
        
        log_message(f"[{idx}/{total_songs}] {title} (ID: {video_id})", "info")
        
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        
        # Check if song exists
        if song_exists(video_id, base_folder):
            existing_file = find_song_by_id(video_id, base_folder)
            current_folder = os.path.dirname(existing_file)
            
            if current_folder == target_folder:
                log_message("Song already in correct folder", "success")
                if is_album_url(playlist_url):
                    update_song_metadata(existing_file, playlist_name, idx)
            else:
                log_message(f"Song exists in: {os.path.basename(current_folder)}", "info")
                if is_album_url(playlist_url):
                    log_message("Moving to album and updating metadata...", "info")
                    new_path = os.path.join(target_folder, os.path.basename(existing_file))
                    os.rename(existing_file, new_path)
                    update_song_metadata(new_path, playlist_name, idx)
                    log_message("Song relocated to album", "success")
                else:
                    log_message("Song already downloaded", "success")
            
            # Record in downloaded file
            if not os.path.exists(record_file) or video_id not in open(record_file).read():
                with open(record_file, 'a') as f:
                    f.write(f"{video_id}\n")
        else:
            log_message("Downloading new song...", "info")
            
            if is_album_url(playlist_url):
                success = download_song(video_url, video_id, target_folder, playlist_name, idx, config)
            else:
                success = download_song(video_url, video_id, target_folder, None, None, config)
            
            if success:
                log_message("Downloaded successfully", "success")
                with open(record_file, 'a') as f:
                    f.write(f"{video_id}\n")
            else:
                log_message("Download failed", "error")
    
    # Download album artwork if it's an album
    if is_album_url(playlist_url):
        download_album_artwork(playlist_url, target_folder, config)
    
    # Generate M3U playlist
    generate_m3u_playlist(playlist_name, playlist_url, song_list, config)


def download_worker():
    """Background worker for processing downloads"""
    while True:
        try:
            task = download_queue.get(timeout=1)
            if task is None:  # Shutdown signal
                break
            
            playlist_urls = task.get('playlists', [])
            config = load_config()
            
            # Reset progress state
            download_status["progress"] = 0
            download_status["total"] = 0
            download_status["current_playlist"] = None
            download_status["current_song"] = None
            
            # Update downloader
            log_message("Updating downloader...", "info")
            socketio.emit('progress', {
                'current': 0,
                'total': 0,
                'playlist': 'System',
                'song': 'Updating downloader...'
            })
            
            try:
                result = subprocess.run(
                    [config["DOWNLOADER_PATH"], "-U"],
                    capture_output=True,
                    timeout=60
                )
                if result.returncode == 0:
                    log_message("Downloader updated successfully", "success")
                else:
                    log_message("Failed to update downloader", "warning")
            except Exception as e:
                log_message(f"Update check failed: {str(e)}", "warning")
            
            # Categorize URLs
            album_urls = []
            playlist_urls_list = []
            
            for url in playlist_urls:
                if is_album_url(url):
                    album_urls.append(url)
                else:
                    playlist_urls_list.append(url)
            
            log_message(f"Found {len(album_urls)} album(s) and {len(playlist_urls_list)} playlist(s)", "info")
            
            # Process albums first
            if album_urls:
                log_message("PASS 1: Processing Albums", "info")
                for url in album_urls:
                    process_playlist(url, config)
            
            # Process playlists
            if playlist_urls_list:
                log_message("PASS 2: Processing Playlists", "info")
                for url in playlist_urls_list:
                    process_playlist(url, config)
            
            log_message("All downloads completed!", "success")
            download_status["is_running"] = False
            download_status["current_playlist"] = "Completed"
            download_status["current_song"] = "All downloads finished"
            socketio.emit('download_complete', {'success': True})
            
        except queue.Empty:
            continue
        except Exception as e:
            log_message(f"Worker error: {str(e)}", "error")
            download_status["is_running"] = False
            download_status["current_playlist"] = "Error"
            download_status["current_song"] = str(e)
            socketio.emit('download_complete', {'success': False, 'error': str(e)})


# Start worker thread
worker_thread = threading.Thread(target=download_worker, daemon=True)
worker_thread.start()


@app.route('/')
def index():
    """Main page"""
    config = load_config()
    return render_template('index.html', config=config, status=download_status)


@app.route('/api/config', methods=['GET', 'POST'])
def api_config():
    """Get or update configuration"""
    if request.method == 'GET':
        return jsonify(load_config())
    
    elif request.method == 'POST':
        config = load_config()
        data = request.json
        
        # Update configuration
        for key in ['BASE_FOLDER', 'DOWNLOADER_PATH', 'RECORD_FILE_NAME', 
                    'PARALLEL_LIMIT', 'PLAYLIST_M3U_FOLDER', 'MUSIC_MOUNT_PATH']:
            if key in data:
                config[key] = data[key]
        
        save_config(config)
        return jsonify({'success': True, 'config': config})


@app.route('/api/playlists', methods=['GET', 'POST', 'DELETE'])
def api_playlists():
    """Manage playlists"""
    config = load_config()
    
    if request.method == 'GET':
        return jsonify({'playlists': config.get('PLAYLISTS', [])})
    
    elif request.method == 'POST':
        data = request.json
        url = data.get('url', '').strip()
        
        if not url:
            return jsonify({'success': False, 'error': 'URL is required'}), 400
        
        if url not in config['PLAYLISTS']:
            config['PLAYLISTS'].append(url)
            save_config(config)
        
        return jsonify({'success': True, 'playlists': config['PLAYLISTS']})
    
    elif request.method == 'DELETE':
        data = request.json
        url = data.get('url', '').strip()
        
        if url in config['PLAYLISTS']:
            config['PLAYLISTS'].remove(url)
            save_config(config)
        
        return jsonify({'success': True, 'playlists': config['PLAYLISTS']})


@app.route('/api/download/start', methods=['POST'])
def start_download():
    """Start download process"""
    if download_status["is_running"]:
        return jsonify({'success': False, 'error': 'Download already in progress'}), 400
    
    data = request.json
    playlists = data.get('playlists', [])
    
    if not playlists:
        config = load_config()
        playlists = config.get('PLAYLISTS', [])
    
    if not playlists:
        return jsonify({'success': False, 'error': 'No playlists configured'}), 400
    
    download_status["is_running"] = True
    download_status["logs"] = []
    download_queue.put({'playlists': playlists})
    
    return jsonify({'success': True, 'message': 'Download started'})


@app.route('/api/download/status')
def download_status_endpoint():
    """Get current download status"""
    return jsonify(download_status)


@app.route('/api/logs')
def get_logs():
    """Get download logs"""
    return jsonify({'logs': download_status["logs"]})


@app.route('/api/cron', methods=['GET', 'POST', 'DELETE'])
def api_cron():
    """Manage cron schedule"""
    config = load_config()
    
    if request.method == 'GET':
        # Get next run time if scheduled
        next_run = None
        if config.get('CRON_ENABLED'):
            job = scheduler.get_job('download_job')
            if job:
                next_run = job.next_run_time.strftime('%Y-%m-%d %H:%M:%S') if job.next_run_time else None
        
        return jsonify({
            'enabled': config.get('CRON_ENABLED', False),
            'schedule': config.get('CRON_SCHEDULE', {}),
            'last_run': config.get('LAST_RUN'),
            'next_run': next_run
        })
    
    elif request.method == 'POST':
        data = request.json
        enabled = data.get('enabled', False)
        schedule = data.get('schedule', {})
        
        config['CRON_ENABLED'] = enabled
        config['CRON_SCHEDULE'] = schedule
        
        # Remove existing job if any
        try:
            scheduler.remove_job('download_job')
        except:
            pass
        
        # Add new job if enabled
        if enabled:
            try:
                trigger = CronTrigger(
                    minute=schedule.get('minute', '0'),
                    hour=schedule.get('hour', '2'),
                    day=schedule.get('day', '*'),
                    month=schedule.get('month', '*'),
                    day_of_week=schedule.get('day_of_week', '*')
                )
                scheduler.add_job(
                    func=scheduled_download,
                    trigger=trigger,
                    id='download_job',
                    replace_existing=True
                )
                log_message(f"Cron job scheduled: {schedule}", "success")
            except Exception as e:
                log_message(f"Failed to schedule cron job: {str(e)}", "error")
                return jsonify({'success': False, 'error': str(e)}), 400
        else:
            log_message("Cron job disabled", "info")
        
        save_config(config)
        
        # Get next run time
        next_run = None
        if enabled:
            job = scheduler.get_job('download_job')
            if job:
                next_run = job.next_run_time.strftime('%Y-%m-%d %H:%M:%S') if job.next_run_time else None
        
        return jsonify({
            'success': True,
            'enabled': enabled,
            'schedule': schedule,
            'next_run': next_run
        })
    
    elif request.method == 'DELETE':
        try:
            scheduler.remove_job('download_job')
        except:
            pass
        
        config['CRON_ENABLED'] = False
        save_config(config)
        log_message("Cron job removed", "info")
        
        return jsonify({'success': True})


def scheduled_download():
    """Function called by scheduler"""
    config = load_config()
    
    if download_status["is_running"]:
        log_message("Scheduled download skipped - download already in progress", "warning")
        return
    
    log_message("Starting scheduled download", "info")
    
    playlists = config.get('PLAYLISTS', [])
    if not playlists:
        log_message("No playlists configured for scheduled download", "warning")
        return
    
    download_status["is_running"] = True
    download_queue.put({'playlists': playlists})
    
    # Update last run time
    config['LAST_RUN'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    save_config(config)


@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    emit('status', download_status)


@socketio.on('request_logs')
def handle_request_logs():
    """Send all logs to client"""
    emit('all_logs', {'logs': download_status["logs"]})


if __name__ == '__main__':
    # Ensure config file exists
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG)
    
    # Initialize cron job if enabled
    config = load_config()
    if config.get('CRON_ENABLED', False):
        schedule = config.get('CRON_SCHEDULE', {})
        try:
            trigger = CronTrigger(
                minute=schedule.get('minute', '0'),
                hour=schedule.get('hour', '2'),
                day=schedule.get('day', '*'),
                month=schedule.get('month', '*'),
                day_of_week=schedule.get('day_of_week', '*')
            )
            scheduler.add_job(
                func=scheduled_download,
                trigger=trigger,
                id='download_job',
                replace_existing=True
            )
            logger.info(f"Cron job initialized: {schedule}")
        except Exception as e:
            logger.error(f"Failed to initialize cron job: {str(e)}")
    
    # Run the app
    logger.info("Starting YouTube Music Playlist Downloader")
    socketio.run(app, host='0.0.0.0', port=5000, debug=True, allow_unsafe_werkzeug=True)

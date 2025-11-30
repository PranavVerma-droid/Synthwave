from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_file
from flask_socketio import SocketIO, emit
import os
import json
import yaml
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
import fcntl
import tempfile
import yt_dlp

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)
socketio = SocketIO(app, 
                    cors_allowed_origins="*",
                    async_mode='eventlet',
                    ping_timeout=60,
                    ping_interval=25,
                    logger=True,
                    engineio_logger=False)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize scheduler
scheduler = BackgroundScheduler()
scheduler.start()

# Configuration
SCRIPT_DIR = Path(__file__).parent.absolute()
CONFIG_DIR = SCRIPT_DIR / "config"
LOGS_DIR = SCRIPT_DIR / "logs"
CONFIG_FILE = CONFIG_DIR / "config.yaml"

# Ensure directories exist
CONFIG_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

DEFAULT_CONFIG = {
    "BASE_FOLDER": "/music",
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
    "TIMEOUT_METADATA": 600,
    "TIMEOUT_DOWNLOAD": 1800,
    "MAX_RETRIES": 3,
    "DEBUG_MODE": False,
    "COOKIES_ENABLED": False,
    "DOWNLOAD_MODE": "both"  # Options: "both", "playlists_only", "albums_only"
}

# Global state
download_queue = queue.Queue()
download_status = {
    "is_running": False,
    "current_playlist": None,
    "current_song": None,
    "progress": 0,
    "total": 0,
    "logs": [],
    "debug_logs": [],
    "cancel_requested": False
}


def load_config():
    """Load configuration from YAML file"""
    if CONFIG_FILE.exists():
        max_retries = 3
        for attempt in range(max_retries):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    # Acquire shared lock for reading
                    fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                    try:
                        content = f.read()
                        if not content or not content.strip():
                            logger.warning(f"Config file is empty (attempt {attempt + 1}/{max_retries})")
                            if attempt < max_retries - 1:
                                time.sleep(0.1)  # Wait briefly and retry
                                continue
                            else:
                                logger.error("Config file empty after retries, using defaults")
                                return DEFAULT_CONFIG.copy()
                        
                        config = yaml.safe_load(content)
                        
                        # Validate config has required keys
                        if not isinstance(config, dict) or 'BASE_FOLDER' not in config:
                            logger.warning(f"Config file corrupted (attempt {attempt + 1}/{max_retries})")
                            if attempt < max_retries - 1:
                                time.sleep(0.1)
                                continue
                            else:
                                logger.error("Config file corrupted after retries, using defaults")
                                return DEFAULT_CONFIG.copy()
                        
                        return config
                    finally:
                        # Release lock
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except yaml.YAMLError as e:
                logger.error(f"YAML parse error in config file (attempt {attempt + 1}/{max_retries}): {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(0.1)
                    continue
                else:
                    logger.error("Failed to load config after retries, using defaults")
                    return DEFAULT_CONFIG.copy()
            except Exception as e:
                logger.error(f"Error loading config (attempt {attempt + 1}/{max_retries}): {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(0.1)
                    continue
                else:
                    logger.error("Failed to load config after retries, using defaults")
                    return DEFAULT_CONFIG.copy()
    return DEFAULT_CONFIG.copy()


def save_config(config):
    """Save configuration to YAML file using atomic write"""
    max_retries = 5
    retry_delay = 0.1
    
    for attempt in range(max_retries):
        try:
            # Write to temporary file first (in same directory to avoid cross-device issues)
            temp_fd, temp_path = tempfile.mkstemp(dir=CONFIG_DIR, prefix='.config_', suffix='.yaml.tmp')
            try:
                with os.fdopen(temp_fd, 'w') as f:
                    # Acquire exclusive lock for writing
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                    try:
                        yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
                        f.flush()
                        os.fsync(f.fileno())  # Ensure data is written to disk
                    finally:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                
                # Use rename with retry for cross-device scenarios
                try:
                    os.replace(temp_path, CONFIG_FILE)
                    logger.debug("Config saved successfully")
                    return  # Success!
                except OSError as e:
                    if e.errno == 16:  # EBUSY - Device or resource busy
                        if attempt < max_retries - 1:
                            logger.warning(f"Config file busy, retrying ({attempt + 1}/{max_retries})...")
                            time.sleep(retry_delay * (attempt + 1))  # Exponential backoff
                            # Clean up and retry
                            if os.path.exists(temp_path):
                                os.unlink(temp_path)
                            continue
                        else:
                            # Last resort: try direct write
                            logger.warning("Using fallback: direct write to config file")
                            with open(CONFIG_FILE, 'w') as f:
                                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                                try:
                                    yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
                                    f.flush()
                                    os.fsync(f.fileno())
                                finally:
                                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                            if os.path.exists(temp_path):
                                os.unlink(temp_path)
                            return
                    raise
            except Exception as e:
                # Clean up temp file on error
                if os.path.exists(temp_path):
                    try:
                        os.unlink(temp_path)
                    except:
                        pass
                raise
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"Save config attempt {attempt + 1} failed: {str(e)}")
                time.sleep(retry_delay * (attempt + 1))
                continue
            else:
                logger.error(f"Failed to save config after {max_retries} attempts: {str(e)}")
                raise


def log_message(message, level="info", is_debug=False):
    """Add log message and emit to web clients"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = {
        "timestamp": timestamp,
        "level": level,
        "message": message
    }
    
    if is_debug:
        download_status["debug_logs"].append(log_entry)
        # Keep only last 2000 debug logs
        if len(download_status["debug_logs"]) > 2000:
            download_status["debug_logs"] = download_status["debug_logs"][-2000:]
        try:
            socketio.emit('debug_log', log_entry, namespace='/')
        except Exception as e:
            logger.error(f"Failed to emit debug log via socketio: {str(e)}")
        
        # Always write debug logs to file
        write_to_log_file(message, level.upper())
    else:
        download_status["logs"].append(log_entry)
        # Keep only last 1000 logs
        if len(download_status["logs"]) > 1000:
            download_status["logs"] = download_status["logs"][-1000:]
        try:
            socketio.emit('log', log_entry, namespace='/')
        except Exception as e:
            logger.error(f"Failed to emit log via socketio: {str(e)}")
        
        # Always write regular logs to file
        write_to_log_file(message, level.upper())


# Log History Management
LOGS_INFO_FILE = LOGS_DIR / "logs-info.json"
current_log_file = None
current_log_handler = None

# Playlist Info Cache
PLAYLIST_INFO_FILE = CONFIG_DIR / "playlist-info.json"


def load_playlist_info():
    """Load playlist info cache from JSON file"""
    if PLAYLIST_INFO_FILE.exists():
        try:
            with open(PLAYLIST_INFO_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading playlist info: {e}")
    return {}


def save_playlist_info(playlist_info):
    """Save playlist info cache to JSON file"""
    try:
        with open(PLAYLIST_INFO_FILE, 'w', encoding='utf-8') as f:
            json.dump(playlist_info, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error saving playlist info: {e}")


def is_album_url(url):
    """Check if URL is an album"""
    return "/album/" in url or "OLAK5uy_" in url


def get_playlist_preview(url, force_refresh=False):
    """Get playlist preview from cache or fetch if not available"""
    playlist_info = load_playlist_info()
    
    # Check if we have cached data and not forcing refresh
    if not force_refresh and url in playlist_info:
        return playlist_info[url]
    
    # Fetch new data
    try:
        config = load_config()
        cookies_enabled = config.get('COOKIES_ENABLED', False)
        
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': 'in_playlist',
            'skip_download': True,
        }
        
        if cookies_enabled:
            cookies_file = CONFIG_DIR / "cookies.txt"
            if cookies_file.exists():
                ydl_opts['cookiefile'] = str(cookies_file)
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            if info is None:
                return None
            
            # Handle both playlists and albums
            title = info.get('title', info.get('album', 'Unknown Playlist'))
            # Remove "Album - " prefix if present
            if title.startswith('Album - '):
                title = title[8:]  # Remove first 8 characters
            
            # For albums, try multiple fields to get the artist name
            # Check entries first for artist info, as albums often have it in the track data
            uploader = info.get('uploader') or info.get('channel') or info.get('artist') or info.get('album_artist')
            
            # If still not found and this is an album, try to get artist from first entry
            if not uploader and is_album_url(url):
                entries = info.get('entries', [])
                if entries and len(entries) > 0:
                    first_entry = entries[0]
                    uploader = first_entry.get('artist') or first_entry.get('uploader') or first_entry.get('channel')
            
            # Final fallback
            if not uploader:
                uploader = info.get('uploader_id', 'Unknown Artist')
            thumbnail = info.get('thumbnail', info.get('thumbnails', [{}])[0].get('url', '') if info.get('thumbnails') else '')
            
            # Get entry count
            entries = info.get('entries', [])
            if entries:
                entry_count = len(entries)
            else:
                entry_count = info.get('playlist_count', info.get('track_count', 0))
            
            preview = {
                'title': title,
                'uploader': uploader,
                'thumbnail': thumbnail,
                'entry_count': entry_count,
                'description': info.get('description', ''),
                'url': url,
                'is_album': is_album_url(url),
                'cached_at': datetime.now().isoformat()
            }
            
            # Save to cache
            playlist_info[url] = preview
            save_playlist_info(playlist_info)
            
            return preview
            
    except Exception as e:
        logger.error(f"Error fetching playlist preview for {url}: {e}")
        return None


def load_logs_info():
    """Load logs info from JSON file"""
    if LOGS_INFO_FILE.exists():
        try:
            with open(LOGS_INFO_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading logs info: {str(e)}")
            return {"logs": []}
    return {"logs": []}


def save_logs_info(logs_info):
    """Save logs info to JSON file"""
    try:
        with open(LOGS_INFO_FILE, 'w') as f:
            json.dump(logs_info, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving logs info: {str(e)}")


def create_log_file(trigger_type="manual"):
    """Create a new log file for a download session"""
    global current_log_file, current_log_handler
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = f"log-{timestamp}.log"
    log_filepath = LOGS_DIR / log_filename
    
    # Create log file
    current_log_file = log_filepath
    current_log_handler = open(log_filepath, 'w', buffering=1)  # Line buffering
    
    # Update logs info
    logs_info = load_logs_info()
    
    # Check if this log file already exists in the logs info to prevent duplicates
    existing_entry = None
    for log_entry in logs_info["logs"]:
        if log_entry["filename"] == log_filename:
            existing_entry = log_entry
            break
    
    if existing_entry is None:
        # Only create new entry if it doesn't already exist
        log_entry = {
            "filename": log_filename,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "trigger_type": trigger_type,  # "manual" or "cron"
            "status": "running",
            "playlists_processed": 0,
            "songs_downloaded": 0,
            "errors": 0
        }
        logs_info["logs"].insert(0, log_entry)  # Add to beginning
        save_logs_info(logs_info)
    else:
        # Update existing entry status to running in case it was left in another state
        existing_entry["status"] = "running"
        save_logs_info(logs_info)
    
    # Write header to log file
    header = f"{'='*80}\n"
    header += f"Download Session Started: {log_entry['timestamp']}\n"
    header += f"Trigger Type: {trigger_type.upper()}\n"
    header += f"{'='*80}\n\n"
    current_log_handler.write(header)
    
    return log_filename


def write_to_log_file(message, level="INFO"):
    """Write a message to the current log file"""
    global current_log_handler
    
    if current_log_handler and not current_log_handler.closed:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"[{timestamp}] [{level}] {message}\n"
        try:
            current_log_handler.write(log_line)
        except Exception as e:
            logger.error(f"Error writing to log file: {str(e)}")


def close_log_file(playlists_processed=0, songs_downloaded=0, errors=0, status="completed"):
    """Close the current log file and update its metadata"""
    global current_log_file, current_log_handler
    
    if current_log_handler and not current_log_handler.closed:
        # Write footer
        footer = f"\n{'='*80}\n"
        footer += f"Download Session Ended: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        footer += f"Status: {status.upper()}\n"
        footer += f"Playlists Processed: {playlists_processed}\n"
        footer += f"Songs Downloaded: {songs_downloaded}\n"
        footer += f"Errors: {errors}\n"
        footer += f"{'='*80}\n"
        current_log_handler.write(footer)
        current_log_handler.close()
        
        # Update logs info
        if current_log_file:
            logs_info = load_logs_info()
            filename = current_log_file.name
            for log_entry in logs_info["logs"]:
                if log_entry["filename"] == filename:
                    log_entry["status"] = status
                    log_entry["playlists_processed"] = playlists_processed
                    log_entry["songs_downloaded"] = songs_downloaded
                    log_entry["errors"] = errors
                    log_entry["completed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    break
            save_logs_info(logs_info)
        
        current_log_file = None
        current_log_handler = None


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


class YtdlpLogger:
    """Custom logger for yt-dlp to capture output"""
    def __init__(self, debug_mode=False):
        self.debug_mode = debug_mode
    
    def debug(self, msg):
        if self.debug_mode and msg:
            log_message(msg, "info", is_debug=True)
    
    def info(self, msg):
        if msg:
            log_message(msg, "info", is_debug=self.debug_mode)
    
    def warning(self, msg):
        if msg:
            log_message(msg, "warning", is_debug=self.debug_mode)
    
    def error(self, msg):
        if msg:
            log_message(msg, "error")


def get_ytdlp_opts(config, output_template, extra_opts=None):
    """Get base yt-dlp options"""
    debug_mode = config.get("DEBUG_MODE", False)
    cookies_enabled = config.get("COOKIES_ENABLED", False)
    
    opts = {
        'outtmpl': output_template,
        'format': 'bestaudio[ext=m4a]/best',
        'writethumbnail': True,
        'logger': YtdlpLogger(debug_mode),
        'no_warnings': not debug_mode,
        'quiet': not debug_mode,
        'verbose': True,
        'no_color': True,
        "js_runtimes": {
            "node": {},
        }
    }
    
    if cookies_enabled:
        cookies_file = CONFIG_DIR / "cookies.txt"
        if cookies_file.exists():
            opts['cookiefile'] = str(cookies_file)
            if debug_mode:
                log_message(f"Using cookies file: {cookies_file}", "info", is_debug=True)
    
    if extra_opts:
        opts.update(extra_opts)
    
    return opts


def download_song(video_url, video_id, target_folder, album_name=None, track_number=None, config=None):
    """Download a single song using yt-dlp Python library"""
    if config is None:
        config = load_config()
    
    max_retries = config.get("MAX_RETRIES", 3)
    output_template = f"{target_folder}/%(artist)s - %(title)s - {video_id}.%(ext)s"
    
    # Build postprocessor args
    postprocessors = [
        {
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '0',
        },
        {
            'key': 'FFmpegMetadata',
            'add_metadata': True,
        },
        {
            'key': 'EmbedThumbnail',
            'already_have_thumbnail': False,
        }
    ]
    
    # Parse metadata options
    parse_metadata = [
        'title:%(meta_title)s',
        'artist:%(meta_artist)s',
    ]
    
    if album_name:
        parse_metadata.append(f'{album_name}:%(meta_album)s')
        if track_number:
            parse_metadata.append(f'{track_number}:%(meta_track)s')
    else:
        parse_metadata.append('Unsorted Songs:%(meta_album)s')
    
    ydl_opts = get_ytdlp_opts(config, output_template, {
        'postprocessors': postprocessors,
        'parse_metadata': parse_metadata,
        'postprocessor_args': {
            'EmbedThumbnail+ffmpeg_o': ['-c:v', 'png', '-vf', "crop='if(gt(ih,iw),iw,ih)':'if(gt(iw,ih),ih,iw)'"]
        },
        'overwrites': False,
        'ignoreerrors': False,  # Don't ignore errors during actual download
    })
    
    for attempt in range(max_retries):
        try:
            log_message(f"Downloading (attempt {attempt + 1}/{max_retries})...", "info")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])
            
            # Post-process: Extract, crop, and re-embed thumbnail using ImageMagick
            try:
                mp3_file = f"{target_folder}/*{video_id}.mp3"
                # Find the actual file
                import glob
                mp3_files = glob.glob(mp3_file)
                if mp3_files:
                    actual_mp3 = mp3_files[0]
                    temp_cover = f"{actual_mp3}.cover.png"
                    temp_cropped = f"{actual_mp3}.cover.cropped.png"
                    temp_mp3 = f"{actual_mp3}.tmp.mp3"
                    
                    # Extract embedded artwork
                    extract_result = subprocess.run([
                        "ffmpeg", "-i", actual_mp3,
                        "-an", "-vcodec", "copy",
                        "-y", temp_cover
                    ], capture_output=True, timeout=30)
                    
                    if extract_result.returncode == 0 and os.path.exists(temp_cover):
                        # Crop to square using ImageMagick
                        crop_result = subprocess.run([
                            "convert", temp_cover,
                            "-gravity", "center",
                            "-crop", "1:1",
                            "+repage",
                            temp_cropped
                        ], capture_output=True, timeout=30)
                        
                        if crop_result.returncode == 0 and os.path.exists(temp_cropped):
                            # Re-embed cropped artwork
                            embed_result = subprocess.run([
                                "ffmpeg", "-i", actual_mp3,
                                "-i", temp_cropped,
                                "-map", "0:0", "-map", "1:0",
                                "-c", "copy",
                                "-id3v2_version", "3",
                                "-metadata:s:v", "title=Album cover",
                                "-metadata:s:v", "comment=Cover (front)",
                                "-y", temp_mp3
                            ], capture_output=True, timeout=30)
                            
                            if embed_result.returncode == 0 and os.path.exists(temp_mp3):
                                os.replace(temp_mp3, actual_mp3)
                                log_message("Song artwork cropped to square", "success")
                            else:
                                if os.path.exists(temp_mp3):
                                    os.remove(temp_mp3)
                        
                        # Cleanup temp files
                        for temp_file in [temp_cover, temp_cropped]:
                            if os.path.exists(temp_file):
                                os.remove(temp_file)
            except Exception as crop_error:
                log_message(f"Failed to crop song artwork (non-critical): {str(crop_error)}", "warning")
            
            return True
        except yt_dlp.utils.DownloadError as e:
            error_msg = str(e)
            # Check if it's a permanent error (video unavailable, copyright claim, etc.)
            if any(keyword in error_msg.lower() for keyword in ['unavailable', 'copyright', 'removed', 'deleted', 'private']):
                log_message(f"Video permanently unavailable: {error_msg}", "error")
                return False  # Don't retry for permanent errors
            elif attempt < max_retries - 1:
                log_message(f"Download failed: {error_msg}, retrying...", "warning")
                time.sleep(5)
            else:
                log_message(f"Download failed after {max_retries} attempts: {error_msg}", "error")
                return False
        except Exception as e:
            if attempt < max_retries - 1:
                log_message(f"Download failed: {str(e)}, retrying...", "warning")
                time.sleep(5)
            else:
                log_message(f"Download failed after {max_retries} attempts: {str(e)}", "error")
                return False
    
    return False


def download_album_artwork(album_url, album_folder, config=None):
    """Download album artwork using yt-dlp Python library"""
    if config is None:
        config = load_config()
    
    artwork_file = os.path.join(album_folder, "folder.png")
    
    if os.path.exists(artwork_file):
        log_message("Album artwork already exists", "info")
        return True
    
    log_message("Downloading album artwork...", "info")
    
    # Clean up existing artwork
    for file in Path(album_folder).glob("folder.*"):
        try:
            file.unlink()
        except Exception as e:
            log_message(f"Could not remove old artwork file {file.name}: {str(e)}", "warning")
    
    try:
        # Method 1: Try with FFmpegThumbnailsConvertor postprocessor
        output_template = f"{album_folder}/folder.%(ext)s"
        ydl_opts = get_ytdlp_opts(config, output_template, {
            'writethumbnail': True,
            'skip_download': True,
            'ignoreerrors': False,
            'extract_flat': False,  # Need full extraction for thumbnails
            'postprocessors': [{
                'key': 'FFmpegThumbnailsConvertor',
                'format': 'png',
            }],
        })
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([album_url])
        
        # Check for any downloaded files
        artwork_files = list(Path(album_folder).glob("folder.*"))
        log_message(f"Found {len(artwork_files)} artwork file(s) after download", "info")
        
        # If PNG exists, we're done
        if os.path.exists(artwork_file):
            log_message(f"Album artwork downloaded as PNG: {os.path.getsize(artwork_file)} bytes", "success")
        else:
            # Try to find and convert any downloaded thumbnail
            for file in artwork_files:
                if file.suffix.lower() in ['.jpg', '.jpeg', '.webp']:
                    log_message(f"Converting {file.suffix} to PNG...", "info")
                    try:
                        # Use ffmpeg to convert to PNG
                        result = subprocess.run([
                            "ffmpeg", "-i", str(file),
                            "-y", artwork_file
                        ], capture_output=True, timeout=30)
                        
                        if result.returncode == 0 and os.path.exists(artwork_file):
                            log_message(f"Converted {file.suffix} to PNG successfully", "success")
                            file.unlink()  # Remove original
                            break
                        else:
                            log_message(f"FFmpeg conversion failed: {result.stderr.decode()}", "warning")
                    except Exception as conv_error:
                        log_message(f"Conversion error: {str(conv_error)}", "warning")
        
        # Clean up non-png files
        for file in Path(album_folder).glob("folder.*"):
            if file.suffix != ".png" and file.exists():
                try:
                    file.unlink()
                except Exception as e:
                    log_message(f"Could not remove {file.name}: {str(e)}", "warning")
        
        # Final verification
        if not os.path.exists(artwork_file):
            log_message("Album artwork file not created after all attempts", "error")
            return False
        
        # Crop to square if ImageMagick is available
        try:
            result = subprocess.run([
                "convert", artwork_file,
                "-gravity", "center",
                "-crop", "1:1",
                "+repage",
                f"{artwork_file}.tmp"
            ], capture_output=True, timeout=30)
            
            if result.returncode == 0 and os.path.exists(f"{artwork_file}.tmp"):
                os.replace(f"{artwork_file}.tmp", artwork_file)
                log_message("Album artwork downloaded and cropped to square", "success")
            else:
                log_message("Album artwork downloaded (original aspect ratio)", "success")
        except Exception as crop_error:
            log_message(f"Album artwork downloaded (crop skipped: {str(crop_error)})", "success")
        
        return True
    except Exception as e:
        log_message(f"Failed to download album artwork: {str(e)}", "error")
        # Try to log any files that were created
        artwork_files = list(Path(album_folder).glob("folder.*"))
        if artwork_files:
            log_message(f"Found files: {[f.name for f in artwork_files]}", "info")
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
    """Process a single playlist or album using yt-dlp Python library"""
    # Check for cancellation at the start
    if download_status["cancel_requested"]:
        log_message("Playlist processing cancelled before start", "warning")
        return {'songs_downloaded': 0, 'errors': 0}
    
    base_folder = config["BASE_FOLDER"]
    max_retries = config.get("MAX_RETRIES", 3)
    
    # Track statistics
    songs_downloaded = 0
    errors = 0
    
    log_message(f"Processing: {playlist_url}", "info")
    
    # Get playlist metadata with retry - use extract_flat to avoid failing on single unavailable videos
    playlist_name = None
    for attempt in range(max_retries):
        try:
            log_message(f"Fetching playlist metadata (attempt {attempt + 1}/{max_retries})...", "info")
            ydl_opts = get_ytdlp_opts(config, '', {
                'skip_download': True,
                'extract_flat': 'in_playlist',
                'ignoreerrors': True,
            })
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(playlist_url, download=False)
                playlist_name = info.get('title', info.get('playlist_title', ''))
            
            if playlist_name:
                break
        except Exception as e:
            if attempt < max_retries - 1:
                log_message(f"Failed to fetch playlist metadata: {str(e)}, retrying...", "warning")
                time.sleep(5)
            else:
                log_message(f"Failed to fetch playlist title after {max_retries} attempts: {str(e)}", "error")
                return {'songs_downloaded': 0, 'errors': 1}
    
    if not playlist_name:
        log_message("Failed to fetch playlist title, skipping...", "error")
        return {'songs_downloaded': 0, 'errors': 1}
    
    # Clean playlist name
    if is_album_url(playlist_url):
        playlist_name = re.sub(r'^Album - ', '', playlist_name)
    
    playlist_name = re.sub(r'[^\w\s._-]', '', playlist_name)
    playlist_name = re.sub(r'\s+', ' ', playlist_name).strip()
    
    download_status["current_playlist"] = playlist_name
    
    # Emit initial status
    try:
        socketio.emit('progress', {
            'current': 0,
            'total': 0,
            'playlist': playlist_name,
            'song': 'Fetching song list...'
        }, namespace='/')
    except Exception as e:
        logger.error(f"Failed to emit progress: {str(e)}")
    
    # Determine target folder
    if is_album_url(playlist_url):
        target_folder = os.path.join(base_folder, playlist_name)
    else:
        target_folder = os.path.join(base_folder, "Unsorted Songs")
    
    os.makedirs(target_folder, exist_ok=True)
    
    log_message(f"{'Album' if is_album_url(playlist_url) else 'Playlist'}: '{playlist_name}'", "info")
    log_message(f"Folder: {target_folder}", "info")
    
    # Get song list with retry
    song_list = []
    for attempt in range(max_retries):
        try:
            log_message(f"Fetching song list (attempt {attempt + 1}/{max_retries}, this may take a while for large playlists)...", "info")
            ydl_opts = get_ytdlp_opts(config, '', {
                'skip_download': True,
                'extract_flat': 'in_playlist',
                'quiet': True,
                'ignoreerrors': True,
            })
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(playlist_url, download=False)
                
                if 'entries' in info:
                    unavailable_count = 0
                    for idx, entry in enumerate(info['entries'], 1):
                        if entry:
                            song_list.append({
                                'index': str(idx),
                                'title': entry.get('title', 'Unknown'),
                                'video_id': entry.get('id', '')
                            })
                        else:
                            unavailable_count += 1
                    
                    if unavailable_count > 0:
                        log_message(f"Skipped {unavailable_count} unavailable video(s) in playlist", "warning")
            
            if song_list:
                break
        except Exception as e:
            if attempt < max_retries - 1:
                log_message(f"Failed to retrieve song list: {str(e)}, retrying...", "warning")
                time.sleep(5)
            else:
                log_message(f"Failed to retrieve song list after {max_retries} attempts: {str(e)}", "error")
                return {'songs_downloaded': 0, 'errors': 1}
    
    if not song_list:
        log_message("No available songs found in playlist", "warning")
        return {'songs_downloaded': 0, 'errors': 1}
    
    total_songs = len(song_list)
    log_message(f"Found {total_songs} songs", "success")
    
    # Check for cancellation before starting downloads
    if download_status["cancel_requested"]:
        log_message("Playlist processing cancelled before downloads", "warning")
        return {'songs_downloaded': 0, 'errors': 0}
    
    download_status["total"] = total_songs
    download_status["progress"] = 0
    
    # Emit updated total
    try:
        socketio.emit('progress', {
            'current': 0,
            'total': total_songs,
            'playlist': playlist_name,
            'song': 'Starting download...'
        }, namespace='/')
    except Exception as e:
        logger.error(f"Failed to emit progress: {str(e)}")
    
    record_file = os.path.join(base_folder, config["RECORD_FILE_NAME"])
    
    # Process each song
    for idx, song in enumerate(song_list, 1):
        # Check for cancellation
        if download_status["cancel_requested"]:
            log_message("Cancelling playlist processing...", "warning")
            return {'songs_downloaded': songs_downloaded, 'errors': errors}
        
        video_id = song['video_id']
        title = song['title']
        
        # Skip if video_id is missing or invalid
        if not video_id:
            log_message(f"[{idx}/{total_songs}] Skipping song with missing video ID: {title}", "warning")
            errors += 1
            continue
        
        download_status["current_song"] = f"{title} ({idx}/{total_songs})"
        download_status["progress"] = idx
        try:
            socketio.emit('progress', {
                'current': idx,
                'total': total_songs,
                'playlist': playlist_name,
                'song': title
            }, namespace='/')
        except Exception as e:
            logger.error(f"Failed to emit progress for song {idx}: {str(e)}")
        
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
                songs_downloaded += 1
                with open(record_file, 'a') as f:
                    f.write(f"{video_id}\n")
            else:
                log_message("Download failed", "error")
                errors += 1
    
    # Download album artwork if it's an album
    if is_album_url(playlist_url):
        download_album_artwork(playlist_url, target_folder, config)
    
    # Generate M3U playlist
    generate_m3u_playlist(playlist_name, playlist_url, song_list, config)
    
    return {'songs_downloaded': songs_downloaded, 'errors': errors}


def download_worker():
    """Background worker for processing downloads"""
    while True:
        try:
            task = download_queue.get(timeout=1)
            if task is None:  # Shutdown signal
                break
            
            # Double-check that we're not already running to prevent race conditions
            if download_status["is_running"]:
                log_message("Download already in progress, skipping duplicate task", "warning")
                continue
            
            playlist_urls = task.get('playlists', [])
            trigger_type = task.get('trigger_type', 'manual')
            force_mode = task.get('force_mode', None)  # Override download mode
            config = load_config()
            
            # Set running status before creating log file
            download_status["is_running"] = True
            
            # Create log file for this session
            create_log_file(trigger_type)
            
            playlists_processed = 0
            songs_downloaded = 0
            errors = 0
            
            # Reset progress state and cancel flag
            download_status["progress"] = 0
            download_status["total"] = 0
            download_status["current_playlist"] = None
            download_status["current_song"] = None
            download_status["cancel_requested"] = False
            
            # Update yt-dlp package
            log_message("Updating yt-dlp...", "info")
            try:
                socketio.emit('progress', {
                    'current': 0,
                    'total': 0,
                    'playlist': 'System',
                    'song': 'Updating yt-dlp...'
                }, namespace='/')
            except Exception as e:
                logger.error(f"Failed to emit progress: {str(e)}")
            
            try:
                result = subprocess.run(
                    ["pip", "install", "--upgrade", "yt-dlp[default]"],
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                if result.returncode == 0:
                    log_message("yt-dlp updated successfully", "success")
                else:
                    log_message(f"Failed to update yt-dlp: {result.stderr}", "warning")
                    errors += 1
            except Exception as e:
                log_message(f"Update check failed: {str(e)}", "warning")
                errors += 1
            
            # Categorize URLs
            album_urls = []
            playlist_urls_list = []
            
            for url in playlist_urls:
                if is_album_url(url):
                    album_urls.append(url)
                else:
                    playlist_urls_list.append(url)
            
            # Filter based on download mode (use force_mode if provided)
            download_mode = force_mode if force_mode else config.get('DOWNLOAD_MODE', 'both')
            if download_mode == 'playlists_only':
                album_urls = []  # Skip albums
            elif download_mode == 'albums_only':
                playlist_urls_list = []  # Skip playlists
            # If 'both', keep both lists as-is
            
            mode_source = "forced" if force_mode else "config"
            log_message(f"Download mode: {download_mode} ({mode_source})", "info")
            log_message(f"Processing {len(album_urls)} album(s) and {len(playlist_urls_list)} playlist(s)", "info")
            
            # Process albums first
            if album_urls:
                log_message("PASS 1: Processing Albums", "info")
                for url in album_urls:
                    if download_status["cancel_requested"]:
                        log_message("Download cancelled by user", "warning")
                        break
                    try:
                        stats = process_playlist(url, config)
                        playlists_processed += 1
                        songs_downloaded += stats.get('songs_downloaded', 0)
                        errors += stats.get('errors', 0)
                    except Exception as e:
                        log_message(f"Error processing album: {str(e)}", "error")
                        errors += 1
            
            # Process playlists
            if playlist_urls_list:
                log_message("PASS 2: Processing Playlists", "info")
                for url in playlist_urls_list:
                    if download_status["cancel_requested"]:
                        log_message("Download cancelled by user", "warning")
                        break
                    try:
                        stats = process_playlist(url, config)
                        playlists_processed += 1
                        songs_downloaded += stats.get('songs_downloaded', 0)
                        errors += stats.get('errors', 0)
                    except Exception as e:
                        log_message(f"Error processing playlist: {str(e)}", "error")
                        errors += 1
            
            # Check if cancelled
            if download_status["cancel_requested"]:
                log_message("Download process cancelled!", "warning")
                download_status["is_running"] = False
                download_status["current_playlist"] = "Cancelled"
                download_status["current_song"] = "Download cancelled by user"
                download_status["cancel_requested"] = False
                close_log_file(playlists_processed, songs_downloaded, errors, "cancelled")
                try:
                    socketio.emit('download_complete', {'success': False, 'cancelled': True}, namespace='/')
                except Exception as e:
                    logger.error(f"Failed to emit download_complete: {str(e)}")
            else:
                log_message("All downloads completed!", "success")
                download_status["is_running"] = False
                download_status["current_playlist"] = "Completed"
                download_status["current_song"] = "All downloads finished"
                close_log_file(playlists_processed, songs_downloaded, errors, "completed")
                try:
                    socketio.emit('download_complete', {'success': True}, namespace='/')
                except Exception as e:
                    logger.error(f"Failed to emit download_complete: {str(e)}")
            
        except queue.Empty:
            continue
        except Exception as e:
            log_message(f"Worker error: {str(e)}", "error")
            download_status["is_running"] = False
            download_status["current_playlist"] = "Error"
            download_status["current_song"] = str(e)
            close_log_file(0, 0, 1, "error")
            try:
                socketio.emit('download_complete', {'success': False, 'error': str(e)}, namespace='/')
            except Exception as emit_error:
                logger.error(f"Failed to emit error: {str(emit_error)}")


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
        config = load_config()
        # Check if cookies.txt exists
        cookies_file = CONFIG_DIR / "cookies.txt"
        config['COOKIES_FILE_EXISTS'] = cookies_file.exists()
        return jsonify({'config': config})
    
    elif request.method == 'POST':
        config = load_config()
        data = request.json
        
        # Update configuration
        for key in ['BASE_FOLDER', 'RECORD_FILE_NAME', 
                    'PARALLEL_LIMIT', 'PLAYLIST_M3U_FOLDER', 'MUSIC_MOUNT_PATH', 'DEBUG_MODE', 'COOKIES_ENABLED', 'DOWNLOAD_MODE']:
            if key in data:
                config[key] = data[key]
        
        save_config(config)
        # Check if cookies.txt exists
        cookies_file = CONFIG_DIR / "cookies.txt"
        config['COOKIES_FILE_EXISTS'] = cookies_file.exists()
        return jsonify({'success': True, 'config': config})


@app.route('/api/playlists', methods=['GET', 'POST', 'DELETE'])
def api_playlists():
    """Manage playlists"""
    config = load_config()
    
    if request.method == 'GET':
        playlists = config.get('PLAYLISTS', [])
        playlist_info = load_playlist_info()
        
        # Return playlists with their cached preview data
        playlists_with_previews = []
        for url in playlists:
            preview = playlist_info.get(url)
            if preview:
                playlists_with_previews.append(preview)
            else:
                # Return basic info for uncached playlists
                playlists_with_previews.append({
                    'url': url,
                    'is_album': is_album_url(url),
                    'cached': False
                })
        
        return jsonify({
            'playlists': playlists,
            'previews': playlists_with_previews
        })
    
    elif request.method == 'POST':
        data = request.json
        url = data.get('url', '').strip()
        
        if not url:
            return jsonify({'success': False, 'error': 'URL is required'}), 400
        
        if url not in config['PLAYLISTS']:
            config['PLAYLISTS'].append(url)
            save_config(config)
            
            # Fetch preview data for the new playlist
            preview = get_playlist_preview(url)
            
            return jsonify({
                'success': True, 
                'playlists': config['PLAYLISTS'],
                'preview': preview
            })
        
        return jsonify({'success': True, 'playlists': config['PLAYLISTS']})
    
    elif request.method == 'DELETE':
        data = request.json
        url = data.get('url', '').strip()
        
        if url in config['PLAYLISTS']:
            config['PLAYLISTS'].remove(url)
            save_config(config)
            
            # Optionally remove from cache
            playlist_info = load_playlist_info()
            if url in playlist_info:
                del playlist_info[url]
                save_playlist_info(playlist_info)
        
        return jsonify({'success': True, 'playlists': config['PLAYLISTS']})


@app.route('/api/playlists/preview', methods=['POST'])
def api_playlist_preview():
    """Fetch playlist preview metadata (individual or refresh)"""
    try:
        data = request.json
        url = data.get('url', '').strip()
        force_refresh = data.get('force_refresh', False)
        
        if not url:
            return jsonify({'success': False, 'error': 'URL is required'}), 400
        
        preview = get_playlist_preview(url, force_refresh=force_refresh)
        
        if preview:
            return jsonify({'success': True, 'preview': preview})
        else:
            return jsonify({'success': False, 'error': 'Could not fetch playlist info'}), 400
            
    except Exception as e:
        logger.error(f"Error in playlist preview API: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/playlists/refresh-all', methods=['POST'])
def api_refresh_all_playlists():
    """Refresh all playlist previews"""
    try:
        config = load_config()
        playlists = config.get('PLAYLISTS', [])
        
        refreshed = []
        failed = []
        
        for url in playlists:
            try:
                preview = get_playlist_preview(url, force_refresh=True)
                if preview:
                    refreshed.append(url)
                else:
                    failed.append(url)
            except Exception as e:
                logger.error(f"Failed to refresh {url}: {e}")
                failed.append(url)
        
        return jsonify({
            'success': True,
            'refreshed': len(refreshed),
            'failed': len(failed),
            'failed_urls': failed
        })
        
    except Exception as e:
        logger.error(f"Error refreshing all playlists: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


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
    
    download_status["cancel_requested"] = False
    download_status["logs"] = []
    download_queue.put({'playlists': playlists, 'trigger_type': 'manual'})
    
    return jsonify({'success': True, 'message': 'Download started'})


@app.route('/api/download/start-albums', methods=['POST'])
def start_albums_download():
    """Start download process for albums only"""
    if download_status["is_running"]:
        return jsonify({'success': False, 'error': 'Download already in progress'}), 400
    
    config = load_config()
    all_playlists = config.get('PLAYLISTS', [])
    
    if not all_playlists:
        return jsonify({'success': False, 'error': 'No playlists configured'}), 400
    
    # Count albums for user feedback
    album_count = sum(1 for url in all_playlists if is_album_url(url))
    
    if album_count == 0:
        return jsonify({'success': False, 'error': 'No albums found in playlists'}), 400
    
    download_status["cancel_requested"] = False
    download_status["logs"] = []
    
    # Pass all playlists but force albums_only mode
    download_queue.put({
        'playlists': all_playlists,
        'trigger_type': 'manual_albums',
        'force_mode': 'albums_only'
    })
    
    return jsonify({'success': True, 'message': f'Started downloading {album_count} album(s)'})


@app.route('/api/download/start-playlists', methods=['POST'])
def start_playlists_download():
    """Start download process for playlists only"""
    if download_status["is_running"]:
        return jsonify({'success': False, 'error': 'Download already in progress'}), 400
    
    config = load_config()
    all_playlists = config.get('PLAYLISTS', [])
    
    if not all_playlists:
        return jsonify({'success': False, 'error': 'No playlists configured'}), 400
    
    # Count playlists (non-albums) for user feedback
    playlist_count = sum(1 for url in all_playlists if not is_album_url(url))
    
    if playlist_count == 0:
        return jsonify({'success': False, 'error': 'No playlists found (only albums)'}), 400
    
    download_status["cancel_requested"] = False
    download_status["logs"] = []
    
    # Pass all playlists but force playlists_only mode
    download_queue.put({
        'playlists': all_playlists,
        'trigger_type': 'manual_playlists',
        'force_mode': 'playlists_only'
    })
    
    return jsonify({'success': True, 'message': f'Started downloading {playlist_count} playlist(s)'})


@app.route('/api/download/cancel', methods=['POST'])
def cancel_download():
    """Cancel ongoing download"""
    if not download_status["is_running"]:
        return jsonify({'success': False, 'error': 'No download in progress'}), 400
    
    download_status["cancel_requested"] = True
    log_message("Cancellation requested by user", "warning")
    
    return jsonify({'success': True, 'message': 'Download cancellation requested'})


@app.route('/api/download/status')
def download_status_endpoint():
    """Get current download status"""
    return jsonify(download_status)


@app.route('/api/cookies/upload', methods=['POST'])
def upload_cookies():
    """Upload cookies.txt file"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file provided'}), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'}), 400
        
        if not file.filename.endswith('.txt'):
            return jsonify({'success': False, 'error': 'File must be a .txt file'}), 400
        
        # Save the cookies file
        cookies_path = CONFIG_DIR / "cookies.txt"
        file.save(str(cookies_path))
        
        # Enable cookies in config
        config = load_config()
        config['COOKIES_ENABLED'] = True
        save_config(config)
        
        log_message("Cookies file uploaded successfully", "success")
        return jsonify({'success': True, 'message': 'Cookies file uploaded successfully'})
    
    except Exception as e:
        logger.error(f"Error uploading cookies: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cookies/delete', methods=['POST'])
def delete_cookies():
    """Delete cookies.txt file"""
    try:
        cookies_path = CONFIG_DIR / "cookies.txt"
        
        if cookies_path.exists():
            cookies_path.unlink()
        
        # Disable cookies in config
        config = load_config()
        config['COOKIES_ENABLED'] = False
        save_config(config)
        
        log_message("Cookies file deleted", "info")
        return jsonify({'success': True, 'message': 'Cookies file deleted successfully'})
    
    except Exception as e:
        logger.error(f"Error deleting cookies: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cookies/status')
def cookies_status():
    """Check if cookies file exists"""
    try:
        cookies_path = CONFIG_DIR / "cookies.txt"
        config = load_config()
        
        return jsonify({
            'success': True,
            'exists': cookies_path.exists(),
            'enabled': config.get('COOKIES_ENABLED', False)
        })
    
    except Exception as e:
        logger.error(f"Error checking cookies status: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/logs')
def get_logs():
    """Get download logs"""
    return jsonify({'logs': download_status["logs"]})


@app.route('/api/debug_logs')
def get_debug_logs():
    """Get debug logs"""
    return jsonify({'debug_logs': download_status["debug_logs"]})


@app.route('/api/log-history')
def get_log_history():
    """Get log history"""
    try:
        logs_info = load_logs_info()
        return jsonify({'success': True, 'logs': logs_info.get('logs', [])})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/log-history/<filename>')
def get_log_file(filename):
    """Download a specific log file"""
    try:
        # Validate filename to prevent directory traversal
        if '..' in filename or '/' in filename:
            return jsonify({'success': False, 'error': 'Invalid filename'}), 400
        
        log_file_path = LOGS_DIR / filename
        
        if not log_file_path.exists():
            return jsonify({'success': False, 'error': 'Log file not found'}), 404
        
        with open(log_file_path, 'r') as f:
            content = f.read()
        
        return content, 200, {
            'Content-Type': 'text/plain; charset=utf-8',
            'Content-Disposition': f'attachment; filename="{filename}"'
        }
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/log-history/<filename>', methods=['DELETE'])
def delete_log_file(filename):
    """Delete a specific log file"""
    try:
        # Validate filename to prevent directory traversal
        if '..' in filename or '/' in filename:
            return jsonify({'success': False, 'error': 'Invalid filename'}), 400
        
        log_file_path = LOGS_DIR / filename
        
        if not log_file_path.exists():
            return jsonify({'success': False, 'error': 'Log file not found'}), 404
        
        # Delete the log file
        log_file_path.unlink()
        
        # Update logs info
        logs_info = load_logs_info()
        logs_info['logs'] = [log for log in logs_info.get('logs', []) if log.get('filename') != filename]
        save_logs_info(logs_info)
        
        return jsonify({'success': True, 'message': 'Log file deleted successfully'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


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
    try:
        config = load_config()
        
        if download_status["is_running"]:
            log_message("Scheduled download skipped - download already in progress", "warning")
            return
        
        log_message("Starting scheduled download", "info")
        
        playlists = config.get('PLAYLISTS', [])
        if not playlists:
            log_message("No playlists configured for scheduled download", "warning")
            return
        
        download_queue.put({'playlists': playlists, 'trigger_type': 'cron'})
        
        # Update last run time AFTER queuing to minimize race condition window
        try:
            config['LAST_RUN'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            save_config(config)
        except Exception as e:
            log_message(f"Failed to update last run time: {str(e)}", "warning")
            # Continue anyway - this is not critical
    except Exception as e:
        log_message(f"Scheduled download failed to start: {str(e)}", "error")
        download_status["is_running"] = False


@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    try:
        logger.info(f"Client connected: {request.sid}")
        emit('status', download_status)
        return True
    except Exception as e:
        logger.error(f"Connection error: {str(e)}")
        return False


@socketio.on('request_logs')
def handle_request_logs():
    """Send all logs to client"""
    try:
        emit('all_logs', {'logs': download_status["logs"]})
    except Exception as e:
        logger.error(f"Failed to send logs: {str(e)}")


@socketio.on('request_debug_logs')
def handle_request_debug_logs():
    """Send all debug logs to client"""
    try:
        emit('all_debug_logs', {'debug_logs': download_status["debug_logs"]})
    except Exception as e:
        logger.error(f"Failed to send debug logs: {str(e)}")


# File Browser API
@app.route('/api/files/browse', methods=['GET'])
def browse_files():
    """Browse files and folders in the base directory"""
    try:
        config = load_config()
        base_folder = config.get('BASE_FOLDER', '/music')
        relative_path = request.args.get('path', '')
        
        # Sanitize path to prevent directory traversal
        if '..' in relative_path or relative_path.startswith('/'):
            return jsonify({'error': 'Invalid path'}), 400
        
        full_path = Path(base_folder) / relative_path
        
        if not full_path.exists():
            return jsonify({'error': 'Path does not exist'}), 404
        
        if not full_path.is_dir():
            return jsonify({'error': 'Path is not a directory'}), 400
        
        # Get directory contents
        items = []
        try:
            for item in sorted(full_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                relative_item_path = str(item.relative_to(base_folder))
                item_info = {
                    'name': item.name,
                    'path': relative_item_path,
                    'is_dir': item.is_dir(),
                    'size': item.stat().st_size if item.is_file() else 0,
                    'modified': datetime.fromtimestamp(item.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                }
                
                if item.is_file():
                    ext = item.suffix.lower()
                    if ext in ['.mp3', '.m4a', '.flac', '.wav', '.ogg']:
                        item_info['type'] = 'audio'
                    elif ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']:
                        item_info['type'] = 'image'
                    elif ext in ['.m3u', '.m3u8']:
                        item_info['type'] = 'playlist'
                    elif ext == '.txt':
                        item_info['type'] = 'text'
                    else:
                        item_info['type'] = 'file'
                
                items.append(item_info)
        except PermissionError:
            return jsonify({'error': 'Permission denied'}), 403
        
        return jsonify({
            'path': relative_path,
            'items': items,
            'parent': str(Path(relative_path).parent) if relative_path else None
        })
    
    except Exception as e:
        logger.error(f"Error browsing files: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/files/serve/<path:filepath>')
def serve_file(filepath):
    """Serve audio and image files"""
    try:
        config = load_config()
        base_folder = config.get('BASE_FOLDER', '/music')
        
        # Sanitize path
        if '..' in filepath:
            return jsonify({'error': 'Invalid path'}), 400
        
        full_path = Path(base_folder) / filepath
        
        if not full_path.exists() or not full_path.is_file():
            return jsonify({'error': 'File not found'}), 404
        
        # Check file type
        ext = full_path.suffix.lower()
        allowed_extensions = ['.mp3', '.m4a', '.flac', '.wav', '.ogg', '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']
        
        if ext not in allowed_extensions:
            return jsonify({'error': 'File type not allowed'}), 403
        
        return send_file(str(full_path))
    
    except Exception as e:
        logger.error(f"Error serving file: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/files/search', methods=['GET'])
def search_files():
    """Search for files and folders recursively"""
    try:
        config = load_config()
        base_folder = config.get('BASE_FOLDER', '/music')
        query = request.args.get('query', '').strip().lower()
        search_path = request.args.get('path', '')
        
        if not query:
            return jsonify({'error': 'No search query provided'}), 400
        
        # Sanitize path
        if '..' in search_path or search_path.startswith('/'):
            return jsonify({'error': 'Invalid path'}), 400
        
        start_path = Path(base_folder) / search_path
        
        if not start_path.exists() or not start_path.is_dir():
            return jsonify({'error': 'Invalid search path'}), 400
        
        # Search for matching files
        results = []
        try:
            for item in start_path.rglob('*'):
                # Check if name matches query
                if query in item.name.lower():
                    try:
                        relative_path = str(item.relative_to(base_folder))
                        item_info = {
                            'name': item.name,
                            'path': relative_path,
                            'is_dir': item.is_dir(),
                            'size': item.stat().st_size if item.is_file() else 0,
                            'modified': datetime.fromtimestamp(item.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                            'parent_path': str(item.parent.relative_to(base_folder))
                        }
                        
                        if item.is_file():
                            ext = item.suffix.lower()
                            if ext in ['.mp3', '.m4a', '.flac', '.wav', '.ogg']:
                                item_info['type'] = 'audio'
                            elif ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']:
                                item_info['type'] = 'image'
                            elif ext in ['.m3u', '.m3u8']:
                                item_info['type'] = 'playlist'
                            elif ext == '.txt':
                                item_info['type'] = 'text'
                            else:
                                item_info['type'] = 'file'
                        
                        results.append(item_info)
                        
                        # Limit results to prevent overwhelming response
                        if len(results) >= 500:
                            break
                    except (PermissionError, OSError):
                        continue
        except PermissionError:
            return jsonify({'error': 'Permission denied'}), 403
        
        return jsonify({
            'query': query,
            'search_path': search_path,
            'count': len(results),
            'results': results
        })
    
    except Exception as e:
        logger.error(f"Error searching files: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/files/delete', methods=['DELETE'])
def delete_file():
    """Delete a file or empty folder and remove it from the record file"""
    try:
        config = load_config()
        base_folder = config.get('BASE_FOLDER', '/music')
        filepath = request.json.get('path')
        
        if not filepath:
            return jsonify({'error': 'No file path provided'}), 400
        
        # Sanitize path
        if '..' in filepath:
            return jsonify({'error': 'Invalid path'}), 400
        
        full_path = Path(base_folder) / filepath
        
        if not full_path.exists():
            return jsonify({'error': 'File or folder not found'}), 404
        
        # Handle directory deletion
        if full_path.is_dir():
            # Check if directory is empty
            if any(full_path.iterdir()):
                return jsonify({'error': 'Cannot delete folder: folder is not empty'}), 400
            
            # Delete empty directory
            full_path.rmdir()
            logger.info(f"Deleted empty folder: {filepath}")
            return jsonify({'success': True, 'message': 'Folder deleted successfully'})
        
        # Handle file deletion
        if not full_path.is_file():
            return jsonify({'error': 'Path is not a file or directory'}), 400
        
        # Extract video ID from filename if it's a music file
        video_id = None
        if full_path.suffix.lower() in ['.mp3', '.m4a', '.flac', '.wav', '.ogg']:
            # Filename format: "Artist - Title - VIDEO_ID.ext"
            match = re.search(r' - ([a-zA-Z0-9_-]{11})\.[^.]+$', full_path.name)
            if match:
                video_id = match.group(1)
        
        # Delete the file
        full_path.unlink()
        logger.info(f"Deleted file: {filepath}")
        
        # Remove from record file if video ID was found
        if video_id:
            record_file = Path(base_folder) / config.get('RECORD_FILE_NAME', '.downloaded_videos.txt')
            if record_file.exists():
                try:
                    with open(record_file, 'r') as f:
                        lines = f.readlines()
                    
                    # Filter out the deleted video ID
                    updated_lines = [line for line in lines if video_id not in line]
                    
                    with open(record_file, 'w') as f:
                        f.writelines(updated_lines)
                    
                    logger.info(f"Removed video ID {video_id} from record file")
                except Exception as e:
                    logger.error(f"Error updating record file: {str(e)}")
        
        return jsonify({'success': True, 'message': 'File deleted successfully'})
    
    except Exception as e:
        logger.error(f"Error deleting file: {str(e)}")
        return jsonify({'error': str(e)}), 500


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
    logger.info("Starting Synthwave: YouTube Music Playlist Downloader")
    socketio.run(app, host='0.0.0.0', port=5000, debug=True, allow_unsafe_werkzeug=True)

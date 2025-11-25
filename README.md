# Synthwave 🎵

A self-hosted YouTube Music playlist downloader with a modern web interface, automated scheduling, and Docker support. Download and organize your music playlists with metadata, album artwork, and automatic M3U playlist generation.

## ✨ Features

- 🎨 Modern web interface for managing downloads
- 📅 Scheduled downloads with cron support
- 🎵 Automatic metadata tagging (artist, title, album, track number)
- 🖼️ Album artwork embedding and extraction
- 📝 M3U playlist generation for media servers
- 🔄 Smart duplicate detection and organization
- 📊 Real-time progress tracking with WebSocket updates
- 🐳 Full Docker support with docker-compose
- 🔁 Automatic retry logic for failed downloads
- 📦 Parallel download support

## 🚀 Quick Start

### Using Docker Compose: Production (Recommended)

```bash
git clone https://github.com/PranavVerma-droid/Synthwave.git
cd Synthwave/docker
cp .env.example .env

docker compose up -d
```

Then visit:
```
http://your-server-url:5000
```

### Using Docker Compose: Development Local Test
```bash
git clone https://github.com/PranavVerma-droid/Synthwave.git
cd Synthwave/docker
cp .env.example .env

docker compose -f docker-compose-dev.yml up --build
```

Then visit:
```
http://your-server-url:5000
```

### Manual Installation

```bash
# Clone the repository
git clone https://github.com/PranavVerma-droid/Synthwave.git
cd Synthwave

# Install dependencies
pip install -r requirements.txt

# Run the application
python app.py
```

## 🐳 Docker Images

Pre-built images are available from GitHub Container Registry:

```bash
# Pull latest stable release
docker pull ghcr.io/pranavverma-droid/synthwave:latest

# Pull specific version
docker pull ghcr.io/pranavverma-droid/synthwave:v1.0.0

# Run the container
docker run -d \
  -p 5000:5000 \
  -v /path/to/music:/music \
  -v /path/to/playlists:/playlists \
  ghcr.io/pranavverma-droid/synthwave:latest
```

### Available Tags

- `latest` - Latest stable release (updated on stable releases only)
- `v1.0.0`, `v1.1.0`, etc. - Specific version releases
- Pre-release versions (alpha/beta) are available by their version tags only

## ⚙️ Configuration

Configuration is stored in `config.json` and can be managed through the web interface:

```json
{
  "BASE_FOLDER": "/music",
  "DOWNLOADER_PATH": "/binary/yt-dlp",
  "PLAYLIST_M3U_FOLDER": "/playlists",
  "MUSIC_MOUNT_PATH": "/music",
  "PARALLEL_LIMIT": 4,
  "TIMEOUT_METADATA": 120,
  "TIMEOUT_DOWNLOAD": 600,
  "MAX_RETRIES": 3,
  "CRON_ENABLED": false,
  "CRON_SCHEDULE": {
    "minute": "0",
    "hour": "2",
    "day": "*",
    "month": "*",
    "day_of_week": "*"
  }
}
```

## 📋 Usage

1. **Add Playlists**: Paste YouTube playlist or album URLs in the web interface
2. **Configure Settings**: Adjust download paths, timeouts, and retry limits
3. **Start Download**: Click "Start Download" to begin processing
4. **Schedule Downloads**: Enable cron scheduling for automatic updates
5. **Monitor Progress**: Watch real-time progress and logs in the dashboard

### Supported URLs

- YouTube Playlists: `https://www.youtube.com/playlist?list=...`
- YouTube Albums: `https://music.youtube.com/playlist?list=OLAK5uy_...`
- Individual Videos: `https://www.youtube.com/watch?v=...`

## 🔧 Development

### Prerequisites

- Python 3.8+
- Docker & Docker Compose
- yt-dlp
- ffmpeg (for metadata handling)
- ImageMagick (optional, for artwork processing)

### Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run in development mode
python app.py
```

The application will start on `http://localhost:5000` with hot-reload enabled.

### Building Docker Images

```bash
docker build -t synthwave:dev .
docker run -p 5000:5000 \
  -v $(pwd)/music:/music \
  -v $(pwd)/playlists:/playlists \
  synthwave:dev
```

## 🚢 Release Process

This project uses GitHub Actions for automated Docker builds:

1. Create a new release on GitHub
2. Tag with semantic versioning: `vX.Y.Z` (e.g., `v1.0.0`)
3. Mark as pre-release for alpha/beta versions
4. GitHub Actions automatically:
   - Builds the Docker image
   - Tags with release version
   - Tags as `latest` (stable releases only)
   - Pushes to GitHub Container Registry

## 📁 Project Structure

```
Synthwave/
├── app.py                 # Main Flask application
├── templates/             # HTML templates
├── static/                # CSS, JS, images
├── docker/                # Docker compose configuration
├── Dockerfile             # Docker build configuration
├── requirements.txt       # Python dependencies
├── config.json            # Application configuration
└── .github/workflows/     # CI/CD workflows
```

## 📝 License

MIT License - see [LICENSE](LICENSE) for details.

## 👤 Author

**Pranav Verma**

- GitHub: [@PranavVerma-droid](https://github.com/pranavverma-droid)
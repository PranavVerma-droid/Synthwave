// WebSocket connection
const socket = io();

// Global variables
let autoScroll = true;
let debugAutoScroll = true;
let cronEnabled = false;
let connectionStatus = 'connecting';
let debugMode = false;
let currentPath = '';
let viewMode = 'list';
let searchQuery = '';
let searchDebounceTimer = null;

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    loadPlaylists();
    loadCronSettings();
    loadConfig();
    setupSocketListeners();
    updateCronUI();
    updateConnectionStatus();
    loadLogHistory();
    setupMobileMenu();
    loadFileBrowser('');
    
    // Attach debug mode change listener
    const debugModeSelect = document.getElementById('debug_mode');
    if (debugModeSelect) {
        debugModeSelect.addEventListener('change', previewDebugMode);
    }
    
    // Attach download mode change listener
    const downloadModeSelect = document.getElementById('download_mode');
    if (downloadModeSelect) {
        downloadModeSelect.addEventListener('change', function() {
            updateDownloadModeUI(this.value);
        });
    }
    
    // Attach search listener
    const searchInput = document.getElementById('file-search-input');
    if (searchInput) {
        searchInput.addEventListener('input', handleSearchInput);
        searchInput.addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                performSearch();
            }
        });
    }
});

function updateConnectionStatus() {
    // Create status indicator if not exists
    let statusIndicator = document.getElementById('connection-status');
    if (!statusIndicator) {
        const header = document.querySelector('header');
        statusIndicator = document.createElement('div');
        statusIndicator.id = 'connection-status';
        statusIndicator.style.cssText = 'position: absolute; top: 1rem; right: 2.5rem; padding: 0.5rem 1rem; border-radius: 0.375rem; font-size: 0.75rem; font-weight: 600;';
        header.style.position = 'relative';
        header.appendChild(statusIndicator);
    }
    
    if (connectionStatus === 'connected') {
        statusIndicator.textContent = '🟢 Connected';
        statusIndicator.style.background = 'var(--success)';
        statusIndicator.style.color = 'white';
    } else if (connectionStatus === 'connecting') {
        statusIndicator.textContent = '🟡 Connecting...';
        statusIndicator.style.background = 'var(--warning)';
        statusIndicator.style.color = 'white';
    } else {
        statusIndicator.textContent = '🔴 Disconnected';
        statusIndicator.style.background = 'var(--error)';
        statusIndicator.style.color = 'white';
    }
}

// Socket.io event listeners
function setupSocketListeners() {
    socket.on('connect', function() {
        console.log('Connected to server');
        connectionStatus = 'connected';
        updateConnectionStatus();
        socket.emit('request_logs');
        if (debugMode) {
            socket.emit('request_debug_logs');
        }
        showNotification('Connected to server', 'success');
    });

    socket.on('disconnect', function() {
        console.log('Disconnected from server');
        connectionStatus = 'disconnected';
        updateConnectionStatus();
        showNotification('Disconnected from server', 'warning');
    });

    socket.on('connect_error', function() {
        connectionStatus = 'disconnected';
        updateConnectionStatus();
    });

    socket.on('log', function(data) {
        addLogEntry(data);
    });

    socket.on('all_logs', function(data) {
        const logsContainer = document.getElementById('logs-container');
        logsContainer.innerHTML = '';
        data.logs.forEach(log => addLogEntry(log));
    });

    socket.on('debug_log', function(data) {
        addDebugLogEntry(data);
    });

    socket.on('all_debug_logs', function(data) {
        const debugLogsContainer = document.getElementById('debug-logs-container');
        debugLogsContainer.innerHTML = '';
        data.debug_logs.forEach(log => addDebugLogEntry(log));
    });

    socket.on('progress', function(data) {
        updateProgress(data);
    });

    socket.on('status', function(data) {
        updateStatus(data);
    });

    socket.on('download_complete', function(data) {
        handleDownloadComplete(data);
    });
}

// Configuration Management
async function loadConfig() {
    try {
        const response = await fetch('/api/config');
        const data = await response.json();
        
        debugMode = data.config.DEBUG_MODE || false;
        updateDebugUI();
        updateDownloadModeUI(data.config.DOWNLOAD_MODE || 'both');
        
        // Load cookies status
        loadCookiesStatus();
    } catch (error) {
        console.error('Error loading config:', error);
    }
}

async function saveConfig() {
    const config = {
        BASE_FOLDER: document.getElementById('base_folder').value,
        PLAYLIST_M3U_FOLDER: document.getElementById('playlist_m3u_folder').value,
        MUSIC_MOUNT_PATH: document.getElementById('music_mount_path').value,
        PARALLEL_LIMIT: parseInt(document.getElementById('parallel_limit').value),
        RECORD_FILE_NAME: document.getElementById('record_file_name').value,
        DEBUG_MODE: document.getElementById('debug_mode').value === 'true',
        DOWNLOAD_MODE: document.getElementById('download_mode').value
    };

    try {
        const response = await fetch('/api/config', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(config)
        });

        const result = await response.json();
        
        if (result.success) {
            debugMode = document.getElementById('debug_mode').value === 'true';
            updateDebugUI();
            updateDownloadModeUI(document.getElementById('download_mode').value);
            showNotification('Configuration saved successfully!', 'success');
        } else {
            showNotification('Failed to save configuration', 'error');
        }
    } catch (error) {
        console.error('Error saving config:', error);
        showNotification('Error saving configuration', 'error');
    }
}

// Cookies Management
let selectedCookiesFile = null;

async function loadCookiesStatus() {
    try {
        const response = await fetch('/api/cookies/status');
        const data = await response.json();
        
        if (data.success) {
            updateCookiesUI(data.exists, data.enabled);
        }
    } catch (error) {
        console.error('Error loading cookies status:', error);
    }
}

function updateCookiesUI(exists, enabled) {
    const statusText = document.getElementById('cookies-status-text');
    const deleteBtn = document.getElementById('delete-cookies-btn');
    
    if (exists) {
        statusText.textContent = enabled ? '✅ Cookies file uploaded and enabled' : '⚠️ Cookies file exists but disabled';
        statusText.style.color = enabled ? 'var(--success)' : 'var(--warning)';
        deleteBtn.style.display = 'inline-block';
    } else {
        statusText.textContent = '❌ No cookies file uploaded';
        statusText.style.color = 'var(--text-secondary)';
        deleteBtn.style.display = 'none';
    }
}

function handleCookiesFileSelect(event) {
    const file = event.target.files[0];
    const filenameSpan = document.getElementById('cookies-filename');
    const uploadBtn = document.getElementById('upload-cookies-btn');
    
    if (file) {
        selectedCookiesFile = file;
        filenameSpan.textContent = file.name;
        uploadBtn.style.display = 'inline-block';
    } else {
        selectedCookiesFile = null;
        filenameSpan.textContent = '';
        uploadBtn.style.display = 'none';
    }
}

async function uploadCookies() {
    if (!selectedCookiesFile) {
        showNotification('Please select a cookies.txt file first', 'warning');
        return;
    }
    
    const formData = new FormData();
    formData.append('file', selectedCookiesFile);
    
    try {
        const response = await fetch('/api/cookies/upload', {
            method: 'POST',
            body: formData
        });
        
        const result = await response.json();
        
        if (result.success) {
            showNotification('Cookies file uploaded successfully!', 'success');
            
            // Reset file input
            document.getElementById('cookies-file-input').value = '';
            document.getElementById('cookies-filename').textContent = '';
            document.getElementById('upload-cookies-btn').style.display = 'none';
            selectedCookiesFile = null;
            
            // Reload status
            loadCookiesStatus();
        } else {
            showNotification('Failed to upload cookies: ' + (result.error || 'Unknown error'), 'error');
        }
    } catch (error) {
        console.error('Error uploading cookies:', error);
        showNotification('Error uploading cookies file', 'error');
    }
}

async function deleteCookies() {
    if (!confirm('Are you sure you want to delete the cookies file?')) {
        return;
    }
    
    try {
        const response = await fetch('/api/cookies/delete', {
            method: 'POST'
        });
        
        const result = await response.json();
        
        if (result.success) {
            showNotification('Cookies file deleted successfully', 'success');
            loadCookiesStatus();
        } else {
            showNotification('Failed to delete cookies: ' + (result.error || 'Unknown error'), 'error');
        }
    } catch (error) {
        console.error('Error deleting cookies:', error);
        showNotification('Error deleting cookies file', 'error');
    }
}

// Playlist Management
async function loadPlaylists() {
    try {
        const response = await fetch('/api/playlists');
        const data = await response.json();
        
        updatePlaylistsDisplay(data.playlists, data.previews || []);
    } catch (error) {
        console.error('Error loading playlists:', error);
    }
}

async function addPlaylist() {
    const urlInput = document.getElementById('new_playlist_url');
    const url = urlInput.value.trim();

    if (!url) {
        showNotification('Please enter a playlist URL', 'warning');
        return;
    }

    if (!url.includes('youtube.com') && !url.includes('youtu.be')) {
        showNotification('Please enter a valid YouTube URL', 'warning');
        return;
    }

    try {
        const response = await fetch('/api/playlists', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ url: url })
        });

        const result = await response.json();
        
        if (result.success) {
            // Reload all playlists to get updated list with previews
            loadPlaylists();
            urlInput.value = '';
            showNotification('Playlist added successfully!', 'success');
        } else {
            showNotification('Failed to add playlist', 'error');
        }
    } catch (error) {
        console.error('Error adding playlist:', error);
        showNotification('Error adding playlist', 'error');
    }
}

async function removePlaylist(url) {
    if (!confirm('Are you sure you want to remove this playlist?')) {
        return;
    }

    try {
        const response = await fetch('/api/playlists', {
            method: 'DELETE',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ url: url })
        });

        const result = await response.json();
        
        if (result.success) {
            loadPlaylists();
            showNotification('Playlist removed successfully!', 'success');
        } else {
            showNotification('Failed to remove playlist', 'error');
        }
    } catch (error) {
        console.error('Error removing playlist:', error);
        showNotification('Error removing playlist', 'error');
    }
}

function updatePlaylistsDisplay(playlists, previews) {
    const container = document.getElementById('playlists-container');
    
    // Update playlist count
    const countElement = document.getElementById('playlist-count');
    if (countElement) {
        countElement.textContent = playlists ? playlists.length : 0;
    }
    
    if (!playlists || playlists.length === 0) {
        container.innerHTML = '<p style="text-align: center; color: #888; padding: 20px;">No playlists configured. Add one above!</p>';
        return;
    }

    container.innerHTML = '';
    
    playlists.forEach((url, index) => {
        const item = document.createElement('div');
        item.className = 'playlist-item';
        item.dataset.url = url;

        // Find the matching preview for this URL
        const preview = previews && previews.find(p => p.url === url);
        
        if (preview && preview.cached !== false) {
            // We have cached preview data
            displayPlaylistWithPreview(item, url, preview);
            container.appendChild(item);
        } else {
            // No cached data, fetch it
            const isAlbum = url.includes('OLAK5uy_') || url.includes('/album/');
            
            // Create loading skeleton
            item.innerHTML = `
                <div class="playlist-preview-card">
                    <div class="playlist-preview-loading">
                        <div class="skeleton-thumbnail"></div>
                        <div class="playlist-preview-info">
                            <div class="skeleton-text skeleton-title"></div>
                            <div class="skeleton-text skeleton-subtitle"></div>
                        </div>
                    </div>
                    <button onclick="removePlaylist('${url.replace(/'/g, "\\'")}');" class="btn btn-danger btn-sm playlist-remove-btn">Remove</button>
                </div>
            `;
            
            container.appendChild(item);
            
            // Fetch preview data
            fetchPlaylistPreview(url, item);
        }
    });
}

function displayPlaylistWithPreview(itemElement, url, preview) {
    const isAlbum = preview.is_album;
    const badge = isAlbum ? '📀 Album' : '🎵 Playlist';
    const thumbnail = preview.thumbnail || 'test';
    const description = preview.description && preview.description.trim() !== '' 
        ? `<p class="playlist-description">${escapeHtml(preview.description)}</p>` 
        : '';
    
    itemElement.innerHTML = `
        <div class="playlist-preview-card">
            <a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" class="playlist-preview-link">
                <div class="playlist-preview-content">
                    <img src="${escapeHtml(thumbnail)}" alt="${escapeHtml(preview.title || 'Playlist')}" class="playlist-thumbnail" loading="lazy">
                    <div class="playlist-preview-info">
                        <span class="playlist-type-badge">${badge}</span>
                        <h3 class="playlist-title">${escapeHtml(preview.title || 'Unknown Playlist')}</h3>
                        <p class="playlist-artist">${escapeHtml(preview.uploader || 'Unknown Artist')}</p>
                        <p class="playlist-meta">${preview.entry_count || 0} ${isAlbum ? 'tracks' : 'videos'}</p>
                        ${description}
                    </div>
                </div>
            </a>
            <button onclick="removePlaylist('${url.replace(/'/g, "\\'")}');" class="btn btn-danger btn-sm playlist-remove-btn">Remove</button>
        </div>
    `;
}

async function fetchPlaylistPreview(url, itemElement) {
    try {
        const response = await fetch('/api/playlists/preview', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ url: url })
        });
        
        const data = await response.json();
        
        if (data.success && data.preview) {
            displayPlaylistWithPreview(itemElement, url, data.preview);
        } else {
            // Fallback to simple display
            showSimplePlaylistItem(url, itemElement);
        }
    } catch (error) {
        console.error('Error fetching playlist preview:', error);
        showSimplePlaylistItem(url, itemElement);
    }
}

function showSimplePlaylistItem(url, itemElement) {
    const isAlbum = url.includes('OLAK5uy_') || url.includes('/album/');
    const badge = isAlbum ? '📀 Album' : '🎵 Playlist';
    
    itemElement.querySelector('.playlist-preview-card').innerHTML = `
        <div class="playlist-preview-content">
            <div class="playlist-preview-info" style="margin-left: 0;">
                <span class="playlist-type-badge">${badge}</span>
                <span class="url-text">${escapeHtml(url)}</span>
            </div>
        </div>
        <button onclick="removePlaylist('${url.replace(/'/g, "\\'")}');" class="btn btn-danger btn-sm playlist-remove-btn">Remove</button>
    `;
}

async function refreshAllPlaylists() {
    const refreshBtn = document.getElementById('refresh-all-btn');
    
    if (!refreshBtn) return;
    
    // Disable button and show loading state
    refreshBtn.disabled = true;
    refreshBtn.innerHTML = '⏳ Refreshing... (Don\'t Reload!)';
    
    try {
        const response = await fetch('/api/playlists/refresh-all', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });
        
        const result = await response.json();
        
        if (result.success) {
            // Reload playlists to show updated data
            await loadPlaylists();
            
            const message = `Refreshed ${result.refreshed} playlist(s)` + 
                          (result.failed > 0 ? `, ${result.failed} failed` : '');
            showNotification(message, result.failed > 0 ? 'warning' : 'success');
        } else {
            showNotification('Failed to refresh playlists', 'error');
        }
    } catch (error) {
        console.error('Error refreshing playlists:', error);
        showNotification('Error refreshing playlists', 'error');
    } finally {
        // Re-enable button
        refreshBtn.disabled = false;
        refreshBtn.innerHTML = '🔄 Refresh All';
    }
}

// Download Control
async function startDownload() {
    const startBtn = document.getElementById('start-btn');
    const cancelBtn = document.getElementById('cancel-btn');
    
    startBtn.disabled = true;
    startBtn.textContent = 'Starting...';

    try {
        const response = await fetch('/api/download/start', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({})
        });

        const result = await response.json();
        
        if (result.success) {
            document.getElementById('progress-section').style.display = 'block';
            startBtn.style.display = 'none';
            cancelBtn.style.display = 'inline-flex';
            showNotification('Download started!', 'success');
        } else {
            showNotification(result.error || 'Failed to start download', 'error');
            startBtn.disabled = false;
            startBtn.textContent = 'Start Download';
        }
    } catch (error) {
        console.error('Error starting download:', error);
        showNotification('Error starting download', 'error');
        startBtn.disabled = false;
        startBtn.textContent = 'Start Download';
    }
}

async function runAlbums() {
    const runAlbumsBtn = document.getElementById('run-albums-btn');
    const startBtn = document.getElementById('start-btn');
    const cancelBtn = document.getElementById('cancel-btn');
    
    if (!confirm('This will download only the albums from your playlists. Continue?')) {
        return;
    }
    
    runAlbumsBtn.disabled = true;
    runAlbumsBtn.textContent = '⏳ Starting...';

    try {
        const response = await fetch('/api/download/start-albums', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({})
        });

        const result = await response.json();
        
        if (result.success) {
            document.getElementById('progress-section').style.display = 'block';
            startBtn.style.display = 'none';
            cancelBtn.style.display = 'inline-flex';
            showNotification(result.message || 'Album download started!', 'success');
        } else {
            showNotification(result.error || 'Failed to start album download', 'error');
        }
    } catch (error) {
        console.error('Error starting album download:', error);
        showNotification('Error starting album download', 'error');
    } finally {
        runAlbumsBtn.disabled = false;
        runAlbumsBtn.textContent = '📀 Run Albums';
    }
}

async function runPlaylists() {
    const runPlaylistsBtn = document.getElementById('run-albums-btn'); // Same button, different mode
    const startBtn = document.getElementById('start-btn');
    const cancelBtn = document.getElementById('cancel-btn');
    
    if (!confirm('This will download only the playlists (non-album tracks). Continue?')) {
        return;
    }
    
    runPlaylistsBtn.disabled = true;
    runPlaylistsBtn.textContent = '⏳ Starting...';

    try {
        const response = await fetch('/api/download/start-playlists', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({})
        });

        const result = await response.json();
        
        if (result.success) {
            document.getElementById('progress-section').style.display = 'block';
            startBtn.style.display = 'none';
            cancelBtn.style.display = 'inline-flex';
            showNotification(result.message || 'Playlist download started!', 'success');
        } else {
            showNotification(result.error || 'Failed to start playlist download', 'error');
        }
    } catch (error) {
        console.error('Error starting playlist download:', error);
        showNotification('Error starting playlist download', 'error');
    } finally {
        runPlaylistsBtn.disabled = false;
        runPlaylistsBtn.textContent = '🎵 Run Playlists';
    }
}

async function cancelDownload() {
    const cancelBtn = document.getElementById('cancel-btn');
    const startBtn = document.getElementById('start-btn');
    
    if (!confirm('Are you sure you want to cancel the current download?')) {
        return;
    }
    
    cancelBtn.disabled = true;
    cancelBtn.textContent = 'Cancelling...';

    try {
        const response = await fetch('/api/download/cancel', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({})
        });

        const result = await response.json();
        
        if (result.success) {
            showNotification('Download cancellation requested', 'warning');
        } else {
            showNotification(result.error || 'Failed to cancel download', 'error');
            cancelBtn.disabled = false;
            cancelBtn.textContent = 'Cancel Download';
        }
    } catch (error) {
        console.error('Error cancelling download:', error);
        showNotification('Error cancelling download', 'error');
        cancelBtn.disabled = false;
        cancelBtn.textContent = 'Cancel Download';
    }
}

function updateProgress(data) {
    const current = data.current || 0;
    const total = data.total || 0;
    const percentage = total > 0 ? Math.round((current / total) * 100) : 0;

    document.getElementById('current-playlist').textContent = data.playlist || '-';
    document.getElementById('current-song').textContent = data.song || '-';
    document.getElementById('progress-bar').style.width = percentage + '%';
    document.getElementById('progress-text').textContent = percentage + '%';
    document.getElementById('progress-current').textContent = current;
    document.getElementById('progress-total').textContent = total;
    
    // Show progress section if we have actual data
    if (data.playlist || data.song || total > 0) {
        document.getElementById('progress-section').style.display = 'block';
    }
}

function updateStatus(data) {
    const startBtn = document.getElementById('start-btn');
    const cancelBtn = document.getElementById('cancel-btn');
    
    if (data.is_running) {
        startBtn.style.display = 'none';
        startBtn.disabled = true;
        cancelBtn.style.display = 'inline-flex';
        cancelBtn.disabled = false;
        cancelBtn.textContent = '⛔ Cancel Download';
        document.getElementById('progress-section').style.display = 'block';
    } else {
        startBtn.style.display = 'inline-flex';
        startBtn.disabled = false;
        startBtn.textContent = '🚀 Start Download';
        cancelBtn.style.display = 'none';
        cancelBtn.disabled = false;
        cancelBtn.textContent = '⛔ Cancel Download';
        // Don't hide progress section to show final results
    }
}

function handleDownloadComplete(data) {
    const startBtn = document.getElementById('start-btn');
    const cancelBtn = document.getElementById('cancel-btn');
    
    startBtn.style.display = 'inline-flex';
    startBtn.disabled = false;
    startBtn.textContent = '🚀 Start Download';
    cancelBtn.style.display = 'none';
    cancelBtn.disabled = false;
    cancelBtn.textContent = '⛔ Cancel Download';

    if (data.cancelled) {
        showNotification('Download cancelled by user', 'warning');
    } else if (data.success) {
        showNotification('All downloads completed!', 'success');
    } else {
        showNotification('Download failed: ' + (data.error || 'Unknown error'), 'error');
    }
    
    // Keep progress section visible to show final results
    // User can manually hide it by starting a new download
}

// Logs Management
function addLogEntry(log) {
    const logsContainer = document.getElementById('logs-container');
    const entry = document.createElement('div');
    entry.className = `log-entry log-${log.level}`;
    
    entry.innerHTML = `
        <span class="log-timestamp">[${log.timestamp}]</span>
        <span class="log-message">${escapeHtml(log.message)}</span>
    `;

    logsContainer.appendChild(entry);

    // Auto-scroll if enabled
    if (autoScroll) {
        logsContainer.scrollTop = logsContainer.scrollHeight;
    }
}

function clearLogs() {
    const logsContainer = document.getElementById('logs-container');
    logsContainer.innerHTML = '';
    showNotification('Logs cleared', 'info');
}

function toggleAutoScroll() {
    autoScroll = !autoScroll;
    const btn = document.getElementById('autoscroll-btn');
    btn.textContent = `📜 Auto-scroll: ${autoScroll ? 'ON' : 'OFF'}`;
    showNotification(`Auto-scroll ${autoScroll ? 'enabled' : 'disabled'}`, 'info');
}

// Debug Logs Management
function addDebugLogEntry(log) {
    const debugLogsContainer = document.getElementById('debug-logs-container');
    const entry = document.createElement('div');
    entry.className = `log-entry log-${log.level}`;
    
    entry.innerHTML = `
        <span class="log-timestamp">[${log.timestamp}]</span>
        <span class="log-message">${escapeHtml(log.message)}</span>
    `;

    debugLogsContainer.appendChild(entry);

    // Auto-scroll if enabled
    if (debugAutoScroll) {
        debugLogsContainer.scrollTop = debugLogsContainer.scrollHeight;
    }
}

function clearDebugLogs() {
    const debugLogsContainer = document.getElementById('debug-logs-container');
    debugLogsContainer.innerHTML = '';
    showNotification('Debug logs cleared', 'info');
}

function toggleDebugAutoScroll() {
    debugAutoScroll = !debugAutoScroll;
    const btn = document.getElementById('debug-autoscroll-btn');
    btn.textContent = `📜 Auto-scroll: ${debugAutoScroll ? 'ON' : 'OFF'}`;
    showNotification(`Debug auto-scroll ${debugAutoScroll ? 'enabled' : 'disabled'}`, 'info');
}

function updateDebugUI() {
    const debugSection = document.getElementById('debug-logs');
    const debugNav = document.getElementById('debug-logs-nav');
    
    if (debugMode) {
        debugSection.style.display = 'block';
        debugNav.style.display = 'flex';
        socket.emit('request_debug_logs');
    } else {
        debugSection.style.display = 'none';
        debugNav.style.display = 'none';
    }
}

function updateDownloadModeUI(mode) {
    const runAlbumsBtn = document.getElementById('run-albums-btn');
    
    if (!runAlbumsBtn) return;
    
    switch(mode) {
        case 'both':
            runAlbumsBtn.style.display = 'none';
            break;
        case 'playlists_only':
            runAlbumsBtn.style.display = 'inline-flex';
            runAlbumsBtn.textContent = '📀 Run Albums';
            runAlbumsBtn.title = 'Download albums only';
            runAlbumsBtn.onclick = runAlbums;
            break;
        case 'albums_only':
            runAlbumsBtn.style.display = 'inline-flex';
            runAlbumsBtn.textContent = '🎵 Run Playlists';
            runAlbumsBtn.title = 'Download playlists only';
            runAlbumsBtn.onclick = runPlaylists;
            break;
    }
}

function previewDebugMode() {
    // Preview the debug mode without saving
    debugMode = document.getElementById('debug_mode').value === 'true';
    updateDebugUI();
}

function toggleAutoScroll() {
    autoScroll = !autoScroll;
    const btn = document.getElementById('autoscroll-btn');
    btn.textContent = `📜 Auto-scroll: ${autoScroll ? 'ON' : 'OFF'}`;
    showNotification(`Auto-scroll ${autoScroll ? 'enabled' : 'disabled'}`, 'info');
}

function exportLogs() {
    const logsContainer = document.getElementById('logs-container');
    const logText = Array.from(logsContainer.querySelectorAll('.log-entry')).map(entry => {
        const timestamp = entry.querySelector('.log-timestamp')?.textContent || '';
        const message = entry.querySelector('.log-message')?.textContent || '';
        return `${timestamp} ${message}`;
    }).join('\n');
    
    const blob = new Blob([logText], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `music-downloader-logs-${new Date().toISOString()}.txt`;
    a.click();
    URL.revokeObjectURL(url);
    
    showNotification('Logs exported successfully', 'success');
}

function exportDebugLogs() {
    const debugLogsContainer = document.getElementById('debug-logs-container');
    const logText = Array.from(debugLogsContainer.querySelectorAll('.log-entry')).map(entry => {
        const timestamp = entry.querySelector('.log-timestamp')?.textContent || '';
        const message = entry.querySelector('.log-message')?.textContent || '';
        return `${timestamp} ${message}`;
    }).join('\n');
    
    const blob = new Blob([logText], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `music-downloader-debug-logs-${new Date().toISOString()}.txt`;
    a.click();
    URL.revokeObjectURL(url);
    
    showNotification('Debug logs exported successfully', 'success');
}

// Cron Job Management
async function loadCronSettings() {
    try {
        const response = await fetch('/api/cron');
        const data = await response.json();
        
        cronEnabled = data.enabled;
        document.getElementById('cron-enabled').checked = cronEnabled;
        
        if (data.schedule) {
            document.getElementById('cron-minute').value = data.schedule.minute || '0';
            document.getElementById('cron-hour').value = data.schedule.hour || '2';
            document.getElementById('cron-day').value = data.schedule.day || '*';
            document.getElementById('cron-month').value = data.schedule.month || '*';
            document.getElementById('cron-day-of-week').value = data.schedule.day_of_week || '*';
        }
        
        if (data.next_run) {
            document.getElementById('next-run-time').textContent = formatCronDate(data.next_run);
        }
        
        if (data.last_run) {
            document.getElementById('last-run-time').textContent = formatCronDate(data.last_run);
        }
        
        updateCronUI();
    } catch (error) {
        console.error('Error loading cron settings:', error);
    }
}

function toggleCron() {
    cronEnabled = document.getElementById('cron-enabled').checked;
    updateCronUI();
    
    if (cronEnabled) {
        saveCronSchedule();
    } else {
        disableCron();
    }
}

function updateCronUI() {
    const settingsDiv = document.getElementById('cron-settings');
    const statusText = document.getElementById('cron-status-text');
    const scheduleStatus = document.getElementById('cron-schedule-status');
    
    if (cronEnabled) {
        settingsDiv.style.display = 'block';
        statusText.textContent = 'Enabled';
        statusText.style.color = 'var(--success)';
        statusText.style.fontWeight = '600';
        scheduleStatus.textContent = 'Active';
        scheduleStatus.style.color = 'var(--success)';
    } else {
        settingsDiv.style.display = 'none';
        statusText.textContent = 'Disabled';
        statusText.style.color = 'var(--gray-500)';
        statusText.style.fontWeight = '400';
        scheduleStatus.textContent = 'Not scheduled';
        scheduleStatus.style.color = 'var(--gray-500)';
    }
}

async function saveCronSchedule() {
    const schedule = {
        minute: document.getElementById('cron-minute').value,
        hour: document.getElementById('cron-hour').value,
        day: document.getElementById('cron-day').value,
        month: document.getElementById('cron-month').value,
        day_of_week: document.getElementById('cron-day-of-week').value
    };
    
    try {
        const response = await fetch('/api/cron', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                enabled: true,
                schedule: schedule
            })
        });
        
        const result = await response.json();
        
        if (result.success) {
            if (result.next_run) {
                document.getElementById('next-run-time').textContent = result.next_run;
            }
            showNotification('Schedule updated successfully!', 'success');
        } else {
            showNotification('Failed to update schedule: ' + (result.error || 'Unknown error'), 'error');
        }
    } catch (error) {
        console.error('Error saving cron schedule:', error);
        showNotification('Error updating schedule', 'error');
    }
}

async function disableCron() {
    try {
        const response = await fetch('/api/cron', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                enabled: false,
                schedule: {}
            })
        });
        
        const result = await response.json();
        
        if (result.success) {
            document.getElementById('next-run-time').textContent = '-';
            showNotification('Automated schedule disabled', 'info');
        }
    } catch (error) {
        console.error('Error disabling cron:', error);
        showNotification('Error disabling schedule', 'error');
    }
}

function showCronPresets() {
    document.getElementById('cron-modal').style.display = 'flex';
}

function closeCronModal() {
    document.getElementById('cron-modal').style.display = 'none';
}

function applyCronPreset(minute, hour, day, month, dayOfWeek) {
    document.getElementById('cron-minute').value = minute;
    document.getElementById('cron-hour').value = hour;
    document.getElementById('cron-day').value = day;
    document.getElementById('cron-month').value = month;
    document.getElementById('cron-day-of-week').value = dayOfWeek;
    
    closeCronModal();
    showNotification('Preset applied! Click "Update Schedule" to save.', 'info');
}

// Log History Management
async function loadLogHistory() {
    try {
        const response = await fetch('/api/log-history');
        const data = await response.json();
        
        if (data.success) {
            updateLogHistoryDisplay(data.logs);
        } else {
            showNotification('Failed to load log history: ' + data.error, 'error');
        }
    } catch (error) {
        showNotification('Error loading log history: ' + error.message, 'error');
        console.error('Load log history error:', error);
    }
}

function updateLogHistoryDisplay(logs) {
    const container = document.getElementById('log-history-container');
    
    if (!logs || logs.length === 0) {
        container.innerHTML = '<div class="log-history-placeholder"><p>No download history available</p></div>';
        return;
    }
    
    container.innerHTML = '';
    
    logs.forEach(log => {
        const logItem = document.createElement('div');
        logItem.className = 'log-history-item';
        
        const statusClass = log.status === 'completed' ? 'status-completed' : 
                           log.status === 'running' ? 'status-running' : 
                           log.status === 'cancelled' ? 'status-cancelled' : 
                           'status-error';
        
        const triggerBadge = log.trigger_type === 'cron' ? 
            '<span class="trigger-badge trigger-cron">🕐 Cron</span>' : 
            '<span class="trigger-badge trigger-manual">👤 Manual</span>';
        
        const completedTime = log.completed_at ? 
            `<div class="log-meta-item"><strong>Completed:</strong> ${formatCronDate(log.completed_at)}</div>` : '';
        
        logItem.innerHTML = `
            <div class="log-history-header">
                <div class="log-history-title">
                    <span class="log-status ${statusClass}">${log.status.toUpperCase()}</span>
                    ${triggerBadge}
                    <span class="log-timestamp">${formatCronDate(log.timestamp)}</span>
                </div>
                <div class="log-history-actions">
                    <button onclick="downloadLogFile('${log.filename}')" class="btn btn-sm btn-secondary">
                        📥 Download
                    </button>
                    <button onclick="deleteLogFile('${log.filename}')" class="btn btn-sm btn-danger">
                        🗑️ Delete
                    </button>
                </div>
            </div>
            <div class="log-history-meta">
                <div class="log-meta-item">
                    <strong>Playlists:</strong> ${log.playlists_processed || 0}
                </div>
                <div class="log-meta-item">
                    <strong>Songs:</strong> ${log.songs_downloaded || 0}
                </div>
                <div class="log-meta-item">
                    <strong>Errors:</strong> ${log.errors || 0}
                </div>
                ${completedTime}
            </div>
        `;
        
        container.appendChild(logItem);
    });
}

async function downloadLogFile(filename) {
    try {
        const response = await fetch(`/api/log-history/${filename}`);
        
        if (!response.ok) {
            throw new Error('Failed to download log file');
        }
        
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
        
        showNotification('Log file downloaded successfully', 'success');
    } catch (error) {
        showNotification('Error downloading log file: ' + error.message, 'error');
        console.error('Download log file error:', error);
    }
}

async function deleteLogFile(filename) {
    if (!confirm(`Are you sure you want to delete log file "${filename}"? This action cannot be undone.`)) {
        return;
    }
    
    try {
        const response = await fetch(`/api/log-history/${filename}`, {
            method: 'DELETE'
        });
        
        const data = await response.json();
        
        if (data.success) {
            showNotification('Log file deleted successfully', 'success');
            loadLogHistory(); // Reload the log history
        } else {
            showNotification('Failed to delete log file: ' + data.error, 'error');
        }
    } catch (error) {
        showNotification('Error deleting log file: ' + error.message, 'error');
        console.error('Delete log file error:', error);
    }
}

// Utility Functions
function formatCronDate(dateString) {
    if (!dateString || dateString === 'Never' || dateString === 'Not scheduled') {
        return dateString;
    }
    
    try {
        const date = new Date(dateString);
        
        // Get day with ordinal suffix
        const day = date.getDate();
        const suffix = ['th', 'st', 'nd', 'rd'];
        const v = day % 100;
        const ordinal = day + (suffix[(v - 20) % 10] || suffix[v] || suffix[0]);
        
        // Get month name
        const months = ['January', 'February', 'March', 'April', 'May', 'June', 
                       'July', 'August', 'September', 'October', 'November', 'December'];
        const month = months[date.getMonth()];
        
        // Get year
        const year = date.getFullYear();
        
        // Get hour with AM/PM
        let hours = date.getHours();
        const ampm = hours >= 12 ? 'PM' : 'AM';
        hours = hours % 12;
        hours = hours ? hours : 12; // 0 should be 12
        
        // Get minutes
        const minutes = date.getMinutes();
        const minuteStr = minutes > 0 ? `:${minutes.toString().padStart(2, '0')}` : '';
        
        return `${ordinal} ${month} ${year} ${hours}${minuteStr}${ampm}`;
    } catch (e) {
        return dateString;
    }
}

function escapeHtml(text) {
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };
    return text.replace(/[&<>"']/g, m => map[m]);
}

function showNotification(message, type = 'info') {
    // Create notification element
    const notification = document.createElement('div');
    notification.className = 'notification-toast';
    
    const colors = {
        success: 'var(--success)',
        error: 'var(--error)',
        warning: 'var(--warning)',
        info: 'var(--info)'
    };
    
    const icons = {
        success: '✅',
        error: '❌',
        warning: '⚠️',
        info: 'ℹ️'
    };
    
    notification.style.background = colors[type] || colors.info;
    notification.innerHTML = `
        <span style="font-size: 1.25rem;">${icons[type] || icons.info}</span>
        <span>${escapeHtml(message)}</span>
    `;

    document.body.appendChild(notification);

    // Remove after 4 seconds
    setTimeout(() => {
        notification.style.animation = 'slideOutRight 0.3s ease';
        setTimeout(() => notification.remove(), 300);
    }, 4000);
}

// Keyboard shortcuts
document.addEventListener('keydown', function(e) {
    // Ctrl+Enter to add playlist
    if (e.ctrlKey && e.key === 'Enter') {
        const urlInput = document.getElementById('new_playlist_url');
        if (document.activeElement === urlInput) {
            addPlaylist();
        }
    }
    
    // Escape to close modal
    if (e.key === 'Escape') {
        closeCronModal();
    }
});

// Allow Enter key to add playlist
document.getElementById('new_playlist_url')?.addEventListener('keypress', function(e) {
    if (e.key === 'Enter') {
        addPlaylist();
    }
});

// Close modal when clicking outside
document.getElementById('cron-modal')?.addEventListener('click', function(e) {
    if (e.target === this) {
        closeCronModal();
    }
});

// Download Mode Help Modal Functions
function showDownloadModeHelp() {
    const modal = document.getElementById('download-mode-help-modal');
    if (modal) {
        modal.style.display = 'flex';
    }
}

function closeDownloadModeHelp() {
    const modal = document.getElementById('download-mode-help-modal');
    if (modal) {
        modal.style.display = 'none';
    }
}

// Close download mode help modal when clicking outside
document.getElementById('download-mode-help-modal')?.addEventListener('click', function(e) {
    if (e.target === this) {
        closeDownloadModeHelp();
    }
});

// Mobile Menu Functions
function setupMobileMenu() {
    const hamburger = document.querySelector('.hamburger');
    const sidebar = document.querySelector('.sidebar');
    const overlay = document.querySelector('.sidebar-overlay');
    const navItems = document.querySelectorAll('.nav-item');
    
    if (!hamburger || !sidebar || !overlay) return;
    
    // Toggle menu
    hamburger.addEventListener('click', function(e) {
        e.stopPropagation();
        toggleMobileMenu();
    });
    
    // Close menu when clicking overlay
    overlay.addEventListener('click', function() {
        closeMobileMenu();
    });
    
    // Close menu when clicking nav items
    navItems.forEach(item => {
        item.addEventListener('click', function() {
            if (window.innerWidth <= 1024) {
                closeMobileMenu();
            }
        });
    });
    
    // Close menu on window resize if too large
    window.addEventListener('resize', function() {
        if (window.innerWidth > 1024) {
            closeMobileMenu();
        }
    });
}

function toggleMobileMenu() {
    const hamburger = document.querySelector('.hamburger');
    const sidebar = document.querySelector('.sidebar');
    const overlay = document.querySelector('.sidebar-overlay');
    
    hamburger.classList.toggle('active');
    sidebar.classList.toggle('active');
    overlay.classList.toggle('active');
    
    // Prevent body scroll when menu is open
    if (sidebar.classList.contains('active')) {
        document.body.style.overflow = 'hidden';
    } else {
        document.body.style.overflow = '';
    }
}

function closeMobileMenu() {
    const hamburger = document.querySelector('.hamburger');
    const sidebar = document.querySelector('.sidebar');
    const overlay = document.querySelector('.sidebar-overlay');
    
    hamburger.classList.remove('active');
    sidebar.classList.remove('active');
    overlay.classList.remove('active');
    document.body.style.overflow = '';
}

// File Browser Functions
async function loadFileBrowser(path) {
    currentPath = path;
    searchQuery = '';
    const container = document.getElementById('file-browser-container');
    const parentBtn = document.getElementById('parent-btn');
    const searchInput = document.getElementById('file-search-input');
    const clearBtn = document.getElementById('clear-search-btn');
    const infoDiv = document.getElementById('file-browser-info');
    
    // Clear search
    if (searchInput) searchInput.value = '';
    clearBtn.style.display = 'none';
    infoDiv.style.display = 'none';
    
    try {
        container.innerHTML = '<div class="file-browser-placeholder"><p>Loading files...</p></div>';
        
        const response = await fetch(`/api/files/browse?path=${encodeURIComponent(path)}`);
        
        if (!response.ok) {
            throw new Error(`Failed to load files: ${response.statusText}`);
        }
        
        const data = await response.json();
        
        // Update breadcrumb
        updateBreadcrumb(path);
        
        // Show/hide parent button
        if (path) {
            parentBtn.style.display = 'inline-flex';
        } else {
            parentBtn.style.display = 'none';
        }
        
        // Display files
        displayFiles(data.items);
        
    } catch (error) {
        console.error('Error loading files:', error);
        container.innerHTML = `<div class="file-browser-placeholder"><p>Error: ${error.message}</p></div>`;
        showNotification('Failed to load files', 'error');
    }
}

function displayFiles(items) {
    const container = document.getElementById('file-browser-container');
    
    // Apply view mode class
    container.classList.remove('grid-view', 'compact-view');
    if (viewMode === 'grid') {
        container.classList.add('grid-view');
    } else if (viewMode === 'compact') {
        container.classList.add('compact-view');
    }
    
    if (!items || items.length === 0) {
        container.innerHTML = '<div class="file-browser-placeholder"><p>This folder is empty</p></div>';
        return;
    }
    
    const fileList = document.createElement('ul');
    fileList.className = 'file-list';
    
    items.forEach(item => {
        const li = document.createElement('li');
        li.className = `file-item ${item.is_dir ? 'folder' : 'file'}`;
        
        // Get icon
        let icon = '📄';
        if (item.is_dir) {
            icon = '📁';
        } else if (item.type === 'audio') {
            icon = '🎵';
        } else if (item.type === 'image') {
            icon = '🖼️';
        } else if (item.type === 'playlist') {
            icon = '📋';
        } else if (item.type === 'text') {
            icon = '📝';
        }
        
        // Format size
        let sizeStr = '';
        if (!item.is_dir && item.size) {
            sizeStr = formatFileSize(item.size);
        }
        
        // Highlight search term
        let displayName = escapeHtml(item.name);
        if (searchQuery) {
            const regex = new RegExp(`(${escapeRegex(searchQuery)})`, 'gi');
            displayName = displayName.replace(regex, '<span class="search-highlight">$1</span>');
        }
        
        // Show parent path in search results
        let parentPathHtml = '';
        if (searchQuery && item.parent_path) {
            parentPathHtml = `<div class="parent-path">📂 ${escapeHtml(item.parent_path)}</div>`;
        }
        
        li.innerHTML = `
            <div class="file-icon">${icon}</div>
            <div class="file-info">
                <div class="file-name">${displayName}</div>
                ${parentPathHtml}
                <div class="file-meta">
                    ${sizeStr ? `<span>${sizeStr}</span>` : ''}
                    <span>${item.modified}</span>
                </div>
            </div>
            <div class="file-actions">
                ${item.is_dir ? getFolderActions(item) : getFileActions(item)}
            </div>
        `;
        
        // Add click handler
        if (item.is_dir) {
            li.addEventListener('click', (e) => {
                if (!e.target.closest('.file-actions')) {
                    navigateToPath(item.path);
                }
            });
        } else if (item.type === 'audio') {
            li.addEventListener('click', (e) => {
                if (!e.target.closest('.file-actions')) {
                    playAudio(item.path, item.name);
                }
            });
        } else if (item.type === 'image') {
            li.addEventListener('click', (e) => {
                if (!e.target.closest('.file-actions')) {
                    viewImage(item.path, item.name);
                }
            });
        }
        
        fileList.appendChild(li);
    });
    
    container.innerHTML = '';
    container.appendChild(fileList);
}

function getFileActions(item) {
    let actions = '';
    
    if (item.type === 'audio') {
        actions += `<button onclick="playAudio('${escapeHtml(item.path)}', '${escapeHtml(item.name)}'); event.stopPropagation();" class="btn btn-primary btn-sm">▶️ Play</button>`;
    }
    
    if (item.type === 'image') {
        actions += `<button onclick="viewImage('${escapeHtml(item.path)}', '${escapeHtml(item.name)}'); event.stopPropagation();" class="btn btn-primary btn-sm">👁️ View</button>`;
    }
    
    actions += `<button onclick="deleteItem('${escapeHtml(item.path)}', '${escapeHtml(item.name)}', false); event.stopPropagation();" class="btn btn-danger btn-sm">🗑️ Delete</button>`;
    
    return actions;
}

function getFolderActions(item) {
    return `<button onclick="deleteItem('${escapeHtml(item.path)}', '${escapeHtml(item.name)}', true); event.stopPropagation();" class="btn btn-danger btn-sm">🗑️ Delete</button>`;
}

function updateBreadcrumb(path) {
    const breadcrumb = document.getElementById('file-breadcrumb');
    breadcrumb.innerHTML = '<span class="breadcrumb-item" onclick="navigateToPath(\'\')">🏠 Music</span>';
    
    if (path) {
        const parts = path.split('/').filter(p => p);
        let currentPath = '';
        
        parts.forEach((part, index) => {
            currentPath += (index > 0 ? '/' : '') + part;
            const pathCopy = currentPath;
            
            const separator = document.createElement('span');
            separator.className = 'breadcrumb-separator';
            separator.textContent = '/';
            breadcrumb.appendChild(separator);
            
            const item = document.createElement('span');
            item.className = 'breadcrumb-item';
            item.textContent = part;
            item.onclick = () => navigateToPath(pathCopy);
            breadcrumb.appendChild(item);
        });
    }
}

function navigateToPath(path) {
    loadFileBrowser(path);
}

function navigateToParent() {
    if (!currentPath) return;
    
    const parts = currentPath.split('/').filter(p => p);
    parts.pop();
    const parentPath = parts.join('/');
    
    loadFileBrowser(parentPath);
}

function refreshFileBrowser() {
    loadFileBrowser(currentPath);
    showNotification('File browser refreshed', 'info');
}

function playAudio(filepath, filename) {
    const modal = document.getElementById('audio-modal');
    const title = document.getElementById('audio-modal-title');
    const audio = document.getElementById('audio-player');
    const source = document.getElementById('audio-source');
    
    title.textContent = filename;
    source.src = `/api/files/serve/${encodeURIComponent(filepath)}`;
    audio.load();
    
    modal.style.display = 'flex';
    
    // Auto play
    audio.play().catch(e => {
        console.error('Error playing audio:', e);
        showNotification('Failed to play audio', 'error');
    });
}

function closeAudioModal() {
    const modal = document.getElementById('audio-modal');
    const audio = document.getElementById('audio-player');
    
    audio.pause();
    modal.style.display = 'none';
}

function viewImage(filepath, filename) {
    const modal = document.getElementById('image-modal');
    const title = document.getElementById('image-modal-title');
    const img = document.getElementById('image-viewer-img');
    
    title.textContent = filename;
    img.src = `/api/files/serve/${encodeURIComponent(filepath)}`;
    
    modal.style.display = 'flex';
}

function closeImageModal() {
    const modal = document.getElementById('image-modal');
    modal.style.display = 'none';
}

async function deleteItem(filepath, filename, isFolder) {
    const itemType = isFolder ? 'folder' : 'file';
    let confirmMessage = `Are you sure you want to delete ${itemType} "${filename}"?`;
    
    if (isFolder) {
        confirmMessage += '\n\nNote: Only empty folders can be deleted.';
    } else {
        confirmMessage += '\n\nThis will also remove it from the download records.';
    }
    
    if (!confirm(confirmMessage)) {
        return;
    }
    
    try {
        const response = await fetch('/api/files/delete', {
            method: 'DELETE',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ path: filepath })
        });
        
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || `Failed to delete ${itemType}`);
        }
        
        showNotification(`"${filename}" deleted successfully`, 'success');
        refreshFileBrowser();
        
    } catch (error) {
        console.error(`Error deleting ${itemType}:`, error);
        showNotification(`Failed to delete ${itemType}: ${error.message}`, 'error');
    }
}

function formatFileSize(bytes) {
    if (bytes === 0) return '0 B';
    
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
}

// Search Functions
function handleSearchInput(e) {
    const query = e.target.value.trim();
    const clearBtn = document.getElementById('clear-search-btn');
    
    if (query) {
        clearBtn.style.display = 'inline-flex';
        
        // Debounce search
        clearTimeout(searchDebounceTimer);
        searchDebounceTimer = setTimeout(() => {
            performSearch();
        }, 500);
    } else {
        clearBtn.style.display = 'none';
        if (searchQuery) {
            clearSearch();
        }
    }
}

async function performSearch() {
    const searchInput = document.getElementById('file-search-input');
    const query = searchInput.value.trim();
    
    if (!query) {
        loadFileBrowser(currentPath);
        return;
    }
    
    searchQuery = query;
    const container = document.getElementById('file-browser-container');
    const infoDiv = document.getElementById('file-browser-info');
    const infoSpan = document.getElementById('search-results-info');
    
    try {
        container.innerHTML = '<div class="file-browser-placeholder"><p>Searching...</p></div>';
        
        const response = await fetch(`/api/files/search?query=${encodeURIComponent(query)}&path=${encodeURIComponent(currentPath)}`);
        
        if (!response.ok) {
            throw new Error(`Search failed: ${response.statusText}`);
        }
        
        const data = await response.json();
        
        // Show search info
        infoDiv.style.display = 'block';
        infoSpan.textContent = `Found ${data.count} result${data.count !== 1 ? 's' : ''} for "${query}"${currentPath ? ` in "${currentPath}" and subdirectories` : ''}`;
        
        // Display results
        displayFiles(data.results);
        
    } catch (error) {
        console.error('Error searching files:', error);
        container.innerHTML = `<div class="file-browser-placeholder"><p>Error: ${error.message}</p></div>`;
        showNotification('Search failed', 'error');
    }
}

function clearSearch() {
    const searchInput = document.getElementById('file-search-input');
    const clearBtn = document.getElementById('clear-search-btn');
    const infoDiv = document.getElementById('file-browser-info');
    
    searchInput.value = '';
    searchQuery = '';
    clearBtn.style.display = 'none';
    infoDiv.style.display = 'none';
    
    loadFileBrowser(currentPath);
}

function escapeRegex(str) {
    return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// View Mode Functions
function setViewMode(mode) {
    viewMode = mode;
    
    const listBtn = document.getElementById('view-list-btn');
    const compactBtn = document.getElementById('view-compact-btn');
    const gridBtn = document.getElementById('view-grid-btn');
    
    // Remove active class from all buttons
    listBtn.classList.remove('active');
    compactBtn.classList.remove('active');
    gridBtn.classList.remove('active');
    
    // Add active class to selected button
    if (mode === 'list') {
        listBtn.classList.add('active');
    } else if (mode === 'compact') {
        compactBtn.classList.add('active');
    } else {
        gridBtn.classList.add('active');
    }
    
    // Refresh display with new view mode
    const container = document.getElementById('file-browser-container');
    if (container.querySelector('.file-list')) {
        container.classList.remove('grid-view', 'compact-view');
        if (mode === 'grid') {
            container.classList.add('grid-view');
        } else if (mode === 'compact') {
            container.classList.add('compact-view');
        }
    }
}

// Close modals when clicking outside
document.addEventListener('click', function(e) {
    const imageModal = document.getElementById('image-modal');
    const audioModal = document.getElementById('audio-modal');
    
    if (e.target === imageModal) {
        closeImageModal();
    }
    
    if (e.target === audioModal) {
        closeAudioModal();
    }
});

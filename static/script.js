// WebSocket connection
const socket = io();

// Global variables
let autoScroll = true;
let cronEnabled = false;
let connectionStatus = 'connecting';

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    loadPlaylists();
    loadCronSettings();
    setupSocketListeners();
    updateCronUI();
    updateConnectionStatus();
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
async function saveConfig() {
    const config = {
        BASE_FOLDER: document.getElementById('base_folder').value,
        DOWNLOADER_PATH: document.getElementById('downloader_path').value,
        PLAYLIST_M3U_FOLDER: document.getElementById('playlist_m3u_folder').value,
        MUSIC_MOUNT_PATH: document.getElementById('music_mount_path').value,
        PARALLEL_LIMIT: parseInt(document.getElementById('parallel_limit').value),
        RECORD_FILE_NAME: document.getElementById('record_file_name').value
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
            showNotification('Configuration saved successfully!', 'success');
        } else {
            showNotification('Failed to save configuration', 'error');
        }
    } catch (error) {
        console.error('Error saving config:', error);
        showNotification('Error saving configuration', 'error');
    }
}

// Playlist Management
async function loadPlaylists() {
    try {
        const response = await fetch('/api/playlists');
        const data = await response.json();
        
        updatePlaylistsDisplay(data.playlists);
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
            updatePlaylistsDisplay(result.playlists);
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
            updatePlaylistsDisplay(result.playlists);
            showNotification('Playlist removed successfully!', 'success');
        } else {
            showNotification('Failed to remove playlist', 'error');
        }
    } catch (error) {
        console.error('Error removing playlist:', error);
        showNotification('Error removing playlist', 'error');
    }
}

function updatePlaylistsDisplay(playlists) {
    const container = document.getElementById('playlists-container');
    
    if (!playlists || playlists.length === 0) {
        container.innerHTML = '<p style="text-align: center; color: #888; padding: 20px;">No playlists configured. Add one above!</p>';
        return;
    }

    container.innerHTML = '';
    
    playlists.forEach(url => {
        const item = document.createElement('div');
        item.className = 'playlist-item';
        item.dataset.url = url;

        const isAlbum = url.includes('OLAK5uy_') || url.includes('/album/');
        const badge = isAlbum ? '📀 Album' : '🎵 Playlist';

        item.innerHTML = `
            <div class="playlist-url">
                <span class="playlist-type-badge">${badge}</span>
                <span class="url-text">${url}</span>
            </div>
            <button onclick="removePlaylist('${url.replace(/'/g, "\\'")}');" class="btn btn-danger btn-sm">Remove</button>
        `;

        container.appendChild(item);
    });
}

// Download Control
async function startDownload() {
    const startBtn = document.getElementById('start-btn');
    
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
    if (data.is_running) {
        document.getElementById('start-btn').disabled = true;
        document.getElementById('start-btn').textContent = 'Download in Progress...';
        document.getElementById('progress-section').style.display = 'block';
    } else {
        document.getElementById('start-btn').disabled = false;
        document.getElementById('start-btn').textContent = 'Start Download';
        // Don't hide progress section to show final results
    }
}

function handleDownloadComplete(data) {
    const startBtn = document.getElementById('start-btn');
    startBtn.disabled = false;
    startBtn.textContent = 'Start Download';

    if (data.success) {
        showNotification('All downloads completed successfully!', 'success');
    } else {
        showNotification('Download completed with errors: ' + (data.error || 'Unknown error'), 'error');
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
            document.getElementById('next-run-time').textContent = data.next_run;
        }
        
        if (data.last_run) {
            document.getElementById('last-run-time').textContent = data.last_run;
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

// Utility Functions
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

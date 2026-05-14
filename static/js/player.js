// -*- coding: utf-8 -*-
// 音乐播放器控制器

const audio = document.getElementById('audioPlayer');
const btnPlay = document.getElementById('btnPlay');
const btnPrev = document.getElementById('btnPrev');
const btnNext = document.getElementById('btnNext');
const btnLyric = document.getElementById('btnLyric');
const btnVolume = document.getElementById('btnVolume');
const volumeSlider = document.getElementById('volumeSlider');
const progressBar = document.getElementById('progressBar');
const progressFill = document.getElementById('progressFill');
const currentTimeEl = document.getElementById('currentTime');
const totalTimeEl = document.getElementById('totalTime');
const playerCover = document.getElementById('playerCover');
const playerName = document.getElementById('playerName');
const playerArtist = document.getElementById('playerArtist');
const lyricPanel = document.getElementById('lyricPanel');
const lyricBody = document.getElementById('lyricBody');
const lyricClose = document.getElementById('lyricClose');
const playerBar = document.getElementById('playerBar');

// 播放列表和状态
let playlist = [];
let currentIndex = -1;
let currentSongId = null;
let currentSongData = null;
let isPlaying = false;
let isLoading = false;

// 设置初始音量
audio.volume = 1.0;

const volumePct = document.getElementById('volumePct');

// ========== 播放歌曲 ==========
playSong = async function(songId) {
    if (isLoading) {
        showToast('正在加载中，请稍候...', 'info');
        return;
    }

    isLoading = true;
    playerName.textContent = '加载中...';
    playerArtist.textContent = '正在获取播放链接';
    playerBar.classList.add('loading');

    try {
        // 获取歌曲详情
        const resp = await fetch(`/api/song/${songId}`);
        const data = await resp.json();
        if (data.code !== '200') {
            showToast(data.message || '获取歌曲信息失败', 'error');
            isLoading = false;
            playerBar.classList.remove('loading');
            return;
        }

        currentSongData = data.data;
        currentSongId = songId;

        // 更新播放器UI
        playerName.textContent = data.data.name;
        playerArtist.textContent = data.data.artist;
        if (data.data.cover_url && !data.data.cover_url.includes('default-cover')) {
            playerCover.src = data.data.cover_url;
        } else {
            playerCover.src = '/static/img/default-cover.svg';
        }

        // 更新歌词
        updateLyrics(data.data.lyric);

        // 添加到播放列表
        if (!playlist.find(s => s.id === songId)) {
            playlist.push(data.data);
        }
        currentIndex = playlist.findIndex(s => s.id === songId);

        // 获取播放链接（通过代理）
        showToast(`正在获取: ${data.data.name}`, 'info');
        audio.src = `/api/song/${songId}/play`;
        
        try {
            await audio.play();
            isPlaying = true;
            btnPlay.textContent = '⏸';
            showToast(`正在播放: ${data.data.name}`, 'success');
        } catch (playErr) {
            console.error('播放失败:', playErr);
            // 可能是浏览器阻止了自动播放
            if (playErr.name === 'NotAllowedError') {
                showToast('请点击播放按钮开始播放', 'info');
                btnPlay.textContent = '▶';
            } else {
                showToast('播放失败，可能无法获取音频源', 'error');
                btnPlay.textContent = '▶';
            }
            isPlaying = false;
        }

    } catch(e) {
        console.error('播放出错:', e);
        showToast('播放出错: ' + e.message, 'error');
        btnPlay.textContent = '▶';
        isPlaying = false;
    } finally {
        isLoading = false;
        playerBar.classList.remove('loading');
    }
};

// ========== 控制按钮 ==========
btnPlay.addEventListener('click', () => {
    if (isLoading) return;
    
    if (audio.src && audio.src !== window.location.origin + '/') {
        if (audio.paused) {
            audio.play().then(() => {
                isPlaying = true;
                btnPlay.textContent = '⏸';
            }).catch(() => {
                showToast('播放被浏览器阻止，请重试', 'error');
            });
        } else {
            audio.pause();
            isPlaying = false;
            btnPlay.textContent = '▶';
        }
    } else if (playlist.length > 0) {
        // 没有加载过但列表有歌曲
        playSong(playlist[currentIndex >= 0 ? currentIndex : 0].id);
    }
});

btnPrev.addEventListener('click', () => {
    if (playlist.length === 0) return;
    currentIndex = (currentIndex - 1 + playlist.length) % playlist.length;
    playSong(playlist[currentIndex].id);
});

btnNext.addEventListener('click', () => {
    if (playlist.length === 0) return;
    currentIndex = (currentIndex + 1) % playlist.length;
    playSong(playlist[currentIndex].id);
});

// ========== 进度条 ==========
audio.addEventListener('timeupdate', () => {
    if (!audio.duration || isNaN(audio.duration)) return;
    const pct = (audio.currentTime / audio.duration) * 100;
    progressFill.style.width = pct + '%';
    currentTimeEl.textContent = formatTime(audio.currentTime);
    totalTimeEl.textContent = formatTime(audio.duration);
});

audio.addEventListener('ended', () => {
    isPlaying = false;
    btnPlay.textContent = '▶';
    // 自动下一曲
    if (playlist.length > 1) {
        currentIndex = (currentIndex + 1) % playlist.length;
        playSong(playlist[currentIndex].id);
    }
});

audio.addEventListener('error', (e) => {
    console.error('Audio error:', audio.error);
    let msg = '音频加载失败';
    if (audio.error) {
        switch(audio.error.code) {
            case 1: msg = '音频加载被中断'; break;
            case 2: msg = '网络错误，无法加载音频'; break;
            case 3: msg = '音频解码失败'; break;
            case 4: msg = '音频源不可用'; break;
        }
    }
    showToast(msg, 'error');
    isPlaying = false;
    btnPlay.textContent = '▶';
    isLoading = false;
    playerBar.classList.remove('loading');
});

audio.addEventListener('waiting', () => {
    playerBar.classList.add('loading');
});

audio.addEventListener('canplay', () => {
    playerBar.classList.remove('loading');
});

// 点击进度条跳转
progressBar.addEventListener('click', (e) => {
    if (!audio.duration || isNaN(audio.duration)) return;
    const rect = progressBar.getBoundingClientRect();
    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    audio.currentTime = pct * audio.duration;
});

// ========== 音量控制 ==========
function updateVolumeUI() {
    const pct = Math.round(audio.volume * 100);
    volumeSlider.value = pct;
    if (volumePct) volumePct.textContent = pct + '%';
    if (pct === 0) btnVolume.textContent = '🔇';
    else if (pct < 50) btnVolume.textContent = '🔉';
    else btnVolume.textContent = '🔊';
}

volumeSlider.addEventListener('input', (e) => {
    audio.volume = e.target.value / 100;
    updateVolumeUI();
});

btnVolume.addEventListener('click', () => {
    if (audio.volume > 0) {
        audio.dataset.prevVolume = audio.volume;
        audio.volume = 0;
    } else {
        audio.volume = parseFloat(audio.dataset.prevVolume || 1.0);
    }
    updateVolumeUI();
});

// ========== 歌词面板 ==========
btnLyric.addEventListener('click', () => {
    lyricPanel.classList.toggle('hidden');
});

lyricClose.addEventListener('click', () => {
    lyricPanel.classList.add('hidden');
});

function updateLyrics(lyricText) {
    if (!lyricText || !lyricText.trim()) {
        lyricBody.innerHTML = '<p class="lyric-line empty">暂无歌词</p>';
        return;
    }
    const lines = lyricText.split('\n');
    lyricBody.innerHTML = lines.map(line => `<p class="lyric-line">${line || '&nbsp;'}</p>`).join('');
}

// ========== 工具函数 ==========
function formatTime(sec) {
    if (!sec || isNaN(sec)) return '0:00';
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${s.toString().padStart(2, '0')}`;
}

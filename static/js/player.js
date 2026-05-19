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
const btnHeart = document.getElementById('btnHeart');

// 播放列表和状态
let playlist = [];        // 当前播放队列
let currentIndex = -1;    // 当前播放歌曲在队列中的索引
let currentSongId = null;
let currentSongData = null;
let isPlaying = false;
let isLoading = false;

// ========== 跨页面播放持久化 ==========
// 页面卸载前保存播放状态到 localStorage
window.addEventListener('beforeunload', () => {
    if (!currentSongId) return;
    const state = {
        songId: currentSongId,
        songName: playerName.textContent,
        songArtist: playerArtist.textContent,
        coverSrc: playerCover.src,
        currentTime: audio.currentTime,
        duration: audio.duration,
        isPlaying: !audio.paused,
        volume: audio.volume,
        currentIndex: currentIndex,
        playlist: playlist.map(s => ({
            id: s.id,
            name: s.name,
            artist: s.artist,
            cover_url: s.cover_url || '',
            hot_score: s.hot_score || 0,
            genre: s.genre || '',
            fav_status: s.fav_status || 0,
        })),
    };
    try {
        localStorage.setItem('playerState', JSON.stringify(state));
    } catch(e) {
        // localStorage 可能已满，忽略
    }
});

// 页面加载时恢复播放状态
function restorePlayerState() {
    try {
        const raw = localStorage.getItem('playerState');
        if (!raw) return;
        const state = JSON.parse(raw);

        // 恢复播放列表
        if (state.playlist && state.playlist.length > 0) {
            playlist = state.playlist;
            currentIndex = state.currentIndex >= 0 ? state.currentIndex : 0;
        }

        // 恢复UI
        if (state.songId) {
            currentSongId = state.songId;
            playerName.textContent = state.songName || '未知';
            playerArtist.textContent = state.songArtist || '未知';
            if (state.coverSrc && !state.coverSrc.includes('default-cover')) {
                playerCover.src = state.coverSrc;
            } else {
                playerCover.src = '/static/img/default-cover.svg';
            }
        }

        // 恢复音量
        if (typeof state.volume === 'number') {
            audio.volume = state.volume;
            updateVolumeUI();
        }

        // 恢复播放位置和状态
        if (state.songId && state.isPlaying) {
            audio.src = `/api/song/${state.songId}/play`;
            audio.addEventListener('loadedmetadata', function onMeta() {
                audio.removeEventListener('loadedmetadata', onMeta);
                if (state.currentTime && isFinite(state.currentTime)) {
                    audio.currentTime = Math.min(state.currentTime, audio.duration || state.currentTime);
                }
                audio.play().then(() => {
                    isPlaying = true;
                    btnPlay.textContent = '⏸';
                }).catch(() => {
                    // 浏览器可能阻止自动播放，需要用户交互
                    isPlaying = false;
                    btnPlay.textContent = '▶';
                });
            });
            // 处理加载失败的情况
            audio.addEventListener('error', function onErr() {
                audio.removeEventListener('error', onErr);
                isPlaying = false;
                btnPlay.textContent = '▶';
            }, { once: true });
        } else if (state.songId && state.currentTime) {
            // 暂停状态也要恢复进度显示
            audio.src = `/api/song/${state.songId}/play`;
            audio.addEventListener('loadedmetadata', function onMeta() {
                audio.removeEventListener('loadedmetadata', onMeta);
                audio.currentTime = Math.min(state.currentTime, audio.duration || state.currentTime);
                // 暂停状态：不自动播放，但更新进度条
                if (state.duration && isFinite(state.duration)) {
                    const pct = (audio.currentTime / state.duration) * 100;
                    progressFill.style.width = pct + '%';
                    currentTimeEl.textContent = formatTime(audio.currentTime);
                    totalTimeEl.textContent = formatTime(state.duration);
                }
            }, { once: true });
        }

        // 获取歌曲详情来更新心形按钮和歌词
        if (state.songId) {
            fetch(`/api/song/${state.songId}`).then(r => r.json()).then(data => {
                if (data.code === '200') {
                    currentSongData = data.data;
                    updateHeartButton(data.data.fav_status);
                    updateLyrics(data.data.lyric);
                }
            }).catch(() => {});
        }
    } catch(e) {
        console.warn('恢复播放状态失败:', e);
    }
}

// ========== LRC歌词解析与同步 ==========
let parsedLyrics = [];      // [{time: 秒数, text: "歌词"}, ...]
let currentLyricIndex = -1; // 当前高亮行索引

// 设置初始音量
audio.volume = 1.0;

const volumePct = document.getElementById('volumePct');

/**
 * 解析LRC格式歌词
 * 支持格式: [mm:ss.xx]歌词、[mm:ss.xxx]歌词
 * 支持一行多个时间戳: [00:12.00][00:45.30]歌词
 */
function parseLRC(lrcText) {
    if (!lrcText || !lrcText.trim()) return [];
    
    const lines = lrcText.split('\n');
    const result = [];
    const timeRegex = /\[(\d{2}):(\d{2})\.(\d{2,3})\]/g;
    
    for (const line of lines) {
        const timestamps = [];
        let match;
        
        while ((match = timeRegex.exec(line)) !== null) {
            const min = parseInt(match[1], 10);
            const sec = parseInt(match[2], 10);
            const ms = match[3].length === 2 
                ? parseInt(match[3], 10) * 10 
                : parseInt(match[3], 10);
            timestamps.push(min * 60 + sec + ms / 1000);
        }
        
        // 提取歌词文本（去掉所有时间戳）
        const text = line.replace(/\[\d{2}:\d{2}\.\d{2,3}\]/g, '').trim();
        
        if (timestamps.length > 0 && text) {
            for (const t of timestamps) {
                result.push({ time: t, text: text });
            }
        }
    }
    
    // 按时间排序
    result.sort((a, b) => a.time - b.time);
    return result;
}

/**
 * 根据当前播放时间找到对应的歌词行索引
 */
function findCurrentLyricIndex(currentTime) {
    if (parsedLyrics.length === 0) return -1;
    
    // 如果还没到第一句歌词
    if (currentTime < parsedLyrics[0].time) return -1;
    
    // 二分查找：找到最后一个 time <= currentTime 的行
    let lo = 0, hi = parsedLyrics.length - 1;
    let result = -1;
    
    while (lo <= hi) {
        const mid = Math.floor((lo + hi) / 2);
        if (parsedLyrics[mid].time <= currentTime) {
            result = mid;
            lo = mid + 1;
        } else {
            hi = mid - 1;
        }
    }
    
    return result;
}

/**
 * 渲染歌词面板
 */
function updateLyrics(lyricText) {
    currentLyricIndex = -1;
    
    if (!lyricText || !lyricText.trim()) {
        parsedLyrics = [];
        lyricBody.innerHTML = '<p class="lyric-line empty">暂无歌词</p>';
        return;
    }
    
    // 尝试解析LRC格式
    parsedLyrics = parseLRC(lyricText);
    
    if (parsedLyrics.length > 0) {
        // 有LRC时间戳 - 渲染同步歌词
        lyricBody.innerHTML = parsedLyrics.map((item, idx) => 
            `<p class="lyric-line" data-idx="${idx}">${item.text || '&nbsp;'}</p>`
        ).join('');
    } else {
        // 无时间戳 - 普通文本显示
        parsedLyrics = [];
        const lines = lyricText.split('\n');
        // 过滤掉空行和纯元数据行
        const filtered = lines.filter(l => {
            const trimmed = l.trim();
            return trimmed && !trimmed.startsWith('[by:') && !trimmed.startsWith('[ti:') 
                && !trimmed.startsWith('[ar:') && !trimmed.startsWith('[al:')
                && !trimmed.startsWith('[offset:') && !trimmed.startsWith('[00:00.00]');
        });
        lyricBody.innerHTML = filtered.map((line, idx) => 
            `<p class="lyric-line" data-idx="${idx}">${line || '&nbsp;'}</p>`
        ).join('');
    }
}

/**
 * 高亮当前歌词行并自动滚动
 */
function highlightCurrentLyric(index) {
    if (index === currentLyricIndex) return;
    currentLyricIndex = index;
    
    // 移除所有高亮
    const allLines = lyricBody.querySelectorAll('.lyric-line');
    allLines.forEach(el => el.classList.remove('active', 'nearby'));
    
    if (index < 0 || index >= parsedLyrics.length) return;
    
    // 高亮当前行
    const currentLine = lyricBody.querySelector(`.lyric-line[data-idx="${index}"]`);
    if (!currentLine) return;
    
    currentLine.classList.add('active');
    
    // 附近行微弱高亮
    for (let offset = -1; offset <= 1; offset += 2) {
        const nearIdx = index + offset;
        if (nearIdx >= 0 && nearIdx < parsedLyrics.length) {
            const nearLine = lyricBody.querySelector(`.lyric-line[data-idx="${nearIdx}"]`);
            if (nearLine) nearLine.classList.add('nearby');
        }
    }
    
    // 自动滚动 - 将当前行滚动到面板中央
    const panelHeight = lyricBody.clientHeight;
    const lineTop = currentLine.offsetTop;
    const lineHeight = currentLine.offsetHeight;
    const scrollTarget = lineTop - panelHeight / 2 + lineHeight / 2;
    
    lyricBody.scrollTo({
        top: Math.max(0, scrollTarget),
        behavior: 'smooth'
    });
}

// ========== 设置播放队列 ==========
// songs: [{id, name, artist, cover_url, ...}, ...]
// startIndex: 从哪首歌开始播放
function setPlaylist(songs, startIndex = 0) {
    if (!songs || !songs.length) return;
    playlist = songs.slice();  // 拷贝，避免引用问题
    currentIndex = startIndex;
}

// ========== 播放歌曲 ==========
playSong = async function(songId, songList = null, listIndex = -1) {
    if (isLoading) {
        showToast('正在加载中，请稍候...', 'info');
        return;
    }

    // 如果传入了歌曲列表，设置为新播放队列
    if (songList && songList.length > 0) {
        playlist = songList.slice();
        currentIndex = listIndex >= 0 ? listIndex : playlist.findIndex(s => s.id === songId);
    } else {
        // 没有传列表时，检查当前播放队列中是否已有此歌
        const idx = playlist.findIndex(s => s.id === songId);
        if (idx >= 0) {
            currentIndex = idx;
        } else {
            // 单独播放，追加到队列末尾
            currentIndex = playlist.length;
        }
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

        // 更新歌词（解析LRC）
        updateLyrics(data.data.lyric);

        // 如果不在列表中，追加到末尾
        const existIdx = playlist.findIndex(s => s.id === songId);
        if (existIdx < 0) {
            playlist.push(data.data);
            currentIndex = playlist.length - 1;
        }

        // 更新心形按钮状态
        updateHeartButton(data.data.fav_status);

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
        playSong(playlist[currentIndex >= 0 ? currentIndex : 0].id);
    }
});

btnPrev.addEventListener('click', () => {
    if (playlist.length === 0) return;
    // 如果当前歌曲播放超过3秒，则回到歌曲开头；否则切上一首
    if (audio.currentTime > 3) {
        audio.currentTime = 0;
        return;
    }
    currentIndex = (currentIndex - 1 + playlist.length) % playlist.length;
    playSong(playlist[currentIndex].id);
});

btnNext.addEventListener('click', () => {
    if (playlist.length === 0) return;
    currentIndex = (currentIndex + 1) % playlist.length;
    playSong(playlist[currentIndex].id);
});

// ========== 进度条 + 歌词同步 ==========
audio.addEventListener('timeupdate', () => {
    if (!audio.duration || isNaN(audio.duration)) return;
    const pct = (audio.currentTime / audio.duration) * 100;
    progressFill.style.width = pct + '%';
    currentTimeEl.textContent = formatTime(audio.currentTime);
    totalTimeEl.textContent = formatTime(audio.duration);
    
    // 歌词同步
    if (parsedLyrics.length > 0) {
        const idx = findCurrentLyricIndex(audio.currentTime);
        highlightCurrentLyric(idx);
    }
});

audio.addEventListener('ended', () => {
    isPlaying = false;
    btnPlay.textContent = '▶';
    currentLyricIndex = -1;
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

// 点击歌词行跳转播放位置
lyricBody.addEventListener('click', (e) => {
    const line = e.target.closest('.lyric-line');
    if (!line || parsedLyrics.length === 0) return;
    
    const idx = parseInt(line.dataset.idx, 10);
    if (isNaN(idx) || idx < 0 || idx >= parsedLyrics.length) return;
    
    // 跳转到该歌词行的时间
    const targetTime = parsedLyrics[idx].time;
    if (audio.duration && !isNaN(audio.duration)) {
        audio.currentTime = targetTime;
        highlightCurrentLyric(idx);
    }
});

// ========== 工具函数 ==========
function formatTime(sec) {
    if (!sec || isNaN(sec)) return '0:00';
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${s.toString().padStart(2, '0')}`;
}

// ========== 心形喜欢按钮 ==========
/**
 * 更新播放器栏的心形按钮状态
 * @param {number} favStatus - 0=未喜欢, 1=喜欢, -1=不喜欢
 */
function updateHeartButton(favStatus) {
    if (!btnHeart) return;
    if (favStatus === 1) {
        btnHeart.classList.add('liked');
        btnHeart.textContent = '♥';
        btnHeart.title = '取消喜欢';
    } else {
        btnHeart.classList.remove('liked');
        btnHeart.textContent = '♡';
        btnHeart.title = '喜欢';
    }
}

/**
 * 从播放器栏点击心形按钮
 */
async function toggleFavoriteFromPlayer() {
    if (!currentSongId) {
        showToast('请先播放一首歌曲', 'info');
        return;
    }
    await toggleFavorite(currentSongId, btnHeart);
}

// ========== 页面加载时恢复播放状态 ==========
document.addEventListener('DOMContentLoaded', restorePlayerState);

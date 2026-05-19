// -*- coding: utf-8 -*-
// 全局工具函数

// Toast 通知
function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    document.body.appendChild(toast);
    requestAnimationFrame(() => toast.classList.add('show'));
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, 2500);
}

// 全局播放歌曲函数（由 player.js 实现）
// playSong(songId, songList=null, listIndex=-1)
let playSong = function(songId, songList, listIndex) {
    showToast('播放器加载中...', 'info');
};

// 导航栏交互
document.addEventListener('DOMContentLoaded', function() {
    // 移动端菜单切换
    const toggle = document.getElementById('navToggle');
    const menu = document.getElementById('navMenu');
    if (toggle && menu) {
        toggle.addEventListener('click', () => {
            menu.classList.toggle('active');
            toggle.classList.toggle('active');
        });
    }

    // 更新认证区域
    updateAuthArea();
});

// 更新导航栏认证区域
async function updateAuthArea() {
    const area = document.getElementById('authArea');
    if (!area) return;

    try {
        const resp = await fetch('/api/user/info');
        const data = await resp.json();
        if (data.code === '200') {
            const u = data.data;
            area.innerHTML = `
                <a href="/profile" class="nav-link">👤 ${u.nickname || u.username}</a>
                <a href="/favorites" class="nav-link">❤️ 喜欢</a>
                <a href="#" class="nav-link" onclick="logout(event)">退出</a>
            `;
        } else {
            area.innerHTML = `
                <a href="/login" class="nav-link">登录</a>
                <a href="/register" class="nav-link">注册</a>
            `;
        }
    } catch(e) {
        area.innerHTML = `
            <a href="/login" class="nav-link">登录</a>
            <a href="/register" class="nav-link">注册</a>
        `;
    }
}

// ========== 全局喜欢/收藏功能 ==========
let _isLoggedIn = false;  // 缓存登录状态

// 检查登录状态
async function checkLogin() {
    try {
        const resp = await fetch('/api/user/info');
        const data = await resp.json();
        _isLoggedIn = (data.code === '200');
    } catch(e) {
        _isLoggedIn = false;
    }
    return _isLoggedIn;
}

/**
 * 切换喜欢/取消喜欢
 * @param {number} songId - 歌曲ID
 * @param {HTMLElement} btnEl - 心形按钮元素（可选，用于更新UI）
 */
async function toggleFavorite(songId, btnEl) {
    // 先检查登录状态
    const loggedIn = await checkLogin();
    if (!loggedIn) {
        showToast('请先登录后再收藏歌曲', 'error');
        setTimeout(() => {
            window.location.href = '/login?next=' + encodeURIComponent(window.location.pathname + window.location.search);
        }, 800);
        return;
    }

    // 判断当前状态：如果已经是喜欢，则取消；否则设为喜欢
    const isLiked = btnEl && btnEl.classList.contains('liked');
    const likeStatus = isLiked ? 0 : 1;

    try {
        const resp = await fetch(`/api/song/${songId}/favorite`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({like_status: likeStatus})
        });
        const data = await resp.json();
        if (data.code === '200') {
            // 更新按钮UI
            if (btnEl) {
                if (likeStatus === 1) {
                    btnEl.classList.add('liked');
                    btnEl.textContent = '♥';
                } else {
                    btnEl.classList.remove('liked');
                    btnEl.textContent = '♡';
                }
            }
            // 同步更新播放器栏心形按钮
            if (typeof currentSongId !== 'undefined' && songId === currentSongId && typeof btnHeart !== 'undefined') {
                if (likeStatus === 1) {
                    btnHeart.classList.add('liked');
                    btnHeart.textContent = '♥';
                } else {
                    btnHeart.classList.remove('liked');
                    btnHeart.textContent = '♡';
                }
            }
            showToast(likeStatus === 1 ? '已添加到喜欢 ❤️' : '已取消喜欢', 'success');
        } else if (data.code === '401') {
            showToast('请先登录后再收藏歌曲', 'error');
            setTimeout(() => {
                window.location.href = '/login?next=' + encodeURIComponent(window.location.pathname + window.location.search);
            }, 800);
        } else {
            showToast(data.message || '操作失败', 'error');
        }
    } catch(e) {
        showToast('网络错误，请重试', 'error');
    }
}

// 退出登录
async function logout(e) {
    e.preventDefault();
    try {
        await fetch('/logout', {method: 'POST'});
        showToast('已退出登录', 'success');
        setTimeout(() => location.href = '/', 500);
    } catch(e) {
        showToast('退出失败', 'error');
    }
}

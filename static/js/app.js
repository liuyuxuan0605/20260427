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
let playSong = function(songId) {
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

// ═══════════════════════════════════════════════════════════════
//  chat.js  —  UOLI Chatbot JS Frontend
//  Handles: SSE streaming, message rendering, session management,
//           markdown, suggestion chips, history, auto-resize, etc.
// ═══════════════════════════════════════════════════════════════

// ── Config ───────────────────────────────────────────────────
const API_BASE      = '';          // Same origin — FastAPI serves this file
const STREAM_URL    = '/chat/stream';
const MAX_CHARS     = 2000;
const SESSION_KEY   = 'uoli_session_id';
const HISTORY_KEY   = 'uoli_chat_history';

// ── DOM refs ─────────────────────────────────────────────────
const messagesArea  = document.getElementById('messages-area');
const chatInput     = document.getElementById('chat-input');
const sendBtn       = document.getElementById('btn-send');
const charCounter   = document.getElementById('char-counter');
const welcomeScreen = document.getElementById('welcome-screen');
const errorBanner   = document.getElementById('error-banner');
const errorMsg      = document.getElementById('error-msg');
const agentBadge    = document.getElementById('header-agent-badge');
const agentLabel    = document.getElementById('agent-label');
const historyList   = document.getElementById('history-list');
const toast         = document.getElementById('toast');
const sidebarEl     = document.getElementById('sidebar');
const sidebarOverlay= document.getElementById('sidebar-overlay');
const btnSidebarToggle = document.getElementById('btn-sidebar-toggle');
const sidebarStats  = document.getElementById('sidebar-stats');

// ── Session ──────────────────────────────────────────────────
const SESSION_LIST_KEY = 'uoli_sessions_list';

function getSessionsList() {
    try {
        const raw = localStorage.getItem(SESSION_LIST_KEY);
        return raw ? JSON.parse(raw) : [];
    } catch { return []; }
}

function saveSessionsList(list) {
    localStorage.setItem(SESSION_LIST_KEY, JSON.stringify(list));
}

let sessionsList = getSessionsList();
let sessionId = localStorage.getItem(SESSION_KEY);
let conversationHistory = [];

function saveHistory() {
    const trimmed = conversationHistory.slice(-60); // keep last 60 turns
    try {
        localStorage.setItem(HISTORY_KEY + '_' + sessionId, JSON.stringify(trimmed));
    } catch {}
}

function loadHistory() {
    try {
        const raw = localStorage.getItem(HISTORY_KEY + '_' + sessionId);
        if (raw) conversationHistory = JSON.parse(raw);
        else conversationHistory = [];
    } catch { conversationHistory = []; }
}

// ── Markdown renderer (lightweight, no deps) ─────────────────
function renderMarkdown(text) {
    if (!text) return '';

    // Escape HTML first
    const esc = (s) => s
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');

    // Process code blocks first (protect them from other rules)
    const codeBlocks = [];
    text = text.replace(/```[\w]*\n?([\s\S]*?)```/g, (_, code) => {
        const idx = codeBlocks.length;
        codeBlocks.push(`<pre><code>${esc(code.trim())}</code></pre>`);
        return `\x00CODE${idx}\x00`;
    });

    // Inline code
    text = text.replace(/`([^`]+)`/g, (_, code) => `<code>${esc(code)}</code>`);

    // Headers
    text = text.replace(/^### (.+)$/gm, '<h3>$1</h3>');
    text = text.replace(/^## (.+)$/gm,  '<h2>$1</h2>');
    text = text.replace(/^# (.+)$/gm,   '<h1>$1</h1>');

    // Bold / italic
    text = text.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
    text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    text = text.replace(/\*(.+?)\*/g, '<em>$1</em>');

    // Links
    text = text.replace(/\[([^\]]+)\]\(([^)]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener">$1</a>');

    // Tables (simple |..| format)
    text = text.replace(/((?:\|.+\|\n?)+)/g, (match) => {
        const rows = match.trim().split('\n');
        if (rows.length < 2) return match;
        const isSeparator = (r) => /^\|[-| :]+\|$/.test(r.trim());
        let html = '<table>';
        let inBody = false;
        rows.forEach((row, i) => {
            if (isSeparator(row)) { inBody = true; return; }
            const cells = row.split('|').slice(1, -1).map(c => c.trim());
            const tag = (!inBody && i === 0) ? 'th' : 'td';
            html += '<tr>' + cells.map(c => `<${tag}>${c}</${tag}>`).join('') + '</tr>';
        });
        html += '</table>';
        return html;
    });

    // Unordered lists
    text = text.replace(/((?:^[ \t]*[-*+] .+\n?)+)/gm, (match) => {
        const items = match.trim().split('\n').map(l =>
            `<li>${l.replace(/^[ \t]*[-*+] /, '').trim()}</li>`).join('');
        return `<ul>${items}</ul>`;
    });

    // Ordered lists
    text = text.replace(/((?:^[ \t]*\d+\. .+\n?)+)/gm, (match) => {
        const items = match.trim().split('\n').map(l =>
            `<li>${l.replace(/^[ \t]*\d+\. /, '').trim()}</li>`).join('');
        return `<ol>${items}</ol>`;
    });

    // Blockquotes
    text = text.replace(/^> (.+)$/gm, '<blockquote>$1</blockquote>');

    // Horizontal rule
    text = text.replace(/^---+$/gm, '<hr>');

    // Paragraphs (double newline)
    text = text.replace(/\n\n+/g, '</p><p>');
    text = '<p>' + text + '</p>';

    // Single newlines → <br> inside paragraphs
    text = text.replace(/<\/?(h[1-6]|ul|ol|li|pre|blockquote|table|tr|th|td|hr)[^>]*>/g, m => m);
    text = text.replace(/(?<!<\/?(h[1-6]|ul|ol|li|pre|blockquote|table|tr|th|td|hr)>)\n(?!<(h[1-6]|ul|ol|li|pre|blockquote|table|tr|th|td|hr))/g, '<br>');

    // Restore code blocks
    text = text.replace(/\x00CODE(\d+)\x00/g, (_, i) => codeBlocks[Number(i)]);

    // Clean up empty paragraphs
    text = text.replace(/<p><\/p>/g, '');
    text = text.replace(/<p>(<(?:h[1-6]|ul|ol|pre|table|blockquote|hr)[^>]*>)/g, '$1');
    text = text.replace(/(<\/(?:h[1-6]|ul|ol|pre|table|blockquote|hr)>)<\/p>/g, '$1');

    return text;
}

// ── Time formatting ───────────────────────────────────────────
function formatTime(date) {
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

// ── Source pill HTML ─────────────────────────────────────────
function buildSourcesHtml(sources) {
    if (!sources || sources.length === 0) return '';
    const pills = sources.slice(0, 5).map(src => {
        try {
            const u = new URL(src);
            const label = u.pathname.replace(/\/$/, '').split('/').pop() || u.hostname;
            return `<a class="source-pill" href="${src}" target="_blank" rel="noopener" title="${src}">
                        🔗 ${label}
                    </a>`;
        } catch {
            return '';
        }
    }).join('');
    return `<div class="sources-row">${pills}</div>`;
}

// ── Append a user message bubble ─────────────────────────────
function appendUserMessage(text) {
    hideWelcome();
    const now = new Date();
    const wrapper = document.createElement('div');
    wrapper.className = 'msg-wrapper user';
    wrapper.innerHTML = `
        <div>
            <div class="msg-bubble">${escapeHtml(text)}</div>
            <div class="msg-time">${formatTime(now)}</div>
        </div>
    `;
    messagesArea.appendChild(wrapper);
    scrollToBottom();
    return wrapper;
}

// ── Append a bot message bubble (streaming-ready) ────────────
function appendBotMessage() {
    const now = new Date();
    const wrapper = document.createElement('div');
    wrapper.className = 'msg-wrapper bot';
    wrapper.innerHTML = `
        <div>
            <div class="msg-bubble" id="streaming-bubble">
                <span class="streaming-cursor"></span>
            </div>
            <div class="msg-time" id="msg-time-el">${formatTime(now)}</div>
        </div>
    `;
    messagesArea.appendChild(wrapper);
    scrollToBottom();
    return wrapper;
}

function renderPastUserMessage(text, timeStr) {
    const time = new Date(timeStr);
    const wrapper = document.createElement('div');
    wrapper.className = 'msg-wrapper user';
    wrapper.innerHTML = `
        <div>
            <div class="msg-bubble">${escapeHtml(text)}</div>
            <div class="msg-time">${formatTime(time)}</div>
        </div>
    `;
    messagesArea.appendChild(wrapper);
}

function renderPastBotMessage(text, timeStr) {
    const time = new Date(timeStr);
    const wrapper = document.createElement('div');
    wrapper.className = 'msg-wrapper bot';
    wrapper.innerHTML = `
        <div>
            <div class="msg-bubble">${renderMarkdown(text)}</div>
            <div class="msg-time">${formatTime(time)}</div>
        </div>
    `;
    messagesArea.appendChild(wrapper);
}

// ── Typing indicator ─────────────────────────────────────────
let typingWrapper = null;

function showTyping() {
    hideWelcome();
    
    // Inject CSS dynamically for the progressive dotted red spinner
    if (!document.getElementById('dotted-loader-css')) {
        const style = document.createElement('style');
        style.id = 'dotted-loader-css';
        style.innerHTML = `
            .dotted-loader {
                width: 26px;
                height: 26px;
                margin-left: 10px;
                margin-top: 5px;
            }
            .mask-draw {
                stroke-dasharray: 63;
                stroke-dashoffset: 63;
                animation: draw-circle 1.5s infinite;
            }
            @keyframes draw-circle {
                0% { stroke-dashoffset: 63; }
                85% { stroke-dashoffset: 0; }
                100% { stroke-dashoffset: 0; }
            }
        `;
        document.head.appendChild(style);
    }

    typingWrapper = document.createElement('div');
    typingWrapper.className = 'msg-wrapper bot';
    typingWrapper.id = 'typing-indicator-wrapper';
    typingWrapper.innerHTML = `
        <div class="msg-avatar bot-av" style="background: transparent; border: none; padding: 0; box-shadow: none; display: flex; align-items: flex-start; justify-content: center; width: 40px; height: 40px; margin-right: 8px;">
            <svg class="dotted-loader" viewBox="0 0 24 24">
                <defs>
                    <mask id="fill-mask">
                        <circle cx="12" cy="12" r="10" fill="none" stroke="white" stroke-width="6" class="mask-draw" transform="rotate(-90 12 12)"></circle>
                    </mask>
                </defs>
                <!-- Faint background track -->
                <circle cx="12" cy="12" r="10" fill="none" stroke="#fee2e2" stroke-width="2.5"></circle>
                <!-- Tiny red dots that get revealed -->
                <circle cx="12" cy="12" r="10" fill="none" stroke="#ef4444" stroke-width="3" stroke-dasharray="0.1 6.28" stroke-linecap="round" mask="url(#fill-mask)"></circle>
            </svg>
        </div>
        <div class="msg-bubble" style="background: transparent; box-shadow: none; padding-left: 0;">
            <span style="color: #6b7280; font-style: italic; font-size: 14px;">Thinking...</span>
        </div>
    `;
    messagesArea.appendChild(typingWrapper);
    scrollToBottom();
}

function hideTyping() {
    if (typingWrapper) {
        typingWrapper.remove();
        typingWrapper = null;
    }
}

// ── Utils ─────────────────────────────────────────────────────
function escapeHtml(text) {
    return text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function scrollToBottom(smooth = true) {
    requestAnimationFrame(() => {
        messagesArea.scrollTo({
            top: messagesArea.scrollHeight,
            behavior: smooth ? 'smooth' : 'instant'
        });
    });
}

function hideWelcome() {
    if (welcomeScreen && welcomeScreen.style.display !== 'none') {
        welcomeScreen.style.opacity = '0';
        welcomeScreen.style.transform = 'scale(0.96)';
        welcomeScreen.style.transition = 'all 0.25s ease';
        setTimeout(() => { welcomeScreen.style.display = 'none'; }, 250);
    }
}

function showError(msg) {
    errorMsg.textContent = msg;
    errorBanner.classList.add('visible');
    setTimeout(() => errorBanner.classList.remove('visible'), 6000);
}

function showToast(msg, icon = '✅') {
    toast.textContent = icon + ' ' + msg;
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 2500);
}

function setAgentBadge(agentName) {
    const map = {
        faculty:    { label: 'Faculty Agent',    icon: '👨‍🏫' },
        admissions: { label: 'Admissions Agent', icon: '📋' },
        policy:     { label: 'Policy Agent',     icon: '📜' },
        offices:    { label: 'Offices Agent',    icon: '🏛️' },
        campus:     { label: 'Campus Agent',     icon: '🏫' },
        general:    { label: 'General Agent',    icon: '🤖' },
    };
    const info = map[agentName] || { label: 'UOLI Assistant', icon: '🎓' };
    agentLabel.textContent = `${info.icon} ${info.label}`;
}

// ── History sidebar ───────────────────────────────────────────
function refreshHistorySidebar() {
    if (!historyList) return;
    
    if (sessionsList.length === 0) {
        historyList.innerHTML = `<div style="padding:10px 10px; font-size:12px; color:var(--text-subtle);">No history yet</div>`;
        return;
    }
    
    historyList.innerHTML = sessionsList.map((s, i) => `
        <div class="history-item ${s.id === sessionId ? 'active' : ''}" onclick="loadSession('${s.id}')">
            <span class="hist-icon">💬</span>
            <span class="hist-text">${escapeHtml(s.title)}</span>
        </div>
    `).join('');
}

window.loadSession = function(sid) {
    if (sid === sessionId || isStreaming) return;
    
    sessionId = sid;
    localStorage.setItem(SESSION_KEY, sessionId);
    loadHistory();
    
    messagesArea.innerHTML = '';
    
    if (conversationHistory.length === 0) {
        if (welcomeScreen) {
            welcomeScreen.style.display = '';
            welcomeScreen.style.opacity = '1';
            messagesArea.appendChild(welcomeScreen);
        }
    } else {
        if (welcomeScreen) welcomeScreen.style.display = 'none';
        conversationHistory.forEach(msg => {
            if (msg.role === 'user') renderPastUserMessage(msg.text, msg.time);
            else renderPastBotMessage(msg.text, msg.time);
        });
        scrollToBottom(false);
    }
    
    agentLabel.textContent = '🎓 UOLI Assistant';
    refreshHistorySidebar();
    
    if (window.innerWidth <= 768 && sidebarEl.classList.contains('open')) {
        sidebarEl.classList.remove('open');
        sidebarOverlay.classList.remove('visible');
    }
}

// ── Fetch health stats for sidebar ───────────────────────────
async function fetchAndShowStats() {
    if (!sidebarStats) return;
    try {
        const r = await fetch('/health');
        if (!r.ok) return;
        const data = await r.json();
        sidebarStats.innerHTML = `
            <div class="stat-row">
                <span class="stat-label"><span class="status-dot"></span> Status</span>
                <span class="stat-value">${data.status === 'healthy' ? 'Online' : data.status}</span>
            </div>
            <div class="stat-row">
                <span class="stat-label">📚 Docs</span>
                <span class="stat-value">${data.docs_loaded?.toLocaleString() ?? '—'}</span>
            </div>
            <div class="stat-row">
                <span class="stat-label">💬 Sessions</span>
                <span class="stat-value">${data.active_sessions ?? '—'}</span>
            </div>
            <div class="stat-row">
                <span class="stat-label">🗄️ Redis</span>
                <span class="stat-value">${data.redis_connected ? 'Connected' : 'Local'}</span>
            </div>
        `;
    } catch {
        sidebarStats.innerHTML = `<div class="stat-row"><span class="stat-label">⚠️ API offline</span></div>`;
    }
}

// ── Core: Send message via SSE ────────────────────────────────
let isStreaming = false;
let currentEventSource = null;

async function sendMessage(userText) {
    userText = userText.trim();
    if (!userText || isStreaming) return;

    isStreaming = true;
    sendBtn.disabled = true;
    chatInput.disabled = true;
    chatInput.value = '';
    chatInput.style.height = 'auto';
    charCounter.textContent = `0 / ${MAX_CHARS}`;
    errorBanner.classList.remove('visible');

    // Record user message
    conversationHistory.push({ role: 'user', text: userText, time: new Date().toISOString() });
    saveHistory();
    
    const currentSession = sessionsList.find(s => s.id === sessionId);
    if (currentSession && conversationHistory.length === 1) {
        currentSession.title = userText.slice(0, 40) + (userText.length > 40 ? '...' : '');
        saveSessionsList(sessionsList);
    }
    refreshHistorySidebar();

    // Render user bubble
    appendUserMessage(userText);
    showTyping();

    // Build SSE URL
    const params = new URLSearchParams({ message: userText, session_id: sessionId });
    const url = `${STREAM_URL}?${params.toString()}`;

    let fullText = '';
    let sources  = [];
    let botWrapper = null;
    let streamingBubble = null;
    let firstToken = true;

    currentEventSource = new EventSource(url);

    currentEventSource.onmessage = (event) => {
        const data = event.data;

        if (data === '[DONE]') {
            // Stream complete
            if (streamingBubble) {
                // Remove cursor, finalize markdown
                streamingBubble.innerHTML = renderMarkdown(fullText) + buildSourcesHtml(sources);
            }
            conversationHistory.push({ role: 'bot', text: fullText, time: new Date().toISOString() });
            saveHistory();
            refreshHistorySidebar();

            cleanup();
            return;
        }

        if (data.startsWith('[ERROR]')) {
            hideTyping();
            showError('Server error: ' + data.slice(7));
            cleanup();
            return;
        }

        if (data.startsWith('[SOURCES]')) {
            try { sources = JSON.parse(data.slice(9)); } catch {}
            return;
        }

        // Detect agent hint embedded in first token (optional — backend can prefix)
        if (firstToken && data.startsWith('[AGENT:')) {
            const match = data.match(/\[AGENT:(\w+)\]/);
            if (match) { setAgentBadge(match[1]); return; }
        }
        firstToken = false;

        // First real token — swap typing indicator for actual bubble
        if (!botWrapper) {
            hideTyping();
            botWrapper = appendBotMessage();
            streamingBubble = botWrapper.querySelector('#streaming-bubble');
        }

        // Append token
        fullText += data;
        if (streamingBubble) {
            streamingBubble.innerHTML = renderMarkdown(fullText) +
                '<span class="streaming-cursor"></span>';
            scrollToBottom(false);
        }
    };

    currentEventSource.onerror = (err) => {
        hideTyping();
        if (fullText && streamingBubble) {
            // Partial response — finalize what we have
            streamingBubble.innerHTML = renderMarkdown(fullText) + buildSourcesHtml(sources);
        } else {
            showError('Connection error. Check that the API server is running.');
        }
        cleanup();
    };

    function cleanup() {
        if (currentEventSource) {
            currentEventSource.close();
            currentEventSource = null;
        }
        isStreaming = false;
        sendBtn.disabled = false;
        chatInput.disabled = false;
        chatInput.focus();
        hideTyping();
        scrollToBottom();
    }
}

// ── New chat ──────────────────────────────────────────────────
function startNewChat(render = true) {
    if (isStreaming) return;
    // Clear session & history
    sessionId = 'sess_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
    localStorage.setItem(SESSION_KEY, sessionId);
    
    sessionsList.unshift({ id: sessionId, title: 'New Conversation' });
    saveSessionsList(sessionsList);
    
    conversationHistory = [];
    saveHistory();

    if (render) {
        // Clear message area, show welcome
        messagesArea.innerHTML = '';
        if (welcomeScreen) {
            welcomeScreen.style.display = '';
            welcomeScreen.style.opacity = '1';
            welcomeScreen.style.transform = '';
            messagesArea.appendChild(welcomeScreen);
        }

        // Reset badge
        agentLabel.textContent = '🎓 UOLI Assistant';
        refreshHistorySidebar();
        chatInput.focus();
        fetchAndShowStats();
        showToast('New conversation started', '✨');

        // Tell backend to clear session (fire & forget)
        fetch(`/session/${sessionId}`, { method: 'DELETE' }).catch(() => {});
    }
}

// ── Input auto-resize ─────────────────────────────────────────
chatInput.addEventListener('input', () => {
    chatInput.style.height = 'auto';
    chatInput.style.height = Math.min(chatInput.scrollHeight, 140) + 'px';
    const len = chatInput.value.length;
    charCounter.textContent = `${len} / ${MAX_CHARS}`;
    charCounter.classList.toggle('warn', len > 1600);
    charCounter.classList.toggle('danger', len > 1900);
});

// ── Send on Enter (Shift+Enter = newline) ────────────────────
chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage(chatInput.value);
    }
});

// ── Send button click ────────────────────────────────────────
sendBtn.addEventListener('click', () => sendMessage(chatInput.value));

// ── Suggestion chips ─────────────────────────────────────────
document.addEventListener('click', (e) => {
    const chip = e.target.closest('.suggestion-chip');
    if (chip) {
        const text = chip.dataset.query;
        if (text) {
            chatInput.value = text;
            sendMessage(text);
        }
    }
});

// ── Sidebar toggle (mobile) ──────────────────────────────────
if (btnSidebarToggle) {
    btnSidebarToggle.addEventListener('click', () => {
        sidebarEl.classList.toggle('open');
        sidebarOverlay.classList.toggle('visible');
    });
}

if (sidebarOverlay) {
    sidebarOverlay.addEventListener('click', () => {
        sidebarEl.classList.remove('open');
        sidebarOverlay.classList.remove('visible');
    });
}

// ── New chat button ───────────────────────────────────────────
const btnNewChat = document.getElementById('btn-new-chat');
if (btnNewChat) {
    btnNewChat.addEventListener('click', startNewChat);
}

// ── Clear session button ─────────────────────────────────────
const btnClearSession = document.getElementById('btn-clear-session');
if (btnClearSession) {
    btnClearSession.addEventListener('click', () => {
        fetch(`/session/${sessionId}`, { method: 'DELETE' }).catch(() => {});
        showToast('Session memory cleared', '🗑️');
    });
}

// ── Init ──────────────────────────────────────────────────────
(function init() {
    if (!sessionId || !sessionsList.find(s => s.id === sessionId)) {
        startNewChat(false);
    } else {
        loadHistory();
    }

    refreshHistorySidebar();
    
    if (conversationHistory.length > 0) {
        if (welcomeScreen) welcomeScreen.style.display = 'none';
        conversationHistory.forEach(msg => {
            if (msg.role === 'user') renderPastUserMessage(msg.text, msg.time);
            else renderPastBotMessage(msg.text, msg.time);
        });
        scrollToBottom(false);
    }

    fetchAndShowStats();
    chatInput.focus();

    // Refresh stats every 30s
    setInterval(fetchAndShowStats, 30_000);
})();

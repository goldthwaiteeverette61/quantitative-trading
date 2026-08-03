/**
 * 策略管理看板 — 前端逻辑
 */

// ===== 主题系统 =====
function setTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('qt-theme', theme);
    // 更新选中指示
    document.querySelectorAll('.theme-dot').forEach(dot => {
        dot.style.outline = dot.dataset.theme === theme ? '2px solid var(--accent)' : 'none';
        dot.style.outlineOffset = '2px';
    });
}
// 页面加载时立即恢复主题（避免闪烁）
(function() {
    const saved = localStorage.getItem('qt-theme') || 'navy';
    document.documentElement.setAttribute('data-theme', saved);
    document.addEventListener('DOMContentLoaded', () => setTheme(saved));
})();

// ===== 全局状态 =====
let currentSourceType = 'text';
let strategies = [];
let generatedFilename = '';

// ===== 公共 Toast 提示 =====
function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    const bgColor = type === 'error' ? 'var(--red)' : (type === 'success' ? 'var(--green)' : 'var(--accent)');
    toast.style.cssText = `
        position: fixed;
        top: 20px;
        left: 50%;
        transform: translateX(-50%);
        background: ${bgColor};
        color: #fff;
        padding: 12px 24px;
        border-radius: 8px;
        font-size: 14px;
        font-weight: 500;
        z-index: 10000;
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        opacity: 0;
        transition: opacity 0.3s ease, top 0.3s ease;
    `;
    toast.innerText = message;
    document.body.appendChild(toast);

    // Animate in
    setTimeout(() => {
        toast.style.opacity = '1';
        toast.style.top = '40px';
    }, 10);

    // Animate out
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.top = '20px';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// ===== 公共 Confirm 对话框 (取代原生避免 Webview 拦截) =====
function showConfirm(message, onConfirm) {
    const overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.6);backdrop-filter:blur(2px);z-index:10000;display:flex;align-items:center;justify-content:center;animation:fade-in 0.2s;';

    const box = document.createElement('div');
    box.style.cssText = 'background:var(--bg-card);border:1px solid var(--border);border-radius:8px;padding:24px;width:320px;text-align:center;box-shadow:0 8px 30px rgba(0,0,0,0.3);';

    const text = document.createElement('p');
    text.innerText = message;
    text.style.cssText = 'font-size:15px;margin-bottom:24px;color:var(--text-primary);line-height:1.5';

    const btns = document.createElement('div');
    btns.style.cssText = 'display:flex;justify-content:center;gap:12px;';

    const btnCancel = document.createElement('button');
    btnCancel.innerText = '取消';
    btnCancel.className = 'btn';
    btnCancel.style.cssText = 'background:var(--bg-secondary);border:1px solid var(--border);padding:8px 20px;color:var(--text-primary)';
    btnCancel.onclick = () => overlay.remove();

    const btnOk = document.createElement('button');
    btnOk.innerText = '确认操作';
    btnOk.className = 'btn btn-primary';
    btnOk.style.cssText = 'background:var(--red);padding:8px 20px';
    btnOk.onclick = () => { overlay.remove(); onConfirm(); };

    btns.appendChild(btnCancel);
    btns.appendChild(btnOk);
    box.appendChild(text);
    box.appendChild(btns);
    overlay.appendChild(box);
    document.body.appendChild(overlay);
}

// ===== 初始化 =====
document.addEventListener('DOMContentLoaded', () => {
    initTabs();
    loadStrategies();
    loadPortfolioStrategies();
    loadStrategyList();  // 加载策略列表
    loadDeployStrategies(); // 预加载以填充筛选器等下拉选项
});

// ===== Tab 导航 =====
function initTabs() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const tab = btn.dataset.tab;
            switchTab(tab);
        });
    });
    // 恢复上次的 Tab，默认 dashboard
    const savedTab = localStorage.getItem('qt-active-tab') || 'dashboard';
    switchTab(savedTab);
}

function switchTab(tab) {
    const btn = document.querySelector(`.tab-btn[data-tab="${tab}"]`);
    if (!btn) {
        // 被删除的Tab或无效Tab，回退到 dashboard
        if (tab !== 'dashboard') { switchTab('dashboard'); }
        return;
    }

    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));

    btn.classList.add('active');
    const panel = document.getElementById(`panel-${tab}`);
    if (panel) panel.classList.add('active');
    localStorage.setItem('qt-active-tab', tab);

    // 针对特定面板触发加载
    if (tab === 'dashboard') loadDashboard();
    if (tab === 'paper-trading') pollAutopilotTasks();
    if (tab === 'macro-dashboard') loadMacroDashboard();
}

// ===== 大盘概览面板 =====
async function loadDashboard() {
    try {
        // 1. 加载大盘指数
        const resIdx = await fetch('/api/market/indices');
        const dataIdx = await resIdx.json();
        const tbody = document.getElementById('dashboard-indices-body');
        if (dataIdx.status === 'ok' && dataIdx.data.length > 0) {
            tbody.innerHTML = dataIdx.data.map(idx => {
                const color = idx.change >= 0 ? 'var(--red)' : 'var(--green)';
                const arrow = idx.change >= 0 ? '▲' : '▼';
                return `
                    <tr>
                        <td style="font-weight:600">${idx.name}</td>
                        <td style="text-align:right">${idx.price.toFixed(2)}</td>
                        <td style="color:${color}; font-weight:bold; text-align:right">${arrow}${idx.change.toFixed(2)}</td>
                        <td style="color:${color}; font-weight:bold; text-align:right">${arrow}${idx.change_pct.toFixed(2)}%</td>
                    </tr>
                `;
            }).join('');
        }

        // 2. 加载我的实盘汇总
        const resTasks = await fetch('/api/paper/tasks');
        const dataTasks = await resTasks.json();

        let totalAsset = 0;
        let totalProfit = 0;
        let totalInitial = 0;
        const tasksList = document.getElementById('dash-tasks-list');

        if (!dataTasks.tasks || dataTasks.tasks.length === 0) {
            tasksList.innerHTML = '<div style="text-align:center; padding:20px; color:var(--text-muted); font-size:13px;">暂无运行中的任务</div>';
        } else {
            const activeTasks = dataTasks.tasks.filter(t => t.status === 'running');

            dataTasks.tasks.forEach(t => {
                totalAsset += (t.fin_total || 0);
                totalProfit += (t.fin_profit || 0);
                totalInitial += (t.fin_initial || (t.fin_total - t.fin_profit) || 0);
            });

            if (activeTasks.length === 0) {
                tasksList.innerHTML = '<div style="text-align:center; padding:10px; color:var(--text-muted); font-size:13px;">有历史任务，但当前无运行中任务</div>';
            } else {
                tasksList.innerHTML = activeTasks.map(t => {
                    const color = (t.fin_profit||0) >= 0 ? 'var(--red)' : 'var(--green)';
                    const sign = (t.fin_profit||0) >= 0 ? '+' : '';
                    const curInit = t.fin_initial || (t.fin_total - t.fin_profit) || 1;
                    const pct = ((t.fin_profit||0) / curInit * 100).toFixed(2);
                    return `
                        <div style="padding:10px 12px; background:var(--bg-secondary); border-radius:6px; font-size:13px; display:flex; justify-content:space-between; align-items:center;">
                            <div style="display:flex; align-items:flex-start; gap:8px;">
                                <span style="color:var(--green); margin-top:2px;">●</span>
                                <div>
                                    <div style="font-weight:600; margin-bottom:2px;">${t.strategy_name}</div>
                                    <div style="font-size:11px; color:var(--text-muted); padding-right:12px; display:-webkit-box; -webkit-line-clamp:1; -webkit-box-orient:vertical; overflow:hidden;">${t.strategy_desc || '按指定调仓日和资产池进行自动化选股'}</div>
                                </div>
                            </div>
                            <div style="text-align:right;">
                                <div>${formatMoney(t.fin_total||0)}</div>
                                <div style="color:${color}; font-size:12px; font-weight:600">${sign}${formatMoney(t.fin_profit||0)} &nbsp;(${sign}${pct}%)</div>
                            </div>
                        </div>
                    `;
                }).join('');
            }
        }

        document.getElementById('dash-total-asset').textContent = formatMoney(totalAsset);
        const pColor = totalProfit >= 0 ? 'var(--red)' : 'var(--green)';
        const pSign = totalProfit >= 0 ? '+' : '';
        const totalPct = totalInitial > 0 ? (totalProfit / totalInitial * 100).toFixed(2) : '0.00';
        document.getElementById('dash-total-profit').innerHTML = `${pSign}${formatMoney(totalProfit)} <span style="font-size:16px;opacity:0.9;font-weight:500;margin-left:4px">(${pSign}${totalPct}%)</span>`;
        document.getElementById('dash-total-profit').style.color = pColor;

    } catch(e) {
        console.error('加载 Dashboard 失败:', e);
    }
}

// ===== 加载策略（仅填充评测下拉） =====
async function loadStrategies() {
    try {
        const resp = await fetch('/api/strategies');
        const data = await resp.json();
        strategies = data.strategies || [];
        populateStrategySelect(strategies);
    } catch (e) {
        console.error('加载策略失败:', e);
    }
}

function populateStrategySelect(list) {
    const sel = document.getElementById('eval-strategy');
    if (!sel) return; // 防止元素不存在时崩溃
    sel.innerHTML = list.map(s =>
        `<option value="${esc(s.key)}">${esc(s.name)}</option>`
    ).join('');
}

// ===== 策略评测 =====
async function runEvaluate() {
    const key = document.getElementById('eval-strategy').value;
    const symbol = document.getElementById('eval-symbol').value.trim();
    const days = parseInt(document.getElementById('eval-days').value);

    if (!key || !symbol) return;
    showLoading('正在回测并评分...');

    try {
        const resp = await fetch('/api/evaluate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ strategy_key: key, symbol, days }),
        });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || '评测失败');
        }

        const data = await resp.json();
        renderScorecard(data.score);
    } catch (e) {
        document.getElementById('eval-result').innerHTML =
            `<div class="empty-state"><div class="empty-icon">❌</div><p>${esc(e.message)}</p></div>`;
    } finally {
        hideLoading();
    }
}

function renderScorecard(score) {
    const gradeClass = `grade-${score.grade}`;
    const circleColor = getGradeColor(score.grade);

    let dimHtml = score.dimensions.map(d => {
        const pct = Math.min(d.score, 100);
        const color = pct >= 70 ? 'var(--green)' : pct >= 40 ? 'var(--yellow)' : 'var(--red)';
        return `
        <div class="dim-item">
            <div>
                <div class="dim-name">${esc(d.name)}</div>
                <div class="dim-value">${d.raw_value.toFixed(2)} <span style="color:var(--text-muted);font-size:12px">(${d.score.toFixed(0)}分 × ${(d.weight * 100).toFixed(0)}%)</span></div>
            </div>
            <div class="dim-bar"><div class="dim-bar-fill" style="width:${pct}%;background:${color}"></div></div>
        </div>`;
    }).join('');

    document.getElementById('eval-result').innerHTML = `
    <div class="scorecard">
        <div class="score-total">
            <div class="score-circle" style="background:${circleColor}15;border:3px solid ${circleColor}">
                <span class="number" style="color:${circleColor}">${score.total_score.toFixed(1)}</span>
                <span class="label">总分</span>
            </div>
            <span class="grade-label ${gradeClass}">${'★'.repeat(score.grade_stars)}  ${score.grade}</span>
            <div style="font-size:13px;color:var(--text-secondary);margin-top:8px">
                <div>策略: ${esc(score.strategy_name)}</div>
                <div>股票: ${esc(score.symbol)}</div>
                <div>收益: <span style="color:${score.total_return_pct >= 0 ? 'var(--green)' : 'var(--red)'}">${score.total_return_pct >= 0 ? '+' : ''}${score.total_return_pct.toFixed(2)}%</span></div>
                <div>交易: ${score.total_trades} 笔</div>
            </div>
        </div>
        <div>
            <canvas id="radarCanvas" width="320" height="320"></canvas>
            <div class="dimension-list">${dimHtml}</div>
        </div>
    </div>`;

    drawRadar(score.dimensions);
}

// ===== 雷达图 =====
function drawRadar(dimensions) {
    const canvas = document.getElementById('radarCanvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const w = canvas.width, h = canvas.height;
    const cx = w / 2, cy = h / 2;
    const R = 120;
    const n = dimensions.length;

    ctx.clearRect(0, 0, w, h);

    // 网格
    for (let ring = 1; ring <= 5; ring++) {
        const r = R * ring / 5;
        ctx.beginPath();
        for (let i = 0; i <= n; i++) {
            const angle = (Math.PI * 2 * i / n) - Math.PI / 2;
            const x = cx + r * Math.cos(angle);
            const y = cy + r * Math.sin(angle);
            i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
        }
        ctx.closePath();
        ctx.strokeStyle = 'rgba(99, 179, 237, 0.1)';
        ctx.lineWidth = 1;
        ctx.stroke();
    }

    // 轴线
    for (let i = 0; i < n; i++) {
        const angle = (Math.PI * 2 * i / n) - Math.PI / 2;
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(cx + R * Math.cos(angle), cy + R * Math.sin(angle));
        ctx.strokeStyle = 'rgba(99, 179, 237, 0.15)';
        ctx.stroke();
    }

    // 数据多边形
    ctx.beginPath();
    dimensions.forEach((d, i) => {
        const score = Math.min(d.score, 100) / 100;
        const angle = (Math.PI * 2 * i / n) - Math.PI / 2;
        const x = cx + R * score * Math.cos(angle);
        const y = cy + R * score * Math.sin(angle);
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.closePath();
    ctx.fillStyle = 'rgba(99, 179, 237, 0.2)';
    ctx.fill();
    ctx.strokeStyle = 'rgba(99, 179, 237, 0.8)';
    ctx.lineWidth = 2;
    ctx.stroke();

    // 顶点圆点
    dimensions.forEach((d, i) => {
        const score = Math.min(d.score, 100) / 100;
        const angle = (Math.PI * 2 * i / n) - Math.PI / 2;
        const x = cx + R * score * Math.cos(angle);
        const y = cy + R * score * Math.sin(angle);
        ctx.beginPath();
        ctx.arc(x, y, 4, 0, Math.PI * 2);
        ctx.fillStyle = '#63b3ed';
        ctx.fill();
    });

    // 标签
    ctx.fillStyle = '#94a3b8';
    ctx.font = '12px Inter, sans-serif';
    ctx.textAlign = 'center';
    dimensions.forEach((d, i) => {
        const angle = (Math.PI * 2 * i / n) - Math.PI / 2;
        const labelR = R + 24;
        const x = cx + labelR * Math.cos(angle);
        const y = cy + labelR * Math.sin(angle);
        ctx.fillText(d.name, x, y + 4);
    });
}

// ===== 排行榜 =====
async function runLeaderboard() {
    const symbol = document.getElementById('lb-symbol').value.trim();
    const days = parseInt(document.getElementById('lb-days').value);
    if (!symbol) return;
    showLoading('正在运行所有策略回测...');

    try {
        const resp = await fetch('/api/leaderboard', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol, days }),
        });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || '排行榜生成失败');
        }

        const data = await resp.json();
        renderLeaderboard(data.rankings, data.symbol);
    } catch (e) {
        document.getElementById('lb-result').innerHTML =
            `<div class="empty-state"><div class="empty-icon">❌</div><p>${esc(e.message)}</p></div>`;
    } finally {
        hideLoading();
    }
}

function renderLeaderboard(rankings, symbol) {
    if (!rankings.length) {
        document.getElementById('lb-result').innerHTML =
            '<div class="empty-state"><div class="empty-icon">🏆</div><p>暂无排行数据</p></div>';
        return;
    }

    let rows = rankings.map((r, i) => {
        const rank = i + 1;
        const rankClass = rank <= 3 ? `rank-${rank}` : 'rank-n';
        const retColor = r.total_return_pct >= 0 ? 'var(--green)' : 'var(--red)';
        const gradeClass = `grade-${r.grade}`;
        return `<tr>
            <td><span class="rank-badge ${rankClass}">${rank}</span></td>
            <td><strong>${esc(r.strategy_name)}</strong></td>
            <td><span class="badge badge-grade ${gradeClass}">${r.grade}</span></td>
            <td><strong>${r.total_score.toFixed(1)}</strong></td>
            <td style="color:${retColor}">${r.total_return_pct >= 0 ? '+' : ''}${r.total_return_pct.toFixed(2)}%</td>
            <td>${r.annual_return_pct >= 0 ? '+' : ''}${r.annual_return_pct.toFixed(2)}%</td>
            <td>${r.total_trades}</td>
            <td>${r.dimensions.map(d => d.score.toFixed(0)).join(' / ')}</td>
        </tr>`;
    }).join('');

    document.getElementById('lb-result').innerHTML = `
    <table class="leaderboard-table">
        <thead><tr>
            <th>#</th><th>策略</th><th>等级</th><th>总分</th>
            <th>总收益</th><th>年化</th><th>交易数</th><th>各维度评分</th>
        </tr></thead>
        <tbody>${rows}</tbody>
    </table>`;
}

// ===== AI 策略生成 =====
function setSourceType(type) {
    currentSourceType = type;
    document.querySelectorAll('.source-type-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.type === type);
    });

    const area = document.getElementById('source-input-area');
    if (type === 'url') {
        area.innerHTML = `
        <div class="form-group">
            <label>URL 地址</label>
            <input type="url" class="form-input" id="ai-content" placeholder="https://..." style="width:100%">
        </div>`;
    } else {
        area.innerHTML = `
        <div class="form-group">
            <label>决策来源内容</label>
            <textarea class="form-textarea" id="ai-content" placeholder="粘贴交易策略思路、文章内容，或者描述你想要的策略逻辑..."></textarea>
        </div>`;
    }
}

async function fetchAIModels() {
    const apiKey = document.getElementById('ai-key').value.trim();
    const endpoint = document.getElementById('ai-endpoint').value.trim();

    if (!apiKey) { alert('请输入 AI API Key 以获取模型列表'); return; }

    showLoading('正在获取可用模型列表...');
    try {
        const resp = await fetch('/api/models', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ai_api_key: apiKey, ai_endpoint: endpoint || null }),
        });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || '获取模型失败');
        }

        const data = await resp.json();
        const modelSel = document.getElementById('ai-model');
        if (data.models && data.models.length > 0) {
            modelSel.innerHTML = data.models.map(m => `<option value="${esc(m)}">${esc(m)}</option>`).join('');
            alert(`成功获取 ${data.models.length} 个模型`);
        } else {
            alert('未能获取到模型列表，请检查 API Key 和 Endpoint');
        }
    } catch (e) {
        alert('获取模型失败: ' + e.message);
    } finally {
        hideLoading();
    }
}

async function runGenerate() {
    const apiKey = document.getElementById('ai-key').value.trim();
    const endpoint = document.getElementById('ai-endpoint').value.trim();
    const model = document.getElementById('ai-model').value;
    const content = document.getElementById('ai-content').value.trim();

    if (!apiKey) { alert('请输入 AI API Key'); return; }
    if (!content) { alert('请输入决策来源内容'); return; }

    showLoading('AI 正在分析并生成策略代码...');

    try {
        const resp = await fetch('/api/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                source_type: currentSourceType,
                content,
                ai_api_key: apiKey,
                ai_endpoint: endpoint || null,
                model: model || null,
            }),
        });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || '生成失败');
        }

        const data = await resp.json();
        generatedFilename = data.filename;

        document.getElementById('ai-result').innerHTML = `
        <h3 style="margin-top:24px;margin-bottom:8px">📄 生成结果：${esc(data.strategy_name)}</h3>
        <p style="color:var(--text-secondary);font-size:13px;margin-bottom:12px">文件: ${esc(data.filename)} — 请预览代码后确认注册</p>
        <div class="code-preview">${escCode(data.code)}</div>
        <div style="margin-top:16px;display:flex;gap:12px">
            <button class="btn btn-success" onclick="confirmStrategy()">✅ 确认注册</button>
            <button class="btn" style="background:var(--bg-secondary);color:var(--text-secondary);border:1px solid var(--border)" onclick="document.getElementById('ai-result').innerHTML=''">❌ 放弃</button>
        </div>`;
    } catch (e) {
        document.getElementById('ai-result').innerHTML =
            `<div class="empty-state" style="padding:24px"><div class="empty-icon">❌</div><p>${esc(e.message)}</p></div>`;
    } finally {
        hideLoading();
    }
}

async function confirmStrategy() {
    if (!generatedFilename) return;
    showLoading('正在注册策略...');

    try {
        const resp = await fetch('/api/confirm', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filename: generatedFilename }),
        });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || '注册失败');
        }

        document.getElementById('ai-result').innerHTML =
            '<div class="empty-state" style="padding:24px"><div class="empty-icon">🎉</div><p>策略注册成功！刷新策略库可查看。</p></div>';

        // 刷新策略库
        await loadStrategies();
    } catch (e) {
        alert('注册失败: ' + e.message);
    } finally {
        hideLoading();
    }
}

// ===== 工具函数 =====
function showLoading(text) {
    document.getElementById('loading-text').textContent = text || '正在处理...';
    document.getElementById('loading').style.display = 'flex';
}

function hideLoading() {
    document.getElementById('loading').style.display = 'none';
}

function getGradeColor(grade) {
    const map = { '精选': '#48bb78', '合格': '#63b3ed', '观察': '#f6e05e', '淘汰': '#fc8181' };
    return map[grade] || '#64748b';
}

function esc(str) {
    if (!str) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function escCode(code) {
    return esc(code);
}

// ===== V2 组合回测 =====

async function loadPortfolioStrategies() {
    try {
        const resp = await fetch('/api/portfolio/strategies');
        const data = await resp.json();
        const sel = document.getElementById('pf-strategy');
        if (sel && data.strategies) {
            sel.innerHTML = data.strategies.map(s =>
                `<option value="${esc(s.key)}">${esc(s.name)} (${esc(s.description || '')})</option>`
            ).join('');
        }
    } catch (e) {
        console.error('加载组合策略失败:', e);
    }
}

async function runPortfolioBacktest() {
    const indexCode = document.getElementById('pf-index').value;
    const strategyKey = document.getElementById('pf-strategy').value;
    const topN = parseInt(document.getElementById('pf-topn').value);
    const days = parseInt(document.getElementById('pf-days').value);
    const capital = parseFloat(document.getElementById('pf-capital').value) * 10000;

    if (!strategyKey) return;
    showLoading('正在获取股票池并运行组合回测（可能需要1-2分钟）...');

    try {
        const resp = await fetch('/api/portfolio/backtest', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                index_code: indexCode,
                strategy_key: strategyKey,
                days, top_n: topN,
                initial_capital: capital,
            }),
        });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || '组合回测失败');
        }

        const data = await resp.json();
        renderPortfolioResult(data.result, data.score);
    } catch (e) {
        document.getElementById('pf-result').innerHTML =
            `<div class="empty-state"><div class="empty-icon">❌</div><p>${esc(e.message)}</p></div>`;
    } finally {
        hideLoading();
    }
}

function renderPortfolioResult(result, score) {
    const gradeClass = `grade-${score.grade}`;
    const circleColor = getGradeColor(score.grade);

    // 评分维度
    let dimHtml = score.dimensions.map(d => {
        const pct = Math.min(d.score, 100);
        const color = pct >= 70 ? 'var(--green)' : pct >= 40 ? 'var(--yellow)' : 'var(--red)';
        return `
        <div class="dim-item">
            <div>
                <div class="dim-name">${esc(d.name)}</div>
                <div class="dim-value">${d.raw_value.toFixed(2)} <span style="color:var(--text-muted);font-size:12px">(${d.score.toFixed(0)}分 × ${(d.weight * 100).toFixed(0)}%)</span></div>
            </div>
            <div class="dim-bar"><div class="dim-bar-fill" style="width:${pct}%;background:${color}"></div></div>
        </div>`;
    }).join('');

    // 持仓列表
    let holdingsHtml = '';
    if (result.final_holdings && result.final_holdings.length > 0) {
        let rows = result.final_holdings.slice(0, 10).map(h => {
            const pColor = h.pnl_pct >= 0 ? 'var(--green)' : 'var(--red)';
            return `<tr>
                <td>${esc(h.symbol)}</td>
                <td>${esc(h.name)}</td>
                <td>${(h.weight * 100).toFixed(1)}%</td>
                <td style="color:${pColor}">${h.pnl_pct >= 0 ? '+' : ''}${h.pnl_pct.toFixed(1)}%</td>
            </tr>`;
        }).join('');
        holdingsHtml = `
        <h4 style="margin-top:20px;margin-bottom:8px">📋 当前持仓（前10只）</h4>
        <table class="leaderboard-table">
            <thead><tr><th>代码</th><th>名称</th><th>权重</th><th>浮盈</th></tr></thead>
            <tbody>${rows}</tbody>
        </table>`;
    }

    document.getElementById('pf-result').innerHTML = `
    <div class="scorecard" style="margin-top:20px">
        <div class="score-total">
            <div class="score-circle" style="background:${circleColor}15;border:3px solid ${circleColor}">
                <span class="number" style="color:${circleColor}">${score.total_score.toFixed(1)}</span>
                <span class="label">总分</span>
            </div>
            <span class="grade-label ${gradeClass}">${'★'.repeat(score.grade_stars)}  ${score.grade}</span>
            <div style="font-size:13px;color:var(--text-secondary);margin-top:8px">
                <div>策略: ${esc(result.strategy_name)}</div>
                <div>基准: ${esc(result.index_name)}</div>
                <div>收益: <span style="color:${result.total_return_pct >= 0 ? 'var(--green)' : 'var(--red)'}">${result.total_return_pct >= 0 ? '+' : ''}${result.total_return_pct.toFixed(2)}%</span></div>
                <div>年化: ${result.annual_return_pct >= 0 ? '+' : ''}${result.annual_return_pct.toFixed(2)}%</div>
                <div>最大回撤: ${result.max_drawdown_pct.toFixed(2)}%</div>
                <div>夏普: ${result.sharpe_ratio.toFixed(2)}</div>
                <div>调仓: ${result.total_rebalances} 次</div>
                <div>持仓: 平均 ${result.avg_holdings.toFixed(0)} 只</div>
                <div>交易成本: ¥${Number(result.total_trade_cost).toLocaleString()}</div>
            </div>
        </div>
        <div>
            <div class="dimension-list">${dimHtml}</div>
        </div>
    </div>
    ${holdingsHtml}`;

    // 绘制净值曲线
    if (result.nav_series && result.nav_series.length > 0) {
        drawNavChart(result.nav_series, result.strategy_name);
    }
}

function drawNavChart(navSeries, title) {
    const canvas = document.getElementById('pf-nav-chart');
    if (!canvas) return;

    canvas.style.display = 'block';
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;

    // 设置 canvas 实际尺寸
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = 300 * dpr;
    canvas.style.height = '300px';
    ctx.scale(dpr, dpr);

    const w = rect.width;
    const h = 300;
    const pad = { top: 30, right: 20, bottom: 40, left: 60 };
    const plotW = w - pad.left - pad.right;
    const plotH = h - pad.top - pad.bottom;

    ctx.clearRect(0, 0, w, h);

    // 数据
    const navs = navSeries.map(p => p.nav);
    const dates = navSeries.map(p => p.date);
    const minNav = Math.min(...navs) * 0.99;
    const maxNav = Math.max(...navs) * 1.01;
    const navRange = maxNav - minNav || 0.01;

    // 背景
    ctx.fillStyle = '#0f172a';
    ctx.fillRect(0, 0, w, h);

    // 网格线
    ctx.strokeStyle = 'rgba(99, 179, 237, 0.08)';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 5; i++) {
        const y = pad.top + plotH * i / 5;
        ctx.beginPath();
        ctx.moveTo(pad.left, y);
        ctx.lineTo(w - pad.right, y);
        ctx.stroke();
    }

    // Y 轴标签
    ctx.fillStyle = '#64748b';
    ctx.font = '11px Inter, sans-serif';
    ctx.textAlign = 'right';
    for (let i = 0; i <= 5; i++) {
        const val = maxNav - navRange * i / 5;
        const y = pad.top + plotH * i / 5;
        ctx.fillText(val.toFixed(3), pad.left - 8, y + 4);
    }

    // 基准线 nav=1.0
    if (minNav <= 1.0 && maxNav >= 1.0) {
        const baseY = pad.top + plotH * (maxNav - 1.0) / navRange;
        ctx.strokeStyle = 'rgba(248, 113, 113, 0.4)';
        ctx.setLineDash([6, 4]);
        ctx.beginPath();
        ctx.moveTo(pad.left, baseY);
        ctx.lineTo(w - pad.right, baseY);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = '#f87171';
        ctx.textAlign = 'left';
        ctx.fillText('1.000', w - pad.right + 4, baseY + 4);
    }

    // 净值曲线
    ctx.beginPath();
    navs.forEach((nav, i) => {
        const x = pad.left + plotW * i / (navs.length - 1 || 1);
        const y = pad.top + plotH * (maxNav - nav) / navRange;
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.strokeStyle = '#63b3ed';
    ctx.lineWidth = 2;
    ctx.stroke();

    // 渐变填充
    const grd = ctx.createLinearGradient(0, pad.top, 0, h - pad.bottom);
    grd.addColorStop(0, 'rgba(99, 179, 237, 0.25)');
    grd.addColorStop(1, 'rgba(99, 179, 237, 0.02)');

    ctx.lineTo(pad.left + plotW, h - pad.bottom);
    ctx.lineTo(pad.left, h - pad.bottom);
    ctx.closePath();
    ctx.fillStyle = grd;
    ctx.fill();

    // X 轴日期标签
    ctx.fillStyle = '#64748b';
    ctx.font = '10px Inter, sans-serif';
    ctx.textAlign = 'center';
    const labelCount = Math.min(6, dates.length);
    for (let i = 0; i < labelCount; i++) {
        const idx = Math.floor(i * (dates.length - 1) / (labelCount - 1 || 1));
        const x = pad.left + plotW * idx / (dates.length - 1 || 1);
        ctx.fillText(dates[idx], x, h - pad.bottom + 18);
    }

    // 标题
    ctx.fillStyle = '#e2e8f0';
    ctx.font = 'bold 13px Inter, sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText(`📈 ${title} — 净值曲线`, pad.left, 20);

    // 终值标注
    const lastNav = navs[navs.length - 1];
    const retPct = ((lastNav - 1) * 100).toFixed(2);
    ctx.fillStyle = lastNav >= 1 ? '#48bb78' : '#f56565';
    ctx.font = 'bold 12px Inter, sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText(`终值 ${lastNav.toFixed(4)} (${lastNav >= 1 ? '+' : ''}${retPct}%)`, w - pad.right, 20);
}

async function runPortfolioLeaderboard() {
    const indexCode = document.getElementById('pf-index').value;
    const topN = parseInt(document.getElementById('pf-topn').value);
    const days = parseInt(document.getElementById('pf-days').value);
    const capital = parseFloat(document.getElementById('pf-capital').value) * 10000;

    showLoading('正在运行所有组合策略回测（可能需要几分钟）...');

    try {
        const resp = await fetch('/api/portfolio/leaderboard', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                index_code: indexCode, days, top_n: topN,
                initial_capital: capital,
            }),
        });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || '排行榜失败');
        }

        const data = await resp.json();
        renderPortfolioLeaderboard(data.rankings, data.index);
    } catch (e) {
        document.getElementById('pf-result').innerHTML =
            `<div class="empty-state"><div class="empty-icon">❌</div><p>${esc(e.message)}</p></div>`;
    } finally {
        hideLoading();
    }
}

function renderPortfolioLeaderboard(rankings, indexCode) {
    if (!rankings || !rankings.length) {
        document.getElementById('pf-result').innerHTML =
            '<div class="empty-state"><div class="empty-icon">🏆</div><p>暂无排行数据</p></div>';
        return;
    }

    let rows = rankings.map((r, i) => {
        const rank = i + 1;
        const rankClass = rank <= 3 ? `rank-${rank}` : 'rank-n';
        const retColor = r.total_return_pct >= 0 ? 'var(--green)' : 'var(--red)';
        const gradeClass = `grade-${r.grade}`;
        return `<tr>
            <td><span class="rank-badge ${rankClass}">${rank}</span></td>
            <td><strong>${esc(r.strategy_name)}</strong></td>
            <td><span class="badge badge-grade ${gradeClass}">${r.grade}</span></td>
            <td><strong>${r.total_score.toFixed(1)}</strong></td>
            <td style="color:${retColor}">${r.total_return_pct >= 0 ? '+' : ''}${r.total_return_pct.toFixed(2)}%</td>
            <td>${r.annual_return_pct >= 0 ? '+' : ''}${r.annual_return_pct.toFixed(2)}%</td>
            <td>${r.max_drawdown_pct.toFixed(2)}%</td>
            <td>${r.sharpe_ratio.toFixed(2)}</td>
        </tr>`;
    }).join('');

    document.getElementById('pf-result').innerHTML = `
    <h4 style="margin:20px 0 12px">🏆 组合策略排行榜</h4>
    <table class="leaderboard-table">
        <thead><tr>
            <th>#</th><th>策略</th><th>等级</th><th>总分</th>
            <th>总收益</th><th>年化</th><th>最大回撤</th><th>夏普</th>
        </tr></thead>
        <tbody>${rows}</tbody>
    </table>`;

    // 隐藏净值图
    const canvas = document.getElementById('pf-nav-chart');
    if (canvas) canvas.style.display = 'none';
}

// ===== 组合策略指南功能 =====

// 加载组合策略卡片
async function loadPortfolioStrategyCards() {
    const container = document.getElementById('portfolio-strategy-cards');
    if (!container) return;

    try {
        const resp = await fetch('/api/portfolio/strategies');
        if (!resp.ok) throw new Error('获取策略列表失败');

        const data = await resp.json();
        const strategies = data.strategies || [];

        if (strategies.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon">📦</div>
                    <p>暂无组合策略</p>
                </div>
            `;
            return;
        }

        // 策略图标映射
        const strategyIcons = {
            'low_pe_rotation': '📊',
            'small_cap': '🎯',
            'momentum_factor': '📈',
            'reversal': '🔄',
            'value_momentum': '💎',
            'low_turnover': '🐢',
            'friction_risk_filter': '🛡️',
            'momentum_breakout': '🦅',
        };

        // 策略描述映射
        const strategyDescs = {
            'low_pe_rotation': '动态市盈率被低估时买入 (PE<15)，估值修复时卖出 (PE>25)，适合价值投资者。',
            'small_cap': '选择市值最小的 20-30 只股票，每月调仓，享受小市值溢价，适合追求高弹性。',
            'momentum_factor': '按 20 日/60 日动量打分，选前 15 只强势股，趋势跟踪，牛市表现优异。',
            'reversal': '超跌反弹策略，买入近期跌幅最大的股票，博取均值回归收益。',
            'value_momentum': '结合低估值和高动量，选既有安全边际又有上涨动力的股票。',
            'low_turnover': '低换手率策略，选择交易不活跃的股票，避免拥挤交易。',
            'friction_risk_filter': '全天候一票否决前置风控。自动隔离高波发套和无流动性的崩盘股。',
            'momentum_breakout': '捕获缩量酝酿后，首日带量突破前期历史箱体的新高牛股。',
        };

        // 策略标签映射
        const strategyTags = {
            'low_pe_rotation': ['价值投资', '长线', '稳健'],
            'small_cap': ['高弹性', '成长', '波动大'],
            'momentum_factor': ['趋势跟踪', '牛市', '动量'],
            'reversal': ['超跌反弹', '短线', '均值回归'],
            'value_momentum': ['价值 + 动量', '平衡', '稳健增长'],
            'low_turnover': ['低换手', '冷门股', '差异化'],
            'friction_risk_filter': ['风险隔离', '极压黑名单', '对冲利器'],
            'momentum_breakout': ['高赔率', '日内追板', '硬核止损'],
        };

        let html = '';
        strategies.forEach(s => {
            const key = s.key || s.name;
            const icon = strategyIcons[key] || '📈';
            const desc = strategyDescs[key] || s.description || '组合策略';
            const tags = strategyTags[key] || ['组合策略'];

            html += `
                <div class="strategy-card">
                    <div class="strategy-card-header">
                        <div class="strategy-card-icon">${icon}</div>
                        <div class="strategy-card-title">${s.name || key}</div>
                    </div>
                    <div class="strategy-card-desc">${desc}</div>
                    <div class="strategy-card-meta">
                        ${tags.map(t => `<span class="strategy-tag">${t}</span>`).join('')}
                    </div>
                </div>
            `;
        });

        container.innerHTML = html;
    } catch (e) {
        console.error('加载组合策略卡片失败:', e);
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-icon">❌</div>
                <p>加载失败：${e.message}</p>
            </div>
        `;
    }
}

// 页面加载时初始化
document.addEventListener('DOMContentLoaded', function() {
    // 监听 Tab 切换
    const portfolioGuideTab = document.getElementById('tab-btn-portfolio-guide');
    if (portfolioGuideTab) {
        portfolioGuideTab.addEventListener('click', function() {
            loadPortfolioStrategyCards();
        });
    }
});

// ===== 组合策略列表功能 =====

let currentStrategy = null;

// 加载策略列表
async function loadStrategyList(filter = 'all') {
    const container = document.getElementById('strategy-list');
    const countEl = document.getElementById('strategy-count');
    if (!container) return;

    try {
        const resp = await fetch('/api/portfolio/strategies');
        if (!resp.ok) throw new Error('获取策略列表失败');

        const data = await resp.json();
        let strategies = data.strategies || [];

        // 过滤
        if (filter !== 'all') {
            const categoryMap = {
                'value': ['low_pe_rotation', 'value_momentum', 'low_turnover'],
                'growth': ['small_cap', 'momentum_factor'],
                'momentum': ['momentum_factor', 'reversal', 'momentum_breakout'],
            };
            strategies = strategies.filter(s => {
                const key = s.key || s.name;
                return categoryMap[filter]?.includes(key);
            });
        }

        countEl.textContent = `${strategies.length} 个策略`;

        if (strategies.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon">📦</div>
                    <p>暂无策略</p>
                </div>
            `;
            return;
        }

        let html = '';
        strategies.forEach((s, index) => {
            const key = s.key || s.name;
            const rank = index + 1;
            const rankClass = rank <= 3 ? `rank-${rank}` : 'rank-other';

            html += `
                <div class="strategy-item" data-key="${key}" onclick="selectStrategy('${key}', ${rank})">
                    <div class="strategy-rank rank-${rankClass}">${rank}</div>
                    <div class="strategy-info">
                        <div class="strategy-name">${s.name || key}</div>
                        <div class="strategy-brief">${s.description || '组合策略'}</div>
                    </div>
                </div>
            `;
        });

        container.innerHTML = html;
    } catch (e) {
        console.error('加载策略列表失败:', e);
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-icon">❌</div>
                <p>加载失败：${e.message}</p>
            </div>
        `;
    }
}

// 选择策略
async function selectStrategy(key, rank = 1) {
    // 更新选中状态
    document.querySelectorAll('.strategy-item').forEach(item => {
        item.classList.toggle('active', item.dataset.key === key);
    });

    // 显示详情
    const placeholder = document.getElementById('detail-placeholder');
    const content = document.getElementById('detail-content');

    if (placeholder) placeholder.style.display = 'none';
    if (content) content.style.display = 'block';

    // 每次切换策略时，重置回 "概览" Tab
    document.querySelectorAll('.detail-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.detail-tab-content').forEach(c => c.classList.remove('active'));
    const overviewTab = document.querySelector('.detail-tab[data-tab="overview"]');
    if (overviewTab) overviewTab.classList.add('active');
    const tabOverviewContent = document.getElementById('tab-overview');
    if (tabOverviewContent) tabOverviewContent.classList.add('active');

    // 加载详情
    await loadStrategyDetail(key, rank);
}

// 加载策略详情
async function loadStrategyDetail(key, rank = 1) {
    const strategyData = {
        'low_pe_rotation': {
            name: '低 PE 轮动',
            category: '价值',
            brief: '动态市盈率被低估时买入，估值修复时卖出',
            frequency: '月度',
            holdings: '20-30 只',
            capital: '50 万+',
            risk: '⭐⭐ 中低',
            logic: [
                '筛选全市场股票，剔除 ST、*ST、停牌股票',
                '计算 PE、ROE、ROA 等基本面指标',
                '选择 PE < 15 且 ROE > 10% 的股票',
                '按 PE 升序排序，选前 20-30 只',
                '等权重配置，每月调仓'
            ],
            params: [
                {name: 'PE 阈值', default: '< 15', desc: '市盈率上限', range: '10-30'},
                {name: 'ROE 阈值', default: '> 10%', desc: '净资产收益率下限', range: '5%-20%'},
                {name: 'ROA 阈值', default: '> 5%', desc: '总资产收益率下限', range: '3%-15%'},
                {name: '持仓数量', default: '20 只', desc: '同时持有的股票数量', range: '10-50 只'},
                {name: '调仓周期', default: '20 交易日', desc: '多久调仓一次', range: '5-60 交易日'}
            ]
        },
        'small_cap': {
            name: '小市值因子',
            category: '成长',
            brief: '选择市值最小的 20-30 只股票，每月调仓',
            frequency: '月度',
            holdings: '20-30 只',
            capital: '50 万+',
            risk: '⭐⭐⭐⭐ 中高',
            logic: [
                '筛选全市场股票，剔除 ST、*ST、科创板',
                '计算总市值、流通市值',
                '选择市值 < 50 亿的股票',
                '按市值升序排序，选前 20-30 只',
                '等权重配置，每月调仓'
            ],
            params: [
                {name: '市值上限', default: '< 50 亿', desc: '总市值上限', range: '30-100 亿'},
                {name: '持仓数量', default: '20 只', desc: '同时持有的股票数量', range: '10-50 只'},
                {name: '调仓周期', default: '20 交易日', desc: '多久调仓一次', range: '5-60 交易日'}
            ]
        },
        'momentum_factor': {
            name: '动量因子',
            category: '动量',
            brief: '按 20 日/60 日动量打分，选前 15 只强势股',
            frequency: '双周',
            holdings: '15-20 只',
            capital: '50 万+',
            risk: '⭐⭐⭐⭐ 中高',
            logic: [
                '筛选全市场股票，剔除 ST、停牌',
                '计算 20 日、60 日、120 日涨幅',
                '按 40%/40%/20% 权重计算综合动量得分',
                '选择得分前 15-20 只股票',
                '等权重配置，每 2 周调仓'
            ],
            params: [
                {name: '持仓数量', default: '15 只', desc: '同时持有的股票数量', range: '10-30 只'},
                {name: '20 日权重', default: '40%', desc: '短期动量权重', range: '20%-60%'},
                {name: '60 日权重', default: '40%', desc: '中期动量权重', range: '20%-60%'},
                {name: '止损线', default: '-15%', desc: '单只股票最大亏损', range: '-10%~-20%'}
            ]
        },
        'reversal': {
            name: '超跌反弹',
            category: '动量',
            brief: '买入近期跌幅最大的股票，博取均值回归收益',
            frequency: '周度/月度',
            holdings: '10-20 只',
            capital: '50 万+',
            risk: '⭐⭐⭐⭐ 中高',
            logic: [
                '筛选全市场股票，剔除 ST、停牌',
                '计算过去 20 日真实跌幅，规避长牛见顶股票',
                '选取得分最低（跌幅最大）的前 10-20 只股票',
                '等权重配置，定期调仓'
            ],
            params: [
                {name: '持仓数量', default: '20 只', desc: '同时持有的股票数量', range: '10-30 只'},
                {name: '跌幅周期', default: '20 日', desc: '计算跌幅的周期', range: '5-60 日'}
            ]
        },
        'value_momentum': {
            name: '价值动量',
            category: '混合',
            brief: '结合低估值和高动量，选既有安全边际又有上涨动力的股票',
            frequency: '月度',
            holdings: '20 只',
            capital: '100 万+',
            risk: '⭐⭐⭐ 中等',
            logic: [
                '筛选全市场股票，剔除 ST、停牌',
                '分别计算 PE/PB 估值因子得分和 20/60日 动量因子得分',
                '等权或者按比例合并估值与动量得分',
                '选择综合得分最高的前 20 只股票',
                '等权重配置，每月调仓'
            ],
            params: [
                {name: '持仓数量', default: '20 只', desc: '同时持有的股票数量', range: '10-50 只'},
                {name: '估值总权重', default: '50%', desc: '价值因子权重', range: '30%-70%'},
                {name: '动量总权重', default: '50%', desc: '动量因子权重', range: '30%-70%'}
            ]
        },
        'low_turnover': {
            name: '低换手冷门股',
            category: '价值',
            brief: '低换手率策略，选择交易不活跃的股票，避免拥挤交易',
            frequency: '月度',
            holdings: '20-30 只',
            capital: '50 万+',
            risk: '⭐⭐ 中低',
            logic: [
                '筛选全市场股票，剔除 ST、近半年翻倍妖股等',
                '计算过去 1 个月及 3 个月的日均换手率',
                '选择换手率最低的前 20-30 只股票',
                '等权重配置，每月调仓'
            ],
            params: [
                {name: '持仓数量', default: '30 只', desc: '同时持有的股票数量', range: '10-50 只'},
                {name: '换手率要求', default: '< 1%', desc: '股票日均换手率上限', range: '< 0.5% ~ < 2%'}
            ]
        },
        'four_stirrers': {
            name: '四大搅屎棍',
            category: '动量',
            brief: '避险防御策略。一旦四大防守板块领涨则空仓，否则买小盘绩优',
            frequency: '周度/月度',
            holdings: '10-20 只',
            capital: '50 万+',
            risk: '⭐⭐ 中低',
            logic: [
                '监控「银行、煤炭、钢铁、有色」四大防守板块指数乖离率',
                '若四大代表板块位居全市场领涨排头兵，触发清仓避险动作',
                '若处于安全成长期，从中小盘中挑选 PB/PE > 0.15 的高 ROE 个股',
                '按市值从小到大排序等权买入'
            ],
            params: [
                {name: '避险敏感度', default: 'Top 2', desc: '防守板块进入前几名触发避险', range: 'Top 1-3'},
                {name: '持仓数量', default: '10 只', desc: '正常安全期持有的股票数量', range: '5-20 只'}
            ]
        },
        'friction_risk_filter': {
            name: '高频摩擦风控',
            category: '风控',
            brief: '一票否决垫底的微盘劣质股和高位换手的游资盘',
            frequency: '日度拦截',
            holdings: '灵活组配',
            capital: '随意',
            risk: '✅ 极低',
            logic: [
                '获取全市场的成交摩擦与波动指标',
                '计算 Amihud 极压流动性损失（过滤垫底 10%）',
                '计算 Turnover Vol 换手剧烈程度（过滤垫底 10%）',
                '计算 Idiosyncratic 特质波动（过滤垫底 10%）',
                '只要撞线立即拉入强风控黑名单'
            ],
            params: [
                {name: '拉黑阈值', default: '10%', desc: '抛弃全市场最差的首尾比例', range: '5%-20%'}
            ]
        },
        'momentum_breakout': {
            name: '首日动能突破',
            category: '动量',
            brief: '盘中每分钟侦测全 A 股，捕获沉寂后第一日爆发突破 20 日新高的大盘标的。',
            frequency: '日内微秒级',
            holdings: '约 5 只',
            capital: '灵活分配',
            risk: '⭐⭐⭐⭐ 激进',
            logic: [
                '①【门槛】排除市值<20亿或>500亿的标的，剔除ST和新股。',
                '②【首日起爆验证】昨日的收盘价不能是 20 天的新高，昨天的成交量不能超过均值两倍。必须是今天平地起爆。',
                '③【量价齐升碰撞】今收盘价必须突破过去 20 天最高点，并且当日成交量放大达到均量 3 倍以上，但又不能过热超过 5 倍。',
                '④【严格止损机制】一旦跌破买入当日的「昨收价」，视作假突破当场无条件斩仓止损。',
                '⑤【移动追踪止盈】盈利期间记录最高股价，当该股从最高点回撤 8.0% 即刻触发落袋为安。',
                '⑥【发呆出局机制】持有超过 2 日，但总体收益率在 2% 以下原地踏步，剥夺持仓资格让位给新突破点。',
                '🛑【封板豁免特权】如果判定该股涨幅逼近涨停板(主板10%/创业板20%...)，今日锁定不触发任何卖出指令。'
            ],
            params: [
                {name: '爆量起步边界', default: '> 3.0倍', desc: '成交量/过去20天均量', range: '>2倍 - 4倍'},
                {name: '天量诱多封顶', default: '< 5.0倍', desc: '阻止高位套牢', range: '<4倍 - 7倍'},
                {name: '移动止盈回撤点', default: '8.0%', desc: '跌出这个范围果断锁定利润', range: '5% - 15%'},
                {name: '破位止损线', default: '昨收价', desc: '绝不允许跌穿发动行情的基石（硬止损）', range: '无需配置'},
            ]
        }
    };

    const data = strategyData[key];
    if (!data) return;

    currentStrategy = {key, ...data};

    // 更新详情
    document.getElementById('detail-rank').textContent = rank;
    document.getElementById('detail-name').textContent = data.name;
    document.getElementById('detail-category').textContent = data.category;
    document.getElementById('overview-frequency').textContent = data.frequency;
    document.getElementById('overview-holdings').textContent = data.holdings;
    document.getElementById('overview-capital').textContent = data.capital;
    document.getElementById('overview-risk').textContent = data.risk;
    document.getElementById('overview-desc').textContent = data.brief;

    // 更新逻辑步骤
    const logicContainer = document.getElementById('logic-steps');
    if (logicContainer) {
        let html = '';
        data.logic.forEach((step, i) => {
            html += `
                <div class="logic-step">
                    <span class="step-num">${i + 1}</span>
                    <div class="step-content">
                        <p>${step}</p>
                    </div>
                </div>
            `;
        });
        logicContainer.innerHTML = html;
    }

    // 更新参数表格
    const paramsContainer = document.getElementById('params-tbody');
    if (paramsContainer) {
        let html = '';
        data.params.forEach(p => {
            html += `
                <tr>
                    <td>${p.name}</td>
                    <td>${p.default}</td>
                    <td>${p.desc}</td>
                    <td>${p.range}</td>
                </tr>
            `;
        });
        paramsContainer.innerHTML = html;
    }
}

// 运行回测
function runBacktestFromDetail() {
    if (!currentStrategy) return;

    // 切换到回测 Tab
    document.getElementById('tab-btn-portfolio-list').click();

    // 填充参数
    setTimeout(() => {
        const strategySelect = document.getElementById('pf-strategy');
        if (strategySelect) {
            strategySelect.value = currentStrategy.key;
        }
        document.getElementById('pf-run').click();
    }, 300);
}

// 术语弹窗
function showTermModal(term) {
    const terms = {
        'PE': {
            title: 'PE（市盈率）',
            content: `
                <p><strong>市盈率（Price-to-Earnings Ratio）</strong></p>
                <p>计算公式：PE = 股价 ÷ 每股收益</p>
                <p>含义：投资者愿意为每 1 元净利润支付的价格</p>
                <div class="term-examples">
                    <h4>示例：</h4>
                    <ul>
                        <li>PE = 10：回本需要 10 年</li>
                        <li>PE = 20：回本需要 20 年</li>
                    </ul>
                </div>
                <div class="term-tips">
                    <h4>如何使用：</h4>
                    <ul>
                        <li>PE 越低，估值越便宜</li>
                        <li>PE 越高，增长预期越高</li>
                        <li>不同行业 PE 差异大，需横向对比</li>
                    </ul>
                </div>
            `
        },
        'ROE': {
            title: 'ROE（净资产收益率）',
            content: `
                <p><strong>净资产收益率（Return on Equity）</strong></p>
                <p>计算公式：ROE = 净利润 ÷ 净资产</p>
                <p>含义：公司用股东的钱赚钱的能力</p>
                <div class="term-examples">
                    <h4>示例：</h4>
                    <ul>
                        <li>ROE = 15%：每 100 元净资产赚 15 元</li>
                        <li>ROE = 30%：每 100 元净资产赚 30 元</li>
                    </ul>
                </div>
                <div class="term-tips">
                    <h4>如何使用：</h4>
                    <ul>
                        <li>ROE 越高，盈利能力越强</li>
                        <li>连续 5 年 ROE>15% 为优秀公司</li>
                        <li>警惕 ROE 突然大幅波动</li>
                    </ul>
                </div>
            `
        },
        'ROA': {
            title: 'ROA（总资产收益率）',
            content: `
                <p><strong>总资产收益率（Return on Assets）</strong></p>
                <p>计算公式：ROA = 净利润 ÷ 总资产</p>
                <p>含义：公司用全部资产赚钱的能力</p>
                <div class="term-examples">
                    <h4>示例：</h4>
                    <ul>
                        <li>ROA = 5%：每 100 元总资产赚 5 元</li>
                        <li>ROA = 10%：每 100 元总资产赚 10 元</li>
                    </ul>
                </div>
                <div class="term-tips">
                    <h4>如何使用：</h4>
                    <ul>
                        <li>ROA 越高，资产利用效率越高</li>
                        <li>ROA>5% 为良好，>10% 为优秀</li>
                        <li>重资产行业 ROA 普遍较低</li>
                    </ul>
                </div>
            `
        }
    };

    const termData = terms[term];
    if (!termData) return;

    document.getElementById('term-title').textContent = termData.title;
    document.getElementById('term-content').innerHTML = termData.content;
    document.getElementById('term-modal').classList.add('active');
}

function hideTermModal(event) {
    if (!event || event.target.id === 'term-modal') {
        document.getElementById('term-modal').classList.remove('active');
    }
}

// 切换详情 Tab
document.addEventListener('DOMContentLoaded', function() {
    // 策略列表过滤
    document.querySelectorAll('.filter-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
            this.classList.add('active');
            loadStrategyList(this.dataset.filter);
        });
    });

    // 详情 Tab 切换
    document.querySelectorAll('.detail-tab').forEach(tab => {
        tab.addEventListener('click', function() {
            const tabName = this.dataset.tab;
            document.querySelectorAll('.detail-tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.detail-tab-content').forEach(c => c.classList.remove('active'));

            this.classList.add('active');
            document.getElementById('tab-' + tabName).classList.add('active');
        });
    });

    // 加载策略列表
    loadStrategyList();
});

// ===== 模拟交易功能 =====

// 运行模拟交易
async function runPaperTrading() {
    if (!currentStrategy) {
        alert('请先选择一个策略');
        return;
    }

    // 获取参数
    const days = parseInt(document.getElementById('sim-days')?.value || 60);
    const capital = parseFloat(document.getElementById('sim-capital')?.value || 100000);
    const rebalanceFreq = document.getElementById('sim-frequency')?.value || 'monthly';

    showLoading(`正在运行模拟交易（可能需要 1-2 分钟）...`);

    try {
        const resp = await fetch('/api/portfolio/simulate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                strategy_key: currentStrategy.key,
                days: days,
                initial_capital: capital,
                rebalance_freq: rebalanceFreq,
            }),
        });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || '模拟交易失败');
        }

        const data = await resp.json();
        renderSimulationResult(data.result);
    } catch (e) {
        console.error('模拟交易失败:', e);
        alert(`模拟交易失败：${e.message}`);
    }
}

// 渲染模拟结果
function renderSimulationResult(result) {
    const container = document.getElementById('sim-result');
    if (!container) return;

    let html = `
        <div class="simulation-result">
            <h3>📊 模拟交易结果</h3>

            <div class="result-summary">
                <div class="summary-card">
                    <div class="summary-label">初始资金</div>
                    <div class="summary-value">¥${(result.initial_capital / 10000).toFixed(0)}万</div>
                </div>
                <div class="summary-card">
                    <div class="summary-label">最终资产</div>
                    <div class="summary-value">¥${(result.final_value / 10000).toFixed(0)}万</div>
                </div>
                <div class="summary-card">
                    <div class="summary-label">总盈亏</div>
                    <div class="summary-value ${result.total_profit >= 0 ? 'positive' : 'negative'}">
                        ¥${result.total_profit.toLocaleString(undefined, {maximumFractionDigits: 0})}
                    </div>
                </div>
                <div class="summary-card">
                    <div class="summary-label">总收益率</div>
                    <div class="summary-value ${result.total_profit_pct >= 0 ? 'positive' : 'negative'}">
                        ${result.total_profit_pct.toFixed(2)}%
                    </div>
                </div>
            </div>

            ${result.positions && result.positions.length > 0 ? `
                <h4>📈 当前持仓</h4>
                <table class="position-table">
                    <thead>
                        <tr>
                            <th>代码</th>
                            <th>名称</th>
                            <th>股数</th>
                            <th>成本</th>
                            <th>现价</th>
                            <th>盈亏</th>
                            <th>盈亏%</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${result.positions.map(p => `
                            <tr>
                                <td>${p.code}</td>
                                <td>${p.name}</td>
                                <td>${p.shares.toLocaleString()}</td>
                                <td>¥${p.cost_price.toFixed(2)}</td>
                                <td>¥${p.current_price.toFixed(2)}</td>
                                <td class="${p.profit >= 0 ? 'positive' : 'negative'}">
                                    ¥${p.profit.toLocaleString(undefined, {maximumFractionDigits: 0})}
                                </td>
                                <td class="${p.profit_pct >= 0 ? 'positive' : 'negative'}">
                                    ${p.profit_pct.toFixed(2)}%
                                </td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            ` : ''}

            ${result.trades && result.trades.length > 0 ? `
                <h4>📝 交易记录</h4>
                <table class="trade-table">
                    <thead>
                        <tr>
                            <th>时间</th>
                            <th>操作</th>
                            <th>代码</th>
                            <th>股数</th>
                            <th>价格</th>
                            <th>金额</th>
                            <th>原因</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${result.trades.map(t => `
                            <tr>
                                <td>${t.time}</td>
                                <td class="${t.action === 'BUY' ? 'positive' : 'negative'}">${t.action}</td>
                                <td>${t.code}</td>
                                <td>${t.shares.toLocaleString()}</td>
                                <td>¥${t.price.toFixed(2)}</td>
                                <td>¥${t.amount.toLocaleString(undefined, {maximumFractionDigits: 0})}</td>
                                <td>${t.reason}</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            ` : ''}
        </div>
    `;

    container.innerHTML = html;
}

// ===== 数据管理与后台采集逻辑 =====
let crawlerPoller = null;

function pollCrawlerStatus() {
    fetch('/api/data/status')
        .then(r => r.json())
        .then(data => {
            if(!data.stats) return;

            document.getElementById('dm-total-symbols').innerText = data.stats.total_symbols + ' 只';
            document.getElementById('dm-total-rows').innerText = (data.stats.total_rows / 10000).toFixed(2) + ' 万条';

            const crawler = data.crawler;
            const progressContainer = document.getElementById('dm-progress-container');
            const btnStart = document.getElementById('dm-btn-daily');
            const btnStop = document.getElementById('dm-btn-stop');

            if (crawler.is_running || crawler.progress > 0) {
                progressContainer.style.display = 'block';
                document.getElementById('dm-status-text').innerText = crawler.message;

                const percent = crawler.total > 0 ? ((crawler.progress / crawler.total) * 100).toFixed(1) : 0;
                document.getElementById('dm-progress-text').innerText = `${crawler.progress}/${crawler.total} (${percent}%)`;
                document.getElementById('dm-progress-bar').style.width = `${percent}%`;

                document.getElementById('dm-stats-success').innerText = crawler.success;
                document.getElementById('dm-stats-skipped').innerText = crawler.skipped;
                document.getElementById('dm-stats-failed').innerText = crawler.failed;
            }

            const btnPrehot = document.getElementById('dm-btn-prehot');
            if (btnPrehot) {
                btnPrehot.style.display = crawler.is_running ? 'none' : 'inline-flex';
            }
            if (crawler.is_running) {
                btnStart.style.display = 'none';
                btnStop.style.display = 'inline-flex';
            } else {
                btnStart.style.display = 'inline-flex';
                btnStop.style.display = 'none';
            }
        })
        .catch(err => console.error('[DM] 轮询状态异常', err));
}

function startCrawler120() {
    document.getElementById('dm-days').value = 120;
    startCrawler();
}

function startCrawlerDaily() {
    document.getElementById('dm-days').value = 5;
    startCrawler();
}

function startCrawler() {
    const pool = document.getElementById('dm-pool').value;
    const days = parseInt(document.getElementById('dm-days').value) || 3650;

    document.getElementById('dm-btn-daily').innerText = '启动中...';
    document.getElementById('dm-btn-daily').disabled = true;

    fetch('/api/data/start', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ pool_name: pool, days: days })
    }).then(r => r.json()).then(data => {
        if(data.detail) {
            alert('启动失败：' + data.detail);
            document.getElementById('dm-btn-daily').innerText = '🔄 手动同步最新 K 线数据';
            document.getElementById('dm-btn-daily').disabled = false;
        } else {
            document.getElementById('dm-status-text').innerText = '启动成功，连线中...';
            pollCrawlerStatus();
        }
    }).catch(err => {
        alert('控制请求发送失败，请检查网络');
        document.getElementById('dm-btn-daily').innerText = '🔄 手动同步最新 K 线数据';
        document.getElementById('dm-btn-daily').disabled = false;
    });
}

function stopCrawler() {
    document.getElementById('dm-btn-stop').innerText = '🛑 等待当前循环结束...';
    document.getElementById('dm-btn-stop').disabled = true;

    fetch('/api/data/stop', { method: 'POST' })
        .then(r => r.json())
        .then(() => {
            setTimeout(() => {
                document.getElementById('dm-btn-stop').innerText = '🛑 停止请求循环';
                document.getElementById('dm-btn-stop').disabled = false;
            }, 3000);
        });
}

// 自动挂载状态拉取，并开启长轮询
document.addEventListener('DOMContentLoaded', () => {
    pollCrawlerStatus();
    setInterval(pollCrawlerStatus, 2000);
});

// ===== 模拟实盘交易逻辑 =====
let currentPaperAccount = 'local_1';  // 默认本地账户

function formatMoney(val) {
    return '¥' + parseFloat(val).toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
}

function getMarketPrefix(symbol) {
    // 6开头（含688科创板）= 上海, 0/3开头 = 深圳, 4/8开头(非688) = 北交所
    if (symbol.startsWith('6')) return 'SH';
    if (symbol.startsWith('0') || symbol.startsWith('3')) return 'SZ';
    if (symbol.startsWith('4') || symbol.startsWith('8')) return 'BJ';
    return 'SZ'; // 兜底
}

const INTRADAY_OBSERVER_STRATEGIES = new Set(['volume_shock_observer']);
const DEFAULT_DEPLOY_INTERVAL_OPTIONS = [
    { value: '14', label: '每 14 天' },
    { value: '30', label: '每 30 天' },
    { value: '60', label: '每 60 天' },
    { value: '90', label: '每 90 天' },
];

function isIntradayObserverStrategy(strategyKey) {
    return INTRADAY_OBSERVER_STRATEGIES.has(strategyKey);
}

function setSelectOptions(selectEl, options, selectedValue) {
    if (!selectEl) return;
    selectEl.innerHTML = '';
    options.forEach(item => {
        const opt = document.createElement('option');
        opt.value = item.value;
        opt.textContent = item.label;
        opt.selected = item.value === selectedValue;
        selectEl.appendChild(opt);
    });
}

function restoreDefaultDeployIntervalOptions() {
    const interval = document.getElementById('deploy-interval');
    if (!interval) return;
    const current = interval.value || '14';
    const valid = DEFAULT_DEPLOY_INTERVAL_OPTIONS.some(item => item.value === current);
    setSelectOptions(interval, DEFAULT_DEPLOY_INTERVAL_OPTIONS, valid ? current : '14');
}

function applyDeployStrategyDefaults() {
    const strategyKey = document.getElementById('deploy-strategy')?.value || '';
    const interval = document.getElementById('deploy-interval');
    const intervalLabel = document.getElementById('deploy-interval-label');
    const topN = document.getElementById('deploy-topn');
    const capital = document.getElementById('deploy-capital');
    const allocation = document.getElementById('deploy-allocation');
    const fixedLots = document.getElementById('deploy-fixed-lots');
    const indexCode = document.getElementById('deploy-index');
    const stampTax = document.getElementById('deploy-stamp-tax');
    const commission = document.getElementById('deploy-commission');
    const slippage = document.getElementById('deploy-slippage');

    if (isIntradayObserverStrategy(strategyKey)) {
        if (intervalLabel) intervalLabel.textContent = '扫描方式';
        setSelectOptions(interval, [{ value: '1', label: '盘中实时扫描（约60秒）' }], '1');
        if (interval) interval.disabled = true;
        if (topN) topN.value = '5';
        if (capital) capital.value = '100000';
        if (allocation) allocation.value = 'fixed_lots';
        if (fixedLots) fixedLots.value = '1';
        if (indexCode) indexCode.value = 'all_a';
        if (stampTax) stampTax.value = '0.05';
        if (commission) commission.value = '0.03';
        if (slippage) slippage.value = '0.5';
    } else {
        if (intervalLabel) intervalLabel.textContent = '调仓周期';
        restoreDefaultDeployIntervalOptions();
        if (interval) interval.disabled = false;
    }

    if (typeof toggleLotsInput === 'function') {
        toggleLotsInput();
    }
}

async function loadDeployStrategies() {
    try {
        const adoptResp = await fetch('/api/strategy/adoptions');
        const adoptData = await adoptResp.json();
        const adoptions = adoptData.adoptions || {};
        const statuses = adoptData.statuses || {};

        const resp = await fetch('/api/portfolio/strategies');
        const data = await resp.json();

        const sel = document.getElementById('deploy-strategy');
        // 加入 "请选择" 作为第一个空选项
        sel.innerHTML = '<option value="">-- 请选择策略 --</option>';
        sel.onchange = applyDeployStrategyDefaults;

        const fSel = document.getElementById('pt-filter-strategy');
        if (fSel) fSel.innerHTML = '<option value="all">所有模型策略</option>';

        const allStrs = data.strategies || [];
        allStrs.forEach(s => {
            const isAdopted = adoptions[s.key] === true;
            const sStatus = statuses[s.key] || 'normal';
            const isNoSignal = sStatus === 'no_signal';
            const isHeavyLoss = sStatus === 'heavy_loss';

            // 模拟实盘部署下拉：剔除 no_signal 和 heavy_loss 策略
            if (!isNoSignal && !isHeavyLoss) {
                const opt = document.createElement('option');
                opt.value = s.key;
                opt.textContent = (isAdopted ? '✓ ' : '') + s.name;
                sel.appendChild(opt);
            }

            if (fSel) {
                const fOpt = document.createElement('option');
                fOpt.value = s.key;
                fOpt.textContent = (isNoSignal ? '⚠️ ' : isHeavyLoss ? '📉 ' : '') + s.name;
                fSel.appendChild(fOpt);
            }
        });
        applyDeployStrategyDefaults();
    } catch(e) {
        console.error('加载实盘可用策略失败', e);
    }
}

function toggleDeployForm() {
    const panel = document.getElementById('deploy-form-panel');
    if (panel.style.display === 'none') {
        panel.style.display = 'grid';
        loadDeployStrategies();
    } else {
        panel.style.display = 'none';
    }
}

function startAutopilot() {
    const strategyKey = document.getElementById('deploy-strategy').value;
    let indexCode = document.getElementById('deploy-index').value;
    let rebalanceDays = parseInt(document.getElementById('deploy-interval').value);
    let topN = parseInt(document.getElementById('deploy-topn').value || 50);
    let initialCapital = parseFloat(document.getElementById('deploy-capital').value || 1000000);
    let allocation = document.getElementById('deploy-allocation').value;
    let fixedLots = parseInt(document.getElementById('deploy-fixed-lots').value || 10);

    let stampTax = parseFloat(document.getElementById('deploy-stamp-tax').value || 0.05) / 100;
    let commission = parseFloat(document.getElementById('deploy-commission').value || 0.03) / 100;
    let slippage = parseFloat(document.getElementById('deploy-slippage').value || 0.3) / 100;

    if (!strategyKey) {
        showToast('👉 请先选择一个策略！', 'error');
        return;
    }

    if (isIntradayObserverStrategy(strategyKey)) {
        indexCode = 'all_a';
        rebalanceDays = 1;
        topN = topN || 5;
        initialCapital = initialCapital || 100000;
        allocation = 'fixed_lots';
        fixedLots = fixedLots || 1;
        stampTax = 0.0005;
        commission = 0.0003;
        slippage = 0.005;
    }

    const btn = document.getElementById('deploy-btn');
    btn.disabled = true;
    btn.innerHTML = '⏳ 正在启动...';

    fetch('/api/paper/task/start', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            account_id: currentPaperAccount,
            strategy_key: strategyKey,
            index_code: indexCode,
            rebalance_days: rebalanceDays,
            top_n: topN,
            initial_capital: initialCapital,
            allocation: allocation,
            fixed_lots: fixedLots,
            stamp_tax_rate: stampTax,
            commission_rate: commission,
            slippage_rate: slippage
        })
    }).then(r => r.json()).then(res => {
        btn.disabled = false;
        btn.innerHTML = '▶ 启动策略';

        if (res.status === 'ok') {
            document.getElementById('deploy-form-panel').style.display = 'none';
            pollAutopilotTasks();
            showToast('✅ 策略启动成功！并且已创建独立模拟账户。', 'success');
        } else {
            showToast('启动失败: ' + (res.detail || '未知错误'), 'error');
        }
    }).catch(err => {
        btn.disabled = false;
        btn.innerHTML = '▶ 启动策略';
        showToast('网络异常: ' + err.message, 'error');
    });
}

let _cachedTasks = [];  // 缓存任务数据供详情页使用

function pollAutopilotTasks() {
    fetch('/api/paper/tasks')
        .then(r => r.json())
        .then(data => {
            const container = document.getElementById('autopilot-tasks-container');

            // 读取最新的筛选排序条件
            const filterStrat = document.getElementById('pt-filter-strategy') ? document.getElementById('pt-filter-strategy').value : 'all';
            const filterIdx = document.getElementById('pt-filter-index') ? document.getElementById('pt-filter-index').value : 'all';
            const sortBy = document.getElementById('pt-sort-by') ? document.getElementById('pt-sort-by').value : 'time_desc';

            let tasks = data.tasks || [];
            _cachedTasks = tasks;  // 缓存完整任务列表

            // 执行筛选
            if (filterStrat !== 'all') {
                tasks = tasks.filter(t => t.strategy_key === filterStrat);
            }
            if (filterIdx !== 'all') {
                tasks = tasks.filter(t => t.index_code === filterIdx);
            }

            // 执行排序
            if (sortBy === 'time_desc') {
                tasks.sort((a,b) => (b.created_at || '').localeCompare(a.created_at || ''));
            } else if (sortBy === 'time_asc') {
                tasks.sort((a,b) => (a.created_at || '').localeCompare(b.created_at || ''));
            } else if (sortBy === 'profit_desc') {
                tasks.sort((a,b) => (b.fin_profit || 0) - (a.fin_profit || 0));
            } else if (sortBy === 'profit_asc') {
                tasks.sort((a,b) => (a.fin_profit || 0) - (b.fin_profit || 0));
            } else if (sortBy === 'profit_pct_desc') {
                tasks.sort((a,b) => {
                    const pctA = (a.fin_initial > 0) ? (a.fin_profit || 0) / a.fin_initial : 0;
                    const pctB = (b.fin_initial > 0) ? (b.fin_profit || 0) / b.fin_initial : 0;
                    return pctB - pctA;
                });
            } else if (sortBy === 'profit_pct_asc') {
                tasks.sort((a,b) => {
                    const pctA = (a.fin_initial > 0) ? (a.fin_profit || 0) / a.fin_initial : 0;
                    const pctB = (b.fin_initial > 0) ? (b.fin_profit || 0) / b.fin_initial : 0;
                    return pctA - pctB;
                });
            } else if (sortBy === 'name_asc') {
                tasks.sort((a,b) => (a.strategy_name || '').localeCompare(b.strategy_name || ''));
            } else if (sortBy === 'name_desc') {
                tasks.sort((a,b) => (b.strategy_name || '').localeCompare(a.strategy_name || ''));
            } else if (sortBy === 'anomaly_desc') {
                tasks.sort((a,b) => (b.last_anomaly_at || '').localeCompare(a.last_anomaly_at || ''));
            } else if (sortBy === 'anomaly_count_desc') {
                tasks.sort((a,b) => (b.anomaly_count || 0) - (a.anomaly_count || 0));
            }

            if (tasks.length === 0) {
                container.innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:40px">📭 当前筛选条件下无符合的任务流</div>';
                return;
            }
            const totalCount = tasks.length;
            const runningCount = tasks.filter(t => t.status === 'running').length;
            const stoppedCount = totalCount - runningCount;
            const indexNames = {'csi300': '沪深300', 'csi500': '中证500', 'csi1000': '中证1000', 'all_a': '全A'};
            container.innerHTML = `<div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;padding:8px 14px;background:var(--bg-secondary);border-radius:var(--radius);font-size:13px;color:var(--text-secondary)">
                <span style="font-weight:600;color:var(--text-primary)">总数: ${totalCount}</span>
                <span style="color:var(--green)">🟢 运行中: ${runningCount}</span>
                ${stoppedCount > 0 ? `<span style="color:var(--text-muted)">⚫ 已停止: ${stoppedCount}</span>` : ''}
            </div>` + tasks.map((t, idx) => {
                const isRunning = t.status === 'running';
                const isIntraday = isIntradayObserverStrategy(t.strategy_key);
                const statusDot = isRunning ? '🟢' : (t.status === 'stopped' ? '⚫' : '🟡');
                const borderColor = isRunning ? 'var(--green)' : 'var(--border)';
                let statusText = isRunning ? '运行中' : (t.status === 'stopped' ? '已停止' : '启动中');
                let progressMsg = '';
                if (isRunning && t.message && (isIntraday || t.message.includes('📊') || t.message.includes('🧮') || t.message.includes('📋') || t.message.includes('📤') || t.message.includes('📥') || t.message.includes('🔄 正在执行'))) {
                    progressMsg = t.message;
                    if (t.progress) statusText = `运行中 (${t.progress})`;
                } else if (t.status === 'starting' && t.progress) {
                    statusText = `⏳ 拉底仓: ${t.progress}`;
                }
                const scheduleHtml = isIntraday ? `
                            <span>📡 模式: 盘中扫描</span>
                            <span>⏱ 间隔: 约60秒</span>
                            <span>🔄 买入信号: ${t.rebalance_count}次</span>
                        ` : `
                            <span>📅 周期: ${t.rebalance_days}天</span>
                            <span>🔄 已调仓: ${t.rebalance_count}次</span>
                            <span>⏱ 上次: ${t.last_rebalance}</span>
                            <span>⏳ 下次: ${t.next_rebalance}</span>
                        `;

                return `
                <div style="background:var(--bg-card);border:1px solid ${borderColor};border-radius:var(--radius);padding:16px 20px;display:flex;align-items:center;gap:16px;transition:var(--transition);cursor:pointer"
                     onclick="viewTaskDetail('${t.account_id}','${t.strategy_name}','${t.task_id}')"
                     onmouseover="this.style.background='var(--bg-card-hover)';this.style.transform='translateX(4px)'"
                     onmouseout="this.style.background='var(--bg-card)';this.style.transform='none'">

                    <div style="flex:0 0 auto;display:flex;flex-direction:column;align-items:center;gap:4px">
                        <span style="font-size:11px;color:var(--text-muted);font-weight:600">#${idx + 1}</span>
                        <span style="font-size:20px">${statusDot}</span>
                    </div>

                    <div style="flex:1;min-width:0">
                        <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
                            <strong style="font-size:15px">${t.strategy_name}</strong>
                            <span class="badge badge-category" style="font-size:11px;padding:2px 8px">${indexNames[t.index_code] || t.index_code}</span>
                            <span style="font-size:11px;color:var(--text-muted);font-family:monospace">#${t.task_id}</span>
                        </div>
                        <div style="font-size:12px;color:var(--text-secondary);margin-bottom:12px;padding-right:16px;">
                            ${t.strategy_desc || '按指定调仓日和资产池进行自动化选股'}
                        </div>

                        ${progressMsg ? `<div style="font-size:12px;color:#60a5fa;background:rgba(96,165,250,0.08);border:1px solid rgba(96,165,250,0.2);border-radius:6px;padding:6px 12px;margin-bottom:8px;display:flex;align-items:center;gap:6px">
                            <span class="spinner" style="display:inline-block;width:14px;height:14px;border:2px solid rgba(96,165,250,0.3);border-top-color:#60a5fa;border-radius:50%;animation:spin 1s linear infinite"></span>
                            ${progressMsg}
                        </div>` : ''}

                        <!-- 核心资产指标 -->
                        <div style="display:flex;gap:20px;margin-bottom:8px;align-items:center;font-family:monospace;font-size:13px">
                            <div title="总资产"><span style="color:var(--text-muted)">资产:</span> <span style="font-weight:600;color:var(--text-primary)">${formatMoney(t.fin_total || 0)}</span></div>
                            <div title="可用现金"><span style="color:var(--text-muted)">现金:</span> <span>${formatMoney(t.fin_cash || 0)}</span></div>
                            <div title="持仓市值"><span style="color:var(--text-muted)">市值:</span> <span>${formatMoney(t.fin_market || 0)}</span></div>
                            <div title="总盈亏"><span style="color:var(--text-muted)">总盈亏:</span> <span style="font-weight:600;color:${(t.fin_profit||0) >= 0 ? 'var(--red)' : 'var(--green)'}">${(t.fin_profit||0) >= 0 ? '+' : ''}${formatMoney(t.fin_profit || 0)} <small>(${t.fin_initial > 0 ? ((t.fin_profit||0) >= 0 ? '+' : '') + ((t.fin_profit||0) / t.fin_initial * 100).toFixed(2) + '%' : '--'})</small></span></div>
                        </div>

                        <!-- 周期与时间状态 -->
                        <div style="font-size:12px;color:var(--text-secondary);display:flex;gap:16px;flex-wrap:wrap">
                            ${scheduleHtml}
                            ${t.anomaly_count > 0 ? `<span style="color:#fbbf24;background:rgba(251,191,36,0.12);padding:1px 8px;border-radius:4px;border:1px solid rgba(251,191,36,0.3);font-weight:500" title="最近异常: ${t.last_anomaly_at}">⚠️ 异常${t.anomaly_count}条 · ${t.last_anomaly_at ? t.last_anomaly_at.substring(0,16) : '--'}</span>` : ''}
                            <span style="color:var(--text-muted)">| 印花${((t.stamp_tax_rate||0)*100).toFixed(2)}% 佣金${((t.commission_rate||0)*100).toFixed(2)}% 滑点${((t.slippage_rate||0)*100).toFixed(1)}%</span>
                        </div>
                    </div>

                    <!-- 右侧操作区 -->
                    <div style="flex:0 0 auto;display:flex;flex-direction:column;gap:8px;align-items:flex-end" onclick="event.stopPropagation()">
                        <span style="font-size:12px;color:var(--text-muted);padding:4px 10px;background:var(--bg-secondary);border-radius:12px">${statusText}</span>
                        <div style="display:flex;gap:6px">
                            <button class="btn" style="padding:4px 10px;font-size:12px;background:var(--bg-secondary);color:var(--text-primary);border:1px solid var(--border);border-radius:6px" onclick="viewTaskDetail('${t.account_id}','${t.strategy_name}','${t.task_id}')">📄详情</button>
                            ${isRunning ? `<button class="btn" style="padding:4px 10px;font-size:12px;background:#c53030;color:#fff;border:none;border-radius:6px" onclick="stopAutopilotTask('${t.task_id}')">⏹停</button>` : `<button class="btn" style="padding:4px 10px;font-size:12px;background:#2563eb;color:#fff;border:none;border-radius:6px" onclick="restartAutopilotTask('${t.task_id}')">▶启</button>`}
                            <button class="btn" style="padding:4px 10px;font-size:12px;background:var(--bg-secondary);color:var(--red);border:1px solid var(--red);border-radius:6px" onclick="deleteAutopilotTask('${t.task_id}')">❌删</button>
                        </div>
                    </div>
                </div>`;
            }).join('');
        });
}

function viewTaskDetail(accountId, strategyName, taskId) {
    const listView = document.getElementById('pt-list-view');
    const detailView = document.getElementById('pt-detail-view');
    const panel = document.getElementById('task-detail-panel');

    if (listView) listView.style.display = 'none';
    if (detailView) {
        detailView.style.display = 'block';
        panel.style.display = 'block';
    } else {
        panel.style.display = 'block';
    }

    document.getElementById('task-detail-title').textContent = `📋 任务详情 — ${strategyName} (#${taskId})`;

    // 从缓存中获取任务的交易成本参数并展示
    const taskInfo = _cachedTasks.find(t => t.task_id === taskId) || {};
    const costInfoEl = document.getElementById('pt-cost-info');
    if (costInfoEl) {
        const stampPct = ((taskInfo.stamp_tax_rate || 0) * 100).toFixed(2);
        const commPct = ((taskInfo.commission_rate || 0) * 100).toFixed(2);
        const slipPct = ((taskInfo.slippage_rate || 0) * 100).toFixed(2);
        const indexNames = {'csi300':'沪深300','csi500':'中证500','csi1000':'中证1000','all_a':'全A'};
        const modeInfo = isIntradayObserverStrategy(taskInfo.strategy_key)
            ? `盘中扫描 · 覆盖 ${indexNames[taskInfo.index_code] || taskInfo.index_code || '--'}`
            : `调仓周期 ${taskInfo.rebalance_days || '--'} 天 · 覆盖指数 ${indexNames[taskInfo.index_code] || taskInfo.index_code || '--'}`;
        costInfoEl.innerHTML = `
            <div style="display:flex;gap:16px;align-items:center;flex-wrap:wrap">
                <span style="color:var(--text-muted);font-size:12px">📋 交易成本参数:</span>
                <span style="font-size:12px;padding:2px 8px;border-radius:4px;background:var(--bg-secondary);border:1px solid var(--border)">印花税 <b style="color:var(--yellow)">${stampPct}%</b></span>
                <span style="font-size:12px;padding:2px 8px;border-radius:4px;background:var(--bg-secondary);border:1px solid var(--border)">佣金 <b style="color:var(--yellow)">${commPct}%</b></span>
                <span style="font-size:12px;padding:2px 8px;border-radius:4px;background:var(--bg-secondary);border:1px solid var(--border)">滑点 <b style="color:var(--yellow)">${slipPct}%</b></span>
                <span style="font-size:11px;color:var(--text-muted)">(${modeInfo})</span>
            </div>`;
        costInfoEl.style.display = 'block';
    }

    const progressInfoEl = document.getElementById('pt-progress-info');
    if (progressInfoEl) {
        let progressMsg = '';
        if (taskInfo.status === 'running' && taskInfo.message && (taskInfo.message.includes('📊') || taskInfo.message.includes('🧮') || taskInfo.message.includes('📋') || taskInfo.message.includes('📤') || taskInfo.message.includes('📥') || taskInfo.message.includes('🔄 正在执行'))) {
            progressMsg = taskInfo.message;
        }

        if (progressMsg) {
            progressInfoEl.innerHTML = `
                <div style="font-size:14px;color:#60a5fa;display:flex;align-items:center;gap:8px">
                    <span class="spinner" style="display:inline-block;width:16px;height:16px;border:2px solid rgba(96,165,250,0.3);border-top-color:#60a5fa;border-radius:50%;animation:spin 1s linear infinite"></span>
                    <strong style="color:var(--text-primary)">当前状态：</strong>${progressMsg}
                </div>`;
            progressInfoEl.style.display = 'block';
            progressInfoEl.style.background = 'rgba(96,165,250,0.08)';
            progressInfoEl.style.borderColor = 'rgba(96,165,250,0.2)';
        } else {
            progressInfoEl.style.display = 'none';
        }
    }

    fetch(`/api/paper/account/${accountId}`)
        .then(r => r.json())
        .then(res => {
            if (res.status === 'ok') {
                const d = res.data;
                const icp = document.getElementById('pt-initial-capital-label');
                if (icp) icp.textContent = `(初始资金: ${formatMoney(d.initial_capital)})`;
                document.getElementById('pt-total-value').innerText = formatMoney(d.total_value);
                document.getElementById('pt-cash').innerText = formatMoney(d.cash);
                document.getElementById('pt-market-value').innerText = formatMoney(d.market_value);

                const ptProfit = document.getElementById('pt-total-profit');
                const profitPct = d.initial_capital > 0 ? (d.total_profit / d.initial_capital * 100) : 0;
                ptProfit.innerHTML = (d.total_profit >= 0 ? '+' : '') + formatMoney(d.total_profit) + ` <small style="font-size:14px">(${profitPct >= 0 ? '+' : ''}${profitPct.toFixed(2)}%)</small>`;
                ptProfit.style.color = d.total_profit >= 0 ? 'var(--red)' : 'var(--green)';

                // 显示交易费用提示
                const feeHint = document.getElementById('pt-commission-hint');
                if (feeHint && d.total_commission > 0) {
                    feeHint.textContent = `(含交易费 ${formatMoney(d.total_commission)})`;
                    feeHint.style.display = 'inline';
                } else if (feeHint) {
                    feeHint.style.display = 'none';
                }

                const tbody = document.getElementById('pt-positions-body');
                if (!d.positions || d.positions.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--text-muted);padding:24px">📭 该策略尚无持仓，等待首次调仓触发</td></tr>';
                } else {
                    // 按浮动盈亏百分比倒序排列
                    d.positions.sort((a, b) => (b.profit_pct || 0) - (a.profit_pct || 0));
                    tbody.innerHTML = d.positions.map(p => {
                        const color = p.profit >= 0 ? 'var(--red)' : 'var(--green)';
                        const arrow = p.profit >= 0 ? '▲' : '▼';
                        const lots = Math.floor(p.shares / 100);
                        const initialAmount = p.shares * p.cost_price;
                        // 根据代码前缀判断市场 (6开头=上海, 0/3开头=深圳, 4/8开头=北交所)
                        const mkt = getMarketPrefix(p.symbol);
                        const f10Url = `https://emweb.securities.eastmoney.com/pc_hsf10/pages/index.html?type=web&code=${mkt}${p.symbol}#/cwfx`;
                        return `
                            <tr>
                                <td style="font-family:monospace">
                                    <a href="${f10Url}" target="_blank" rel="noopener"
                                       style="color:var(--accent);text-decoration:none;border-bottom:1px dashed var(--accent)"
                                       title="查看 ${p.name || p.symbol} 财务报表 (F10)">${p.symbol}</a>
                                </td>
                                <td>
                                    <a href="${f10Url}" target="_blank" rel="noopener"
                                       style="color:var(--text-primary);text-decoration:none"
                                       title="查看财务报表">${p.name || '--'}</a>
                                </td>
                                <td style="text-align:right">${lots}手 / ${p.shares}股</td>
                                <td style="text-align:right">${p.cost_price.toFixed(3)}</td>
                                <td style="text-align:right">${formatMoney(initialAmount)}</td>
                                <td style="text-align:right;font-weight:600">${p.current_price.toFixed(3)}</td>
                                <td style="text-align:right">${formatMoney(p.market_value)}</td>
                                <td style="color:${color};font-weight:bold;text-align:right">${arrow} ${formatMoney(p.profit)} (${p.profit_pct >= 0 ? '+' : ''}${p.profit_pct.toFixed(2)}%)</td>
                            </tr>
                        `;
                    }).join('');
                }
            }
        });

    fetch(`/api/paper/account/${accountId}/trades`)
        .then(r => r.json())
        .then(res => {
            if (res.status === 'ok') {
                renderRebalanceHistory(res.data);
            }
        });

    // 加载调仓异常日志
    fetch(`/api/paper/task/${taskId}/anomalies`)
        .then(r => r.json())
        .then(res => {
            if (res.status === 'ok') {
                renderAnomalyLog(res.anomalies);
                // 更新入口按钮上的异常徽章
                const badge = document.getElementById('anomaly-badge');
                if (badge && res.anomalies && res.anomalies.length > 0) {
                    badge.textContent = res.anomalies.length + '条';
                    badge.style.display = 'inline';
                } else if (badge) {
                    badge.style.display = 'none';
                }
            }
        })
        .catch(err => console.error('加载调仓异常日志失败', err));

    window.scrollTo({ top: 0, behavior: 'smooth' });
}

function renderAnomalyLog(anomalies) {
    const container = document.getElementById('pt-anomaly-log-body');
    if (!container) return;
    if (!anomalies || anomalies.length === 0) {
        container.innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:24px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg-secondary)">✅ 暂无调仓异常记录</div>';
        return;
    }

    // 按调仓轮次分组
    const groups = {};
    anomalies.forEach(a => {
        const round = a.rebalance_round || 0;
        if (!groups[round]) groups[round] = [];
        groups[round].push(a);
    });

    // 按轮次降序
    const sortedRounds = Object.keys(groups).sort((a, b) => parseInt(b) - parseInt(a));

    // 异常类型映射
    const typeLabels = {
        'PRICE_TOO_HIGH': { icon: '💰', label: '股价过高', color: 'var(--yellow)' },
        'INSUFFICIENT_FUNDS': { icon: '💸', label: '资金不足', color: 'var(--red)' },
        'CASH_EXHAUSTED': { icon: '🏦', label: '现金耗尽', color: 'var(--red)' },
        'QUOTE_FAIL': { icon: '📡', label: '报价失败', color: 'var(--text-muted)' },
        'BUY_FAILED': { icon: '❌', label: '买入失败', color: 'var(--red)' },
        'LIMIT_UP': { icon: '🔒', label: '涨停封板', color: '#ff6b6b' },
        'T_PLUS_1': { icon: '⏳', label: 'T+1限制', color: 'var(--purple)' },
        'MARKET_CLOSED': { icon: '⏱️', label: '非交易时段', color: 'var(--text-muted)' },
    };

    let html = `<div style="margin-bottom:12px;padding:12px 16px;background:rgba(252,196,25,0.08);border:1px solid rgba(252,196,25,0.25);border-radius:var(--radius-sm);font-size:13px;color:var(--yellow)">
        ⚠️ 共发现 <strong>${anomalies.length}</strong> 条调仓异常，涉及 <strong>${sortedRounds.length}</strong> 次调仓。小资金+等分金额模式下，高价股容易因1手金额超出等分额度而无法买入。
    </div>`;

    sortedRounds.forEach(round => {
        const items = groups[round];
        const tDate = items[0].trade_date;
        const cTime = items[0].created_at || '--';
        const displayTime = tDate ? `${tDate} 调仓抛出 (${cTime})` : cTime;

        html += `
        <div style="border:1px solid rgba(252,196,25,0.3);border-radius:var(--radius-sm);margin-bottom:16px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.15)">
            <div style="background:rgba(252,196,25,0.06);padding:10px 16px;border-bottom:1px solid rgba(252,196,25,0.2);display:flex;justify-content:space-between;align-items:center">
                <div>
                    <span style="font-weight:600;font-size:14px;color:var(--text-primary)">⚠️ 第 ${round} 次调仓异常</span>
                    <span style="color:var(--text-muted);font-size:12px;margin-left:8px">${displayTime}</span>
                </div>
                <span style="font-size:12px;padding:2px 10px;background:rgba(252,196,25,0.15);color:var(--yellow);border-radius:12px;font-weight:500">${items.length} 条异常</span>
            </div>
            <div style="padding:12px 16px;background:var(--bg-card)">
                <table style="width:100%;border-collapse:collapse;font-size:13px">
                    <thead>
                        <tr style="border-bottom:1px solid var(--border)">
                            <th style="text-align:left;padding:6px 8px;color:var(--text-muted);font-weight:500;font-size:12px">类型</th>
                            <th style="text-align:left;padding:6px 8px;color:var(--text-muted);font-weight:500;font-size:12px">股票</th>
                            <th style="text-align:right;padding:6px 8px;color:var(--text-muted);font-weight:500;font-size:12px">股价</th>
                            <th style="text-align:right;padding:6px 8px;color:var(--text-muted);font-weight:500;font-size:12px">1手需</th>
                            <th style="text-align:right;padding:6px 8px;color:var(--text-muted);font-weight:500;font-size:12px">可用现金</th>
                            <th style="text-align:left;padding:6px 8px;color:var(--text-muted);font-weight:500;font-size:12px">详情</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${items.map(a => {
                            const info = typeLabels[a.anomaly_type] || { icon: '❓', label: a.anomaly_type, color: 'var(--text-muted)' };
                            const mkt = getMarketPrefix(a.symbol);
                            const f10Url = `https://emweb.securities.eastmoney.com/pc_hsf10/pages/index.html?type=web&code=${mkt}${a.symbol}#/cwfx`;
                            return `
                            <tr style="border-bottom:1px solid var(--border)">
                                <td style="padding:8px;white-space:nowrap">
                                    <span style="display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:4px;background:${info.color}15;color:${info.color};font-size:12px;font-weight:500;border:1px solid ${info.color}30">
                                        ${info.icon} ${info.label}
                                    </span>
                                </td>
                                <td style="padding:8px;font-family:monospace">
                                    <a href="${f10Url}" target="_blank" rel="noopener" style="color:var(--accent);text-decoration:none;border-bottom:1px dashed var(--accent)" title="查看 ${a.symbol} 财务报表">${a.symbol}</a>
                                </td>
                                <td style="padding:8px;text-align:right;font-family:monospace">${a.stock_price > 0 ? '¥' + a.stock_price.toFixed(2) : '--'}</td>
                                <td style="padding:8px;text-align:right;font-family:monospace;color:var(--red)">${a.required_amount > 0 ? '¥' + a.required_amount.toLocaleString('zh-CN', {maximumFractionDigits: 0}) : '--'}</td>
                                <td style="padding:8px;text-align:right;font-family:monospace">${a.available_cash > 0 ? '¥' + a.available_cash.toLocaleString('zh-CN', {maximumFractionDigits: 0}) : '--'}</td>
                                <td style="padding:8px;font-size:12px;color:var(--text-secondary);max-width:300px;word-break:break-all">${a.detail}</td>
                            </tr>`;
                        }).join('')}
                    </tbody>
                </table>
            </div>
        </div>`;
    });

    container.innerHTML = html;
}

function renderRebalanceHistory(trades) {
    const container = document.getElementById('pt-rebalance-history-body');
    if (!container) return;
    if (!trades || trades.length === 0) {
        container.innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:24px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg-secondary)">📭 暂无调仓历史记录</div>';
        return;
    }

    // 按照 trade_time 的分钟级(前16个字符: YYYY-MM-DD HH:MM)进行分组
    const groups = {};
    trades.forEach(t => {
        const timeKey = t.trade_time.substring(0, 16);
        if (!groups[timeKey]) groups[timeKey] = [];
        groups[timeKey].push(t);
    });

    // 按照时间降序排
    const sortedKeys = Object.keys(groups).sort((a,b)=>b.localeCompare(a));

    let html = '';
    sortedKeys.forEach((key, index) => {
        const groupTrades = groups[key];

        let buyItems = '';
        let sellItems = '';
        let totalCost = 0;
        let totalSold = 0;
        let totalCommission = 0;

        groupTrades.forEach(tr => {
            totalCommission += tr.commission || 0;
            if (tr.action === 'BUY') {
                const remarkHtml = tr.remark ? `<div style="font-size:11px;color:var(--text-muted);margin-top:4px;border-top:1px dashed rgba(252,129,129,0.2);padding-top:4px;">备注: ${tr.remark}</div>` : '';
                buyItems += `<div style="color:var(--text-primary);background:rgba(252,129,129,0.08);padding:8px 12px;border-radius:4px;margin:4px 0;font-size:13px;border:1px solid rgba(252,129,129,0.2)">
                    <strong style="color:var(--red)">${tr.symbol} ${tr.name || ''}</strong> &nbsp;买入 ${tr.shares}股 @ ${formatMoney(tr.price)}
                    ${remarkHtml}
                </div>`;
                totalCost += tr.amount || (tr.shares * tr.price);
            } else {
                const remarkHtml = tr.remark ? `<div style="font-size:11px;color:var(--text-muted);margin-top:4px;border-top:1px dashed rgba(72,187,120,0.2);padding-top:4px;">备注: ${tr.remark}</div>` : '';
                sellItems += `<div style="color:var(--text-primary);background:rgba(72,187,120,0.08);padding:8px 12px;border-radius:4px;margin:4px 0;font-size:13px;border:1px solid rgba(72,187,120,0.2)">
                    <strong style="color:var(--green)">${tr.symbol} ${tr.name || ''}</strong> &nbsp;卖出 ${tr.shares}股 @ ${formatMoney(tr.price)}
                    ${remarkHtml}
                </div>`;
                totalSold += tr.amount || (tr.shares * tr.price);
            }
        });

        const summary = `<span style="font-weight:600;font-size:14px;color:var(--text-primary)">🔄 第 ${sortedKeys.length - index} 次调仓</span>
            <span style="color:var(--text-muted);font-size:12px;margin-left:8px">(${key})</span>`;

        html += `
            <div style="border:1px solid var(--border);border-radius:var(--radius-sm);margin-bottom:16px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.2)">
                <div style="background:var(--bg-secondary);padding:12px 16px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center">
                    <div>${summary}</div>
                    <div style="font-size:12px;color:var(--text-secondary)">
                        <span style="color:var(--green)">回笼现金: ${formatMoney(totalSold)}</span> &nbsp;|&nbsp;
                        <span style="color:var(--red)">买入花费: ${formatMoney(totalCost)}</span> &nbsp;|&nbsp;
                        <span style="color:var(--yellow)">交易费用: ${formatMoney(totalCommission)}</span>
                    </div>
                </div>
                <div style="padding:16px;background:var(--bg-card);font-size:13px">
                    <div style="margin-bottom:12px">
                        <strong style="color:var(--text-primary);display:block;margin-bottom:6px">📈 新增买入/加仓:</strong>
                        <div>${buyItems || '<span style="color:var(--text-muted);font-size:12px">本次无主动买入建仓动作</span>'}</div>
                    </div>
                    <div>
                        <strong style="color:var(--text-primary);display:block;margin-bottom:6px">📉 减持抛售/清仓:</strong>
                        <div>${sellItems || '<span style="color:var(--text-muted);font-size:12px">本次无卖出清仓动作</span>'}</div>
                    </div>
                </div>
            </div>
        `;
    });
    container.innerHTML = html;
}

function closeTaskDetail() {
    const listView = document.getElementById('pt-list-view');
    const detailView = document.getElementById('pt-detail-view');
    const panel = document.getElementById('task-detail-panel');

    // 先关闭子页面
    closeSubPage();

    if (listView) listView.style.display = 'block';
    if (detailView) detailView.style.display = 'none';
    if (panel) panel.style.display = 'none';

    // 返回时自动拉取最新任务状态
    pollAutopilotTasks();
}

function showSubPage(pageName) {
    // 隐藏主详情面板
    document.getElementById('task-detail-panel').style.display = 'none';
    // 显示目标子页面
    const subpage = document.getElementById('subpage-' + pageName);
    if (subpage) {
        subpage.style.display = 'block';
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }
}

function closeSubPage() {
    // 隐藏所有子页面
    document.querySelectorAll('[id^="subpage-"]').forEach(el => el.style.display = 'none');
    // 恢复主详情面板
    const panel = document.getElementById('task-detail-panel');
    if (panel) panel.style.display = 'block';
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

function stopAutopilotTask(taskId) {
    showConfirm('确定要停止该策略守护任务吗？', () => {
        showToast('⏳ 正在停止...', 'success');
        fetch(`/api/paper/task/stop/${taskId}`, { method: 'POST' })
            .then(r => {
                if (!r.ok) return r.json().then(e => { throw new Error(e.detail || '停止失败') });
                return r.json();
            })
            .then(() => {
                showToast('✅ 已发送停止指令', 'success');
                pollAutopilotTasks();
            }).catch(e => showToast(e.message, 'error'));
    });
}

function restartAutopilotTask(taskId) {
    showConfirm('确定要重新启动该策略守护任务吗？', () => {
        showToast('⏳ 正在重启...', 'success');
        fetch(`/api/paper/task/restart/${taskId}`, { method: 'POST' })
            .then(r => {
                if (!r.ok) return r.json().then(e => { throw new Error(e.detail || '重启失败') });
                return r.json();
            })
            .then(() => {
                showToast('✅ 策略已重新启动', 'success');
                pollAutopilotTasks();
            }).catch(e => showToast(e.message, 'error'));
    });
}

function deleteAutopilotTask(taskId) {
    showConfirm('确定要永久删除此策略任务和其关联的历史隔离模拟资金池吗？删除不可恢复！', () => {
        showToast('⏳ 正在删除...', 'success');
        fetch(`/api/paper/task/${taskId}`, { method: 'DELETE' })
            .then(r => {
                if (!r.ok) return r.json().then(e => { throw new Error(e.detail || '删除失败') });
                return r.json();
            })
            .then(res => {
                if (res.status === 'deleted') {
                    showToast('✅ 已彻底删除', 'success');
                }
                pollAutopilotTasks();
            }).catch(e => showToast(e.message, 'error'));
    });
}

// 模拟实盘 Tab 事件绑定与自动轮询
let autopilotPollTimer = null;
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            if(btn.dataset.tab === 'paper-trading') {
                pollAutopilotTasks();
                if (autopilotPollTimer) clearInterval(autopilotPollTimer);
                autopilotPollTimer = setInterval(pollAutopilotTasks, 5000);
            } else {
                if (autopilotPollTimer) { clearInterval(autopilotPollTimer); autopilotPollTimer = null; }
            }
        });
    });
});

// ===== 策略列表管理（卡片式） =====
let adoptedStrategies = {};
let allStrategies = [];

async function loadStrategyManageList() {
    let strategyStatuses = {};
    try {
        const adoptResp = await fetch('/api/strategy/adoptions');
        const adoptData = await adoptResp.json();
        adoptedStrategies = adoptData.adoptions || {};
        strategyStatuses = adoptData.statuses || {};
    } catch(e) { console.error('加载采用状态失败', e); }

    try {
        const resp = await fetch('/api/portfolio/strategies');
        const data = await resp.json();
        allStrategies = data.strategies || [];
        const container = document.getElementById('strategy-manage-cards');
        if (!allStrategies.length) {
            container.innerHTML = '<div class="empty-state"><div class="empty-icon">📭</div><p>暂无可用策略</p></div>';
            return;
        }
        const categoryIcons = {'价值': '💎', '成长': '🚀', '动量': '⚡', '选股': '🎯', '组合': '📊'};
        const categoryBg = {
            '价值': 'var(--green)', '成长': 'var(--accent)',
            '动量': 'var(--yellow)', '选股': 'var(--purple)', '组合': 'var(--accent)',
        };
        container.innerHTML = allStrategies.map(s => {
            const adopted = adoptedStrategies[s.key] === true;
            const status = strategyStatuses[s.key] || 'normal';
            const isNoSignal = status === 'no_signal';
            const isHeavyLoss = status === 'heavy_loss';
            const icon = categoryIcons[s.category] || '📊';
            const badgeBg = categoryBg[s.category] || 'var(--text-muted)';

            // 状态标签
            let statusLabel, statusStyle;
            if (isNoSignal) {
                statusLabel = '⚠️ 待研究(无信号)';
                statusStyle = 'color:#f59e0b;';
            } else if (isHeavyLoss) {
                statusLabel = '📉 亏损严重(待验证)';
                statusStyle = 'color:#ef4444;';
            } else if (adopted) {
                statusLabel = '✅ 已采用';
                statusStyle = 'color:var(--green);';
            } else {
                statusLabel = '⏸ 未采用';
                statusStyle = '';
            }

            // 卡片样式
            let cardExtraStyle = '';
            if (isNoSignal) cardExtraStyle = 'opacity:0.65;border-color:#f59e0b44;';
            else if (isHeavyLoss) cardExtraStyle = 'opacity:0.75;border-color:#ef444444;';

            return `
            <div class="sm-card" style="${cardExtraStyle}">
                ${adopted ? '<div class="adopted-ribbon">已采用</div>' : ''}
                ${isNoSignal ? '<div class="adopted-ribbon" style="background:linear-gradient(135deg,#f59e0b,#d97706);right:auto;left:-8px;">待研究</div>' : ''}
                ${isHeavyLoss ? '<div class="adopted-ribbon" style="background:linear-gradient(135deg,#ef4444,#b91c1c);right:auto;left:-8px;">待验证</div>' : ''}
                <div class="sm-card-header">
                    <div class="sm-card-title">${icon} ${s.name}</div>
                    <span class="sm-card-badge" style="background:${badgeBg}22;color:${badgeBg};border:1px solid ${badgeBg}55">${s.category}</span>
                </div>
                <div style="margin:-4px 0 6px;font-family:monospace;font-size:11px;color:var(--text-muted);background:var(--bg-secondary);display:inline-block;padding:2px 8px;border-radius:4px;border:1px solid var(--border)">${s.key}</div>
                <div class="sm-card-desc">${s.description}</div>
                <div class="sm-card-meta">
                    <span style="${statusStyle}">${statusLabel}</span>
                </div>
                <div class="sm-card-actions">
                    ${isNoSignal ? `
                        <button class="btn" onclick="toggleStrategyStatus('${s.key}', 'normal')" style="background:#f59e0b22;color:#f59e0b;border:1px solid #f59e0b">🔄 恢复正常</button>
                    ` : isHeavyLoss ? `
                        <button class="btn" id="zero-cost-btn-${s.key}" onclick="runZeroCostVerify('${s.key}', '${s.name}')" style="background:#ef444422;color:#ef4444;border:1px solid #ef4444">🔬 零成本验证</button>
                        <button class="btn" onclick="toggleStrategyStatus('${s.key}', 'normal')" style="background:#f59e0b22;color:#f59e0b;border:1px solid #f59e0b">🔄 恢复正常</button>
                    ` : `
                        <button class="btn" onclick="toggleAdoptStrategy('${s.key}', ${!adopted})" style="${adopted
                            ? 'background:var(--red-bg);color:var(--red);border:1px solid var(--red)'
                            : 'background:var(--green-bg);color:var(--green);border:1px solid var(--green)'
                        }">${adopted ? '✕ 取消采用' : '✓ 采用'}</button>
                        <button class="btn" onclick="toggleStrategyStatus('${s.key}', 'heavy_loss')" style="background:var(--bg-secondary);color:var(--text-muted);border:1px solid var(--border);font-size:11px" title="标记为亏损严重，需要验证是否因交易成本导致">📉 标记亏损</button>
                    `}
                    <button class="btn" onclick="showStrategyDetailModal('${s.key}')" style="background:var(--bg-secondary);color:var(--text);border:1px solid var(--border)">📄 详情</button>
                    <button class="btn" onclick="goToBacktest('${s.key}')" style="background:var(--accent-glow);color:var(--accent);border:1px solid var(--accent)">🧪 回测</button>
                </div>
                <div id="zero-cost-result-${s.key}" style="display:none"></div>
            </div>`;
        }).join('');
    } catch(e) { console.error('加载策略列表失败', e); }
}

async function toggleAdoptStrategy(key, adopt) {
    try {
        await fetch('/api/strategy/adopt', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ strategy_key: key, adopted: adopt })
        });
    } catch(e) { console.error('切换采用状态失败', e); }
    loadStrategyManageList();
}

async function toggleStrategyStatus(key, status) {
    try {
        await fetch('/api/strategy/status', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ strategy_key: key, status: status })
        });
    } catch(e) { console.error('切换策略状态失败', e); }
    loadStrategyManageList();
}

// ===== 显示策略风控详情弹窗 =====
function showStrategyDetailModal(key) {
    // 借用之前在 strategyData 中注册好的策略字典数据
    // 位于 loadStrategyDetail 函数定义域中，这里由于作用域限制我们通过构造个同样的或直接提取
    // 简单起见，可以把它定义为全局或者在这个函数内复原关键策略字典
    const mockStrategyDataList = {
        'low_pe_rotation': {
            name: '低 PE 轮动', risk: '⭐⭐ 中低', frequency: '月度', holdings: '20-30 只',
            logic: ['筛选全市场股票，剔除 ST、*ST、停牌股票', '计算 PE 估值', '按 PE 升序排序，选前 20-30 只并等权重配置'],
            params: [{name: 'PE 阈值', default: '< 15', desc: '市盈率上限', range: '10-30'}]
        },
        'friction_risk_filter': {
            name: '高频摩擦风控', risk: '✅ 极低', frequency: '日度拦截', holdings: '灵活组配',
            logic: ['计算 Amihud 极压流动性损失', '计算 Turnover Vol 换手剧烈程度', '计算 Idiosyncratic 特质波动', '只要撞线立即拉入强风控黑名单'],
            params: [{name: '拉黑阈值', default: '10%', desc: '抛弃全市场最差的首尾比例', range: '5%-20%'}]
        },
        'momentum_breakout': {
            name: '首日动能突破风控防线',
            risk: '⭐⭐⭐⭐ 激进带甲',
            frequency: '日内微秒级扫描',
            holdings: '资产等权配置上限 5 只',
            logic: [
                '🛡️【追高守卫】如果该股昨日收盘价已经是过去20日的历史最高，视作已经反弹过热，直接过滤（不当接盘侠）。',
                '🛡️【静默发酵验证】如果昨日成交量已经异常放大（超过均幅2倍），说明早已走漏风声或是诱多，放弃。',
                '⚡【首日起爆确认】今日盘中突破过去20天前高，且成交量必须在均量3倍-5倍之间（真突破）。',
                '🛑【铁血硬止损】以建仓基准日的【昨收价】为死亡判定线，一旦跌破，该异动判为“假突破挂顶”，系统无条件挂单清盘斩仓以保底。',
                '💰【动态追踪止盈】建仓后系统将全天候监控该股创下的最近新高价格，从新高回撤达到绝对值 8.0% 时，启动获利了结机制，锁定利润不回吐。',
                '⌛【时间熬损剔除】若持仓超过 2 个交易日且账户未达到 2.0% 收益，表明动能消散（装死），直接踢出策略池，腾出资金让位给新一轮突破机会。',
                '🛡️【T+1防误杀保护】建仓首日（T日），策略开启锁仓豁免，避免受到当日日内波动的扫损导致A股T+1规则引发异常。',
                '🛡️【涨停最高豁免】盘中若监控到股价封板（主板10%, 科创20%, 北交30%...）距离涨停限制不足0.2元时，豁免上述所有出局规则强行持筹。'
            ],
            params: [
                {name: '爆量起步边界', default: '> 3.0倍', desc: '过滤缩量假突破', range: '>2倍 - 4倍'},
                {name: '天量诱多封顶', default: '< 5.0倍', desc: '阻止高位派发期套牢', range: '<4倍 - 7倍'},
                {name: '移动追踪折返点', default: '-8.0%', desc: '从创新高点往下回撤的止盈离场阈值', range: '5% - 15%'},
                {name: '绝杀底线止损', default: '建仓日昨收', desc: '跌穿该线代表上涨结构崩盘，触发后强平', range: '系统锁定执行'},
                {name: '时间止损(废牌)', default: '2 日不涨跌', desc: '超过天数不爆发踢出池子', range: '1-3天'}
            ]
        }
    };

    // 如果没有找到详细配置，则填充统一样板
    let sdata = mockStrategyDataList[key];
    if (!sdata && allStrategies) {
         let fallback = allStrategies.find(item => item.key === key);
         if(fallback) {
             sdata = {
                 name: fallback.name, risk: '⭐⭐⭐ 中等', frequency: '通常为日级别', holdings: '组合持仓',
                 logic: ['此策略详细逻辑目前封装在后端引擎中，依据量价信号、横截面打分、或机器学习动态决策。'],
                 params: [{name: '核心参数', default: '系统默认', desc: '该策略基于内部模型演算，通过配置面板调整宏观变量。', range: '动态演算'}]
             };
         }
    }
    if (!sdata) return;

    // 填充内容
    document.getElementById('modal-detail-title').textContent = '📄 ' + sdata.name;
    document.getElementById('modal-detail-risk').textContent = sdata.risk;
    document.getElementById('modal-detail-freq').textContent = sdata.frequency;
    document.getElementById('modal-detail-holdings').textContent = sdata.holdings;

    // 渲染逻辑
    document.getElementById('modal-detail-logic').innerHTML = sdata.logic.map((txt, i) => {
        return `<div style="display:flex; gap:8px;">
            <span style="color:var(--accent); font-weight:bold;">${i+1}.</span>
            <span style="flex:1; font-size:14px; line-height:1.5;">${txt}</span>
        </div>`;
    }).join('');

    // 渲染参数
    document.getElementById('modal-detail-params').innerHTML = sdata.params.map(p => {
        return `<tr>
            <td style="font-weight:bold; color:var(--text);">${p.name}</td>
            <td style="color:var(--accent); font-family:monospace;">${p.default}</td>
            <td style="font-size:13px; color:var(--text-secondary);">${p.desc}</td>
            <td style="font-size:12px; color:var(--text-muted);">${p.range}</td>
        </tr>`;
    }).join('');

    // 显示模态框
    const modal = document.getElementById('strategy-detail-modal');
    modal.style.display = 'flex';
    // 触发展现动画
    setTimeout(() => {
        modal.classList.add('active');
        modal.querySelector('.modal-container').style.transform = 'scale(1)';
        modal.querySelector('.modal-container').style.opacity = '1';
    }, 10);
}

// ===== 零成本对比验证 =====
async function runZeroCostVerify(strategyKey, strategyName) {
    const btn = document.getElementById(`zero-cost-btn-${strategyKey}`);
    const resultDiv = document.getElementById(`zero-cost-result-${strategyKey}`);
    if (!btn || !resultDiv) return;

    btn.disabled = true;
    btn.innerHTML = '⏳ 零成本回测运行中...';
    resultDiv.style.display = 'block';
    resultDiv.innerHTML = `
        <div style="padding:16px;background:var(--bg-secondary);border-radius:var(--radius-sm);margin-top:12px;border:1px solid var(--border)">
            <div style="display:flex;align-items:center;gap:8px;color:var(--text-secondary)">
                <div class="spinner" style="width:16px;height:16px;border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin 0.8s linear infinite"></div>
                正在运行零成本回测（印花税=0, 佣金=0, 滑点=0）...
            </div>
        </div>`;

    try {
        // 同时发起两组回测：含成本（默认参数）和零成本
        const commonPayload = {
            strategy_key: strategyKey,
            index_code: 'csi500',
            days: 3650,
            initial_capital: 500000,
            top_n: 10,
        };

        const [withCostRes, zeroCostRes] = await Promise.all([
            fetch('/api/portfolio/backtest', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    ...commonPayload,
                    stamp_tax_rate: 0.0005,
                    commission_rate: 0.0003,
                    slippage_rate: 0.005,
                }),
            }).then(r => r.json()),
            fetch('/api/portfolio/backtest', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    ...commonPayload,
                    stamp_tax_rate: 0,
                    commission_rate: 0,
                    slippage_rate: 0,
                }),
            }).then(r => r.json()),
        ]);

        btn.disabled = false;
        btn.innerHTML = '🔬 零成本验证';

        const wc = withCostRes.result || {};
        const zc = zeroCostRes.result || {};

        const wcReturn = wc.total_return_pct || 0;
        const zcReturn = zc.total_return_pct || 0;
        const costImpact = zcReturn - wcReturn;  // 交易成本吃掉的收益
        const wcAnnual = wc.annual_return_pct || 0;
        const zcAnnual = zc.annual_return_pct || 0;

        // 判断结论
        let verdict, verdictColor, verdictIcon;
        if (zcReturn > 0 && wcReturn < 0) {
            // 零成本盈利但含成本亏损 → 交易成本是主因
            verdict = '🎯 交易成本是亏损主因！零成本下策略盈利，建议优化调仓频率或降低交易成本';
            verdictColor = '#f59e0b';
            verdictIcon = '⚠️';
        } else if (zcReturn < 0 && wcReturn < 0) {
            // 零成本也亏损 → 策略本身有问题
            verdict = '❌ 策略本身逻辑存在问题！零成本下依然亏损，建议重新审视策略逻辑';
            verdictColor = '#ef4444';
            verdictIcon = '🚫';
        } else if (costImpact > 20) {
            // 成本影响超过20%
            verdict = `⚡ 交易成本影响非常大（吃掉 ${costImpact.toFixed(1)}% 收益），建议降低调仓频率`;
            verdictColor = '#f59e0b';
            verdictIcon = '⚠️';
        } else {
            verdict = '✅ 交易成本影响可控，策略逻辑正常';
            verdictColor = '#22c55e';
            verdictIcon = '✅';
        }

        const fmtPct = (v) => `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
        const pctColor = (v) => v >= 0 ? 'var(--red)' : 'var(--green)';

        resultDiv.innerHTML = `
        <div style="padding:16px;background:var(--bg-secondary);border-radius:var(--radius-sm);margin-top:12px;border:1px solid ${verdictColor}44">
            <div style="font-weight:600;font-size:14px;margin-bottom:12px;color:var(--text-primary)">
                ${verdictIcon} 零成本对比验证结果 — ${strategyName}
            </div>

            <table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:12px">
                <thead>
                    <tr style="border-bottom:2px solid var(--border)">
                        <th style="text-align:left;padding:8px;color:var(--text-muted);font-weight:500">指标</th>
                        <th style="text-align:right;padding:8px;color:var(--text-muted);font-weight:500">含成本</th>
                        <th style="text-align:right;padding:8px;color:var(--text-muted);font-weight:500">零成本</th>
                        <th style="text-align:right;padding:8px;color:var(--text-muted);font-weight:500">差异</th>
                    </tr>
                </thead>
                <tbody>
                    <tr style="border-bottom:1px solid var(--border)">
                        <td style="padding:8px">总收益率</td>
                        <td style="padding:8px;text-align:right;font-weight:600;color:${pctColor(wcReturn)};font-family:monospace">${fmtPct(wcReturn)}</td>
                        <td style="padding:8px;text-align:right;font-weight:600;color:${pctColor(zcReturn)};font-family:monospace">${fmtPct(zcReturn)}</td>
                        <td style="padding:8px;text-align:right;font-weight:700;color:#f59e0b;font-family:monospace">${fmtPct(costImpact)}</td>
                    </tr>
                    <tr style="border-bottom:1px solid var(--border)">
                        <td style="padding:8px">年化收益</td>
                        <td style="padding:8px;text-align:right;color:${pctColor(wcAnnual)};font-family:monospace">${fmtPct(wcAnnual)}</td>
                        <td style="padding:8px;text-align:right;color:${pctColor(zcAnnual)};font-family:monospace">${fmtPct(zcAnnual)}</td>
                        <td style="padding:8px;text-align:right;color:#f59e0b;font-family:monospace">${fmtPct(zcAnnual - wcAnnual)}</td>
                    </tr>
                    <tr style="border-bottom:1px solid var(--border)">
                        <td style="padding:8px">最大回撤</td>
                        <td style="padding:8px;text-align:right;color:var(--red);font-family:monospace">${(wc.max_drawdown_pct||0).toFixed(2)}%</td>
                        <td style="padding:8px;text-align:right;color:var(--red);font-family:monospace">${(zc.max_drawdown_pct||0).toFixed(2)}%</td>
                        <td style="padding:8px;text-align:right;font-family:monospace">—</td>
                    </tr>
                    <tr style="border-bottom:1px solid var(--border)">
                        <td style="padding:8px">夏普比率</td>
                        <td style="padding:8px;text-align:right;font-family:monospace">${(wc.sharpe_ratio||0).toFixed(2)}</td>
                        <td style="padding:8px;text-align:right;font-family:monospace">${(zc.sharpe_ratio||0).toFixed(2)}</td>
                        <td style="padding:8px;text-align:right;font-family:monospace">—</td>
                    </tr>
                    <tr>
                        <td style="padding:8px">交易成本累计</td>
                        <td style="padding:8px;text-align:right;font-family:monospace;color:var(--red)">¥${(wc.total_trade_cost||0).toLocaleString('zh-CN',{maximumFractionDigits:0})}</td>
                        <td style="padding:8px;text-align:right;font-family:monospace;color:var(--green)">¥0</td>
                        <td style="padding:8px;text-align:right;font-family:monospace">—</td>
                    </tr>
                </tbody>
            </table>

            <div style="padding:12px 16px;background:${verdictColor}11;border:1px solid ${verdictColor}33;border-radius:var(--radius-sm);font-size:13px;color:${verdictColor};font-weight:500">
                ${verdict}
            </div>
        </div>`;

    } catch(e) {
        btn.disabled = false;
        btn.innerHTML = '🔬 零成本验证';
        resultDiv.innerHTML = `
        <div style="padding:16px;background:#ef444411;border:1px solid #ef444433;border-radius:var(--radius-sm);margin-top:12px;color:#ef4444">
            ❌ 验证失败: ${e.message}
        </div>`;
    }
}

let pendingBacktestStrategy = null;

function goToBacktest(strategyKey) {
    // 设置全局标志位，然后模拟点击切换Tab
    pendingBacktestStrategy = strategyKey;
    const btn = document.querySelector('.tab-btn[data-tab="backtest"]');
    if (btn) btn.click();
}

// ===== 回测中心 =====

async function loadBacktestPage() {
    // 填充策略下拉
    try {
        const resp = await fetch('/api/portfolio/strategies');
        const data = await resp.json();
        const sel = document.getElementById('bt-strategy-select');

        let targetValue = sel.value;
        // 如果是从策略列表点过来的，优先使用带过来的策略KEY，并清空旧回测面板
        if (pendingBacktestStrategy) {
            targetValue = pendingBacktestStrategy;
            pendingBacktestStrategy = null;
            document.getElementById('bt-result-panel').style.display = 'none';
        }

        sel.innerHTML = '<option value="">-- 请选择策略 --</option>' +
            (data.strategies || []).map(s =>
                `<option value="${s.key}">${s.name}</option>`
            ).join('');

        if (targetValue) sel.value = targetValue;

        // 切换策略时清空旧结果并联动更新历史记录
        sel.onchange = () => {
            document.getElementById('bt-result-panel').style.display = 'none';
            loadBacktestHistory();
        };
    } catch(e) { console.error(e); }
    loadBacktestHistory();
}

const backtestPollTimers = {};

window.stopBacktestTask = function(taskId) {
    // 1. 先调用后端 API 发送真正的停止信号
    fetch(`/api/portfolio/backtest/stop/${taskId}`, { method: 'POST' })
        .then(r => r.json())
        .then(res => {
            showToast(`⏹ ${res.message || '已发送停止信号'}`, 'success');
        })
        .catch(err => {
            console.error('停止请求失败:', err);
            showToast('停止请求失败', 'error');
        });
    // 2. 清除前端轮询
    if (backtestPollTimers[taskId]) {
        clearInterval(backtestPollTimers[taskId]);
        delete backtestPollTimers[taskId];
    }
    // 3. 延时刷新列表（等后端处理完）
    setTimeout(loadBacktestHistory, 1500);
}

function monitorBacktestTask(taskId, strategyKey) {
    if (backtestPollTimers[taskId]) return; // already monitoring

    backtestPollTimers[taskId] = setInterval(() => {
        fetch(`/api/portfolio/backtest/task/${taskId}`)
        .then(r => r.json())
        .then(taskData => {
            if (taskData.status === 'not_found' || taskData.status === 'failed' || taskData.status === 'cancelled') {
                clearInterval(backtestPollTimers[taskId]);
                delete backtestPollTimers[taskId];
                loadBacktestHistory();
            }
            else if (taskData.status === 'completed') {
                clearInterval(backtestPollTimers[taskId]);
                delete backtestPollTimers[taskId];

                // Refresh history list so the row updates to completed UI
                loadBacktestHistory();

                const r = taskData.result;
                const currentSel = document.getElementById('bt-strategy-select')?.value;
                if (!currentSel || currentSel === strategyKey) {
                    document.getElementById('bt-result-panel').style.display = 'block';
                    // We don't have strategyName in monitor function directly, but can infer from select if matched

                    const finalValue = r.initial_capital * r.final_nav;
                    const returnPct = r.total_return_pct;
                    const profitColor = returnPct >= 0 ? 'var(--red)' : 'var(--green)';

                    document.getElementById('bt-result-stats').innerHTML = `
                        <div class="stat-card"><div class="stat-label">初始资金</div><div class="stat-value">¥${formatMoney(r.initial_capital)}</div></div>
                        <div class="stat-card"><div class="stat-label">最终市值</div><div class="stat-value" style="color:${profitColor}">¥${formatMoney(finalValue)}</div></div>
                        <div class="stat-card"><div class="stat-label">总收益率</div><div class="stat-value" style="color:${profitColor};font-size:24px">${returnPct >= 0 ? '+' : ''}${returnPct.toFixed(2)}%</div></div>
                        <div class="stat-card"><div class="stat-label">年化收益</div><div class="stat-value" style="color:${(r.annual_return_pct||0) >= 0 ? 'var(--red)' : 'var(--green)'}">${r.annual_return_pct != null ? (r.annual_return_pct >= 0 ? '+' : '') + r.annual_return_pct.toFixed(2) + '%' : '--'}</div></div>
                        <div class="stat-card"><div class="stat-label">最大回撤</div><div class="stat-value" style="color:var(--red)">${r.max_drawdown_pct != null ? r.max_drawdown_pct.toFixed(2) + '%' : '--'}</div></div>
                        <div class="stat-card"><div class="stat-label">夏普比率</div><div class="stat-value">${r.sharpe_ratio != null ? r.sharpe_ratio.toFixed(2) : '--'}</div></div>
                        <div class="stat-card"><div class="stat-label">年化波动率</div><div class="stat-value">${r.volatility_pct != null ? r.volatility_pct.toFixed(2) + '%' : '--'}</div></div>
                        <div class="stat-card"><div class="stat-label">调仓次数</div><div class="stat-value">${r.total_rebalances || '--'}</div></div>
                    `;
                    document.getElementById('bt-result-panel').scrollIntoView({ behavior: 'smooth', block: 'start' });
                }
            }
        }).catch(err => console.error("Poll failed", err));
    }, 2000);
}

function runBacktest() {
    const strategyKey = document.getElementById('bt-strategy-select').value;
    const indexCode = document.getElementById('bt-index').value;
    const startDate = document.getElementById('bt-start-date').value;
    const endDate = document.getElementById('bt-end-date').value;
    const capital = parseInt(document.getElementById('bt-capital').value);
    const topN = parseInt(document.getElementById('bt-topn').value);

    const stampTax = parseFloat(document.getElementById('bt-stamp-tax').value || 0.05) / 100;
    const commission = parseFloat(document.getElementById('bt-commission').value || 0.03) / 100;
    const slippage = parseFloat(document.getElementById('bt-slippage').value || 0.3) / 100;

    if (!strategyKey) {
        showToast('请先选择一个策略', 'warning');
        return;
    }
    if (!startDate || !endDate) {
        showToast('请设置回测开始和结束日期', 'warning');
        return;
    }
    if (startDate >= endDate) {
        showToast('开始日期必须早于结束日期', 'warning');
        return;
    }

    fetch('/api/portfolio/backtest/async', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            strategy_key: strategyKey, index_code: indexCode,
            start_date: startDate, end_date: endDate,
            initial_capital: capital, top_n: topN,
            stamp_tax_rate: stampTax, commission_rate: commission, slippage_rate: slippage
        })
    }).then(r => r.json()).then(res => {
        if (res.task_id) {
            showToast('已派发组合回测任务（支持多任务并发）', 'success');
            // 依赖服务端直接产生一条 running 的数据，所以直接延时一秒钟重刷列表即可
            setTimeout(loadBacktestHistory, 500);
        } else {
            showToast('获取任务ID失败', 'error');
        }
    }).catch(err => {
        showToast('请求失败:' + err.message, 'error');
    });
}

async function loadBacktestHistory() {
    const tbody = document.getElementById('bt-history-body');
    try {
        const resp = await fetch('/api/backtest/history');
        const data = await resp.json();
        let history = data.history || [];

        // 联动：根据当前下拉选择的策略进行过滤
        const selValue = document.getElementById('bt-strategy-select')?.value;
        if (selValue) {
            history = history.filter(h => h.strategy_key === selValue);
        }

        if (!history.length) {
            tbody.innerHTML = '<tr><td colspan="13" style="text-align:center;color:var(--text-muted);padding:30px">📭 暂无此策略的回测记录</td></tr>';
            return;
        }

        tbody.innerHTML = history.map(h => {
            const costInfo = `印花${((h.stamp_tax_rate??0.0005)*100).toFixed(2)}% 佣金${((h.commission_rate??0.0003)*100).toFixed(2)}% 滑点${((h.slippage_rate??0.003)*100).toFixed(1)}%`;

            // ---- 正在执行中的任务 ----
            if (h.status === 'running' || h.status === 'pending') {
                if (h.task_id) monitorBacktestTask(h.task_id, h.strategy_key);
                return `
                <tr style="background:rgba(245, 158, 11, 0.05)">
                    <td style="font-size:12px;color:var(--text-muted);white-space:nowrap">${h.created_at}</td>
                    <td><strong>${h.strategy_name}</strong></td>
                    <td>${h.index_code}</td>
                    <td style="text-align:center;font-size:12px">${h.start_date && h.end_date ? h.start_date + ' ~ ' + h.end_date : (h.days >= 243 ? (h.days / 243).toFixed(1) + '年' : h.days + '天')}</td>
                    <td style="text-align:center">${h.top_n || 30}</td>
                    <td style="text-align:right;font-family:monospace">¥${formatMoney(h.initial_capital)}</td>
                    <td colspan="6" style="text-align:center;color:#f59e0b;">
                        <div style="display:flex;align-items:center;justify-content:center;gap:6px;">
                            <div style="width:12px;height:12px;border:2px solid currentColor;border-top-color:transparent;border-radius:50%;animation:spin 0.8s linear infinite;"></div>
                            <span>任务执行中...</span>
                            <button onclick="stopBacktestTask('${h.task_id}')" style="background:rgba(239,68,68,0.1);color:var(--red);border:1px solid var(--red);padding:2px 8px;border-radius:4px;cursor:pointer;margin-left:10px;font-size:11px">停止</button>
                        </div>
                    </td>
                    <td style="text-align:center"><button onclick="deleteBacktestRecord(${h.id})" title="删除" style="background:none;border:none;color:var(--text-muted);cursor:pointer;font-size:14px;opacity:0.5" onmouseover="this.style.opacity=1;this.style.color='#ef4444'" onmouseout="this.style.opacity=0.5;this.style.color='var(--text-muted)'">🗑️</button></td>
                </tr>`;
            }

            // ---- 失败的任务 ----
            if (h.status === 'failed') {
                return `
                <tr style="opacity:0.6">
                    <td style="font-size:12px;color:var(--text-muted);white-space:nowrap">${h.created_at}</td>
                    <td><strong>${h.strategy_name}</strong></td>
                    <td>${h.index_code}</td>
                    <td style="text-align:center;font-size:12px">${h.start_date && h.end_date ? h.start_date + ' ~ ' + h.end_date : (h.days >= 243 ? (h.days / 243).toFixed(1) + '年' : h.days + '天')}</td>
                    <td style="text-align:center">${h.top_n || 30}</td>
                    <td style="text-align:right;font-family:monospace">¥${formatMoney(h.initial_capital)}</td>
                    <td colspan="6" style="text-align:center;color:#ef4444;">❌ 回测失败</td>
                    <td style="text-align:center"><button onclick="deleteBacktestRecord(${h.id})" title="删除此失败记录" style="background:rgba(239,68,68,0.1);border:1px solid #ef4444;color:#ef4444;cursor:pointer;font-size:12px;padding:3px 10px;border-radius:4px" onmouseover="this.style.background='#ef4444';this.style.color='#fff'" onmouseout="this.style.background='rgba(239,68,68,0.1)';this.style.color='#ef4444'">删除</button></td>
                </tr>`;
            }

            // ---- 已完成的任务（正常渲染） ----
            const pct = h.return_pct || 0;
            const color = pct >= 0 ? 'var(--red)' : 'var(--green)';
            const arrow = pct >= 0 ? '▲' : '▼';
            const annPct = h.annual_return_pct || 0;
            const annColor = annPct >= 0 ? 'var(--red)' : 'var(--green)';
            return `
            <tr>
                <td style="font-size:12px;color:var(--text-muted);white-space:nowrap">${h.created_at}</td>
                <td><strong>${h.strategy_name}</strong></td>
                <td>${h.index_code}</td>
                <td style="text-align:center;font-size:12px">${h.start_date && h.end_date ? h.start_date + ' ~ ' + h.end_date : (h.days >= 243 ? (h.days / 243).toFixed(1) + '年' : h.days + '天')}</td>
                <td style="text-align:center">${h.top_n || 30}</td>
                <td style="text-align:right;font-family:monospace">¥${formatMoney(h.initial_capital)}</td>
                <td style="text-align:right;font-family:monospace">¥${formatMoney(h.final_value)}</td>
                <td style="text-align:right;color:${color};font-weight:bold">${arrow} ${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%</td>
                <td style="text-align:right;color:${annColor}">${annPct >= 0 ? '+' : ''}${annPct.toFixed(2)}%</td>
                <td style="text-align:right;color:var(--red)">${h.max_drawdown_pct != null ? h.max_drawdown_pct.toFixed(2) + '%' : '--'}</td>
                <td style="text-align:right">${h.sharpe_ratio != null ? h.sharpe_ratio.toFixed(2) : '--'}</td>
                <td style="font-size:11px;color:var(--text-muted);white-space:nowrap">${costInfo}</td>
                <td style="text-align:center"><button onclick="deleteBacktestRecord(${h.id})" title="删除" style="background:none;border:none;color:var(--text-muted);cursor:pointer;font-size:14px;opacity:0.4" onmouseover="this.style.opacity=1;this.style.color='#ef4444'" onmouseout="this.style.opacity=0.4;this.style.color='var(--text-muted)'">🗑️</button></td>
            </tr>`;
        }).join('');
    } catch(e) {
        console.error('loadBacktestHistory error:', e);
        tbody.innerHTML = '<tr><td colspan="13" style="text-align:center;color:var(--text-muted);padding:30px">加载失败</td></tr>';
    }
}

function deleteBacktestRecord(recordId) {
    showConfirm('确定要删除这条回测记录吗？此操作不可撤销。', async () => {
        try {
            const resp = await fetch(`/api/backtest/history/${recordId}`, { method: 'DELETE' });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            showToast('✅ 已删除', 'success');
            loadBacktestHistory();
        } catch (e) {
            showToast('❌ 删除失败: ' + e.message, 'error');
        }
    });
}

// Tab 切换时加载数据
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            if (btn.dataset.tab === 'strategy-manage') loadStrategyManageList();
            if (btn.dataset.tab === 'backtest') loadBacktestPage();
        });
    });
    const savedTab = localStorage.getItem('qt-active-tab');
    if (savedTab === 'strategy-manage') setTimeout(loadStrategyManageList, 300);
    if (savedTab === 'backtest') setTimeout(loadBacktestPage, 300);
});


/* ========================================================
   极限矩阵压测 (Matrix Lab)
   ======================================================== */
let matrixPollInterval = null;
let currentMatrixTaskId = null;

async function startMatrixTask() {
    const btn = document.getElementById('matrix-start-btn');
    const stopBtn = document.getElementById('matrix-stop-btn');
    const capitals = document.getElementById('matrix-capitals').value.split(',').map(s=>parseInt(s.trim())).filter(x=>!isNaN(x));
    const years = document.getElementById('matrix-years').value.split(',').map(s=>parseInt(s.trim())).filter(x=>!isNaN(x));
    const strats = document.getElementById('matrix-strats').value.split(',').map(s=>s.trim()).filter(x=>x);

    if(!capitals.length || !years.length || !strats.length) {
        alert("请输入有效的逗号分隔参数！");
        return;
    }

    btn.disabled = true;
    btn.innerHTML = '🚀 系统正在启动推进器...';
    document.getElementById('matrix-live-status').textContent = '初始化中...';
    document.getElementById('matrix-live-progress').textContent = '0';
    document.getElementById('matrix-live-logs').textContent = '正在呼叫后台计算集群...';
    document.getElementById('matrix-results-body').innerHTML = '<tr><td colspan="6" style="text-align:center; padding:30px; color:var(--text-muted);">正在进行极限矩阵推演...</td></tr>';

    try {
        const resp = await fetch('/api/matrix/start', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ capitals, years, strats })
        });
        const res = await resp.json();
        if(res.status === 'ok') {
            currentMatrixTaskId = res.task_id;
            if(stopBtn) stopBtn.style.display = 'inline-block';
            startMatrixPolling();
            setTimeout(loadMatrixHistoryList, 2000);
        } else {
            throw new Error(res.error || '无法启动任务');
        }
    } catch(e) {
        alert("启动失败: " + e.message);
        btn.disabled = false;
        btn.innerHTML = '🚀 启动压测引擎';
    }
}

async function stopMatrixTask() {
    // 如果当前没有跟踪的 taskId，尝试从服务器找正在运行的任务
    if(!currentMatrixTaskId) {
        try {
            const resp = await fetch('/api/matrix/tasks');
            const data = await resp.json();
            if(data.tasks) {
                const running = data.tasks.find(t => t.status === 'running' || t.status === 'pending');
                if(running) currentMatrixTaskId = running.id;
            }
        } catch(e) {}
    }
    if(!currentMatrixTaskId) { alert('没有正在运行的压测任务'); return; }

    const stopBtn = document.getElementById('matrix-stop-btn');
    stopBtn.disabled = true;
    stopBtn.innerHTML = '⛔ 正在停止...';
    try {
        await fetch(`/api/matrix/stop/${currentMatrixTaskId}`, { method: 'POST' });
        if(matrixPollInterval) { clearInterval(matrixPollInterval); matrixPollInterval = null; }
        const startBtn = document.getElementById('matrix-start-btn');
        if(startBtn) { startBtn.disabled = false; startBtn.innerHTML = '🚀 启动压测引擎'; }
        document.getElementById('matrix-live-status').innerHTML = '<span style="color:var(--red)">已停止</span>';
        currentMatrixTaskId = null;
        loadMatrixHistoryList();
    } catch(e) { console.error(e); }
    stopBtn.disabled = false;
    stopBtn.innerHTML = '⛔ 停止';
}

function startMatrixPolling() {
    if(matrixPollInterval) clearInterval(matrixPollInterval);
    matrixPollInterval = setInterval(pollMatrixTaskDetail, 1000);
}

async function pollMatrixTaskDetail(taskIdOverride = null) {
    const tid = taskIdOverride || currentMatrixTaskId;
    if(!tid) return;

    try {
        const resp = await fetch(`/api/matrix/task/${tid}`);
        const data = await resp.json();
        if(data.status === 'ok' && data.task) {
            const task = data.task;

            // 更新 UI 看板
            document.getElementById('matrix-live-status').innerHTML =
                task.status === 'running' ? '<span style="color:#f59e0b;animation:blink 1s infinite">推演计算中...</span>' :
                task.status === 'completed' ? '<span style="color:var(--green)">已完成</span>' :
                task.status === 'failed' ? '<span style="color:var(--red)">任务崩溃</span>' : '排队中';

            document.getElementById('matrix-live-progress').textContent = task.progress || 0;

            const logEl = document.getElementById('matrix-live-logs');
            if(task.logs) {
                if(logEl.textContent !== task.logs && logEl.scrollHeight - logEl.scrollTop < logEl.clientHeight + 50) {
                    logEl.textContent = task.logs;
                    logEl.scrollTop = logEl.scrollHeight;
                } else if(logEl.textContent !== task.logs) {
                    logEl.textContent = task.logs;
                }
            }

            // 如果完成或失败，停止轮询，恢复按钮，并渲染结果
            if (task.status === 'completed' || task.status === 'failed') {
                if(!taskIdOverride && matrixPollInterval) {
                    clearInterval(matrixPollInterval);
                    matrixPollInterval = null;
                    const btn = document.getElementById('matrix-start-btn');
                    if(btn) {
                        btn.disabled = false;
                        btn.innerHTML = '🚀 重新启动压测引擎';
                    }
                    const stopBtn = document.getElementById('matrix-stop-btn');
                    if(stopBtn) stopBtn.style.display = 'none';
                    loadMatrixHistoryList();
                }

                if (task.results && task.results.length > 0) {
                    renderMatrixResults(task.results);
                }
            }
        }
    } catch(e) { console.error('Poll matrix task failed:', e); }
}

function renderMatrixResults(results) {
    const tbody = document.getElementById('matrix-results-body');
    if(!results || !results.length) return;

    tbody.innerHTML = results.map(r => {
        const isBust = parseFloat(r.total_return) < 0;
        const color = isBust ? 'var(--green)' : 'var(--red)';
        return `
        <tr style="${isBust ? 'background:rgba(72, 187, 120, 0.05);' : ''}">
            <td style="font-weight:600">${r.model}</td>
            <td style="text-align:center">${r.years}</td>
            <td style="text-align:right; font-family:monospace">${r.capital}</td>
            <td style="text-align:right; font-family:monospace; color:${color}; font-weight:bold">${r.total_return}</td>
            <td style="text-align:right; font-family:monospace; color:var(--red)">${r.max_drawdown}</td>
            <td style="text-align:right; font-family:monospace">${r.friction} <small style="color:var(--text-muted)">(${r.friction_ratio})</small></td>
        </tr>
        `;
    }).join('');
}

async function loadMatrixHistoryList() {
    try {
        const resp = await fetch('/api/matrix/tasks');
        const data = await resp.json();
        const container = document.getElementById('matrix-history-list');
        if(!container) return;
        if(data.status === 'ok' && data.tasks && data.tasks.length > 0) {
            container.innerHTML = data.tasks.map(t => {
                const isRunning = t.status === 'running';
                const isDone = t.status === 'completed';
                const statusColor = isRunning ? '#f59e0b' : isDone ? 'var(--green)' : 'var(--red)';
                const icon = isRunning ? '⏳' : isDone ? '✅' : '❌';
                return `
                <div style="background:var(--bg-secondary); border:1px solid var(--border); padding:10px; border-radius:6px; cursor:pointer; display:flex; justify-content:space-between; align-items:center"
                     onclick="viewMatrixHistory('${t.id}')">
                    <div style="display:flex; flex-direction:column; gap:4px;">
                        <span style="font-weight:600; color:var(--text-primary)">${t.task_name}</span>
                        <span style="font-size:11px; color:var(--text-muted)">${t.created_at}</span>
                    </div>
                    <div style="text-align:right; display:flex; flex-direction:column; align-items:flex-end;">
                        <span style="color:${statusColor}; font-size:12px; font-weight:500">${icon} ${t.status}</span>
                        <span style="font-size:11px; color:var(--text-muted)">进度: ${t.progress}%</span>
                    </div>
                </div>
                `;
            }).join('');
        } else {
            container.innerHTML = '<div style="color:var(--text-muted); text-align:center; padding:20px;">暂无历史实验记录</div>';
        }
    } catch(e) { console.error('加载压测历史失败', e); }
}

function viewMatrixHistory(taskId) {
    if(matrixPollInterval) clearInterval(matrixPollInterval);
    currentMatrixTaskId = taskId;

    // reset ui
    document.getElementById('matrix-live-logs').textContent = '加载历史档案中...';
    document.getElementById('matrix-results-body').innerHTML = '<tr><td colspan="6" style="text-align:center; color:var(--text-muted);">正在加载报表...</td></tr>';

    pollMatrixTaskDetail(taskId);
}

// 绑定 Tab 切换事件以加载历史
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            if (btn.dataset.tab === 'matrix-lab') loadMatrixHistoryList();
            if (btn.dataset.tab !== 'matrix-lab' && matrixPollInterval) {
                clearInterval(matrixPollInterval);
                matrixPollInterval = null;
            }
        });
    });
    // 页面加载时，如果当前 Tab 就是矩阵压测，自动拉取历史
    const savedTab = localStorage.getItem('qt-active-tab');
    if (savedTab === 'matrix-lab') setTimeout(loadMatrixHistoryList, 300);
});


// ===== K线查看器 =====
let _klineData = [];       // 原始K线数据
let _klineMAVisible = { 5: true, 10: true, 20: true, 60: false };
const _klineMAColors = { 5: '#ef8b2c', 10: '#1b9af7', 20: '#e040fb', 60: '#00e676' };

async function loadKlineChart() {
    const symbol = document.getElementById('kline-symbol-input').value.trim();
    if (!symbol) { showToast('请输入股票代码', 'error'); return; }
    const days = parseInt(document.getElementById('kline-days-select').value) || 250;
    const btn = document.getElementById('kline-load-btn');
    btn.disabled = true; btn.textContent = '⏳ 加载中...';

    try {
        const resp = await fetch(`/api/stock/kline/${symbol}?days=${days}`);
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${resp.status}`);
        }
        const data = await resp.json();
        _klineData = data.klines || [];

        // 更新股票信息
        document.getElementById('kline-stock-info').style.display = 'inline';
        document.getElementById('kline-stock-name').textContent = data.name || symbol;
        document.getElementById('kline-stock-code').textContent = symbol;
        document.getElementById('kline-stock-count').textContent = `共 ${data.count} 根K线`;
        document.getElementById('kline-empty-state').style.display = 'none';

        renderKlineChart();
        showToast(`✅ ${data.name}(${symbol}) 加载完成`, 'success');
    } catch (e) {
        showToast(`❌ 加载失败: ${e.message}`, 'error');
    } finally {
        btn.disabled = false; btn.textContent = '🔍 查看K线';
    }
}

function toggleMA(btn, period) {
    _klineMAVisible[period] = !_klineMAVisible[period];
    btn.classList.toggle('active');
    if (_klineMAVisible[period]) {
        btn.style.background = `rgba(${period===5?'239,139,44':period===10?'27,154,247':period===20?'224,64,251':'0,230,118'}, 0.1)`;
    } else {
        btn.style.background = 'transparent';
    }
    if (_klineData.length) renderKlineChart();
}

function _calcMA(data, period) {
    const result = [];
    for (let i = 0; i < data.length; i++) {
        if (i < period - 1) { result.push(null); continue; }
        let sum = 0;
        for (let j = i - period + 1; j <= i; j++) sum += data[j].close;
        result.push(sum / period);
    }
    return result;
}

function renderKlineChart() {
    if (!_klineData.length) return;
    const kCanvas = document.getElementById('kline-canvas');
    const vCanvas = document.getElementById('kline-volume-canvas');
    const dpr = window.devicePixelRatio || 1;

    // 设置 canvas 尺寸
    const kRect = kCanvas.getBoundingClientRect();
    kCanvas.width = kRect.width * dpr;
    kCanvas.height = kRect.height * dpr;
    const vRect = vCanvas.getBoundingClientRect();
    vCanvas.width = vRect.width * dpr;
    vCanvas.height = vRect.height * dpr;

    const kCtx = kCanvas.getContext('2d');
    const vCtx = vCanvas.getContext('2d');
    kCtx.scale(dpr, dpr);
    vCtx.scale(dpr, dpr);

    const W = kRect.width, H = kRect.height;
    const VH = vRect.height;
    const data = _klineData;
    const n = data.length;

    const padL = 60, padR = 20, padT = 10, padB = 24;
    const chartW = W - padL - padR;
    const chartH = H - padT - padB;
    const volChartH = VH - 4 - padB;

    const candleW = Math.max(1, Math.min(12, (chartW / n) * 0.7));
    const gap = chartW / n;

    // 价格范围
    let minP = Infinity, maxP = -Infinity;
    let maxVol = 0;
    data.forEach(d => {
        if (d.low < minP) minP = d.low;
        if (d.high > maxP) maxP = d.high;
        if (d.volume > maxVol) maxVol = d.volume;
    });
    const pRange = maxP - minP || 1;
    const priceY = (p) => padT + chartH - ((p - minP) / pRange * chartH);
    const volY = (v) => 4 + volChartH - (v / (maxVol || 1) * volChartH);

    // 清空
    kCtx.clearRect(0, 0, W, H);
    vCtx.clearRect(0, 0, vRect.width, VH);

    // 背景网格 + 价格标签
    kCtx.strokeStyle = 'rgba(255,255,255,0.05)';
    kCtx.lineWidth = 0.5;
    kCtx.font = '10px monospace';
    kCtx.fillStyle = 'rgba(255,255,255,0.35)';
    const gridLines = 6;
    for (let i = 0; i <= gridLines; i++) {
        const y = padT + (chartH / gridLines) * i;
        kCtx.beginPath(); kCtx.moveTo(padL, y); kCtx.lineTo(W - padR, y); kCtx.stroke();
        const price = maxP - (pRange / gridLines) * i;
        kCtx.fillText(price.toFixed(2), 4, y + 3);
    }

    // 日期标签
    kCtx.fillStyle = 'rgba(255,255,255,0.3)';
    const dateInterval = Math.max(1, Math.floor(n / 8));
    for (let i = 0; i < n; i += dateInterval) {
        const x = padL + i * gap + gap / 2;
        kCtx.fillText(data[i].date.slice(5), x - 14, H - 4);
        vCtx.fillStyle = 'rgba(255,255,255,0.3)';
        vCtx.font = '10px monospace';
        vCtx.fillText(data[i].date.slice(5), x - 14, VH - 4);
    }

    // 画蜡烛
    const upColor = '#ef5350';    // 红涨
    const downColor = '#26a69a';  // 绿跌

    for (let i = 0; i < n; i++) {
        const d = data[i];
        const x = padL + i * gap + gap / 2;
        const isUp = d.close >= d.open;
        const color = isUp ? upColor : downColor;

        // 影线
        kCtx.strokeStyle = color;
        kCtx.lineWidth = 1;
        kCtx.beginPath();
        kCtx.moveTo(x, priceY(d.high));
        kCtx.lineTo(x, priceY(d.low));
        kCtx.stroke();

        // 实体
        const top = priceY(Math.max(d.open, d.close));
        const bot = priceY(Math.min(d.open, d.close));
        const bodyH = Math.max(1, bot - top);

        if (isUp) {
            kCtx.strokeStyle = color;
            kCtx.lineWidth = 1;
            kCtx.strokeRect(x - candleW / 2, top, candleW, bodyH);
        } else {
            kCtx.fillStyle = color;
            kCtx.fillRect(x - candleW / 2, top, candleW, bodyH);
        }

        // 成交量柱子
        const vy = volY(d.volume);
        vCtx.fillStyle = isUp ? 'rgba(239,83,80,0.5)' : 'rgba(38,166,154,0.5)';
        vCtx.fillRect(x - candleW / 2, vy, candleW, volChartH - vy + 4);
    }

    // 画均线
    const maData = {};
    [5, 10, 20, 60].forEach(p => {
        if (_klineMAVisible[p]) maData[p] = _calcMA(data, p);
    });

    Object.entries(maData).forEach(([period, values]) => {
        kCtx.strokeStyle = _klineMAColors[period];
        kCtx.lineWidth = 1.2;
        kCtx.beginPath();
        let started = false;
        values.forEach((v, i) => {
            if (v === null) return;
            const x = padL + i * gap + gap / 2;
            const y = priceY(v);
            if (!started) { kCtx.moveTo(x, y); started = true; }
            else kCtx.lineTo(x, y);
        });
        kCtx.stroke();
    });

    // 十字光标 hover
    kCanvas.onmousemove = (e) => {
        const rect = kCanvas.getBoundingClientRect();
        const mx = e.clientX - rect.left;
        const idx = Math.floor((mx - padL) / gap);
        if (idx < 0 || idx >= n) return;
        const d = data[idx];
        const info = document.getElementById('kline-crosshair-info');
        const chg = d.change_pct >= 0 ? `+${d.change_pct.toFixed(2)}%` : `${d.change_pct.toFixed(2)}%`;
        const chgColor = d.change_pct >= 0 ? '#ef5350' : '#26a69a';
        info.innerHTML = `${d.date} | 开:${d.open} 高:${d.high} 低:${d.low} 收:${d.close} | <span style="color:${chgColor}">${chg}</span> | 量:${(d.volume/10000).toFixed(0)}万`;
    };
}

// 窗口大小变化时重绘
window.addEventListener('resize', () => { if (_klineData.length) renderKlineChart(); });


// ===== 宏观仪表盘 =====
let _macroDashboardLoaded = false;

async function loadMacroDashboard() {
    if (_macroDashboardLoaded) return;
    _macroDashboardLoaded = true;

    // 并行加载市场温度 + 宏观指标
    const [tempRes, macroRes] = await Promise.all([
        fetch('/api/macro/market-temp').then(r => r.json()).catch(() => null),
        fetch('/api/macro/indicators').then(r => r.json()).catch(() => null),
    ]);

    // 市场温度图表
    if (tempRes && tempRes.status === 'ok' && tempRes.data.length > 0) {
        const data = tempRes.data;
        const dates = data.map(d => d.date.slice(5)); // MM-DD
        const ztValues = data.map(d => d.zt_count);
        const dtValues = data.map(d => d.dt_count);
        const northValues = data.map(d => d.north_net_flow);
        const marginValues = data.map(d => d.margin_balance);

        // 涨停/跌停柱状图
        _drawBarChart('macro-chart-zt', dates, [
            { values: ztValues, color: 'rgba(239, 68, 68, 0.7)', label: '涨停' },
            { values: dtValues, color: 'rgba(34, 197, 94, 0.7)', label: '跌停' },
        ], { threshold: 30, thresholdLabel: '杀跌线(30)', thresholdColor: 'rgba(239, 68, 68, 0.5)' });

        // 北向资金柱状图 (正负着色)
        _drawFlowChart('macro-chart-north', dates, northValues);

        // 两融余额折线图
        _drawLineChart('macro-chart-margin', dates, marginValues, {
            color: 'rgba(59, 130, 246, 0.8)', fillColor: 'rgba(59, 130, 246, 0.08)',
            unit: '亿', decimals: 0,
        });
    }

    // 宏观指标
    const container = document.getElementById('macro-indicators-container');
    if (macroRes && macroRes.status === 'ok') {
        const indicators = macroRes.data;
        let html = '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px">';
        for (const [key, ind] of Object.entries(indicators)) {
            const canvasId = `macro-ind-${key}`;
            html += `
                <div class="card" style="padding:16px">
                    <div style="font-weight:600;margin-bottom:4px">${ind.name}</div>
                    <p style="font-size:12px;color:var(--text-secondary);margin:0 0 8px">${ind.description}</p>
                    <canvas id="${canvasId}" height="180"></canvas>
                </div>
            `;
        }
        html += '</div>';
        container.innerHTML = html;

        // 延迟渲染确保 canvas 已挂载
        requestAnimationFrame(() => {
            for (const [key, ind] of Object.entries(indicators)) {
                const dates = ind.dates.map(d => {
                    // 格式化日期：取月份部分
                    const parts = d.split(/[-年月日]/g).filter(Boolean);
                    return parts.length >= 2 ? `${parts[0].slice(-2)}/${parts[1]}` : d.slice(-5);
                });
                _drawLineChart(`macro-ind-${key}`, dates, ind.values, {
                    color: key === 'cpi' ? 'rgba(239, 68, 68, 0.8)' :
                           key === 'ppi' ? 'rgba(245, 158, 11, 0.8)' :
                           'rgba(59, 130, 246, 0.8)',
                    fillColor: key === 'cpi' ? 'rgba(239, 68, 68, 0.06)' :
                               key === 'ppi' ? 'rgba(245, 158, 11, 0.06)' :
                               'rgba(59, 130, 246, 0.06)',
                    unit: '%', decimals: 1, showZeroLine: true,
                });
            }
        });
    } else {
        container.innerHTML = '<p style="color:var(--text-secondary)">宏观数据加载失败，请稍后重试</p>';
    }
}

// --- 通用Canvas绘图工具 ---

function _drawLineChart(canvasId, labels, values, opts = {}) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);
    const W = rect.width, H = rect.height;
    const pad = { top: 20, right: 12, bottom: 30, left: 50 };
    const cW = W - pad.left - pad.right;
    const cH = H - pad.top - pad.bottom;

    const clean = values.map(v => v == null ? NaN : +v).filter(v => !isNaN(v));
    if (clean.length === 0) { ctx.fillStyle = '#888'; ctx.fillText('暂无数据', W/2-30, H/2); return; }
    let minV = Math.min(...clean), maxV = Math.max(...clean);
    const range = maxV - minV || 1;
    minV -= range * 0.08; maxV += range * 0.08;

    const toX = i => pad.left + (i / (values.length - 1)) * cW;
    const toY = v => pad.top + (1 - (v - minV) / (maxV - minV)) * cH;

    // 网格线
    ctx.strokeStyle = 'rgba(128,128,128,0.15)';
    ctx.lineWidth = 0.5;
    for (let i = 0; i <= 4; i++) {
        const y = pad.top + (i / 4) * cH;
        ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(W - pad.right, y); ctx.stroke();
        const val = maxV - (i / 4) * (maxV - minV);
        ctx.fillStyle = 'rgba(128,128,128,0.6)'; ctx.font = '10px system-ui';
        ctx.textAlign = 'right';
        ctx.fillText(`${val.toFixed(opts.decimals ?? 1)}${opts.unit || ''}`, pad.left - 4, y + 3);
    }

    // X 轴标签
    ctx.fillStyle = 'rgba(128,128,128,0.6)'; ctx.font = '9px system-ui'; ctx.textAlign = 'center';
    const step = Math.max(1, Math.floor(labels.length / 8));
    for (let i = 0; i < labels.length; i += step) {
        ctx.fillText(labels[i], toX(i), H - pad.bottom + 14);
    }

    // 零线
    if (opts.showZeroLine && minV < 0 && maxV > 0) {
        const y0 = toY(0);
        ctx.strokeStyle = 'rgba(128,128,128,0.4)'; ctx.lineWidth = 1; ctx.setLineDash([4, 3]);
        ctx.beginPath(); ctx.moveTo(pad.left, y0); ctx.lineTo(W - pad.right, y0); ctx.stroke();
        ctx.setLineDash([]);
    }

    // 填充区
    ctx.beginPath();
    let started = false;
    for (let i = 0; i < values.length; i++) {
        const v = values[i] == null ? NaN : +values[i];
        if (isNaN(v)) continue;
        if (!started) { ctx.moveTo(toX(i), toY(v)); started = true; }
        else ctx.lineTo(toX(i), toY(v));
    }
    const lastIdx = values.length - 1;
    ctx.lineTo(toX(lastIdx), pad.top + cH);
    for (let i = 0; i < values.length; i++) {
        if (values[i] != null) { ctx.lineTo(toX(i), pad.top + cH); break; }
    }
    ctx.closePath();
    ctx.fillStyle = opts.fillColor || 'rgba(59,130,246,0.08)';
    ctx.fill();

    // 折线
    ctx.beginPath();
    started = false;
    for (let i = 0; i < values.length; i++) {
        const v = values[i] == null ? NaN : +values[i];
        if (isNaN(v)) continue;
        if (!started) { ctx.moveTo(toX(i), toY(v)); started = true; }
        else ctx.lineTo(toX(i), toY(v));
    }
    ctx.strokeStyle = opts.color || 'rgba(59,130,246,0.8)';
    ctx.lineWidth = 1.5;
    ctx.stroke();

    // 最后一个值标注
    const lastVal = clean[clean.length - 1];
    const lastX = toX(values.length - 1);
    const lastY = toY(lastVal);
    ctx.beginPath(); ctx.arc(lastX, lastY, 3, 0, Math.PI * 2);
    ctx.fillStyle = opts.color || 'rgba(59,130,246,0.8)'; ctx.fill();
    ctx.fillStyle = opts.color || 'rgba(59,130,246,1)'; ctx.font = 'bold 11px system-ui'; ctx.textAlign = 'left';
    ctx.fillText(`${lastVal.toFixed(opts.decimals ?? 1)}${opts.unit || ''}`, lastX + 6, lastY + 3);
}

function _drawBarChart(canvasId, labels, series, opts = {}) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);
    const W = rect.width, H = rect.height;
    const pad = { top: 20, right: 12, bottom: 30, left: 40 };
    const cW = W - pad.left - pad.right;
    const cH = H - pad.top - pad.bottom;

    const allVals = series.flatMap(s => s.values.filter(v => v != null).map(Number));
    if (allVals.length === 0) return;
    let maxV = Math.max(...allVals, opts.threshold || 0);
    maxV *= 1.15;

    const toY = v => pad.top + (1 - v / maxV) * cH;
    const barW = (cW / labels.length) * 0.35;

    // Y轴刻度
    ctx.fillStyle = 'rgba(128,128,128,0.6)'; ctx.font = '10px system-ui'; ctx.textAlign = 'right';
    for (let i = 0; i <= 4; i++) {
        const val = maxV * (1 - i / 4);
        const y = pad.top + (i / 4) * cH;
        ctx.strokeStyle = 'rgba(128,128,128,0.12)'; ctx.lineWidth = 0.5;
        ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(W - pad.right, y); ctx.stroke();
        ctx.fillText(Math.round(val), pad.left - 4, y + 3);
    }

    // 柱子
    for (let i = 0; i < labels.length; i++) {
        const x0 = pad.left + (i / labels.length) * cW;
        series.forEach((s, si) => {
            const val = s.values[i];
            if (val == null) return;
            const x = x0 + si * barW + 1;
            const y = toY(+val);
            const h = pad.top + cH - y;
            ctx.fillStyle = s.color;
            ctx.fillRect(x, y, barW - 2, h);
        });
    }

    // 阈值线
    if (opts.threshold) {
        const y = toY(opts.threshold);
        ctx.strokeStyle = opts.thresholdColor || 'red'; ctx.lineWidth = 1; ctx.setLineDash([5, 3]);
        ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(W - pad.right, y); ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = opts.thresholdColor || 'red'; ctx.font = '10px system-ui'; ctx.textAlign = 'left';
        ctx.fillText(opts.thresholdLabel || '', pad.left + 4, y - 4);
    }

    // X轴标签
    ctx.fillStyle = 'rgba(128,128,128,0.6)'; ctx.font = '9px system-ui'; ctx.textAlign = 'center';
    const step = Math.max(1, Math.floor(labels.length / 10));
    for (let i = 0; i < labels.length; i += step) {
        const x = pad.left + ((i + 0.5) / labels.length) * cW;
        ctx.fillText(labels[i], x, H - pad.bottom + 14);
    }

    // 图例
    let lx = W - pad.right - 10;
    ctx.font = '10px system-ui'; ctx.textAlign = 'right';
    series.slice().reverse().forEach(s => {
        ctx.fillStyle = s.color;
        ctx.fillRect(lx - 8, pad.top - 12, 8, 8);
        ctx.fillStyle = 'rgba(128,128,128,0.8)';
        ctx.fillText(s.label, lx - 12, pad.top - 4);
        lx -= ctx.measureText(s.label).width + 24;
    });
}

function _drawFlowChart(canvasId, labels, values) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);
    const W = rect.width, H = rect.height;
    const pad = { top: 16, right: 12, bottom: 30, left: 45 };
    const cW = W - pad.left - pad.right;
    const cH = H - pad.top - pad.bottom;

    const clean = values.filter(v => v != null).map(Number);
    if (clean.length === 0) { ctx.fillStyle = '#888'; ctx.fillText('暂无北向数据', W/2-40, H/2); return; }
    const absMax = Math.max(Math.abs(Math.min(...clean)), Math.abs(Math.max(...clean))) * 1.2 || 50;

    const toY = v => pad.top + (1 - (v + absMax) / (2 * absMax)) * cH;
    const barW = cW / values.length - 1;

    // 零线
    const y0 = toY(0);
    ctx.strokeStyle = 'rgba(128,128,128,0.3)'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(pad.left, y0); ctx.lineTo(W - pad.right, y0); ctx.stroke();

    // Y轴
    ctx.fillStyle = 'rgba(128,128,128,0.6)'; ctx.font = '10px system-ui'; ctx.textAlign = 'right';
    for (const mul of [-1, -0.5, 0, 0.5, 1]) {
        const val = absMax * mul;
        const y = toY(val);
        ctx.fillText(val.toFixed(0), pad.left - 4, y + 3);
    }

    // 柱子 (正=红，负=绿)
    for (let i = 0; i < values.length; i++) {
        if (values[i] == null) continue;
        const val = +values[i];
        const x = pad.left + (i / values.length) * cW;
        ctx.fillStyle = val >= 0 ? 'rgba(239, 68, 68, 0.65)' : 'rgba(34, 197, 94, 0.65)';
        const top = val >= 0 ? toY(val) : y0;
        const h = Math.abs(toY(val) - y0);
        ctx.fillRect(x, top, barW, h);
    }

    // X轴
    ctx.fillStyle = 'rgba(128,128,128,0.6)'; ctx.font = '9px system-ui'; ctx.textAlign = 'center';
    const step = Math.max(1, Math.floor(labels.length / 10));
    for (let i = 0; i < labels.length; i += step) {
        const x = pad.left + ((i + 0.5) / values.length) * cW;
        ctx.fillText(labels[i], x, H - pad.bottom + 14);
    }
}

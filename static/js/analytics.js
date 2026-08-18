'use strict';

let STATE = {
    filter_type: 'today',
    custom_start: null,
    custom_end: null,
    data: null,
};

function fmtMoney(n) {
    return '¥' + (Math.round((n || 0) * 100) / 100).toLocaleString('zh-CN');
}
function fmtNum(n) {
    return (Math.round((n || 0) * 100) / 100).toLocaleString('zh-CN');
}
function pct(n) {
    return (Math.round((n || 0) * 10) / 10) + '%';
}

function showStatus(msg, isErr) {
    const el = document.getElementById('statusBar');
    el.innerHTML = `<div class="alert alert-${isErr ? 'danger' : 'success'} alert-dismissible fade show py-2" role="alert">
        ${msg}<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>`;
    if (!isErr) setTimeout(() => { el.innerHTML = ''; }, 3500);
}

async function checkAuth() {
    const r = await fetch('/api/auth/me');
    const d = await r.json();
    if (!d.authenticated) { window.location.href = '/login'; return false; }
    return true;
}

function card(label, value, colorClass, sub) {
    return `<div class="col-6 col-md-3 col-lg-2">
        <div class="card shadow-sm h-100">
            <div class="card-body py-2">
                <div class="small text-muted">${label}</div>
                <div class="fs-5 fw-bold ${colorClass || 'text-dark'}">${value}</div>
                ${sub ? `<div class="small text-muted">${sub}</div>` : ''}
            </div>
        </div>
    </div>`;
}

function renderBusiness(b) {
    const cards = [
        card('今日 GMV', fmtMoney(b.today_gmv), 'text-primary'),
        card('本周 GMV', fmtMoney(b.week_gmv), 'text-dark'),
        card('本月 GMV', fmtMoney(b.month_gmv), 'text-dark'),
        card('月目标完成率', pct(b.month_completion_pct), b.month_completion_pct >= 100 ? 'text-success' : 'text-warning',
            `目标 ${fmtMoney(b.month_target)}`),
        card('平均每日 GMV', fmtMoney(b.avg_daily_gmv), 'text-info'),
        card('预计月底 GMV', fmtMoney(b.forecast_month_end), 'text-dark',
            `剩 ${b.remain_days} 天`),
        card('距目标缺口', fmtMoney(b.month_remaining), 'text-danger',
            b.forecast_gap > 0 ? `预测缺口 ${fmtMoney(b.forecast_gap)}` : `预测超额 ${fmtMoney(-b.forecast_gap)}`),
    ];
    document.getElementById('businessCards').innerHTML = cards.join('');
}

function renderTables(t) {
    const cards = [
        card('总桌数', fmtNum(t.total_sessions) + ' 桌', 'text-dark',
            `已结 ${t.closed_sessions}`),
        card('平均桌时长', fmtNum(t.avg_duration_min) + ' 分', 'text-dark'),
        card('平均客单价', fmtMoney(t.avg_ticket), 'text-primary'),
        card('八口机占比', pct(t.port_8_ratio), 'text-info',
            `${t.port_8_count} 桌`),
        card('四口机占比', pct(t.port_4_ratio), 'text-info',
            `${t.port_4_count} 桌`),
        card('通宵占比', pct(t.overnight_ratio), 'text-warning',
            `${t.overnight_count} 桌`),
        card('区间营收', fmtMoney(t.gmv), 'text-success'),
    ];
    document.getElementById('tablesCards').innerHTML = cards.join('');
}

function renderCustomers(c) {
    const cards = [
        card('玩家总数', fmtNum(c.total_players) + ' 人', 'text-dark'),
        card('30天活跃', fmtNum(c.active_30d) + ' 人', 'text-success'),
        card('区间新增', fmtNum(c.new_players) + ' 人', 'text-info'),
        card('复购玩家', fmtNum(c.repeat_players) + ' 人', 'text-primary'),
        card('流失预警', fmtNum(c.churned_players) + ' 人', 'text-danger'),
        card('A 级数量', fmtNum(c.level_a_count) + ' 人', 'text-warning'),
        card('B 级数量', fmtNum(c.level_b_count) + ' 人', 'text-secondary'),
    ];
    document.getElementById('customersCards').innerHTML = cards.join('');
}

function renderOperations(o) {
    const cards = [
        card('任务完成率', pct(o.task_completion_rate), o.task_completion_rate >= 70 ? 'text-success' : 'text-warning',
            `${o.task_done}/${o.task_total}`),
        card('主动型数量', fmtNum(o.active_initiative) + ' 人', 'text-success'),
        card('被动型数量', fmtNum(o.passive_initiative) + ' 人', 'text-muted'),
        card('常务候选数量', fmtNum(o.organizer_candidates) + ' 人', 'text-primary'),
        card('优秀组合数量', fmtNum(o.best_combinations_count) + ' 个', 'text-success'),
        card('风险组合数量', fmtNum(o.risk_combinations_count) + ' 个', 'text-danger'),
    ];
    document.getElementById('operationsCards').innerHTML = cards.join('');
}

function renderHistorical(d) {
    const b = d.business || {};
    const cards = [
        card('本月实收（历史组局）', fmtMoney(b.historical_payment_month || 0), 'text-success',
            '来自 Excel 导入'),
        card(`${d.filter && d.filter.label || '区间'}实收（历史组局）`, fmtMoney(d.historical_payment_range || 0), 'text-success',
            '随上方筛选变化'),
    ];
    document.getElementById('historicalCards').innerHTML = cards.join('');
}

function renderRange(data) {
    const f = data.filter || {};
    document.getElementById('rangeLabel').textContent =
        `当前：${f.label || ''}（${f.start || ''} ~ ${f.end || ''}）`;
}

async function loadDashboard() {
    if (!(await checkAuth())) return;
    try {
        let url = '/api/analytics/dashboard?filter_type=' + encodeURIComponent(STATE.filter_type);
        if (STATE.filter_type === 'custom') {
            url += '&custom_start=' + encodeURIComponent(STATE.custom_start || '');
            url += '&custom_end=' + encodeURIComponent(STATE.custom_end || '');
        }
        const r = await fetch(url);
        const d = await r.json();
        if (d.status !== 'ok') { showStatus('加载失败：' + (d.message || '未知错误'), true); return; }
        STATE.data = d.data;
        renderRange(d.data);
        renderBusiness(d.data.business);
        renderHistorical(d.data);
        renderTables(d.data.tables);
        renderCustomers(d.data.customers);
        renderOperations(d.data.operations);
    } catch (e) {
        showStatus('加载失败：' + e.message, true);
    }
}

function setFilter(type) {
    STATE.filter_type = type;
    document.querySelectorAll('#filterGroup .btn').forEach(btn => {
        btn.classList.toggle('active', btn.getAttribute('data-filter') === type);
    });
    loadDashboard();
}

function applyCustom() {
    const s = document.getElementById('customStart').value;
    const e = document.getElementById('customEnd').value;
    if (!s || !e) { showStatus('请先选择开始与结束日期', true); return; }
    if (s > e) { showStatus('开始日期不能晚于结束日期', true); return; }
    STATE.custom_start = s;
    STATE.custom_end = e;
    setFilter('custom');
}

function downloadAI() {
    const date = new Date().toISOString().slice(0, 10);
    showStatus('正在生成 AI 分析包（含 6 份 JSON + 分析提示词）…');
    window.location.href = '/api/analytics/export/json?date=' + encodeURIComponent(date);
    setTimeout(() => showStatus('AI 分析包已生成，开始下载'), 800);
}

function downloadExcel() {
    let url = '/api/analytics/export/excel?filter_type=' + encodeURIComponent(STATE.filter_type);
    if (STATE.filter_type === 'custom') {
        url += '&custom_start=' + encodeURIComponent(STATE.custom_start || '');
        url += '&custom_end=' + encodeURIComponent(STATE.custom_end || '');
    }
    showStatus('正在导出 Excel 经营分析…');
    window.location.href = url;
    setTimeout(() => showStatus('Excel 已导出，开始下载'), 800);
}

document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('#filterGroup .btn').forEach(btn => {
        btn.addEventListener('click', () => setFilter(btn.getAttribute('data-filter')));
    });
    loadDashboard();
});

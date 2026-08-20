// 青鹭收银系统 v3.0 - 前端引擎（单人结账版）

let machines = [];
let currentSessionId = null;
let currentSpId = null;      // 当前结账的 session_player id
let currentMachineId = null;
let selectedDiscountId = null;
let checkoutData = null;
let cart = [];
let allProducts = [];
let playerList = [];
let scanConfirmed = false;
let paymentConfirmEnabled = false;   // 是否启用支付网关确认到账
let paymentConfirmed = false;        // 本次扫码是否已确认到账
let paymentConfirmRef = null;        // 支付流水号 out_trade_no
let addPlayerSessionId = null;

// ===== 工具函数 =====
function formatTime(isoString) {
    const d = new Date(isoString);
    return d.toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit'});
}
function formatDateTime(isoString) {
    const d = new Date(isoString);
    return d.toLocaleString('zh-CN', {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'});
}
function formatDuration(minutes) {
    if (!minutes) return '-';
    const h = Math.floor(minutes / 60);
    const m = Math.round(minutes % 60);
    return h > 0 ? `${h}小时${m}分` : `${m}分钟`;
}
function todayStr() { return new Date().toISOString().split('T')[0]; }
function money(n) { return `¥${(n || 0).toFixed(2)}`; }
function round(n) { return Math.round(n * 100) / 100; }
function escapeJs(str) {
    if (!str) return '';
    return String(str).replace(/[\\'"]/g, '\\$&').replace(/\n/g, '\\n').replace(/\r/g, '');
}

// ===== 初始化 =====
document.addEventListener('DOMContentLoaded', () => {
    const page = document.body.dataset.page;
    if (page === 'dashboard') initDashboard();
    else if (page === 'lottery') initLottery();
    else if (page === 'daily') initDaily();
    else if (page === 'settings') initSettings();
    else if (page === 'products') initProducts();
    else if (page === 'players') initPlayers();
    else if (page === 'members') initMembers();
    else if (page === 'users') initUsers();
    else if (page === 'staff_mgmt') initStaffPage();
    else if (page === 'competition') initCompetition();
});

// ===== 台桌总览 =====
function initDashboard() {
    fetchMachines();
    fetchProductsForCheckout();
    setInterval(() => {
        if (!document.querySelector('.modal.show')) fetchMachines();
    }, 30000);
}

async function fetchMachines() {
    try {
        const res = await fetch('/api/machines');
        machines = await res.json();
        renderMachines();
    } catch (e) { console.error('fetchMachines error:', e); }
}

async function fetchProductsForCheckout() {
    try {
        const res = await fetch('/api/products');
        allProducts = await res.json();
    } catch (e) { console.error(e); }
}

function renderMachines() {
    const container = document.getElementById('machineGrid');
    if (!container) return;
    container.innerHTML = machines.map(m => m.session ? renderActiveCard(m) : renderIdleCard(m)).join('');
}

function renderIdleCard(m) {
    return `<div class="machine-card idle">
        <div class="machine-header">
            <span class="machine-name">${m.name}</span>
            <span class="badge-type">${m.type_label}</span>
        </div>
        <div class="machine-status"><span class="status-dot idle"></span> 空闲</div>
        <button class="btn btn-primary w-100 mt-3" onclick="openStartModal(${m.id})">
            <i class="bi bi-play-circle"></i> 开台
        </button>
    </div>`;
}

function renderActiveCard(m) {
    const s = m.session;
    const players = s.players || [];
    const activeCount = s.active_player_count || 0;
    const checkedCount = s.checked_out_count || 0;

    const playerRows = players.map(p => {
        const isPlaying = p.status === 'playing';
        const statusBadge = isPlaying
            ? `<span class="badge bg-success">游戏中</span>`
            : `<span class="badge bg-secondary">已结账</span>`;
        const feeDisplay = isPlaying ? money(p.current_fee) : money(p.grand_total || p.fee || 0);
        const consumeAmt = p.unsettled_product_total || 0;
        const productBadge = consumeAmt > 0 ? `<span class="badge bg-warning text-dark">消费 ${money(consumeAmt)}</span>` : '';
        const checkoutBtn = isPlaying
            ? `<div class="btn-group btn-group-sm">
                 <button class="btn btn-outline-primary" onclick="event.stopPropagation();openPlayerConsumption(${s.id}, ${p.id}, '${escapeJs(p.player_name)}', '${escapeJs(m.name)}')"><i class="bi bi-bag"></i> 消费</button>
                 <button class="btn btn-danger" onclick="event.stopPropagation();openPlayerCheckout(${s.id}, ${p.id})"><i class="bi bi-cash-coin"></i> 结账</button>
               </div>`
            : `<span class="text-muted small">${p.payment_method ? PAYMENT_LABELS[p.payment_method] || p.payment_method : ''}</span>`;

        return `<div class="player-tile ${isPlaying ? '' : 'checked-out'}" ${isPlaying ? `onclick="openPlayerCheckout(${s.id}, ${p.id})"` : ''}>
            <div class="d-flex justify-content-between align-items-center">
                <div>
                    <span class="fw-bold">${p.player_name}</span>
                    ${p.is_organizer ? '<i class="bi bi-star-fill text-warning ms-1" title="组织者"></i>' : ''}
                    ${statusBadge}
                </div>
                <div class="text-end">
                    <div class="text-muted small">${formatDuration(p.elapsed_minutes)} ${formatTime(p.start_time)}</div>
                </div>
            </div>
            <div class="d-flex justify-content-between align-items-center mt-1">
                <div><span class="fw-bold text-primary">${feeDisplay}</span> ${productBadge}</div>
                <div>${checkoutBtn}</div>
            </div>
        </div>`;
    }).join('');

    return `<div class="machine-card active">
        <div class="machine-header">
            <span class="machine-name">${m.name}</span>
            <span class="badge-type">${m.type_label}</span>
        </div>
        <div class="machine-status">
            <span class="status-dot active"></span> 使用中
            <span class="ms-2 small text-muted">${activeCount}人游戏中 / ${checkedCount}人已结</span>
        </div>
        <div class="player-list">${playerRows}</div>
        ${activeCount > 0 ? `<button class="btn btn-outline-primary btn-sm w-100 mt-2" onclick="addPlayerToSession(${s.id}, '${m.name}')">
            <i class="bi bi-person-plus"></i> 加人
        </button>` : ''}
        <button class="btn btn-outline-danger btn-sm w-100 mt-1" onclick="forceCloseSession(${s.id}, '${m.name}')">
            <i class="bi bi-x-octagon"></i> 强制关台
        </button>
    </div>`;
}

const PAYMENT_LABELS = {
    'scan_wechat': '微信扫码', 'scan_alipay': '支付宝扫码',
    'wechat': '微信', 'alipay': '支付宝', 'cash': '现金', 'member': '会员'
};

function toDatetimeLocalValue(isoString) {
    // 把 ISO 时间转成 datetime-local 的 value 格式 (YYYY-MM-DDTHH:MM)
    const d = isoString ? new Date(isoString) : new Date();
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// ===== 开台（含玩家autocomplete）=====
function openStartModal(machineId) {
    const m = machines.find(x => x.id === machineId);
    if (!m) return;
    currentMachineId = machineId;
    document.getElementById('startMachineName').textContent = `${m.name} (${m.type_label})`;
    document.getElementById('startSessionTime').value = toDatetimeLocalValue();
    const container = document.getElementById('playerInputs');
    container.innerHTML = '';
    for (let i = 0; i < Math.min(4, m.max_players); i++) addPlayerRow(m.max_players);
    new bootstrap.Modal(document.getElementById('startModal')).show();
}

function addPlayerRow(maxPlayers) {
    const container = document.getElementById('playerInputs');
    if (maxPlayers && container.children.length >= maxPlayers) return;
    const row = document.createElement('div');
    row.className = 'player-row';
    row.innerHTML = `
        <div style="flex:1;position:relative">
            <input type="text" class="form-control form-control-sm player-name" placeholder="玩家姓名" oninput="onPlayerNameInput(this)">
            <div class="autocomplete-list d-none"></div>
        </div>
        <select class="form-select form-select-sm player-type">
            <option value="active">主动</option>
            <option value="passive">被动</option>
        </select>
        <div class="form-check"><input type="radio" class="form-check-input player-organizer" name="organizer"><label class="form-check-label small">组织</label></div>
        <input type="datetime-local" class="form-control form-control-sm player-start" title="开始时间" style="width:170px">
        <div class="form-check form-check-inline m-0"><input type="checkbox" class="form-check-input player-overnight" title="通宵"><label class="form-check-label small">通宵</label></div>
        <button class="btn btn-sm btn-outline-danger" onclick="this.parentElement.remove()"><i class="bi bi-x"></i></button>
    `;
    // 默认开始时间=弹窗里的开台时间
    const sessionStart = document.getElementById('startSessionTime');
    row.querySelector('.player-start').value = sessionStart ? sessionStart.value : toDatetimeLocalValue();
    container.appendChild(row);
}

let autocompleteTimer = null;
function onPlayerNameInput(input) {
    const val = input.value.trim();
    const listEl = input.parentElement.querySelector('.autocomplete-list');
    if (!listEl) return;
    clearTimeout(autocompleteTimer);
    if (val.length < 1) { listEl.classList.add('d-none'); return; }
    autocompleteTimer = setTimeout(async () => {
        try {
            const res = await fetch(`/api/players/search?name=${encodeURIComponent(val)}`);
            const players = await res.json();
            if (players.length === 0) { listEl.classList.add('d-none'); return; }
            listEl.innerHTML = players.map(p => `
                <div class="autocomplete-item" onclick="selectAutocompletePlayer(this, '${p.name}', ${p.id})">
                    ${p.name}${p.dan ? `<span class="ac-dan">${p.dan}</span>` : ''}${p.is_member ? ' <i class="bi bi-credit-card-2-front text-success"></i>' : ''}
                </div>
            `).join('');
            listEl.classList.remove('d-none');
        } catch (e) { console.error(e); }
    }, 200);
}

function selectAutocompletePlayer(el, name, id) {
    const row = el.closest('.player-row') || el.closest('.modal-body');
    if (row) {
        const nameInput = row.querySelector('.player-name') || document.getElementById('addPlayerName');
        if (nameInput) nameInput.value = name;
        if (row.dataset !== undefined) row.dataset.playerId = id;
        const list = row.querySelector('.autocomplete-list');
        if (list) list.classList.add('d-none');
    }
    // 加人弹窗的特殊处理
    const addInput = document.getElementById('addPlayerName');
    if (addInput && addInput.value === name) {
        addPlayerSessionId_data = id;
    }
}

let addPlayerSessionId_data = null;

document.addEventListener('click', (e) => {
    if (!e.target.closest('.player-row') && !e.target.closest('#addPlayerModal')) {
        document.querySelectorAll('.autocomplete-list').forEach(el => el.classList.add('d-none'));
    }
});

async function confirmStartSession() {
    const rows = document.querySelectorAll('#playerInputs .player-row');
    const players = [];
    let hasOrganizer = false;
    const sessionStart = document.getElementById('startSessionTime').value;
    rows.forEach(row => {
        const name = row.querySelector('.player-name').value.trim();
        if (name) {
            const isOrg = row.querySelector('.player-organizer').checked;
            if (isOrg) hasOrganizer = true;
            players.push({
                name,
                is_organizer: isOrg,
                visit_type: row.querySelector('.player-type').value,
                player_id: row.dataset.playerId ? parseInt(row.dataset.playerId) : null,
                start_time: row.querySelector('.player-start').value || sessionStart,
                is_overnight: row.querySelector('.player-overnight').checked
            });
        }
    });
    if (players.length === 0) { alert('请至少输入一个玩家'); return; }
    if (!hasOrganizer) players[0].is_organizer = true;
    try {
        const res = await fetch('/api/sessions', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ machine_id: currentMachineId, start_time: sessionStart, players })
        });
        if (res.ok) {
            bootstrap.Modal.getInstance(document.getElementById('startModal')).hide();
            fetchMachines();
        } else { const err = await res.json(); alert(err.error || '开台失败'); }
    } catch (e) { alert('网络错误'); }
}

// ===== 加人 =====
function addPlayerToSession(sessionId, machineName) {
    addPlayerSessionId = sessionId;
    addPlayerSessionId_data = null;
    document.getElementById('addPlayerMachine').textContent = machineName;
    document.getElementById('addPlayerName').value = '';
    document.getElementById('addPlayerStartTime').value = toDatetimeLocalValue();
    document.getElementById('addPlayerOvernight').checked = false;
    document.getElementById('addPlayerAutocomplete').classList.add('d-none');
    new bootstrap.Modal(document.getElementById('addPlayerModal')).show();
    setTimeout(() => document.getElementById('addPlayerName').focus(), 300);
}

async function confirmAddPlayer() {
    const name = document.getElementById('addPlayerName').value.trim();
    if (!name) { alert('请输入玩家姓名'); return; }
    const startTime = document.getElementById('addPlayerStartTime').value;
    const isOvernight = document.getElementById('addPlayerOvernight').checked;
    try {
        const res = await fetch(`/api/sessions/${addPlayerSessionId}/players`, {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ name, player_id: addPlayerSessionId_data, start_time: startTime, is_overnight: isOvernight })
        });
        if (res.ok) {
            bootstrap.Modal.getInstance(document.getElementById('addPlayerModal')).hide();
            fetchMachines();
        } else { const err = await res.json(); alert(err.error || '加人失败'); }
    } catch (e) { alert('网络错误'); }
}

// ===== 强制关台 =====
async function forceCloseSession(sessionId, machineName) {
    if (!confirm(`确定要强制关闭「${machineName}」吗？\n\n所有未结账的玩家将被标记为已结账（不计费），台桌将恢复空闲。`)) return;
    try {
        const res = await fetch(`/api/sessions/${sessionId}/force-close`, { method: 'POST' });
        if (res.ok) {
            alert('台桌已强制关闭');
            fetchMachines();
        } else {
            const err = await res.json();
            alert(err.error || '关台失败');
        }
    } catch (e) { alert('网络错误'); }
}

// ===== 单人结账 =====
async function openPlayerCheckout(sessionId, spId) {
    currentSessionId = sessionId;
    currentSpId = spId;
    selectedDiscountId = null;
    cart = [];
    try {
        const res = await fetch(`/api/sessions/${sessionId}/players/${spId}/preview`);
        checkoutData = await res.json();
        document.getElementById('checkoutPlayerName').textContent = checkoutData.player_name;
        document.getElementById('checkoutMachineName').textContent = `${checkoutData.machine_name} (${checkoutData.type_label})`;

        // 开始时间可编辑
        const startInput = document.getElementById('checkoutStartTimeInput');
        startInput.value = toDatetimeLocalValue(checkoutData.start_time);
        startInput.oninput = onCheckoutStartTimeChange;
        startInput.onchange = onCheckoutStartTimeChange;
        document.getElementById('checkoutOvernight').onchange = onCheckoutOvernightChange;
        const btnApply = document.getElementById('btnApplyStartTime');
        if (btnApply) { btnApply.disabled = false; btnApply.textContent = '应用'; btnApply.classList.remove('btn-success'); btnApply.classList.add('btn-outline-primary'); }
        const status = document.getElementById('startTimeApplyStatus');
        if (status) { status.textContent = '修改时间后请点「应用」'; status.className = 'text-muted'; }

        // 初始化手动台费折扣
        const mdType = document.getElementById('manualDiscountType');
        const mdValue = document.getElementById('manualDiscountValue');
        if (mdType) mdType.value = checkoutData.manual_discount_type || '';
        if (mdValue) mdValue.value = (checkoutData.manual_discount_value > 0 ? checkoutData.manual_discount_value : '');
        updateManualDiscountUnit();

        renderCheckoutFee();

        // 商品选择器
        const productSelect = document.getElementById('productSelect');
        productSelect.innerHTML = '<option value="">选择商品...</option>' + allProducts.map(p => `<option value="${p.id}" data-price="${p.price}" data-name="${p.name}">${p.name} - ${money(p.price)}</option>`).join('');

        // 已有商品（该玩家中已挂账未结算的，标记 persisted 以便删除时同步删库）
        cart = (checkoutData.product_sales || []).map(ps => ({
            id: ps.id,
            product_id: ps.product_id,
            name: ps.product_name,
            price: ps.price,
            qty: ps.quantity,
            total: ps.total,
            is_custom: ps.is_custom,
            custom_category: ps.custom_category,
            persisted: true
        }));
        renderCart();

        document.getElementById('discountSearch').value = '';
        document.getElementById('discountResults').innerHTML = '';
        document.getElementById('selectedDiscount').innerHTML = '';
        if (checkoutData.available_discounts && checkoutData.available_discounts.length > 0) {
            document.getElementById('discountResults').innerHTML = '<small class="text-muted">该玩家有可用优惠券：</small><br>' +
                checkoutData.available_discounts.map(d => `<button class="btn btn-sm btn-outline-success mt-1" onclick="selectDiscount(${d.id}, '${d.discount_type}', ${d.max_deduction})">${d.type_label} (上限¥${d.max_deduction})</button>`).join(' ');
        }

        // 会员信息（每次打开先清空旧状态，仅依据当前 preview 重新渲染，杜绝跨玩家串号）
        const memberInfo = document.getElementById('memberPayInfo');
        const payMember = document.getElementById('payMember');
        if (checkoutData.member_info) {
            memberInfo.classList.remove('d-none', 'alert-secondary');
            memberInfo.classList.add('alert-info');
            memberInfo.innerHTML = `<i class="bi bi-credit-card-2-front"></i> ${checkoutData.player_name} 会员余额: <strong>${money(checkoutData.member_info.balance)}</strong>`;
            // 余额>0 才可选用会员余额支付；余额=0 禁用
            payMember.disabled = checkoutData.member_info.balance <= 0;
            if (payMember.disabled && payMember.checked) document.getElementById('payScanWechat').checked = true;
        } else {
            memberInfo.classList.remove('d-none', 'alert-info');
            memberInfo.classList.add('alert-secondary');
            memberInfo.innerHTML = `<span class="text-muted">非会员</span>`;
            // 非会员：隐藏/禁用会员余额支付
            payMember.disabled = true;
            if (payMember.checked) document.getElementById('payScanWechat').checked = true;
        }

        updateCheckoutTotal();

        // 初始化扫码状态
        scanConfirmed = false;
        paymentConfirmed = false;
        paymentConfirmRef = null;
        const scanInput = document.getElementById('scanInput');
        const scanStatus = document.getElementById('scanStatus');
        scanInput.value = '';
        scanStatus.innerHTML = '';
        scanInput.disabled = false;
        updateScanArea();
        // 拉取支付确认开关（启用后扫码收款必须系统确认到账）
        paymentConfirmEnabled = false;
        fetch('/api/payment/status').then(r => r.json()).then(d => {
            paymentConfirmEnabled = !!(d && d.enabled);
        }).catch(() => { paymentConfirmEnabled = false; });

        document.querySelectorAll('input[name="paymentMethod"]').forEach(radio => {
            radio.onchange = updateScanArea;
        });
        scanInput.onkeydown = onScanKeyDown;

        const modalEl = document.getElementById('checkoutModal');
        modalEl.addEventListener('shown.bs.modal', () => { focusScanInput(); }, {once: true});
        new bootstrap.Modal(modalEl).show();
    } catch (e) { alert('获取结账信息失败'); console.error(e); }
}

function renderCheckoutFee() {
    if (!checkoutData) return;
    document.getElementById('checkoutDuration').textContent = formatDuration(checkoutData.duration_minutes);
    document.getElementById('checkoutFee').textContent = money(checkoutData.fee);
    const table = document.getElementById('feeBreakdownTable');
    table.innerHTML = `
        <thead><tr><th>时段</th><th>时长</th><th>费率</th><th class="text-end">金额</th></tr></thead>
        <tbody>
        ${checkoutData.fee_breakdown.map(b => `<tr>
            <td>${b.label}</td>
            <td>${b.rate_type === 'flat' ? '-' : formatDuration(b.minutes)}</td>
            <td>${b.rate_type === 'flat' ? '包夜' : '¥' + b.rate + '/h'}</td>
            <td class="text-end">${money(b.amount)}</td>
        </tr>`).join('')}
        <tr class="table-total"><td colspan="3" class="text-end">台费合计</td><td class="text-end">${money(checkoutData.fee)}</td></tr>
        </tbody>`;

    // V1.4 计费说明（面向客人的透明计费）
    const expEl = document.getElementById('billingExplanation');
    const exp = checkoutData.billing_explanation;
    if (exp && exp.has_buffer) {
        let rows = '';
        rows += `<div class="d-flex justify-content-between"><span>游玩时长</span><strong>${exp.duration_label}</strong></div>`;
        rows += `<div class="d-flex justify-content-between"><span>${exp.base_label}</span><strong>${money(exp.base_fee)}</strong></div>`;
        if (exp.extra_minutes > 0) {
            const chargedNote = exp.extra_charged_minutes !== exp.extra_minutes
                ? `（计费 ${exp.extra_charged_minutes} 分钟）` : '';
            rows += `<div class="d-flex justify-content-between"><span>超出首小时</span><strong>${exp.extra_minutes} 分钟${chargedNote}</strong></div>`;
            rows += `<div class="d-flex justify-content-between"><span>追加（缓冲阶梯）</span><strong>${money(exp.extra_fee)}</strong></div>`;
        }
        rows += `<div class="d-flex justify-content-between border-top mt-1 pt-1"><span>合计</span><strong class="text-primary">${money(exp.total)}</strong></div>`;
        if (exp.note) rows += `<div class="text-muted mt-1">${exp.note}</div>`;
        expEl.innerHTML = `<div class="fw-bold mb-1">计费说明</div>${rows}`;
        expEl.style.display = 'block';
    } else if (exp && exp.mode === 'flat') {
        expEl.innerHTML = `<div class="fw-bold mb-1">计费说明</div>`
            + `<div class="d-flex justify-content-between"><span>游玩时长</span><strong>${exp.duration_label}</strong></div>`
            + `<div class="d-flex justify-content-between"><span>${exp.base_label}</span><strong>${money(exp.total)}</strong></div>`
            + `<div class="text-muted mt-1">${exp.note || ''}</div>`;
        expEl.style.display = 'block';
    } else {
        expEl.style.display = 'none';
    }
}

let startTimeDebounceTimer = null;

async function refreshCheckoutPreview(showStatus = true) {
    if (!currentSessionId || !currentSpId) return;
    const startTime = document.getElementById('checkoutStartTimeInput').value;
    const isOvernight = document.getElementById('checkoutOvernight').checked;
    const manualDiscountType = document.getElementById('manualDiscountType').value;
    const manualDiscountValue = parseFloat(document.getElementById('manualDiscountValue').value) || 0;
    const btn = document.getElementById('btnApplyStartTime');
    const status = document.getElementById('startTimeApplyStatus');
    if (showStatus && btn) { btn.disabled = true; btn.textContent = '计算中...'; }
    try {
        const res = await fetch(`/api/sessions/${currentSessionId}/players/${currentSpId}/preview`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                start_time: startTime,
                is_overnight: isOvernight,
                manual_discount_type: manualDiscountType,
                manual_discount_value: manualDiscountValue
            })
        });
        if (res.ok) {
            checkoutData = await res.json();
            renderCheckoutFee();
            updateCheckoutTotal();
            if (showStatus) {
                if (btn) { btn.disabled = false; btn.textContent = '已应用'; btn.classList.remove('btn-outline-primary'); btn.classList.add('btn-success'); }
                if (status) { status.textContent = '费用已按新时间更新'; status.className = 'text-success'; }
                setTimeout(() => {
                    if (btn) { btn.textContent = '应用'; btn.classList.remove('btn-success'); btn.classList.add('btn-outline-primary'); }
                    if (status) { status.textContent = ''; }
                }, 1500);
            }
        } else {
            const err = await res.json().catch(() => ({}));
            console.error('preview error', err);
            if (showStatus) {
                if (btn) { btn.disabled = false; btn.textContent = '应用'; }
                if (status) { status.textContent = '失败：' + (err.error || res.statusText); status.className = 'text-danger'; }
                alert('时间更新失败：' + (err.error || res.statusText));
            }
        }
    } catch (e) {
        console.error(e);
        if (showStatus) {
            if (btn) { btn.disabled = false; btn.textContent = '应用'; }
            if (status) { status.textContent = '网络错误'; status.className = 'text-danger'; }
            alert('网络错误，时间未能实时更新');
        }
    }
}

async function applyCheckoutStartTime() {
    // 点击「应用」后，先把修改后的开始时间/通宵标志持久化到数据库，再刷新费用预览
    if (!currentSessionId || !currentSpId) return;
    const startTime = document.getElementById('checkoutStartTimeInput').value;
    const isOvernight = document.getElementById('checkoutOvernight').checked;
    const btn = document.getElementById('btnApplyStartTime');
    const status = document.getElementById('startTimeApplyStatus');
    if (btn) { btn.disabled = true; btn.textContent = '保存中...'; }
    if (status) { status.textContent = '正在保存时间...'; status.className = 'text-muted'; }
    try {
        const saveRes = await fetch(`/api/sessions/${currentSessionId}/players/${currentSpId}/time`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ start_time: startTime, is_overnight: isOvernight })
        });
        if (!saveRes.ok) {
            const err = await saveRes.json().catch(() => ({}));
            throw new Error(err.error || saveRes.statusText);
        }
        // 保存成功后刷新费用预览
        await refreshCheckoutPreview(true);
    } catch (e) {
        console.error(e);
        if (btn) { btn.disabled = false; btn.textContent = '应用'; }
        if (status) { status.textContent = '保存失败：' + e.message; status.className = 'text-danger'; }
        alert('保存失败：' + e.message);
    }
}

function resetApplyStatus() {
    const btn = document.getElementById('btnApplyStartTime');
    const status = document.getElementById('startTimeApplyStatus');
    if (btn) { btn.disabled = false; btn.textContent = '应用'; btn.classList.remove('btn-success'); btn.classList.add('btn-outline-primary'); }
    if (status) { status.textContent = '修改后请点「应用」保存'; status.className = 'text-warning'; }
}

function onCheckoutStartTimeChange() {
    // 输入变更时不自动保存，仅提示需要点「应用」
    resetApplyStatus();
}
function onCheckoutOvernightChange() { resetApplyStatus(); }

function updateManualDiscountUnit() {
    const type = document.getElementById('manualDiscountType').value;
    const unit = document.getElementById('manualDiscountUnit');
    const valueInput = document.getElementById('manualDiscountValue');
    if (unit) unit.textContent = type === 'percent' ? '%' : '元';
    if (valueInput) {
        valueInput.step = type === 'percent' ? '1' : '0.01';
        if (type === 'percent') valueInput.max = '100';
        else valueInput.removeAttribute('max');
    }
}

function onManualDiscountChange() {
    updateManualDiscountUnit();
    refreshCheckoutPreview(false);
}

// ===== 扫码盒逻辑 =====
function isScanPayment(method) {
    return method === 'scan_wechat' || method === 'scan_alipay';
}

function updateScanArea() {
    const method = document.querySelector('input[name="paymentMethod"]:checked').value;
    const scanArea = document.getElementById('scanConfirmArea');
    if (isScanPayment(method)) {
        scanArea.classList.remove('d-none');
        focusScanInput();
    } else {
        scanArea.classList.add('d-none');
    }
}

function focusScanInput() {
    const method = document.querySelector('input[name="paymentMethod"]:checked');
    if (!method || !isScanPayment(method.value)) return;
    const scanInput = document.getElementById('scanInput');
    if (scanInput && !scanInput.disabled) scanInput.focus();
}

function onScanKeyDown(e) {
    if (e.key === 'Enter') {
        e.preventDefault();
        const input = document.getElementById('scanInput');
        const code = input.value.trim();
        if (code.length >= 10) {
            if (paymentConfirmEnabled) {
                // 启用支付确认：调用网关实时确认到账，只有成功才允许结账
                doMicropay(code);
            } else {
                // 未启用支付确认（旧逻辑）：仅标记已扫码，依赖收银员人工确认
                scanConfirmed = true;
                input.disabled = true;
                const maskedCode = code.substring(0, 4) + '****' + code.substring(code.length - 4);
                document.getElementById('scanStatus').innerHTML =
                    `<span class="badge bg-success fs-6"><i class="bi bi-check-circle"></i> 已扫码 ${maskedCode}</span>
                     <div class="mt-1 text-muted small">请确认扫码盒语音播报金额后，点击「确认结账」</div>`;
            }
        } else {
            document.getElementById('scanStatus').innerHTML =
                `<span class="text-danger small">码长度不足，请重新扫描</span>`;
            input.value = '';
        }
    }
}

async function doMicropay(authCode) {
    const input = document.getElementById('scanInput');
    const statusEl = document.getElementById('scanStatus');
    const amountText = (document.getElementById('checkoutFinalAmount').textContent || '0')
        .replace(/[^\d.]/g, '');
    const amount = parseFloat(amountText) || 0;
    const method = document.querySelector('input[name="paymentMethod"]:checked').value;
    input.disabled = true;
    paymentConfirmed = false;
    paymentConfirmRef = null;
    statusEl.innerHTML = `<span class="text-primary"><span class="spinner-border spinner-border-sm"></span> 正在向支付网关确认到账...</span>`;
    try {
        const res = await fetch('/api/payment/micropay', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({auth_code: authCode, amount: amount, method: method})
        });
        const data = await res.json();
        if (res.ok && data.status === 'SUCCESS') {
            paymentConfirmed = true;
            paymentConfirmRef = data.out_trade_no;
            const masked = authCode.substring(0, 4) + '****' + authCode.substring(authCode.length - 4);
            statusEl.innerHTML =
                `<span class="badge bg-success fs-6"><i class="bi bi-check-circle"></i> 已确认到账 ${masked}</span>
                 <div class="mt-1 text-success small">支付网关已确认收款 ¥${amount.toFixed(2)}，可点击「确认结账」</div>`;
        } else if (data.status === 'USERPAYING') {
            statusEl.innerHTML = `<span class="text-warning">顾客支付中，请稍候或让顾客确认支付密码</span>`;
            input.disabled = false;
        } else {
            statusEl.innerHTML = `<span class="text-danger">确认失败：${data.message || '支付未成功'}</span>`;
            input.disabled = false;
            input.value = '';
        }
    } catch (err) {
        statusEl.innerHTML = `<span class="text-danger">确认请求失败，请重试</span>`;
        input.disabled = false;
        input.value = '';
    }
}

function addProductToCart() {
    const select = document.getElementById('productSelect');
    const qty = parseInt(document.getElementById('productQty').value) || 1;
    if (!select.value) return;
    const option = select.selectedOptions[0];
    const productId = parseInt(select.value);
    const price = parseFloat(option.dataset.price);
    const name = option.dataset.name;
    const existing = cart.find(c => c.product_id === productId && !c.is_custom);
    if (existing) {
        existing.qty += qty;
        existing.total = round(existing.price * existing.qty);
    } else {
        cart.push({ product_id: productId, name, price, qty, total: round(price * qty), is_custom: false });
    }
    document.getElementById('productQty').value = 1;
    select.value = '';
    renderCart();
    updateCheckoutTotal();
}

function openCustomProductModal() {
    document.getElementById('customProductName').value = '';
    document.getElementById('customProductCategory').value = 'other';
    document.getElementById('customProductPrice').value = '';
    document.getElementById('customProductQty').value = 1;
    new bootstrap.Modal(document.getElementById('customProductModal')).show();
}

function addCustomProductToCart() {
    const name = document.getElementById('customProductName').value.trim();
    const category = document.getElementById('customProductCategory').value;
    const price = parseFloat(document.getElementById('customProductPrice').value) || 0;
    const qty = parseInt(document.getElementById('customProductQty').value) || 1;
    if (!name || price <= 0) { alert('请输入商品名称和单价'); return; }
    const existing = cart.find(c => c.is_custom && c.name === name && Math.abs(c.price - price) < 0.01);
    if (existing) {
        existing.qty += qty;
        existing.total = round(existing.price * existing.qty);
    } else {
        cart.push({ product_id: null, name, price, qty, total: round(price * qty), is_custom: true, custom_category: category });
    }
    bootstrap.Modal.getInstance(document.getElementById('customProductModal')).hide();
    renderCart();
    updateCheckoutTotal();
}

async function removeCartItem(idx) {
    const item = cart[idx];
    if (item && item.persisted) {
        try {
            const res = await fetch(`/api/product-sales/${item.id}`, { method: 'DELETE' });
            if (!res.ok) {
                const e = await res.json().catch(() => ({}));
                alert(e.error || '删除失败');
                return;
            }
        } catch (e) { alert('网络错误，删除未成功'); return; }
    }
    cart.splice(idx, 1);
    renderCart();
    updateCheckoutTotal();
}

function renderCart() {
    const el = document.getElementById('cartList');
    if (cart.length === 0) { el.innerHTML = '<small class="text-muted">无商品消费</small>'; document.getElementById('cartTotal').innerHTML = ''; return; }
    el.innerHTML = cart.map((c, i) => `
        <div class="cart-item">
            <span class="ci-name">${c.name}${c.is_custom ? ' <span class="badge bg-secondary">无码</span>' : ''}${c.persisted ? ' <span class="badge bg-info">已挂账</span>' : ''}</span>
            <span class="ci-qty">x${c.qty}</span>
            <span class="ci-total">${money(c.total)}</span>
            <button class="btn btn-sm btn-link text-danger p-0" onclick="removeCartItem(${i})"><i class="bi bi-x"></i></button>
        </div>
    `).join('');
    const cartTotal = cart.reduce((s, c) => s + c.total, 0);
    document.getElementById('cartTotal').innerHTML = `<small class="text-muted">商品小计: <strong>${money(cartTotal)}</strong></small>`;
}

// ===== 游戏中玩家消费挂账 =====
let consumeSessionId = null, consumeSpId = null;

async function openPlayerConsumption(sessionId, spId, playerName, machineName) {
    consumeSessionId = sessionId;
    consumeSpId = spId;
    document.getElementById('consumePlayerName').textContent = playerName;
    document.getElementById('consumeMachineName').textContent = machineName;
    const sel = document.getElementById('consumeProductSelect');
    sel.innerHTML = '<option value="">选择商品...</option>' + allProducts.map(p => `<option value="${p.id}" data-price="${p.price}" data-name="${p.name}">${p.name} - ${money(p.price)}</option>`).join('');
    document.getElementById('consumeQty').value = 1;
    document.getElementById('consumeToast').style.display = 'none';
    document.getElementById('consumeCustomRow').style.display = 'none';
    await loadConsumptionList();
    new bootstrap.Modal(document.getElementById('playerConsumptionModal')).show();
}

async function loadConsumptionList() {
    try {
        const res = await fetch(`/api/sessions/${consumeSessionId}/players/${consumeSpId}/consumption`);
        const data = await res.json();
        const el = document.getElementById('consumeList');
        if (!data.items || data.items.length === 0) {
            el.innerHTML = '<small class="text-muted">暂无挂账消费</small>';
        } else {
            el.innerHTML = data.items.map(it => `
                <div class="cart-item">
                    <span class="ci-name">${it.product_name}${it.is_custom ? ' <span class="badge bg-secondary">无码</span>' : ''}</span>
                    <span class="ci-qty">x${it.quantity}</span>
                    <span class="ci-total">${money(it.total)}</span>
                    <button class="btn btn-sm btn-link text-danger p-0" onclick="deleteConsumption(${it.id})"><i class="bi bi-x"></i></button>
                </div>`).join('');
        }
        document.getElementById('consumeTotal').innerHTML = (data.items && data.items.length)
            ? `<small class="text-muted">挂账合计: <strong>${money(data.total)}</strong></small>` : '';
    } catch (e) { console.error(e); }
}

async function addConsumeProduct() {
    const sel = document.getElementById('consumeProductSelect');
    const qty = parseInt(document.getElementById('consumeQty').value) || 1;
    if (!sel.value) { alert('请选择商品'); return; }
    const option = sel.selectedOptions[0];
    const payload = {
        product_id: parseInt(sel.value),
        quantity: qty,
        session_id: consumeSessionId,
        session_player_id: consumeSpId
    };
    try {
        const res = await fetch('/api/products/sell', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            alert('添加失败: ' + (err.error || res.statusText));
            return;
        }
        showConsumeToast(`消费已挂账: ${option.dataset.name} x${qty}`);
        sel.value = '';
        document.getElementById('consumeQty').value = 1;
        await loadConsumptionList();
    } catch (e) { alert('网络错误，挂账未成功'); }
}

function toggleConsumeCustom() {
    const row = document.getElementById('consumeCustomRow');
    row.style.display = row.style.display === 'none' ? 'block' : 'none';
    document.getElementById('consumeCustomName').value = '';
    document.getElementById('consumeCustomPrice').value = '';
    document.getElementById('consumeCustomQty').value = 1;
    document.getElementById('consumeCustomCategory').value = 'other';
}

async function addConsumeCustomProduct() {
    const name = document.getElementById('consumeCustomName').value.trim();
    const category = document.getElementById('consumeCustomCategory').value;
    const price = parseFloat(document.getElementById('consumeCustomPrice').value) || 0;
    const qty = parseInt(document.getElementById('consumeCustomQty').value) || 1;
    if (!name || price <= 0) { alert('请输入无码商品名称和单价'); return; }
    const payload = {
        is_custom: true, custom_name: name, custom_category: category, price, quantity: qty,
        session_id: consumeSessionId, session_player_id: consumeSpId
    };
    try {
        const res = await fetch('/api/products/sell', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            alert('添加失败: ' + (err.error || res.statusText));
            return;
        }
        showConsumeToast(`消费已挂账: ${name} x${qty}`);
        toggleConsumeCustom();
        await loadConsumptionList();
    } catch (e) { alert('网络错误，挂账未成功'); }
}

async function deleteConsumption(id) {
    if (!confirm('确认删除该挂账消费？')) return;
    try {
        const res = await fetch(`/api/product-sales/${id}`, { method: 'DELETE' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { alert(data.error || '删除失败'); return; }
        await loadConsumptionList();
    } catch (e) { alert('网络错误，删除未成功'); }
}

function showConsumeToast(msg) {
    const el = document.getElementById('consumeToast');
    el.textContent = msg;
    el.style.display = 'block';
    setTimeout(() => { el.style.display = 'none'; }, 2500);
}

async function searchDiscounts() {
    const name = document.getElementById('discountSearch').value.trim();
    if (name.length < 1) { document.getElementById('discountResults').innerHTML = ''; return; }
    try {
        const res = await fetch(`/api/discounts/search?name=${encodeURIComponent(name)}`);
        const discounts = await res.json();
        const el = document.getElementById('discountResults');
        el.innerHTML = discounts.length === 0 ? '<small class="text-muted">无可用优惠券</small>' :
            discounts.map(d => `<button class="btn btn-sm btn-outline-success mt-1 d-block text-start" onclick="selectDiscount(${d.id}, '${d.discount_type}', ${d.max_deduction})">${d.player_name} - ${d.type_label} (上限¥${d.max_deduction}) - ${d.lottery_date}</button>`).join('');
    } catch (e) { console.error(e); }
}

function selectDiscount(id, type, maxDeduction) {
    selectedDiscountId = id;
    const labels = {'free': '免单', 'half': '半价', 'discount': '八折'};
    let deduction = 0;
    if (type === 'free') deduction = Math.min(checkoutData.fee, maxDeduction);
    else if (type === 'half') deduction = Math.min(checkoutData.fee * 0.5, maxDeduction);
    else if (type === 'discount') deduction = Math.min(checkoutData.fee * 0.2, maxDeduction);
    document.getElementById('selectedDiscount').innerHTML = `<div class="alert alert-success mb-0">已选: ${labels[type]} - 抵扣 ${money(deduction)}<button class="btn btn-sm btn-link text-danger float-end" onclick="clearDiscount()">取消</button></div>`;
    document.getElementById('discountResults').innerHTML = '';
    updateCheckoutTotal();
}

function clearDiscount() {
    selectedDiscountId = null;
    document.getElementById('selectedDiscount').innerHTML = '';
    updateCheckoutTotal();
}

function updateCheckoutTotal() {
    const fee = checkoutData.fee;
    const feeAfterManual = checkoutData.fee_after_manual !== undefined ? checkoutData.fee_after_manual : fee;
    const manualDiscount = checkoutData.manual_discount_amount || 0;
    const cartTotal = cart.reduce((s, c) => s + c.total, 0);
    let lotteryDeduction = 0;
    const selEl = document.getElementById('selectedDiscount');
    if (selEl.innerHTML.includes('抵扣')) {
        const match = selEl.innerHTML.match(/¥[\d.]+/);
        if (match) lotteryDeduction = parseFloat(match[0].replace('¥', ''));
    }
    // 抽奖抵扣在手动折扣后的台费上计算
    lotteryDeduction = Math.min(lotteryDeduction, feeAfterManual);
    const finalFee = Math.max(feeAfterManual - lotteryDeduction, 0);
    const grandTotal = round(finalFee + cartTotal);
    document.getElementById('checkoutFinalAmount').textContent = money(grandTotal);
    let detail = `台费 ${money(fee)}`;
    if (manualDiscount > 0) detail += ` - 手动折扣 ${money(manualDiscount)}`;
    if (lotteryDeduction > 0) detail += ` - 抵扣 ${money(lotteryDeduction)}`;
    if (cartTotal > 0) detail += ` + 商品 ${money(cartTotal)}`;
    document.getElementById('amountDetail').textContent = detail;

    // 同步显示手动折扣金额
    const mdAmountEl = document.getElementById('manualDiscountAmount');
    if (mdAmountEl) mdAmountEl.textContent = manualDiscount > 0 ? `-${money(manualDiscount)}` : money(0);
}

async function confirmPlayerCheckout() {
    const paymentMethodEl = document.querySelector('input[name="paymentMethod"]:checked');
    if (!paymentMethodEl) { alert('请选择支付方式'); return; }
    const paymentMethod = paymentMethodEl.value;
    const cartTotal = cart.reduce((s, c) => s + c.total, 0);
    let memberId = null;

    if (isScanPayment(paymentMethod)) {
        if (paymentConfirmEnabled) {
            if (!paymentConfirmed) {
                alert('请先让顾客出示付款码，对准扫码盒扫描，待系统显示「已确认到账」后再结账');
                focusScanInput();
                return;
            }
        } else if (!scanConfirmed) {
            alert('请先让顾客扫码完成支付（听到语音播报后），或切换到其他支付方式');
            focusScanInput();
            return;
        }
    }

    if (paymentMethod === 'member') {
        if (!checkoutData.member_info) {
            alert('该玩家非会员，无法使用会员余额支付');
            return;
        }
        memberId = checkoutData.member_info.id;
    }

    // 先保存新增/修改的商品到该玩家
    const existingProductSales = checkoutData.product_sales || [];
    for (const item of cart) {
        const existing = existingProductSales.find(ps =>
            ps.product_id === item.product_id && ps.product_name === item.name &&
            Math.abs(ps.price - item.price) < 0.01
        );
        const isNew = !existing;
        const needMore = existing && item.qty > existing.quantity;
        if (isNew || needMore) {
            const qty = isNew ? item.qty : (item.qty - existing.quantity);
            const payload = {
                quantity: qty,
                session_id: currentSessionId,
                session_player_id: currentSpId,
                member_id: paymentMethod === 'member' ? memberId : null
            };
            if (item.is_custom) {
                payload.is_custom = true;
                payload.custom_name = item.name;
                payload.custom_category = item.custom_category || 'other';
                payload.price = item.price;
            } else {
                payload.product_id = item.product_id;
            }
            try {
                const sellRes = await fetch('/api/products/sell', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                if (!sellRes.ok) {
                    const err = await sellRes.json().catch(() => ({}));
                    alert('商品保存失败：' + (err.error || sellRes.statusText));
                    return;
                }
            } catch (e) {
                alert('网络错误，商品未能保存');
                return;
            }
        }
    }

    const startTime = document.getElementById('checkoutStartTimeInput').value;
    const isOvernight = document.getElementById('checkoutOvernight').checked;
    const manualDiscountType = document.getElementById('manualDiscountType').value;
    const manualDiscountValue = parseFloat(document.getElementById('manualDiscountValue').value) || 0;

    try {
        const res = await fetch(`/api/sessions/${currentSessionId}/players/${currentSpId}/checkout`, {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                discount_id: selectedDiscountId,
                payment_method: paymentMethod,
                member_id: memberId,
                product_total: cartTotal,
                start_time: startTime,
                is_overnight: isOvernight,
                manual_discount_type: manualDiscountType,
                manual_discount_value: manualDiscountValue,
                payment_ref: paymentConfirmEnabled ? paymentConfirmRef : null
            })
        });
        if (res.ok) {
            const result = await res.json();
            let msg = `${checkoutData.player_name} 结账成功\n实收: ${money(result.grand_total || result.final_fee)}`;
            if (result.member_balance_after !== null && result.member_balance_after !== undefined) {
                msg += `\n会员余额: ${money(result.member_balance_after)}`;
            }
            if (result.session_closed) msg += '\n全员已结账，台桌已关闭';
            alert(msg);
            bootstrap.Modal.getInstance(document.getElementById('checkoutModal')).hide();
            fetchMachines();
        } else { const err = await res.json(); alert(err.error || '结账失败'); }
    } catch (e) { alert('网络错误'); }
}

// ===== 抽奖管理 =====
function initLottery() {
    document.getElementById('lotteryDate').value = todayStr();
    document.getElementById('lotteryDate').addEventListener('change', fetchDiscounts);
    document.getElementById('lotteryDateInput').value = todayStr();
    fetchDiscounts();
    document.getElementById('discountForm').addEventListener('submit', async (e) => {
        e.preventDefault();
        const data = {
            lottery_date: document.getElementById('lotteryDateInput').value,
            player_name: document.getElementById('playerNameInput').value.trim(),
            discount_type: document.getElementById('discountTypeInput').value,
        };
        if (!data.player_name) { alert('请输入玩家姓名'); return; }
        try {
            const res = await fetch('/api/discounts', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data) });
            if (res.ok) {
                document.getElementById('playerNameInput').value = '';
                document.getElementById('lotteryDate').value = data.lottery_date;
                fetchDiscounts();
            } else { const err = await res.json(); alert(err.error || '添加失败'); }
        } catch (e) { alert('网络错误'); }
    });
}

async function fetchDiscounts() {
    const date = document.getElementById('lotteryDate').value;
    try {
        const res = await fetch(`/api/discounts?date=${date}`);
        const discounts = await res.json();
        renderDiscounts(discounts);
    } catch (e) { console.error(e); }
}

function renderDiscounts(discounts) {
    const tbody = document.getElementById('discountList');
    if (!tbody) return;
    if (discounts.length === 0) { tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-4">暂无记录</td></tr>'; return; }
    const badgeClass = {'free': 'bg-danger', 'half': 'bg-warning text-dark', 'discount': 'bg-info text-dark'};
    tbody.innerHTML = discounts.map(d => `<tr>
        <td>${d.player_name}</td>
        <td><span class="badge ${badgeClass[d.discount_type] || 'bg-secondary'}">${d.type_label}</span></td>
        <td>¥${d.max_deduction}</td>
        <td>${d.used ? '<span class="badge bg-secondary">已使用</span>' : '<span class="badge bg-success">未使用</span>'}</td>
        <td>${!d.used ? `<button class="btn btn-sm btn-outline-danger" onclick="deleteDiscount(${d.id})">删除</button>` : '-'}</td>
    </tr>`).join('');
}

async function deleteDiscount(id) {
    if (!confirm('确定删除？')) return;
    try { await fetch(`/api/discounts/${id}`, {method: 'DELETE'}); fetchDiscounts(); } catch (e) { alert('删除失败'); }
}

// ===== 日结报表 =====
function initDaily() {
    document.getElementById('dailyDate').value = todayStr();
    fetchDailyReport();
    document.getElementById('dailyDate').addEventListener('change', fetchDailyReport);
}

async function fetchDailyReport() {
    const date = document.getElementById('dailyDate').value;
    try {
        const res = await fetch(`/api/daily?date=${date}`);
        const data = await res.json();
        renderDailyReport(data);
    } catch (e) { console.error(e); }
}

function renderDailyReport(data) {
    const s = data.summary;
    document.getElementById('totalSessions').textContent = s.total_sessions;
    document.getElementById('activeSessions').textContent = s.active_sessions;
    document.getElementById('totalFee').textContent = money(s.total_fee);
    document.getElementById('totalDiscount').textContent = money(s.total_discount);
    document.getElementById('totalRevenue').textContent = money(s.total_revenue);
    document.getElementById('productRevenue').textContent = money(s.product_revenue || 0);
    document.getElementById('grandTotal').textContent = money(s.grand_total || 0);
    document.getElementById('rechargeTotal').textContent = money(s.recharge_total || 0);
    const hp = document.getElementById('historicalPayment');
    if (hp) hp.textContent = money(s.historical_payment || 0);
    document.getElementById('wechatAmount').textContent = money(s.payment_breakdown.wechat);
    document.getElementById('alipayAmount').textContent = money(s.payment_breakdown.alipay);
    document.getElementById('cashAmount').textContent = money(s.payment_breakdown.cash);
    document.getElementById('memberAmount').textContent = money(s.payment_breakdown.member || 0);
    const scanWechat = document.getElementById('scan_wechatAmount');
    const scanAlipay = document.getElementById('scan_alipayAmount');
    if (scanWechat) scanWechat.textContent = money(s.payment_breakdown.scan_wechat || 0);
    if (scanAlipay) scanAlipay.textContent = money(s.payment_breakdown.scan_alipay || 0);
    const tbody = document.getElementById('dailySessions');
    if (data.sessions.length === 0) { tbody.innerHTML = '<tr><td colspan="9" class="text-center text-muted py-4">暂无记录</td></tr>'; return; }
    tbody.innerHTML = data.sessions.map(s => `<tr>
        <td>${formatTime(s.start_time)}</td>
        <td>${s.machine_name}</td>
        <td>${s.type_label}</td>
        <td>${formatDuration(s.duration_minutes)}</td>
        <td>${money(s.fee)}</td>
        <td>${s.product_total > 0 ? money(s.product_total) : '-'}</td>
        <td>${s.discount_amount > 0 ? '-' + money(s.discount_amount) : '-'}</td>
        <td>${money(s.final_fee)}</td>
        <td>${s.payment_label}</td>
    </tr>`).join('');
}

// ===== 系统设置 =====
function initSettings() { fetchSettings(); document.getElementById('saveSettingsBtn').addEventListener('click', saveSettings); }

async function fetchSettings() {
    try {
        const res = await fetch('/api/settings');
        const s = await res.json();
        ['rate_8port','rate_4port','overnight_8port','overnight_4port','selfservice_multiplier',
         'rounding','lottery_free_count','lottery_free_max','lottery_half_count','lottery_half_max',
         'lottery_discount_count','lottery_discount_max'].forEach(key => {
            const el = document.getElementById(key);
            if (el && s[key] !== undefined) el.value = s[key];
        });
    } catch (e) { console.error(e); }
}

async function saveSettings() {
    const keys = ['rate_8port','rate_4port','overnight_8port','overnight_4port','selfservice_multiplier',
         'rounding','lottery_free_count','lottery_free_max','lottery_half_count','lottery_half_max',
         'lottery_discount_count','lottery_discount_max'];
    const data = {};
    keys.forEach(k => {
        const el = document.getElementById(k);
        if (el) data[k] = el.value;
    });
    try {
        const res = await fetch('/api/settings', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data) });
        if (res.ok) alert('设置已保存');
        else alert('保存失败');
    } catch (e) { alert('网络错误'); }
}

// ===== 商品管理 =====
let currentProductCategory = 'all';

function initProducts() {
    fetchProducts();
    document.getElementById('salesDate').value = todayStr();
    document.getElementById('salesDate').addEventListener('change', fetchProductSales);
    fetchProductSales();
}

function filterProducts(cat, btn) {
    currentProductCategory = cat;
    document.querySelectorAll('.btn-group .btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    fetchProducts();
}

async function fetchProducts() {
    try {
        const url = currentProductCategory === 'all' ? '/api/products' : `/api/products?category=${currentProductCategory}`;
        const res = await fetch(url);
        const products = await res.json();
        renderProducts(products);
    } catch (e) { console.error(e); }
}

function renderProducts(products) {
    const container = document.getElementById('productGrid');
    if (products.length === 0) { container.innerHTML = '<small class="text-muted">暂无商品</small>'; return; }
    const catLabels = {'drink': '饮料', 'snack': '零食', 'other': '其他'};
    container.innerHTML = products.map(p => `
        <div class="product-card">
            <div class="d-flex justify-content-between align-items-start">
                <span class="pcat">${catLabels[p.category] || p.category}</span>
                <button class="btn btn-link text-danger p-0" title="删除商品" onclick="event.stopPropagation(); deleteProduct(${p.id}, '${escapeJs(p.name)}')">
                    <i class="bi bi-trash"></i>
                </button>
            </div>
            <div class="pname" onclick="openProductModal(${p.id})">${p.name}</div>
            <div class="pprice" onclick="openProductModal(${p.id})">${money(p.price)}</div>
            <div class="pstock" onclick="openProductModal(${p.id})">${p.stock >= 0 ? `库存: ${p.stock}` : '库存: 不限'}</div>
        </div>
    `).join('');
}

function openProductModal(id) {
    const modal = document.getElementById('productModal');
    document.getElementById('productModalTitle').textContent = id ? '编辑商品' : '添加商品';
    if (id) {
        fetch('/api/products').then(r => r.json()).then(products => {
            const p = products.find(x => x.id === id);
            if (!p) return;
            document.getElementById('productId').value = p.id;
            document.getElementById('productName').value = p.name;
            document.getElementById('productCategory').value = p.category;
            document.getElementById('productPrice').value = p.price;
            document.getElementById('productCost').value = p.cost;
            document.getElementById('productStock').value = p.stock;
        });
    } else {
        document.getElementById('productId').value = '';
        document.getElementById('productName').value = '';
        document.getElementById('productCategory').value = 'drink';
        document.getElementById('productPrice').value = '';
        document.getElementById('productCost').value = '';
        document.getElementById('productStock').value = '-1';
    }
    new bootstrap.Modal(modal).show();
}

async function saveProduct() {
    const id = document.getElementById('productId').value;
    const data = {
        name: document.getElementById('productName').value.trim(),
        category: document.getElementById('productCategory').value,
        price: parseFloat(document.getElementById('productPrice').value),
        cost: parseFloat(document.getElementById('productCost').value) || 0,
        stock: parseInt(document.getElementById('productStock').value),
    };
    if (!data.name || isNaN(data.price)) { alert('请填写名称和售价'); return; }
    try {
        const url = id ? `/api/products/${id}` : '/api/products';
        const method = id ? 'PUT' : 'POST';
        const res = await fetch(url, { method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data) });
        if (res.ok) {
            bootstrap.Modal.getInstance(document.getElementById('productModal')).hide();
            fetchProducts();
        } else { const err = await res.json(); alert(err.error || '保存失败'); }
    } catch (e) { alert('网络错误'); }
}

async function deleteProduct(id, name) {
    if (!confirm(`确认删除商品 "${name}"？\n删除后商品不再显示，历史销售记录仍保留。`)) return;
    try {
        const res = await fetch(`/api/products/${id}`, { method: 'DELETE' });
        if (res.ok) {
            fetchProducts();
        } else {
            const err = await res.json().catch(() => ({}));
            alert(err.error || '删除失败');
        }
    } catch (e) { alert('网络错误'); }
}

async function fetchProductSales() {
    const date = document.getElementById('salesDate').value;
    try {
        const res = await fetch(`/api/products/sales?date=${date}`);
        const sales = await res.json();
        const tbody = document.getElementById('salesList');
        if (sales.length === 0) { tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-3">暂无记录</td></tr>'; return; }
        tbody.innerHTML = sales.map(s => `<tr>
            <td>${formatDateTime(s.created_at)}</td>
            <td>${s.product_name}</td>
            <td>¥${s.price}</td>
            <td>${s.quantity}</td>
            <td>¥${s.total}</td>
            <td>${s.payment_label || '-'}</td>
        </tr>`).join('');
    } catch (e) { console.error(e); }
}

// ===== 玩家管理 =====
function initPlayers() {
    fetchPlayers();
    document.getElementById('playerSearch').addEventListener('input', () => {
        clearTimeout(window.searchTimer);
        window.searchTimer = setTimeout(fetchPlayers, 300);
    });
}

async function fetchPlayers() {
    const search = document.getElementById('playerSearch').value.trim();
    const activity = document.getElementById('filterActivity').value;
    const ptype = document.getElementById('filterType').value;
    const organizer = document.getElementById('filterOrganizer').value;
    const status = document.getElementById('filterStatus').value;
    const params = new URLSearchParams();
    if (search) params.set('search', search);
    if (activity) params.set('activity', activity);
    if (ptype) params.set('type', ptype);
    if (organizer) params.set('organizer', organizer);
    if (status) params.set('status', status);
    try {
        const res = await fetch(`/api/players${params.toString() ? '?' + params : ''}`);
        const players = await res.json();
        renderPlayers(players);
        fetchPlayerStats();
    } catch (e) { console.error(e); }
}

// 顶部统计：归档玩家不进入活跃指标，但单独展示"已归档 X"
async function fetchPlayerStats() {
    try {
        const res = await fetch('/api/players/stats');
        const s = await res.json();
        document.getElementById('statTotal').textContent = s.total_active;
        document.getElementById('statArchived').textContent = `已归档 ${s.total_archived}`;
        document.getElementById('statHigh').textContent = s.high_active;
        document.getElementById('statCompetitive').textContent = s.competitive_active;
        document.getElementById('statOrganizer').textContent = s.organizer_active;
    } catch (e) { console.error(e); }
}

function activityBadge(level) {
    const map = {
        '高': '<span class="badge bg-danger">高</span>',
        '中': '<span class="badge bg-warning text-dark">中</span>',
        '低': '<span class="badge bg-secondary">低</span>',
        '沉睡': '<span class="badge bg-light text-dark border">沉睡</span>',
    };
    return map[level] || '<span class="badge bg-light text-muted border">-</span>';
}

function typeBadge(type) {
    if (type === '竞技') return '<span class="badge bg-primary">竞技</span>';
    if (type === '娱乐') return '<span class="badge bg-info">娱乐</span>';
    return '<span class="text-muted">-</span>';
}

function renderPlayers(players) {
    const tbody = document.getElementById('playerList');
    if (players.length === 0) { tbody.innerHTML = '<tr><td colspan="9" class="text-center text-muted py-4">暂无玩家</td></tr>'; return; }
    const isAdmin = window.CURRENT_USER_ROLE === 'admin';
    tbody.innerHTML = players.map(p => `<tr style="cursor:pointer" onclick="showPlayerDetail(${p.id})">
        <td class="fw-bold">${p.name}${p.status === 'archived' ? ' <span class="badge bg-secondary">已归档</span>' : ''}${p.preferred_name ? `<br><small class="text-muted">${p.preferred_name}</small>` : ''}</td>
        <td>${activityBadge(p.activity_level)}</td>
        <td>${typeBadge(p.player_type)}</td>
        <td>${p.total_visits || 0}次</td>
        <td>${p.last_visit ? `<small>${p.last_visit}</small>` : '-'}</td>
        <td>${p.is_organizer ? '<i class="bi bi-check-circle-fill text-success"></i>' : '-'}</td>
        <td>${p.phone || '-'}</td>
        <td>${p.wechat || '-'}</td>
        <td onclick="event.stopPropagation()" style="white-space:nowrap">
            <button class="btn btn-sm btn-outline-primary" onclick="openPlayerModal(${p.id})">编辑</button>
            ${p.status === 'archived'
                ? `<button class="btn btn-sm btn-outline-success" onclick="restorePlayer(${p.id}, '${p.name.replace(/'/g, "\\'")}')">恢复</button>`
                : `<button class="btn btn-sm btn-outline-warning" onclick="archivePlayer(${p.id}, '${p.name.replace(/'/g, "\\'")}')">归档</button>`}
            ${isAdmin ? `<button class="btn btn-sm btn-outline-danger" onclick="permanentlyDeletePlayer(${p.id}, '${p.name.replace(/'/g, "\\'")}')">永久删除</button>` : ''}
        </td>
    </tr>`).join('');
}

// 归档玩家（弹窗选原因）
let pendingArchiveId = null;
function archivePlayer(id, name) {
    pendingArchiveId = id;
    document.getElementById('archivePlayerName').textContent = name;
    document.getElementById('archiveReasonSelect').value = '';
    new bootstrap.Modal(document.getElementById('archiveReasonModal')).show();
}

async function confirmArchivePlayer() {
    const id = pendingArchiveId;
    if (!id) return;
    const reason = document.getElementById('archiveReasonSelect').value;
    try {
        const res = await fetch(`/api/players/${id}/archive`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({reason})
        });
        if (res.ok) {
            bootstrap.Modal.getInstance(document.getElementById('archiveReasonModal')).hide();
            fetchPlayers();
        } else {
            const err = await res.json().catch(() => ({}));
            alert(err.error || '归档失败');
        }
    } catch (e) { alert('网络错误'); }
}

// 恢复归档玩家
async function restorePlayer(id, name) {
    if (!confirm(`确认恢复玩家 ${name}？恢复后重新进入活跃体系。`)) return;
    try {
        const res = await fetch(`/api/players/${id}/restore`, {method: 'POST'});
        if (res.ok) { fetchPlayers(); }
        else { const err = await res.json().catch(() => ({})); alert(err.error || '恢复失败'); }
    } catch (e) { alert('网络错误'); }
}

// 永久删除（仅管理员可见按钮，后端再做权限+引用双重校验）
async function permanentlyDeletePlayer(id, name) {
    if (!confirm(`⚠️ 永久删除玩家 ${name}？\n\n仅当该玩家从未有历史场次/消费/会员/关系数据时才允许删除，且不可恢复。\n如存在任何历史数据请改用「归档」。`)) return;
    if (!confirm(`再次确认：永久删除 ${name} ？此操作不可撤销。`)) return;
    try {
        const res = await fetch(`/api/players/${id}`, {method: 'DELETE'});
        if (res.ok) { fetchPlayers(); }
        else {
            const err = await res.json().catch(() => ({}));
            if (err.error === 'PLAYER_HAS_HISTORY') {
                alert('该玩家存在历史场次/消费/关系数据，不能永久删除。\n请使用「归档」。');
            } else {
                alert(err.error || '删除失败');
            }
        }
    } catch (e) { alert('网络错误'); }
}

async function showPlayerDetail(id) {
    try {
        const res = await fetch(`/api/players/${id}/detail`);
        const p = await res.json();
        const body = document.getElementById('playerDetailBody');
        const INI = {active:['A','主动型'], semi_active:['B','半主动'], passive:['C','被动型'], unknown:['?','未知']};
        const STY = {competitive:'竞技型', entertainment:'娱乐型', social:'社交型', high_variance:'高波动型', unknown:'未知'};

        const stats = p.visit_stats || {};
        const visits = p.visit_records || [];
        const totalV = stats.total_visits || p.total_visits || 0;
        const compCount = stats.competitive_count || 0;
        const casCount = stats.casual_count || 0;
        const broughtCount = stats.total_brought || 0;
        const overnightCount = stats.total_overnight || 0;

        let html = `
        <div class="row g-3 mb-3">
            <div class="col-md-6">
                <div class="card">
                    <div class="card-body">
                        <h6><i class="bi bi-person"></i> 基本信息</h6>
                        <table class="table table-sm table-borderless">
                            <tr><td class="text-muted" style="width:80px">昵称</td><td class="fw-bold">${p.name}${p.status === 'archived' ? ' <span class="badge bg-secondary">已归档</span>' : ''}</td></tr>
                            <tr><td class="text-muted">真实姓名</td><td>${p.real_name || '-'}</td></tr>
                            <tr><td class="text-muted">称呼</td><td>${p.preferred_name || '-'}</td></tr>
                            <tr><td class="text-muted">性别</td><td>${p.gender || '-'}</td></tr>
                            <tr><td class="text-muted">生日</td><td>${p.birthday || '-'}</td></tr>
                            <tr><td class="text-muted">手机</td><td>${p.phone || '-'}</td></tr>
                            <tr><td class="text-muted">微信</td><td>${p.wechat || '-'}</td></tr>
                            <tr><td class="text-muted">区域</td><td>${p.area || '-'}</td></tr>
                            <tr><td class="text-muted">职业</td><td>${p.occupation || '-'} ${p.industry ? '<small class="text-muted">('+p.industry+')</small>' : ''}</td></tr>
                            <tr><td class="text-muted">QCOS ID</td><td>${p.qcos_id || '-'}</td></tr>
                        </table>
                    </div>
                </div>
            </div>
            <div class="col-md-6">
                <div class="card">
                    <div class="card-body">
                        <h6><i class="bi bi-graph-up"></i> 行为画像</h6>
                        <table class="table table-sm table-borderless">
                            <tr><td class="text-muted" style="width:80px">活跃度</td><td>${activityBadge(p.activity_level)}</td></tr>
                            <tr><td class="text-muted">类型</td><td>${typeBadge(p.player_type)}</td></tr>
                            <tr><td class="text-muted">到店次数</td><td><strong>${totalV}</strong> 次</td></tr>
                            <tr><td class="text-muted">竞技/娱乐</td><td>${compCount}竞技 / ${casCount}娱乐</td></tr>
                            <tr><td class="text-muted">带人次数</td><td>${broughtCount} 次</td></tr>
                            <tr><td class="text-muted">通宵次数</td><td>${overnightCount} 次</td></tr>
                            <tr><td class="text-muted">首次到店</td><td>${stats.first_visit || p.first_visit || '-'}</td></tr>
                            <tr><td class="text-muted">最近到店</td><td>${stats.last_visit || p.last_visit || '-'}</td></tr>
                            <tr><td class="text-muted">主动行为</td><td>${p.active_behavior || '-'}</td></tr>
                            <tr><td class="text-muted">主动性</td><td>${p.initiative_level ? `<span class="badge bg-primary">${INI[p.initiative_level][0]}级</span> ${INI[p.initiative_level][1]} <small class="text-muted">(${p.initiative_score ?? 0}分)</small>` : '-'}</td></tr>
                            <tr><td class="text-muted">适合局型</td><td>${p.table_style_preference ? (STY[p.table_style_preference] || p.table_style_preference) : '-'}</td></tr>
                            <tr><td class="text-muted">体验评分</td><td>${p.experience_score != null ? `<span class="badge bg-${p.experience_score>=70?'success':p.experience_score>=40?'warning':'danger'}">${p.experience_score}</span> <small class="text-muted">正${p.positive_table_count||0}/负${p.negative_table_count||0}/冲突${p.conflict_count||0}</small>` : '<span class="text-muted">暂无反馈</span>'}</td></tr>
                            <tr><td class="text-muted">适配评分</td><td>${p.compatibility_score != null ? `<span class="badge bg-info">${p.compatibility_score}</span>` : '<span class="text-muted">暂无反馈</span>'}</td></tr>
                        </table>
                    </div>
                </div>
            </div>
        </div>`;

        html += `
        <div class="row g-3 mb-3">
            <div class="col-md-6">
                <div class="card">
                    <div class="card-body">
                        <h6><i class="bi bi-controller"></i> 技术与偏好</h6>
                        <table class="table table-sm table-borderless">
                            <tr><td class="text-muted" style="width:80px">段位</td><td>${p.dan || '-'} <small class="text-muted">${p.dan_source === 'store' ? '(店内)' : '(自报)'}</small></td></tr>
                            <tr><td class="text-muted">K值/水平</td><td>${p.skill_level || '-'}</td></tr>
                            <tr><td class="text-muted">常来时段</td><td>${p.preferred_time || '-'}</td></tr>
                            <tr><td class="text-muted">偏好机型</td><td>${p.preferred_mode || p.common_mode || '-'}</td></tr>
                            <tr><td class="text-muted">可否通宵</td><td>${p.can_overnight || '-'}</td></tr>
                            <tr><td class="text-muted">比赛兴趣</td><td>${p.tournament_interest || '-'}</td></tr>
                            <tr><td class="text-muted">饮品偏好</td><td>${p.drink_preference || '-'}</td></tr>
                            <tr><td class="text-muted">价格敏感度</td><td>${p.price_sensitivity || '-'}</td></tr>
                        </table>
                    </div>
                </div>
            </div>
            <div class="col-md-6">
                <div class="card">
                    <div class="card-body">
                        <h6><i class="bi bi-megaphone"></i> CRM与营销</h6>
                        <table class="table table-sm table-borderless">
                            <tr><td class="text-muted" style="width:80px">组织者</td><td>${p.is_organizer ? '<span class="badge bg-success">是</span>' + (p.organizer_level ? ' '+p.organizer_level : '') : '否'} ${p.organizer_candidate ? '<small class="text-muted">候选</small>' : ''}</td></tr>
                            <tr><td class="text-muted">来源渠道</td><td>${p.source_channel || '-'}</td></tr>
                            <tr><td class="text-muted">介绍人</td><td>${p.introducer || '-'}</td></tr>
                            <tr><td class="text-muted">关系强度</td><td>${p.relationship_strength || '-'}</td></tr>
                            <tr><td class="text-muted">维护优先级</td><td>${p.maintenance_priority ? '<span class="badge bg-warning text-dark">'+p.maintenance_priority+'</span>' : '-'}</td></tr>
                            <tr><td class="text-muted">营销标签</td><td>${p.marketing_tags ? p.marketing_tags.split(',').map(t=>'<span class="badge bg-info me-1">'+t.trim()+'</span>').join('') : '-'}</td></tr>
                            <tr><td class="text-muted">风险标签</td><td>${p.risk_tags ? p.risk_tags.split(',').map(t=>'<span class="badge bg-danger me-1">'+t.trim()+'</span>').join('') : '-'}</td></tr>
                            <tr><td class="text-muted">跟进状态</td><td>${p.follow_up_status || '-'}</td></tr>
                            <tr><td class="text-muted">性格标签</td><td>${p.personality_tags || '-'}</td></tr>
                        </table>
                    </div>
                </div>
            </div>
        </div>`;

        if (p.is_member && p.member_info) {
            html += `
            <div class="card mb-3">
                <div class="card-body">
                    <h6><i class="bi bi-credit-card-2-front"></i> 会员信息</h6>
                    <table class="table table-sm table-borderless">
                        <tr><td class="text-muted" style="width:80px">余额</td><td class="fw-bold text-success">¥${(p.member_info.balance||0).toFixed(2)}</td>
                        <td class="text-muted" style="width:80px">累计充值</td><td>¥${(p.member_info.total_recharge||0).toFixed(2)}</td>
                        <td class="text-muted" style="width:80px">累计消费</td><td>¥${(p.member_info.total_spent||0).toFixed(2)}</td></tr>
                    </table>
                </div>
            </div>`;
        }

        if (visits.length > 0) {
            html += `
            <div class="card">
                <div class="card-body">
                    <h6><i class="bi bi-clock-history"></i> 最近到店记录 (${visits.length}条)</h6>
                    <table class="table table-sm table-bordered">
                        <thead><tr><th>日期</th><th>机型</th><th>类型</th><th>带人</th><th>通宵</th><th>组织者</th><th>桌号</th></tr></thead>
                        <tbody>
                        ${visits.map(v => `<tr>
                            <td>${v.visit_date}</td>
                            <td>${v.machine_type || '-'}</td>
                            <td>${v.game_type || '-'}</td>
                            <td>${v.brought_guest ? '<i class="bi bi-check text-success"></i>' : '-'}</td>
                            <td>${v.is_overnight ? '<i class="bi bi-moon text-info"></i>' : '-'}</td>
                            <td>${v.organizer_name && v.organizer_name !== '无' ? v.organizer_name : '-'}</td>
                            <td>${v.table_number || '-'}</td>
                        </tr>`).join('')}
                        </tbody>
                    </table>
                </div>
            </div>`;
        }

        if (p.notes) {
            html += `<div class="alert alert-warning mt-3"><strong>备注：</strong>${p.notes}</div>`;
        }

        body.innerHTML = html;
        loadRelationshipCard(id, body);
        document.getElementById('detailEditBtn').onclick = () => {
            bootstrap.Modal.getInstance(document.getElementById('playerDetailModal')).hide();
            setTimeout(() => openPlayerModal(id), 300);
        };
        new bootstrap.Modal(document.getElementById('playerDetailModal')).show();
    } catch (e) { console.error(e); alert('加载详情失败'); }
}

async function loadRelationshipCard(pid, container) {
    try {
        const res = await fetch(`/api/players/${pid}/relationships`);
        const d = await res.json();
        const card = document.createElement('div');
        card.className = 'card mt-3';
        card.id = 'relCard';
        const relColors = {positive:'success', neutral:'secondary', avoid:'danger'};
        const relLabels = d.rel_types || {positive:'喜欢一起', neutral:'普通', avoid:'避免同桌'};
        let manualHtml = '<p class="text-muted small">暂无人工关系记录。</p>';
        if (d.manual && d.manual.length) {
            manualHtml = d.manual.map(r => `
                <div class="d-flex justify-content-between align-items-center border-bottom py-1">
                    <div><span class="badge bg-${relColors[r.relationship_type]||'secondary'}">${relLabels[r.relationship_type]||r.relationship_type}</span>
                        <strong>${r.other_name}</strong>
                        <small class="text-muted">${r.note||''}</small></div>
                    <button class="btn btn-sm btn-outline-danger py-0" onclick="deleteRelationship(${pid}, ${r.id})"><i class="bi bi-trash"></i></button>
                </div>`).join('');
        }
        let suggHtml = '<p class="text-muted small">暂无同桌历史建议。</p>';
        if (d.suggestions && d.suggestions.length) {
            suggHtml = d.suggestions.map(s => `
                <div class="small py-1 border-bottom">
                    <span class="badge bg-${s.relationship_type==='positive'?'success':'secondary'}">${s.relationship_type==='positive'?'喜好':'普通'}</span>
                    <strong>${s.other_name}</strong>
                    <small class="text-muted">共同同桌${s.co_count}次 · 最近${s.last_co_days}天 · 建议分${s.relationship_score}</small>
                </div>`).join('');
        }
        card.innerHTML = `
            <div class="card-header"><i class="bi bi-diagram-3"></i> 关系管理
                <button class="btn btn-sm btn-outline-primary float-end py-0" onclick="toggleRelForm(${pid})">+ 添加关系</button>
            </div>
            <div class="card-body">
                <h6 class="text-success">人工关系（优先级高于自动）</h6>
                ${manualHtml}
                <h6 class="text-info mt-3">自动同桌建议（仅供参考，不写入avoid）</h6>
                ${suggHtml}
                <div id="relFormBox${pid}" style="display:none" class="mt-3 p-2 border rounded">
                    <div class="row g-2">
                        <div class="col-6"><select class="form-select form-select-sm" id="relOther${pid}"><option value="">选择玩家...</option></select></div>
                        <div class="col-6"><select class="form-select form-select-sm" id="relType${pid}">
                            <option value="positive">喜欢一起(+)</option>
                            <option value="neutral">普通</option>
                            <option value="avoid">避免同桌(-)</option>
                        </select></div>
                        <div class="col-6"><input type="number" class="form-control form-control-sm" id="relScore${pid}" placeholder="关系分 -100~100" value="50"></div>
                        <div class="col-6"><input type="text" class="form-control form-control-sm" id="relNote${pid}" placeholder="备注，如：不喜欢竞技差距大"></div>
                        <div class="col-12 d-flex gap-2">
                            <button class="btn btn-sm btn-primary" onclick="saveRelationship(${pid})">保存</button>
                            <button class="btn btn-sm btn-secondary" onclick="toggleRelForm(${pid})">取消</button>
                        </div>
                    </div>
                </div>
            </div>`;
        container.appendChild(card);
        // 填充玩家下拉
        const sel = document.getElementById('relOther' + pid);
        if (sel) {
            fetch('/api/players').then(r=>r.json()).then(list=>{
                list.filter(p=>p.id!==pid).forEach(p=>{
                    const o=document.createElement('option'); o.value=p.id; o.textContent=p.name; sel.appendChild(o);
                });
            }).catch(()=>{});
        }
    } catch(e) { console.error(e); }
}

function toggleRelForm(pid){ const b=document.getElementById('relFormBox'+pid); b.style.display = b.style.display==='none'?'block':'none'; }

async function saveRelationship(pid){
    const other = document.getElementById('relOther'+pid).value;
    const type = document.getElementById('relType'+pid).value;
    const score = parseInt(document.getElementById('relScore'+pid).value)||0;
    const note = document.getElementById('relNote'+pid).value;
    if(!other){ alert('请选择玩家'); return; }
    try{
        const res = await fetch(`/api/players/${pid}/relationships`, {
            method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({other_id:parseInt(other), relationship_type:type, relationship_score:score, note})
        });
        const d = await res.json();
        if(d.status==='ok'){ loadRelationshipCard(pid, document.getElementById('playerDetailBody')); }
        else alert('保存失败');
    }catch(e){ alert('保存失败：'+e.message); }
}

async function deleteRelationship(pid, rid){
    if(!confirm('确认删除该关系记录？')) return;
    try{
        await fetch(`/api/players/${pid}/relationships/${rid}`, {method:'DELETE'});
        loadRelationshipCard(pid, document.getElementById('playerDetailBody'));
    }catch(e){ alert('删除失败'); }
}

async function openPlayerModal(id) {
    const modal = document.getElementById('playerModal');
    document.getElementById('playerModalTitle').textContent = id ? '编辑玩家' : '添加玩家';
    const fields = ['playerNameInput','playerRealName','playerPreferredName','playerPhone','playerWechat',
        'playerGender','playerBirthday','playerDan','playerDanSource','playerTypeSelect','playerSkillLevel',
        'playerActivityLevel','playerMaintenancePriority','playerIsOrganizer','playerOrganizerLevel',
        'playerArea','playerOccupation','playerIndustry','playerSourceChannel','playerIntroducer',
        'playerDrinkPreference','playerPriceSensitivity','playerMarketingTags','playerRiskTags',
        'playerQcosId','playerNotes'];
    if (id) {
        const res = await fetch(`/api/players/${id}/detail`);
        const p = await res.json();
        document.getElementById('playerId').value = p.id;
        document.getElementById('playerNameInput').value = p.name || '';
        document.getElementById('playerRealName').value = p.real_name || '';
        document.getElementById('playerPreferredName').value = p.preferred_name || '';
        document.getElementById('playerPhone').value = p.phone || '';
        document.getElementById('playerWechat').value = p.wechat || '';
        document.getElementById('playerGender').value = p.gender || '';
        document.getElementById('playerBirthday').value = p.birthday || '';
        document.getElementById('playerDan').value = p.dan || '';
        document.getElementById('playerDanSource').value = p.dan_source || 'self';
        document.getElementById('playerTypeSelect').value = p.player_type || '';
        document.getElementById('playerSkillLevel').value = p.skill_level || '';
        document.getElementById('playerActivityLevel').value = p.activity_level || '';
        document.getElementById('playerMaintenancePriority').value = p.maintenance_priority || '';
        document.getElementById('playerIsOrganizer').value = p.is_organizer ? '1' : '0';
        document.getElementById('playerOrganizerLevel').value = p.organizer_level || '';
        document.getElementById('playerArea').value = p.area || '';
        document.getElementById('playerOccupation').value = p.occupation || '';
        document.getElementById('playerIndustry').value = p.industry || '';
        document.getElementById('playerSourceChannel').value = p.source_channel || '';
        document.getElementById('playerIntroducer').value = p.introducer || '';
        document.getElementById('playerDrinkPreference').value = p.drink_preference || '';
        document.getElementById('playerPriceSensitivity').value = p.price_sensitivity || '';
        document.getElementById('playerMarketingTags').value = p.marketing_tags || '';
        document.getElementById('playerRiskTags').value = p.risk_tags || '';
        document.getElementById('playerQcosId').value = p.qcos_id || '';
        document.getElementById('playerNotes').value = p.notes || '';
    } else {
        document.getElementById('playerId').value = '';
        fields.forEach(fid => {
            const el = document.getElementById(fid);
            if (el.tagName === 'SELECT') el.value = el.querySelector('option')?.value || '';
            else el.value = '';
        });
        document.getElementById('playerDanSource').value = 'self';
        document.getElementById('playerIsOrganizer').value = '0';
    }
    new bootstrap.Modal(modal).show();
}

async function savePlayer() {
    const id = document.getElementById('playerId').value;
    const data = {
        name: document.getElementById('playerNameInput').value.trim(),
        real_name: document.getElementById('playerRealName').value.trim(),
        preferred_name: document.getElementById('playerPreferredName').value.trim(),
        phone: document.getElementById('playerPhone').value.trim(),
        wechat: document.getElementById('playerWechat').value.trim(),
        gender: document.getElementById('playerGender').value,
        birthday: document.getElementById('playerBirthday').value.trim(),
        dan: document.getElementById('playerDan').value.trim(),
        dan_source: document.getElementById('playerDanSource').value,
        player_type: document.getElementById('playerTypeSelect').value,
        skill_level: document.getElementById('playerSkillLevel').value.trim(),
        activity_level: document.getElementById('playerActivityLevel').value,
        maintenance_priority: document.getElementById('playerMaintenancePriority').value,
        is_organizer: document.getElementById('playerIsOrganizer').value === '1',
        organizer_level: document.getElementById('playerOrganizerLevel').value.trim(),
        area: document.getElementById('playerArea').value.trim(),
        occupation: document.getElementById('playerOccupation').value.trim(),
        industry: document.getElementById('playerIndustry').value.trim(),
        source_channel: document.getElementById('playerSourceChannel').value.trim(),
        introducer: document.getElementById('playerIntroducer').value.trim(),
        drink_preference: document.getElementById('playerDrinkPreference').value.trim(),
        price_sensitivity: document.getElementById('playerPriceSensitivity').value,
        marketing_tags: document.getElementById('playerMarketingTags').value.trim(),
        risk_tags: document.getElementById('playerRiskTags').value.trim(),
        qcos_id: document.getElementById('playerQcosId').value.trim(),
        notes: document.getElementById('playerNotes').value.trim(),
    };
    if (!data.name) { alert('请填写昵称'); return; }
    try {
        const url = id ? `/api/players/${id}` : '/api/players';
        const method = id ? 'PUT' : 'POST';
        const res = await fetch(url, { method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data) });
        if (res.ok) {
            bootstrap.Modal.getInstance(document.getElementById('playerModal')).hide();
            fetchPlayers();
        } else { const err = await res.json(); alert(err.error || '保存失败'); }
    } catch (e) { alert('网络错误'); }
}

function searchPlayers() { fetchPlayers(); }

// ===== 会员管理 =====
function initMembers() {
    fetchMembers();
    document.getElementById('memberSearch').addEventListener('input', () => {
        clearTimeout(window.memberSearchTimer);
        window.memberSearchTimer = setTimeout(fetchMembers, 300);
    });
}

let newMemberPlayersCache = [];

async function openNewMemberModal() {
    document.getElementById('newMemberBalance').value = '0';
    document.getElementById('newMemberPayment').value = 'cash';
    document.getElementById('newMemberSearch').value = '';
    document.getElementById('newMemberPlayer').value = '';
    document.getElementById('newMemberPhone').value = '';
    document.getElementById('newMemberPin').value = '';
    document.getElementById('newMemberNoResult').classList.add('d-none');
    hideNewPlayerForm();
    try {
        const res = await fetch('/api/players/non_members');
        newMemberPlayersCache = await res.json();
        renderNewMemberPlayerOptions(newMemberPlayersCache);
    } catch (e) { console.error(e); }
    new bootstrap.Modal(document.getElementById('newMemberModal')).show();
}

function renderNewMemberPlayerOptions(players) {
    const select = document.getElementById('newMemberPlayer');
    select.innerHTML = '<option value="">-- 选择非会员玩家 --</option>' +
        players.map(p => `<option value="${p.id}">${p.name}${p.dan ? ` [${p.dan}]` : ''}${p.phone ? ` (${p.phone})` : ''}</option>`).join('');
}

function filterNewMemberPlayers() {
    const kw = document.getElementById('newMemberSearch').value.trim().toLowerCase();
    const filtered = newMemberPlayersCache.filter(p =>
        (p.name || '').toLowerCase().includes(kw) ||
        (p.phone || '').toLowerCase().includes(kw) ||
        (p.dan || '').toLowerCase().includes(kw)
    );
    renderNewMemberPlayerOptions(filtered);
    const noResult = document.getElementById('newMemberNoResult');
    if (kw && filtered.length === 0) {
        noResult.classList.remove('d-none');
    } else {
        noResult.classList.add('d-none');
    }
    hideNewPlayerForm();
}

function showNewPlayerForm() {
    document.getElementById('newPlayerForm').classList.remove('d-none');
    document.getElementById('newMemberNewName').value = document.getElementById('newMemberSearch').value.trim();
    document.getElementById('newMemberNewPhone').value = '';
    document.getElementById('newMemberNewDan').value = '';
    document.getElementById('newMemberPlayer').value = '';
}

function hideNewPlayerForm() {
    document.getElementById('newPlayerForm').classList.add('d-none');
    document.getElementById('newMemberNewName').value = '';
    document.getElementById('newMemberNewPhone').value = '';
    document.getElementById('newMemberNewDan').value = '';
}

async function confirmNewMember() {
    const playerId = document.getElementById('newMemberPlayer').value;
    const balance = parseFloat(document.getElementById('newMemberBalance').value) || 0;
    const paymentMethod = document.getElementById('newMemberPayment').value;
    const phone = document.getElementById('newMemberPhone').value.trim();
    const pin = document.getElementById('newMemberPin').value.trim();
    const isNewPlayer = !document.getElementById('newPlayerForm').classList.contains('d-none');

    if (pin && !/^\d{6}$/.test(pin)) { alert('消费密码须为6位数字'); return; }

    let payload = { initial_balance: balance, payment_method: paymentMethod, phone, pin };

    if (isNewPlayer) {
        const name = document.getElementById('newMemberNewName').value.trim();
        if (!name) { alert('请输入新玩家姓名'); return; }
        payload.new_player_name = name;
        payload.new_player_phone = document.getElementById('newMemberNewPhone').value.trim();
        payload.new_player_dan = document.getElementById('newMemberNewDan').value.trim();
    } else {
        if (!playerId) { alert('请选择玩家'); return; }
        payload.player_id = parseInt(playerId);
    }

    if (balance < 0) { alert('初始充值金额不能为负数'); return; }
    try {
        const res = await fetch('/api/members', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        if (res.ok) {
            bootstrap.Modal.getInstance(document.getElementById('newMemberModal')).hide();
            fetchMembers();
        } else {
            const err = await res.json();
            alert(err.error || '开通会员失败');
        }
    } catch (e) { alert('网络错误'); }
}

async function fetchMembers() {
    const search = document.getElementById('memberSearch').value.trim();
    try {
        const res = await fetch(`/api/members${search ? '?search=' + encodeURIComponent(search) : ''}`);
        const members = await res.json();
        renderMembers(members);
        // 统计
        const totalBalance = members.reduce((s, m) => s + m.balance, 0);
        const totalRecharge = members.reduce((s, m) => s + m.total_recharge, 0);
        const totalSpent = members.reduce((s, m) => s + m.total_spent, 0);
        document.getElementById('totalMembers').textContent = members.length;
        document.getElementById('totalBalance').textContent = money(totalBalance);
        document.getElementById('totalRecharge').textContent = money(totalRecharge);
        document.getElementById('totalSpent').textContent = money(totalSpent);
    } catch (e) { console.error(e); }
}

function renderMembers(members) {
    const container = document.getElementById('memberList');
    if (members.length === 0) { container.innerHTML = '<small class="text-muted">暂无会员</small>'; return; }
    container.innerHTML = members.map(m => {
        const displayPhone = m.phone || m.player_phone || '无手机';
        const hasPin = m.pin_hash ? '<i class="bi bi-lock-fill text-success ms-1" title="已设消费密码"></i>' : '<i class="bi bi-unlock text-muted ms-1" title="未设消费密码"></i>';
        return `
        <div class="card mb-3">
            <div class="card-body">
                <div class="d-flex justify-content-between align-items-start">
                    <div>
                        <h6 class="mb-1">${m.player_name} ${m.dan ? `<small class="text-muted">${m.dan}</small>` : ''}${hasPin}</h6>
                        <small class="text-muted">${displayPhone} | 会员#${m.id}</small>
                    </div>
                    <div class="text-end">
                        <div class="fs-4 fw-bold text-success">${money(m.balance)}</div>
                        <small class="text-muted">当前余额</small>
                    </div>
                </div>
                <div class="d-flex gap-3 mt-2 text-muted" style="font-size:12px">
                    <span>累计充值: ${money(m.total_recharge)}</span>
                    <span>累计消费: ${money(m.total_spent)}</span>
                </div>
                <div class="d-flex gap-2 mt-3">
                    <button class="btn btn-sm btn-primary" onclick="openRechargeModal(${m.id}, '${escapeJs(m.player_name)}', ${m.balance})"><i class="bi bi-wallet2"></i> 充值</button>
                    <button class="btn btn-sm btn-outline-primary" onclick="openRechargeHistory(${m.id}, '${escapeJs(m.player_name)}')"><i class="bi bi-clock-history"></i> 记录</button>
                    <button class="btn btn-sm btn-outline-secondary" onclick="openEditMemberModal(${m.id}, '${escapeJs(m.player_name)}', '${escapeJs(displayPhone)}')"><i class="bi bi-pencil"></i> 编辑</button>
                    <button class="btn btn-sm btn-outline-danger" onclick="deleteMember(${m.id}, '${escapeJs(m.player_name)}', ${m.balance})"><i class="bi bi-trash"></i> 删除</button>
                </div>
            </div>
        </div>
    `}).join('');
}

function openRechargeModal(id, name, balance) {
    document.getElementById('rechargeMemberId').value = id;
    document.getElementById('rechargeMemberName').textContent = name;
    document.getElementById('rechargeName').textContent = name;
    document.getElementById('rechargeBalance').textContent = balance.toFixed(2);
    document.getElementById('rechargeAmount').value = '';
    document.getElementById('rechargeNote').value = '';
    new bootstrap.Modal(document.getElementById('rechargeModal')).show();
}

function setRechargeAmount(n) {
    document.getElementById('rechargeAmount').value = n;
}

async function confirmRecharge() {
    const id = document.getElementById('rechargeMemberId').value;
    const amount = parseFloat(document.getElementById('rechargeAmount').value);
    if (!amount || amount <= 0) { alert('请输入有效金额'); return; }
    const payment = document.getElementById('rechargePayment').value;
    const note = document.getElementById('rechargeNote').value.trim();
    try {
        const res = await fetch(`/api/members/${id}/recharge`, {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ amount, payment_method: payment, note })
        });
        if (res.ok) {
            const data = await res.json();
            alert(`充值成功！\n新余额: ${money(data.balance)}`);
            bootstrap.Modal.getInstance(document.getElementById('rechargeModal')).hide();
            fetchMembers();
        } else { const err = await res.json(); alert(err.error || '充值失败'); }
    } catch (e) { alert('网络错误'); }
}

async function openRechargeHistory(id, name) {
    document.getElementById('historyMemberName').textContent = name;
    try {
        const res = await fetch(`/api/members/${id}/recharges`);
        const records = await res.json();
        const tbody = document.getElementById('rechargeHistoryList');
        if (records.length === 0) { tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted">暂无记录</td></tr>'; }
        else {
            tbody.innerHTML = records.map(r => `<tr>
                <td>${formatDateTime(r.created_at)}</td>
                <td class="text-success fw-bold">+¥${r.amount}</td>
                <td>¥${r.balance_before} → ¥${r.balance_after}</td>
                <td>${r.payment_method || '-'}</td>
                <td>${r.operator || '-'} ${r.note ? '(' + r.note + ')' : ''}</td>
            </tr>`).join('');
        }
        new bootstrap.Modal(document.getElementById('rechargeHistoryModal')).show();
    } catch (e) { alert('获取记录失败'); }
}

function openEditMemberModal(id, name, phone) {
    document.getElementById('editMemberId').value = id;
    document.getElementById('editMemberName').textContent = name;
    document.getElementById('editMemberPhone').value = phone === '无手机' ? '' : phone;
    document.getElementById('editMemberPin').value = '';
    new bootstrap.Modal(document.getElementById('editMemberModal')).show();
}

async function confirmEditMember() {
    const id = document.getElementById('editMemberId').value;
    const phone = document.getElementById('editMemberPhone').value.trim();
    const pin = document.getElementById('editMemberPin').value.trim();
    if (pin && !/^\d{6}$/.test(pin)) { alert('消费密码须为6位数字'); return; }
    const payload = { phone };
    if (pin) payload.pin = pin;
    try {
        const res = await fetch(`/api/members/${id}`, {
            method: 'PUT', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        if (res.ok) {
            bootstrap.Modal.getInstance(document.getElementById('editMemberModal')).hide();
            fetchMembers();
        } else {
            const err = await res.json();
            alert(err.error || '保存失败');
        }
    } catch (e) { alert('网络错误'); }
}

async function deleteMember(id, name, balance) {
    balance = balance || 0;
    let msg = `确认删除会员 "${name}"？\n删除后该玩家将恢复为非会员，历史充值/消费记录仍保留。`;
    if (balance > 0) {
        msg = `会员 "${name}" 当前余额 ${money(balance)}。\n删除后余额将作废且不可恢复，是否继续？\n（建议先退款或消费完余额后再删除）`;
    }
    if (!confirm(msg)) return;

    try {
        let res = await fetch(`/api/members/${id}`, { method: 'DELETE' });
        if (res.status === 409) {
            const data = await res.json();
            if (!confirm(`余额未清零：${money(data.balance)}。\n强制删除将清空会员绑定，历史记录保留但余额作废，是否继续？`)) return;
            res = await fetch(`/api/members/${id}?force=1`, { method: 'DELETE' });
        }
        if (res.ok) {
            fetchMembers();
        } else {
            const err = await res.json().catch(() => ({}));
            alert(err.error || '删除失败');
        }
    } catch (e) { alert('网络错误'); }
}

// ===== 用户管理 =====
function initUsers() { fetchUsers(); }

async function fetchUsers() {
    try {
        const res = await fetch('/api/users');
        const users = await res.json();
        const tbody = document.getElementById('userList');
        const roleLabels = {'admin': '管理员', 'staff': '店员', 'viewer': '只读'};
        const roleBadges = {'admin': 'bg-danger', 'staff': 'bg-primary', 'viewer': 'bg-secondary'};
        tbody.innerHTML = users.map(u => `<tr>
            <td>${u.username}</td>
            <td>${u.name}</td>
            <td><span class="badge ${roleBadges[u.role] || 'bg-secondary'}">${roleLabels[u.role] || u.role}</span></td>
            <td>${u.is_active ? '<span class="badge bg-success">启用</span>' : '<span class="badge bg-secondary">禁用</span>'}</td>
            <td>${u.last_login ? formatDateTime(u.last_login) : '-'}</td>
            <td>
                <button class="btn btn-sm btn-outline-primary" onclick="openUserModal(${u.id})">编辑</button>
                ${u.username !== 'admin' ? `<button class="btn btn-sm btn-outline-danger" onclick="deleteUser(${u.id})">删除</button>` : ''}
            </td>
        </tr>`).join('');
    } catch (e) { console.error(e); }
}

async function openUserModal(id) {
    const modal = document.getElementById('userModal');
    document.getElementById('userModalTitle').textContent = id ? '编辑用户' : '添加用户';
    document.getElementById('passwordHint').textContent = id ? '(留空不修改)' : '*';
    if (id) {
        const res = await fetch('/api/users');
        const users = await res.json();
        const u = users.find(x => x.id === id);
        if (!u) return;
        document.getElementById('userId').value = u.id;
        document.getElementById('userUsername').value = u.username;
        document.getElementById('userUsername').disabled = true;
        document.getElementById('userName').value = u.name;
        document.getElementById('userPassword').value = '';
        document.getElementById('userRole').value = u.role;
        document.getElementById('userActive').checked = !!u.is_active;
    } else {
        document.getElementById('userId').value = '';
        document.getElementById('userUsername').value = '';
        document.getElementById('userUsername').disabled = false;
        document.getElementById('userName').value = '';
        document.getElementById('userPassword').value = '';
        document.getElementById('userRole').value = 'staff';
        document.getElementById('userActive').checked = true;
    }
    new bootstrap.Modal(modal).show();
}

async function saveUser() {
    const id = document.getElementById('userId').value;
    const data = {
        name: document.getElementById('userName').value.trim(),
        role: document.getElementById('userRole').value,
        is_active: document.getElementById('userActive').checked,
    };
    const password = document.getElementById('userPassword').value;
    if (!id && !password) { alert('请输入密码'); return; }
    if (password) data.password = password;
    if (!id) data.username = document.getElementById('userUsername').value.trim();
    if (!data.name) { alert('请输入姓名'); return; }
    try {
        const url = id ? `/api/users/${id}` : '/api/users';
        const method = id ? 'PUT' : 'POST';
        const res = await fetch(url, { method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data) });
        if (res.ok) {
            bootstrap.Modal.getInstance(document.getElementById('userModal')).hide();
            fetchUsers();
        } else { const err = await res.json(); alert(err.error || '保存失败'); }
    } catch (e) { alert('网络错误'); }
}

async function deleteUser(id) {
    if (!confirm('确定删除该用户？')) return;
    try {
        await fetch(`/api/users/${id}`, {method: 'DELETE'});
        fetchUsers();
    } catch (e) { alert('删除失败'); }
}


// ============================================================
// ===== 场务管理 =====
// ============================================================

let staffAutocompleteTimer = null;

// Tab 切换
function switchTab(tab) {
    document.querySelectorAll('#staffTabs .nav-link').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tab === tab);
    });
    document.querySelectorAll('[id^="tab-"]').forEach(el => {
        el.style.display = el.id === `tab-${tab}` ? '' : 'none';
    });
    if (tab === 'staff') loadStaffList();
    if (tab === 'incentive') loadIncentiveTiers();
    if (tab === 'settlements') loadSettlements();
}

// 看板
async function loadDashboard() {
    try {
        const res = await fetch('/api/staff/dashboard');
        const data = await res.json();
        document.getElementById('statStaffCount').textContent = data.staff.length;
        document.getElementById('statMonthCount').textContent = data.month_count;
        document.getElementById('statMonthTotal').textContent = `¥${data.month_total.toFixed(2)}`;
        let pendingTotal = data.staff.reduce((sum, s) => sum + s.pending_amount, 0);
        document.getElementById('statPendingTotal').textContent = `¥${pendingTotal.toFixed(2)}`;
    } catch (e) { console.error('Dashboard error', e); }
}

// 场务列表
async function loadStaffList() {
    try {
        const res = await fetch('/api/staff?status=active');
        const data = await res.json();
        const tbody = document.getElementById('staffTableBody');
        if (!data.length) {
            tbody.innerHTML = '<tr><td colspan="9" class="text-center text-muted py-4">暂无场务，点击右上角新增</td></tr>';
            return;
        }
        tbody.innerHTML = data.map(s => `
            <tr>
                <td><strong>${s.name}</strong></td>
                <td><span class="badge ${s.staff_type === 'competitive' ? 'bg-danger' : 'bg-info'}">${s.type_label}</span></td>
                <td>${(s.commission_rate * 100).toFixed(1)}%</td>
                <td><span class="badge ${s.status === 'active' ? 'bg-success' : 'bg-secondary'}">${s.status_label}</span></td>
                <td>${s.joined_date || '-'}</td>
                <td class="text-success">¥${(s.total_paid || 0).toFixed(2)}</td>
                <td class="text-danger">${s.pending_count > 0 ? `¥${s.pending_amount.toFixed(2)} (${s.pending_count}笔)` : '-'}</td>
                <td><small class="text-muted">${s.notes || '-'}</small></td>
                <td>
                    <button class="btn btn-sm btn-outline-primary" onclick="editStaff(${s.id})"><i class="bi bi-pencil"></i></button>
                    <button class="btn btn-sm btn-outline-danger" onclick="deleteStaff(${s.id}, '${s.name}')"><i class="bi bi-x-circle"></i></button>
                </td>
            </tr>
        `).join('');
    } catch (e) { console.error('Load staff error', e); }
}

// 新增场务弹窗
function showAddStaffModal() {
    document.getElementById('staffModalTitle').textContent = '新增场务';
    document.getElementById('staffEditId').value = '';
    document.getElementById('staffName').value = '';
    document.getElementById('staffPlayerId').value = '';
    document.getElementById('staffType').value = 'entertainment';
    document.getElementById('staffStatus').value = 'active';
    document.getElementById('staffCommission').value = '';
    document.getElementById('staffJoinedDate').value = todayStr();
    document.getElementById('staffNotes').value = '';
    document.getElementById('staffAutocomplete').style.display = 'none';
    new bootstrap.Modal(document.getElementById('staffModal')).show();
}

// 编辑场务
async function editStaff(id) {
    try {
        const res = await fetch(`/api/staff`);
        const list = await res.json();
        const s = list.find(x => x.id === id);
        if (!s) return;
        document.getElementById('staffModalTitle').textContent = '编辑场务';
        document.getElementById('staffEditId').value = s.id;
        document.getElementById('staffName').value = s.name;
        document.getElementById('staffPlayerId').value = s.player_id || '';
        document.getElementById('staffType').value = s.staff_type;
        document.getElementById('staffStatus').value = s.status;
        document.getElementById('staffCommission').value = s.commission_rate;
        document.getElementById('staffJoinedDate').value = s.joined_date || '';
        document.getElementById('staffNotes').value = s.notes || '';
        document.getElementById('staffAutocomplete').style.display = 'none';
        updateCommissionDisplay();
        new bootstrap.Modal(document.getElementById('staffModal')).show();
    } catch (e) { alert('加载失败'); }
}

// 删除场务
async function deleteStaff(id, name) {
    if (!confirm(`确定将 ${name} 设为离职？`)) return;
    try {
        await fetch(`/api/staff/${id}`, {method: 'DELETE'});
        loadStaffList();
        loadDashboard();
    } catch (e) { alert('操作失败'); }
}

// 玩家搜索（场务弹窗用）
function searchPlayersForStaff(query) {
    clearTimeout(staffAutocompleteTimer);
    if (!query.trim()) {
        document.getElementById('staffAutocomplete').style.display = 'none';
        return;
    }
    staffAutocompleteTimer = setTimeout(async () => {
        try {
            const res = await fetch(`/api/players/search?name=${encodeURIComponent(query)}`);
            const data = await res.json();
            const box = document.getElementById('staffAutocomplete');
            if (!data.length) { box.style.display = 'none'; return; }
            box.innerHTML = data.map(p => `
                <div class="autocomplete-item" onclick="selectStaffPlayer(${p.id}, '${p.name}')">
                    ${p.name} <span class="ac-dan">${p.dan || ''}</span>
                </div>
            `).join('');
            box.style.display = 'block';
        } catch (e) { /* ignore */ }
    }, 300);
}

function selectStaffPlayer(id, name) {
    document.getElementById('staffName').value = name;
    document.getElementById('staffPlayerId').value = id;
    document.getElementById('staffAutocomplete').style.display = 'none';
}

// 提成比例实时显示百分比
function updateCommissionDisplay() {
    const val = parseFloat(document.getElementById('staffCommission').value) || 0;
    document.getElementById('commissionPercent').textContent = (val * 100).toFixed(0) + '%';
}

// 保存场务
async function saveStaff() {
    const id = document.getElementById('staffEditId').value;
    const name = document.getElementById('staffName').value.trim();
    if (!name) { alert('请输入姓名'); return; }
    const data = {
        name,
        player_id: document.getElementById('staffPlayerId').value || null,
        staff_type: document.getElementById('staffType').value,
        status: document.getElementById('staffStatus').value,
        commission_rate: parseFloat(document.getElementById('staffCommission').value) || 0,
        joined_date: document.getElementById('staffJoinedDate').value,
        notes: document.getElementById('staffNotes').value,
    };
    try {
        const url = id ? `/api/staff/${id}` : '/api/staff';
        const method = id ? 'PUT' : 'POST';
        const res = await fetch(url, {method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
        if (res.ok) {
            bootstrap.Modal.getInstance(document.getElementById('staffModal')).hide();
            loadStaffList();
            loadDashboard();
        } else { const err = await res.json(); alert(err.error || '保存失败'); }
    } catch (e) { alert('网络错误'); }
}

// ===== 激励档位 =====
async function loadIncentiveTiers() {
    try {
        const res = await fetch('/api/incentive-tiers');
        const data = await res.json();
        const tbody = document.getElementById('incentiveTableBody');
        if (!data.length) {
            tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-4">暂无档位，点击右上角新增</td></tr>';
            return;
        }
        tbody.innerHTML = data.map(t => `
            <tr>
                <td>${t.sort_order}</td>
                <td>¥${t.min_amount.toFixed(2)}</td>
                <td>${t.max_amount ? '¥' + t.max_amount.toFixed(2) : '<span class="text-muted">无上限</span>'}</td>
                <td class="text-success fw-bold">¥${t.bonus_amount.toFixed(2)}</td>
                <td>${t.description || '-'}</td>
                <td>${t.is_active ? '<span class="badge bg-success">启用</span>' : '<span class="badge bg-secondary">停用</span>'}</td>
                <td>
                    <button class="btn btn-sm btn-outline-primary" onclick="editIncentive(${t.id})"><i class="bi bi-pencil"></i></button>
                    <button class="btn btn-sm btn-outline-danger" onclick="deleteIncentive(${t.id})"><i class="bi bi-trash"></i></button>
                </td>
            </tr>
        `).join('');
    } catch (e) { console.error('Load incentive error', e); }
}

function showAddIncentiveModal() {
    document.getElementById('incentiveModalTitle').textContent = '新增激励档位';
    document.getElementById('incentiveEditId').value = '';
    document.getElementById('incentiveMin').value = '';
    document.getElementById('incentiveMax').value = '';
    document.getElementById('incentiveBonus').value = '';
    document.getElementById('incentiveDesc').value = '';
    document.getElementById('incentiveSort').value = '0';
    new bootstrap.Modal(document.getElementById('incentiveModal')).show();
}

async function editIncentive(id) {
    try {
        const res = await fetch('/api/incentive-tiers');
        const list = await res.json();
        const t = list.find(x => x.id === id);
        if (!t) return;
        document.getElementById('incentiveModalTitle').textContent = '编辑激励档位';
        document.getElementById('incentiveEditId').value = t.id;
        document.getElementById('incentiveMin').value = t.min_amount;
        document.getElementById('incentiveMax').value = t.max_amount || '';
        document.getElementById('incentiveBonus').value = t.bonus_amount;
        document.getElementById('incentiveDesc').value = t.description || '';
        document.getElementById('incentiveSort').value = t.sort_order;
        new bootstrap.Modal(document.getElementById('incentiveModal')).show();
    } catch (e) { alert('加载失败'); }
}

async function deleteIncentive(id) {
    if (!confirm('确定删除该档位？')) return;
    try {
        await fetch(`/api/incentive-tiers/${id}`, {method: 'DELETE'});
        loadIncentiveTiers();
        loadDashboard();
    } catch (e) { alert('删除失败'); }
}

async function saveIncentive() {
    const id = document.getElementById('incentiveEditId').value;
    const minAmt = parseFloat(document.getElementById('incentiveMin').value);
    const bonus = parseFloat(document.getElementById('incentiveBonus').value);
    if (isNaN(minAmt) || isNaN(bonus)) { alert('请填写正确的金额'); return; }
    const data = {
        min_amount: minAmt,
        max_amount: parseFloat(document.getElementById('incentiveMax').value) || null,
        bonus_amount: bonus,
        description: document.getElementById('incentiveDesc').value,
        sort_order: parseInt(document.getElementById('incentiveSort').value) || 0,
    };
    try {
        const url = id ? `/api/incentive-tiers/${id}` : '/api/incentive-tiers';
        const method = id ? 'PUT' : 'POST';
        const res = await fetch(url, {method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
        if (res.ok) {
            bootstrap.Modal.getInstance(document.getElementById('incentiveModal')).hide();
            loadIncentiveTiers();
            loadDashboard();
        } else { const err = await res.json(); alert(err.error || '保存失败'); }
    } catch (e) { alert('网络错误'); }
}

// ===== 结算记录 =====
async function loadSettlements() {
    try {
        // 先加载场务列表到筛选器
        const staffRes = await fetch('/api/staff?status=active');
        const staffList = await staffRes.json();
        const filterSel = document.getElementById('settlementStaffFilter');
        const currentVal = filterSel.value;
        filterSel.innerHTML = '<option value="">全部场务</option>' +
            staffList.map(s => `<option value="${s.id}">${s.name} (${s.type_label})</option>`).join('');
        filterSel.value = currentVal;

        // 同步到新增结算弹窗的场务下拉
        const modalSel = document.getElementById('settlementStaff');
        modalSel.innerHTML = '<option value="">请选择</option>' +
            staffList.map(s => `<option value="${s.id}" data-rate="${s.commission_rate}">${s.name} (${s.type_label})</option>`).join('');

        // 加载结算记录
        const staffId = filterSel.value;
        const status = document.getElementById('settlementStatusFilter').value;
        let url = '/api/staff/settlements';
        const params = [];
        if (staffId) params.push(`staff_id=${staffId}`);
        if (status) params.push(`status=${status}`);
        if (params.length) url += '?' + params.join('&');

        const res = await fetch(url);
        const data = await res.json();
        const tbody = document.getElementById('settlementTableBody');
        if (!data.length) {
            tbody.innerHTML = '<tr><td colspan="11" class="text-center text-muted py-4">暂无结算记录</td></tr>';
            return;
        }
        let totalPayout = 0, totalCommission = 0, totalIncentive = 0;
        tbody.innerHTML = data.map(s => {
            totalPayout += s.total_payout;
            totalCommission += s.commission_amount;
            totalIncentive += s.incentive_amount;
            return `
            <tr>
                <td>${s.settlement_date}</td>
                <td><strong>${s.staff_name}</strong></td>
                <td><span class="badge ${s.staff_type === 'competitive' ? 'bg-danger' : 'bg-info'}">${s.staff_type_label}</span></td>
                <td><small>${s.period_start} ~ ${s.period_end}</small></td>
                <td>¥${s.total_gmv.toFixed(2)}</td>
                <td>${(s.commission_rate * 100).toFixed(1)}%</td>
                <td>¥${s.commission_amount.toFixed(2)}</td>
                <td class="text-success">${s.incentive_amount > 0 ? '¥' + s.incentive_amount.toFixed(2) : '-'}<br><small class="text-muted">${s.incentive_desc || ''}</small></td>
                <td class="fw-bold text-danger">¥${s.total_payout.toFixed(2)}</td>
                <td><span class="badge ${s.status === 'paid' ? 'bg-success' : 'bg-warning'}">${s.status_label}</span></td>
                <td>
                    ${s.status === 'pending' ? `<button class="btn btn-sm btn-outline-success" onclick="markPaid(${s.id})" title="标记已发放"><i class="bi bi-check2-circle"></i></button>` : ''}
                    <button class="btn btn-sm btn-outline-secondary" onclick="editSettlementNote(${s.id}, '${(s.note||'').replace(/'/g, "\\'")}')" title="编辑备注"><i class="bi bi-chat-text"></i></button>
                </td>
            </tr>
        `;}).join('') + `
            <tr class="table-total">
                <td colspan="6">合计</td>
                <td>¥${totalCommission.toFixed(2)}</td>
                <td>¥${totalIncentive.toFixed(2)}</td>
                <td>¥${totalPayout.toFixed(2)}</td>
                <td colspan="2"></td>
            </tr>
        `;
    } catch (e) { console.error('Load settlements error', e); }
}

function showAddSettlementModal() {
    document.getElementById('settlementStaff').value = '';
    document.getElementById('settlementDate').value = todayStr();
    document.getElementById('settlementPeriodStart').value = todayStr();
    document.getElementById('settlementPeriodEnd').value = todayStr();
    document.getElementById('settlementGMV').value = '';
    document.getElementById('settlementCommissionRate').value = '';
    document.getElementById('settlementIncentive').value = '0';
    document.getElementById('settlementNote').value = '';
    document.getElementById('settlementTotalPreview').textContent = '¥0.00';
    document.getElementById('incentiveHint').textContent = '输入GMV后自动匹配档位';
    new bootstrap.Modal(document.getElementById('settlementModal')).show();
}

function onSettlementStaffChange() {
    const sel = document.getElementById('settlementStaff');
    const opt = sel.options[sel.selectedIndex];
    const rate = opt.dataset.rate;
    if (rate) {
        document.getElementById('settlementCommissionRate').value = rate;
        updateSettlementCommissionDisplay();
    }
    previewSettlement();
}

function updateSettlementCommissionDisplay() {
    const val = parseFloat(document.getElementById('settlementCommissionRate').value) || 0;
    document.getElementById('settlementCommissionPercent').textContent = (val * 100).toFixed(0) + '%';
}

// 实时预览结算金额
let settlementPreviewTimer = null;
function previewSettlement() {
    clearTimeout(settlementPreviewTimer);
    settlementPreviewTimer = setTimeout(async () => {
        const gmv = parseFloat(document.getElementById('settlementGMV').value) || 0;
        const rate = parseFloat(document.getElementById('settlementCommissionRate').value) || 0;
        let incentive = parseFloat(document.getElementById('settlementIncentive').value) || 0;
        updateSettlementCommissionDisplay();
        const commission = gmv * rate;
        // 自动匹配激励档位提示
        try {
            const tierRes = await fetch('/api/incentive-tiers');
            const tiers = await tierRes.json();
            const matched = tiers.filter(t => t.is_active && gmv >= t.min_amount && (!t.max_amount || gmv < t.max_amount))
                                 .sort((a, b) => b.min_amount - a.min_amount)[0];
            if (matched) {
                document.getElementById('incentiveHint').innerHTML = `自动匹配: ¥${matched.bonus_amount} (${matched.description || '档位'})`;
                if (incentive === 0) {
                    incentive = matched.bonus_amount;
                    document.getElementById('settlementIncentive').value = incentive;
                }
            } else {
                document.getElementById('incentiveHint').textContent = '无匹配档位';
            }
        } catch (e) { /* ignore */ }
        const total = commission + incentive;
        document.getElementById('settlementTotalPreview').textContent = `¥${total.toFixed(2)}`;
    }, 300);
}

async function saveSettlement() {
    const staffId = document.getElementById('settlementStaff').value;
    const gmv = parseFloat(document.getElementById('settlementGMV').value);
    if (!staffId) { alert('请选择场务'); return; }
    if (isNaN(gmv) || gmv < 0) { alert('请输入正确的总金额'); return; }
    const data = {
        staff_id: parseInt(staffId),
        settlement_date: document.getElementById('settlementDate').value,
        period_start: document.getElementById('settlementPeriodStart').value,
        period_end: document.getElementById('settlementPeriodEnd').value,
        total_gmv: gmv,
        commission_rate: parseFloat(document.getElementById('settlementCommissionRate').value) || 0,
        incentive_amount: parseFloat(document.getElementById('settlementIncentive').value) || 0,
        note: document.getElementById('settlementNote').value,
        status: 'pending',
    };
    try {
        const res = await fetch('/api/staff/settlements', {
            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
        });
        if (res.ok) {
            bootstrap.Modal.getInstance(document.getElementById('settlementModal')).hide();
            loadSettlements();
            loadDashboard();
        } else { const err = await res.json(); alert(err.error || '保存失败'); }
    } catch (e) { alert('网络错误'); }
}

async function markPaid(id) {
    if (!confirm('确认标记为已发放？')) return;
    try {
        await fetch(`/api/staff/settlements/${id}`, {
            method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({status: 'paid'})
        });
        loadSettlements();
        loadDashboard();
    } catch (e) { alert('操作失败'); }
}

async function editSettlementNote(id, currentNote) {
    const note = prompt('编辑备注:', currentNote);
    if (note === null) return;
    try {
        await fetch(`/api/staff/settlements/${id}`, {
            method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({note})
        });
        loadSettlements();
    } catch (e) { alert('操作失败'); }
}

// 场务页初始化
async function initStaffPage() {
    await loadDashboard();
    await loadStaffList();
}

// 提成比例输入实时显示
document.addEventListener('input', function(e) {
    if (e.target.id === 'staffCommission') updateCommissionDisplay();
});

// ============================================================
// ===== 竞争情报系统 =====
// ============================================================

let ciCompetitors = [];
let ciScoreChart = null, ciRadarChart = null, ciTrafficChart = null;
// 各子模块编辑中的记录ID（0=新增）
let ciPricingEditId = 0, ciTrafficEditId = 0, ciSegEditId = 0, ciKpEditId = 0,
    ciScoreEditId = 0, ciCommEditId = 0, ciMktEditId = 0;
// 各子模块列表缓存（编辑回填用）
let ciPricingCache = [], ciTrafficCache = [], ciSegCache = [], ciKpCache = [],
    ciScoreCache = [], ciCommCache = [], ciMktCache = [];

// ===== 竞争情报系统 =====
let CI_META = {};  // 元数据常量（从 /api/ci/meta 加载）

async function initCompetition() {
    // 先加载元数据常量（时间段/玩家类型/评分维度等）
    try {
        const mres = await fetch('/api/ci/meta');
        CI_META = await mres.json();
    } catch (e) { console.error('CI_META加载失败', e); }
    await ciLoadCompetitors();
    await ciLoadDashboard();
    // Tab切换时懒加载
    document.querySelectorAll('#ciTabs a[data-bs-toggle="tab"]').forEach(a => {
        a.addEventListener('shown.bs.tab', e => {
            const href = e.target.getAttribute('href');
            if (href === '#ci-pricing') ciLoadPricing();
            else if (href === '#ci-traffic') ciLoadTraffic();
            else if (href === '#ci-segments') ciLoadSegments();
            else if (href === '#ci-keyplayers') ciLoadKeyPlayers();
            else if (href === '#ci-scores') ciLoadServiceScores();
            else if (href === '#ci-community') ciLoadCommunity();
            else if (href === '#ci-marketing') ciLoadMarketing();
            else if (href === '#ci-analysis') ciLoadAnalysis();
        });
    });
}

// --- Dashboard ---
async function ciLoadDashboard() {
    try {
        const res = await fetch('/api/ci/dashboard');
        const d = await res.json();
        // 统计卡片
        const cards = document.getElementById('ciDashCards');
        const compCount = d.competitors.length;
        const dataCount = Object.values(d.counts).reduce((a,b) => a+b, 0);
        cards.innerHTML = [
            ['竞争店数', compCount, 'primary'],
            ['数据记录', dataCount, 'info'],
            ['价格记录', d.counts.ci_pricing || 0, 'success'],
            ['客流观察', d.counts.ci_traffic || 0, 'warning'],
        ].map(([label, val, color]) =>
            `<div class="col-md-3"><div class="card text-bg-${color}"><div class="card-body py-2 px-3">
                <div class="small">${label}</div><div class="h4 mb-0">${val}</div>
            </div></div></div>`
        ).join('');

        // 评分柱状图
        if (ciScoreChart) ciScoreChart.destroy();
        ciScoreChart = new Chart(document.getElementById('ciScoreChart'), {
            type: 'bar',
            data: {
                labels: d.scores.map(s => s.name),
                datasets: [{
                    label: '竞争评分',
                    data: d.scores.map(s => s.total_score),
                    backgroundColor: d.scores.map(s => s.is_self ? 'rgba(255,99,132,0.6)' : 'rgba(54,162,235,0.6)'),
                }]
            },
            options: {responsive: true, scales: {y: {beginAtZero: true, max: 100}}, plugins: {legend: {display: false}}}
        });

        // 雷达图
        const radarDims = ['env_score','cleanliness_score','ac_air_score','seat_score','staff_attitude_score','response_speed_score','newcomer_friendly_score','regular_maintain_score','community_atmosphere_score','overall_score'];
        const radarLabels = ['环境','卫生','空调','座椅','态度','回复','新人友好','老客维护','社群','整体'];
        if (ciRadarChart) ciRadarChart.destroy();
        ciRadarChart = new Chart(document.getElementById('ciRadarChart'), {
            type: 'radar',
            data: {
                labels: radarLabels,
                datasets: d.latest_scores.map((s, i) => ({
                    label: d.competitors.find(c => c.id === s.competitor_id)?.name || '未知',
                    data: radarDims.map(dim => s[dim] || 0),
                    borderColor: ['rgba(255,99,132,1)','rgba(54,162,235,1)','rgba(75,192,192,1)'][i % 3],
                    backgroundColor: ['rgba(255,99,132,0.1)','rgba(54,162,235,0.1)','rgba(75,192,192,0.1)'][i % 3],
                }))
            },
            options: {responsive: true, scales: {r: {beginAtZero: true, max: 10}}}
        });

        // 客流趋势
        if (ciTrafficChart) ciTrafficChart.destroy();
        const trendDates = [...new Set(d.traffic_trend.map(t => t.obs_date))].sort();
        const trendComps = [...new Set(d.traffic_trend.map(t => t.name))];
        ciTrafficChart = new Chart(document.getElementById('ciTrafficChart'), {
            type: 'line',
            data: {
                labels: trendDates,
                datasets: trendComps.map((name, i) => ({
                    label: name,
                    data: trendDates.map(dt => {
                        const r = d.traffic_trend.find(t => t.obs_date === dt && t.name === name);
                        return r ? Math.round(r.avg_players * 10) / 10 : null;
                    }),
                    borderColor: ['rgba(255,99,132,1)','rgba(54,162,235,1)','rgba(75,192,192,1)'][i % 3],
                    fill: false,
                }))
            },
            options: {responsive: true, scales: {y: {beginAtZero: true}}}
        });

        // 价格对比
        const pc = document.getElementById('ciPricingCompare');
        if (d.latest_pricing.length === 0) {
            pc.innerHTML = '<small class="text-muted">暂无价格数据</small>';
        } else {
            pc.innerHTML = '<table class="table table-sm"><thead><tr><th>店铺</th><th>普通</th><th>通宵</th><th>会员</th><th>新客</th></tr></thead><tbody>' +
                d.latest_pricing.map(p => `<tr><td>${p.competitor_name}</td><td>${p.normal_price||'-'}</td><td>${p.overnight_price||'-'}</td><td>${p.member_price||'-'}</td><td>${p.newcustomer_offer||'-'}</td></tr>`).join('') +
                '</tbody></table>';
        }
    } catch (e) { console.error('CI dashboard error:', e); }
}

// --- 竞争店信息 ---
async function ciLoadCompetitors() {
    try {
        const res = await fetch('/api/ci/competitors');
        ciCompetitors = await res.json();
        const statusMap = CI_META.operating_status || {};
        // 渲染表格
        const body = document.getElementById('ciCompetitorBody');
        body.innerHTML = ciCompetitors.map(c => `<tr ${c.is_self ? 'class="table-info"' : ''}>
            <td><strong>${c.name}</strong>${c.is_self ? ' <span class="badge bg-info">本店</span>' : ''}</td>
            <td>${c.is_self ? '<span class="badge bg-success">—</span>' : (statusMap[c.operating_status] ? `<span class="badge ${c.operating_status==='active'?'bg-success':c.operating_status==='preparing'?'bg-warning text-dark':'bg-secondary'}">${statusMap[c.operating_status]}</span>` : '<span class="badge bg-secondary">未知</span>')}</td>
            <td>${c.address||'-'}</td><td>${c.open_date||'-'}</td><td>${c.area_sqm||'-'}</td>
            <td>${c.table_4port||0}/${c.table_8port||0}</td>
            <td>${c.positioning||'-'}</td><td>${c.target_customers||'-'}</td>
            <td>${(c.known_advantages||'-').substring(0,30)}</td>
            <td>${(c.known_weaknesses||'-').substring(0,30)}</td>
            <td><button class="btn btn-sm btn-outline-primary" onclick="ciEditCompetitor(${c.id})">编辑</button>
                ${!c.is_self ? `<button class="btn btn-sm btn-outline-danger" onclick="ciDeleteCompetitor(${c.id})">删除</button>` : ''}</td>
        </tr>`).join('');
        // 填充筛选器
        ciCompetitors.forEach(c => {
            document.querySelectorAll('[id$="Filter"]').forEach(sel => {
                if (sel.id !== 'ciScoreFilter' || true) {
                    const exists = sel.querySelector(`option[value="${c.id}"]`);
                    if (!exists && sel.id.startsWith('ci')) {
                        sel.insertAdjacentHTML('beforeend', `<option value="${c.id}">${c.name}</option>`);
                    }
                }
            });
        });
    } catch (e) { console.error(e); }
}

function ciEditCompetitor(id) {
    const c = id ? ciCompetitors.find(x => x.id === id) : {};
    const statusMap = CI_META.operating_status || {};
    const statusOpts = Object.entries(statusMap).map(([k,v]) => `<option value="${k}" ${(c.operating_status||'active')===k?'selected':''}>${v}</option>`).join('');
    document.getElementById('ciModalTitle').textContent = id ? '编辑竞争店' : '新增竞争店';
    document.getElementById('ciModalBody').innerHTML = `
        <div class="row g-3">
            <div class="col-md-6"><label class="form-label">店名 *</label><input class="form-control" id="f_name" value="${c.name||''}"></div>
            <div class="col-md-3"><label class="form-label">运营状态</label><select class="form-select" id="f_status">${statusOpts}</select></div>
            <div class="col-md-3"><label class="form-label">开业时间</label><input type="date" class="form-control" id="f_open_date" value="${c.open_date||''}"></div>
            <div class="col-md-6"><label class="form-label">地址</label><input class="form-control" id="f_address" value="${c.address||''}"></div>
            <div class="col-md-3"><label class="form-label">营业时间</label><input class="form-control" id="f_hours" placeholder="如10:00-02:00" value="${c.business_hours||''}"></div>
            <div class="col-md-3"><label class="form-label">联系方式</label><input class="form-control" id="f_contact" placeholder="微信/电话（公开信息）" value="${c.contact||''}"></div>
            <div class="col-md-4"><label class="form-label">面积(㎡)</label><input type="number" class="form-control" id="f_area_sqm" value="${c.area_sqm||''}"></div>
            <div class="col-md-4"><label class="form-label">麻将机总数</label><input type="number" class="form-control" id="f_machine_count" value="${c.machine_count||''}"></div>
            <div class="col-md-2"><label class="form-label">四口桌</label><input type="number" class="form-control" id="f_t4" value="${c.table_4port||0}"></div>
            <div class="col-md-2"><label class="form-label">八口桌</label><input type="number" class="form-control" id="f_t8" value="${c.table_8port||0}"></div>
            <div class="col-md-6"><label class="form-label">店铺定位</label><input class="form-control" id="f_positioning" value="${c.positioning||''}" placeholder="如竞技向/娱乐向/混合"></div>
            <div class="col-md-6"><label class="form-label">主打客户群</label><input class="form-control" id="f_target" value="${c.target_customers||''}"></div>
            <div class="col-md-6"><label class="form-label">主要卖点</label><textarea class="form-control" id="f_selling" rows="2">${c.key_selling_points||''}</textarea></div>
            <div class="col-md-6"><label class="form-label">已知优势</label><textarea class="form-control" id="f_adv" rows="2">${c.known_advantages||''}</textarea></div>
            <div class="col-md-6"><label class="form-label">已知短板</label><textarea class="form-control" id="f_weak" rows="2">${c.known_weaknesses||''}</textarea></div>
            <div class="col-md-6"><label class="form-label">备注</label><textarea class="form-control" id="f_notes" rows="2">${c.notes||''}</textarea></div>
        </div>
        <div class="text-end mt-3"><button class="btn btn-primary" onclick="ciSaveCompetitor(${id||0})">保存</button></div>`;
    new bootstrap.Modal(document.getElementById('ciModal')).show();
}

async function ciSaveCompetitor(id) {
    const data = {
        name: document.getElementById('f_name').value.trim(),
        address: document.getElementById('f_address').value,
        open_date: document.getElementById('f_open_date').value || null,
        area_sqm: parseFloat(document.getElementById('f_area_sqm').value) || null,
        machine_count: parseInt(document.getElementById('f_machine_count').value) || null,
        table_4port: parseInt(document.getElementById('f_t4').value) || 0,
        table_8port: parseInt(document.getElementById('f_t8').value) || 0,
        positioning: document.getElementById('f_positioning').value,
        target_customers: document.getElementById('f_target').value,
        key_selling_points: document.getElementById('f_selling').value,
        known_advantages: document.getElementById('f_adv').value,
        known_weaknesses: document.getElementById('f_weak').value,
        business_hours: document.getElementById('f_hours').value,
        operating_status: document.getElementById('f_status').value,
        contact: document.getElementById('f_contact').value,
        notes: document.getElementById('f_notes').value,
    };
    if (!data.name) { alert('请输入店名'); return; }
    const url = id ? `/api/ci/competitors/${id}` : '/api/ci/competitors';
    const method = id ? 'PUT' : 'POST';
    const res = await fetch(url, {method, headers: {'Content-Type':'application/json'}, body: JSON.stringify(data)});
    if (res.ok) {
        bootstrap.Modal.getInstance(document.getElementById('ciModal')).hide();
        await ciLoadCompetitors();
        await ciLoadDashboard();
    } else { const err = await res.json(); alert(err.error || '保存失败'); }
}

async function ciDeleteCompetitor(id) {
    if (!confirm('确定删除该竞争店？所有关联数据将一并删除。')) return;
    await fetch(`/api/ci/competitors/${id}`, {method: 'DELETE'});
    await ciLoadCompetitors();
    await ciLoadDashboard();
}

// --- 通用：店铺选择器 ---
function ciCompOptions(selected) {
    return ciCompetitors.map(c => `<option value="${c.id}" ${selected==c.id?'selected':''}>${c.name}</option>`).join('');
}

// --- 价格体系 ---
async function ciLoadPricing() {
    const filter = document.getElementById('ciPricingFilter').value;
    const url = filter ? `/api/ci/pricing?competitor_id=${filter}` : '/api/ci/pricing';
    const res = await fetch(url);
    const data = await res.json();
    ciPricingCache = data;
    document.getElementById('ciPricingBody').innerHTML = data.map(p => `<tr>
        <td>${p.competitor_name}</td><td>${p.record_date}</td>
        <td>${p.normal_price||'-'}</td><td>${p.night_price||'-'}</td><td>${p.overnight_price||'-'}</td>
        <td>${p.package_price||'-'}</td><td>${p.member_price||'-'}</td><td>${p.newcustomer_offer||'-'}</td>
        <td>${p.oldcustomer_offer||'-'}</td><td>${p.recharge_promo||'-'}</td>
        <td>${p.tournament_fee||'-'}</td><td>${p.drink_price||'-'}</td>
        <td><button class="btn btn-sm btn-outline-primary" onclick="ciEditPricing(${p.id})">编辑</button>
            <button class="btn btn-sm btn-outline-danger" onclick="ciDeletePricing(${p.id})">删除</button></td>
    </tr>`).join('') || '<tr><td colspan="13" class="text-center text-muted">暂无数据</td></tr>';
}

function ciEditPricing(id) {
    ciPricingEditId = id || 0;
    const p = id ? ciCompetitors.flatMap(c => []).concat(ciPricingCache||[]).find(x => x.id === id) : null;
    document.getElementById('ciModalTitle').textContent = id ? '编辑价格记录' : '新增价格记录';
    document.getElementById('ciModalBody').innerHTML = `
        <div class="row g-3">
            <div class="col-md-6"><label class="form-label">店铺 *</label><select class="form-select" id="p_comp">${ciCompOptions(p?p.competitor_id:'')}</select></div>
            <div class="col-md-6"><label class="form-label">记录日期</label><input type="date" class="form-control" id="p_date" value="${p?p.record_date:todayStr()}"></div>
            <div class="col-md-4"><label class="form-label">普通时段</label><input class="form-control" id="p_normal" placeholder="如¥20/h" value="${p?(p.normal_price||''):''}"></div>
            <div class="col-md-4"><label class="form-label">夜场</label><input class="form-control" id="p_night" value="${p?(p.night_price||''):''}"></div>
            <div class="col-md-4"><label class="form-label">通宵</label><input class="form-control" id="p_overnight" value="${p?(p.overnight_price||''):''}"></div>
            <div class="col-md-4"><label class="form-label">包桌</label><input class="form-control" id="p_package" value="${p?(p.package_price||''):''}"></div>
            <div class="col-md-4"><label class="form-label">会员价</label><input class="form-control" id="p_member" value="${p?(p.member_price||''):''}"></div>
            <div class="col-md-4"><label class="form-label">新客优惠</label><input class="form-control" id="p_new" value="${p?(p.newcustomer_offer||''):''}"></div>
            <div class="col-md-4"><label class="form-label">老客优惠</label><input class="form-control" id="p_old" value="${p?(p.oldcustomer_offer||''):''}"></div>
            <div class="col-md-4"><label class="form-label">充值活动</label><input class="form-control" id="p_recharge" value="${p?(p.recharge_promo||''):''}"></div>
            <div class="col-md-4"><label class="form-label">比赛收费</label><input class="form-control" id="p_tournament" value="${p?(p.tournament_fee||''):''}"></div>
            <div class="col-md-4"><label class="form-label">饮品收费</label><input class="form-control" id="p_drink" value="${p?(p.drink_price||''):''}"></div>
            <div class="col-12"><label class="form-label">备注</label><textarea class="form-control" id="p_notes" rows="2">${p?(p.notes||''):''}</textarea></div>
        </div>
        <div class="text-end mt-3"><button class="btn btn-primary" onclick="ciSavePricing()">保存</button></div>`;
    new bootstrap.Modal(document.getElementById('ciModal')).show();
}

async function ciSavePricing() {
    const data = {
        competitor_id: parseInt(document.getElementById('p_comp').value),
        record_date: document.getElementById('p_date').value,
        normal_price: document.getElementById('p_normal').value,
        night_price: document.getElementById('p_night').value,
        overnight_price: document.getElementById('p_overnight').value,
        package_price: document.getElementById('p_package').value,
        member_price: document.getElementById('p_member').value,
        newcustomer_offer: document.getElementById('p_new').value,
        oldcustomer_offer: document.getElementById('p_old').value,
        recharge_promo: document.getElementById('p_recharge').value,
        tournament_fee: document.getElementById('p_tournament').value,
        drink_price: document.getElementById('p_drink').value,
        notes: document.getElementById('p_notes').value,
    };
    const url = ciPricingEditId ? `/api/ci/pricing/${ciPricingEditId}` : '/api/ci/pricing';
    const method = ciPricingEditId ? 'PUT' : 'POST';
    const res = await fetch(url, {method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
    if (res.ok) { bootstrap.Modal.getInstance(document.getElementById('ciModal')).hide(); ciPricingEditId = 0; ciLoadPricing(); ciLoadDashboard(); }
    else { const e = await res.json(); alert(e.error || '保存失败'); }
}

async function ciDeletePricing(id) {
    if (!confirm('确定删除？')) return;
    await fetch(`/api/ci/pricing/${id}`, {method:'DELETE'});
    ciLoadPricing();
}

// --- 客流观察 ---
async function ciLoadTraffic() {
    const filter = document.getElementById('ciTrafficFilter').value;
    const url = filter ? `/api/ci/traffic?competitor_id=${filter}` : '/api/ci/traffic';
    const res = await fetch(url);
    const data = await res.json();
    ciTrafficCache = data;
    document.getElementById('ciTrafficBody').innerHTML = data.map(t => `<tr>
        <td>${t.competitor_name}</td><td>${t.obs_date}</td><td>${t.time_slot_label}</td>
        <td>${t.observed_tables||0}</td><td>${t.active_players||0}</td>
        <td>${t.is_full?'<span class="badge bg-danger">满</span>':'-'}</td>
        <td>${t.is_queuing?'<span class="badge bg-warning">排队</span>':'-'}</td>
        <td>${t.activity_label||'-'}</td><td>${(t.notes||'').substring(0,30)}</td>
        <td><button class="btn btn-sm btn-outline-primary" onclick="ciEditTraffic(${t.id})">编辑</button>
            <button class="btn btn-sm btn-outline-danger" onclick="ciDeleteTraffic(${t.id})">删除</button></td>
    </tr>`).join('') || '<tr><td colspan="10" class="text-center text-muted">暂无数据</td></tr>';
}

function ciEditTraffic(id) {
    ciTrafficEditId = id || 0;
    const t = id ? ciTrafficCache.find(x => x.id === id) : null;
    const slots = CI_META.time_slots || {};
    const levels = CI_META.activity_levels || {};
    document.getElementById('ciModalTitle').textContent = id ? '编辑客流观察' : '新增客流观察';
    document.getElementById('ciModalBody').innerHTML = `
        <div class="row g-3">
            <div class="col-md-6"><label class="form-label">店铺 *</label><select class="form-select" id="t_comp">${ciCompOptions(t?t.competitor_id:'')}</select></div>
            <div class="col-md-6"><label class="form-label">观察日期</label><input type="date" class="form-control" id="t_date" value="${t?t.obs_date:todayStr()}"></div>
            <div class="col-md-6"><label class="form-label">时间段</label><select class="form-select" id="t_slot">
                ${Object.entries(slots).map(([k,v])=>`<option value="${k}" ${t&&t.time_slot===k?'selected':''}>${v}</option>`).join('')}
            </select></div>
            <div class="col-md-3"><label class="form-label">观察桌数</label><input type="number" class="form-control" id="t_tables" value="${t?(t.observed_tables||0):0}"></div>
            <div class="col-md-3"><label class="form-label">活跃玩家</label><input type="number" class="form-control" id="t_players" value="${t?(t.active_players||0):0}"></div>
            <div class="col-md-3"><div class="form-check"><input class="form-check-input" type="checkbox" id="t_full" ${t&&t.is_full?'checked':''}><label class="form-check-label">是否满桌</label></div></div>
            <div class="col-md-3"><div class="form-check"><input class="form-check-input" type="checkbox" id="t_queue" ${t&&t.is_queuing?'checked':''}><label class="form-check-label">是否排队</label></div></div>
            <div class="col-md-6"><label class="form-label">活跃程度</label><select class="form-select" id="t_level">
                ${Object.entries(levels).map(([k,v])=>`<option value="${k}" ${t&&t.activity_level===k?'selected':''}>${v}</option>`).join('')}
            </select></div>
            <div class="col-12"><label class="form-label">备注</label><textarea class="form-control" id="t_notes" rows="2">${t?(t.notes||''):''}</textarea></div>
        </div>
        <div class="text-end mt-3"><button class="btn btn-primary" onclick="ciSaveTraffic()">保存</button></div>`;
    new bootstrap.Modal(document.getElementById('ciModal')).show();
}

async function ciSaveTraffic() {
    const data = {
        competitor_id: parseInt(document.getElementById('t_comp').value),
        obs_date: document.getElementById('t_date').value,
        time_slot: document.getElementById('t_slot').value,
        observed_tables: parseInt(document.getElementById('t_tables').value)||0,
        active_players: parseInt(document.getElementById('t_players').value)||0,
        is_full: document.getElementById('t_full').checked,
        is_queuing: document.getElementById('t_queue').checked,
        activity_level: document.getElementById('t_level').value,
        notes: document.getElementById('t_notes').value,
    };
    const url = ciTrafficEditId ? `/api/ci/traffic/${ciTrafficEditId}` : '/api/ci/traffic';
    const method = ciTrafficEditId ? 'PUT' : 'POST';
    const res = await fetch(url, {method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
    if (res.ok) { bootstrap.Modal.getInstance(document.getElementById('ciModal')).hide(); ciTrafficEditId = 0; ciLoadTraffic(); ciLoadDashboard(); }
    else { const e = await res.json(); alert(e.error || '保存失败'); }
}

async function ciDeleteTraffic(id) {
    if (!confirm('确定删除？')) return;
    await fetch(`/api/ci/traffic/${id}`, {method:'DELETE'});
    ciLoadTraffic();
}

// --- 玩家画像 ---
async function ciLoadSegments() {
    const filter = document.getElementById('ciSegFilter').value;
    const url = filter ? `/api/ci/player-segments?competitor_id=${filter}` : '/api/ci/player-segments';
    const res = await fetch(url);
    const data = await res.json();
    ciSegCache = data;
    document.getElementById('ciSegBody').innerHTML = data.map(s => `<tr>
        <td>${s.competitor_name}</td><td>${s.player_type_label}</td><td>${s.active_time||'-'}</td>
        <td>${s.spending_label}</td><td>${s.can_bring_guests?'<i class="bi bi-check-lg text-success"></i>':'-'}</td>
        <td>${s.estimated_count||'-'}</td><td>${(s.description||'').substring(0,40)}</td>
        <td><button class="btn btn-sm btn-outline-primary" onclick="ciEditSegment(${s.id})">编辑</button>
            <button class="btn btn-sm btn-outline-danger" onclick="ciDeleteSegment(${s.id})">删除</button></td>
    </tr>`).join('') || '<tr><td colspan="8" class="text-center text-muted">暂无数据</td></tr>';
}

function ciEditSegment(id) {
    ciSegEditId = id || 0;
    const s = id ? ciSegCache.find(x => x.id === id) : null;
    const types = CI_META.player_types || {};
    const spendings = CI_META.spending_levels || {};
    document.getElementById('ciModalTitle').textContent = id ? '编辑玩家画像' : '新增玩家画像';
    document.getElementById('ciModalBody').innerHTML = `
        <div class="row g-3">
            <div class="col-md-6"><label class="form-label">店铺 *</label><select class="form-select" id="s_comp">${ciCompOptions(s?s.competitor_id:'')}</select></div>
            <div class="col-md-6"><label class="form-label">玩家类型</label><select class="form-select" id="s_type">
                ${Object.entries(types).map(([k,v])=>`<option value="${k}" ${s&&s.player_type===k?'selected':''}>${v}</option>`).join('')}
            </select></div>
            <div class="col-md-6"><label class="form-label">活跃时间</label><input class="form-control" id="s_time" placeholder="如周末下午" value="${s?(s.active_time||''):''}"></div>
            <div class="col-md-6"><label class="form-label">消费能力</label><select class="form-select" id="s_spend">
                ${Object.entries(spendings).map(([k,v])=>`<option value="${k}" ${s&&s.spending_level===k?'selected':''}>${v}</option>`).join('')}
            </select></div>
            <div class="col-md-6"><div class="form-check"><input class="form-check-input" type="checkbox" id="s_bring" ${s&&s.can_bring_guests?'checked':''}><label class="form-check-label">有带人能力</label></div></div>
            <div class="col-md-6"><label class="form-label">估计人数</label><input type="number" class="form-control" id="s_count" value="${s?(s.estimated_count||''):''}"></div>
            <div class="col-12"><label class="form-label">特征描述</label><textarea class="form-control" id="s_desc" rows="3">${s?(s.description||''):''}</textarea></div>
        </div>
        <div class="text-end mt-3"><button class="btn btn-primary" onclick="ciSaveSegment()">保存</button></div>`;
    new bootstrap.Modal(document.getElementById('ciModal')).show();
}

async function ciSaveSegment() {
    const data = {
        competitor_id: parseInt(document.getElementById('s_comp').value),
        player_type: document.getElementById('s_type').value,
        active_time: document.getElementById('s_time').value,
        spending_level: document.getElementById('s_spend').value,
        can_bring_guests: document.getElementById('s_bring').checked,
        estimated_count: parseInt(document.getElementById('s_count').value)||null,
        description: document.getElementById('s_desc').value,
    };
    const url = ciSegEditId ? `/api/ci/player-segments/${ciSegEditId}` : '/api/ci/player-segments';
    const method = ciSegEditId ? 'PUT' : 'POST';
    const res = await fetch(url, {method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
    if (res.ok) { bootstrap.Modal.getInstance(document.getElementById('ciModal')).hide(); ciSegEditId = 0; ciLoadSegments(); ciLoadDashboard(); }
    else { const e = await res.json(); alert(e.error || '保存失败'); }
}

async function ciDeleteSegment(id) {
    if (!confirm('确定删除？')) return;
    await fetch(`/api/ci/player-segments/${id}`, {method:'DELETE'});
    ciLoadSegments();
}

// --- 核心玩家 ---
async function ciLoadKeyPlayers() {
    const filter = document.getElementById('ciKpFilter').value;
    const url = filter ? `/api/ci/key-players?competitor_id=${filter}` : '/api/ci/key-players';
    const res = await fetch(url);
    const data = await res.json();
    ciKpCache = data;
    document.getElementById('ciKpBody').innerHTML = data.map(k => `<tr>
        <td>${k.competitor_name}</td><td>${k.anonymous_id||'-'}</td><td>${k.freq_label}</td>
        <td>${k.usual_group_size||'-'}</td><td>${k.skill_label}</td>
        <td>${k.spending_power||'-'}</td><td>${k.influence_label}</td>
        <td>${k.conversion_value||'-'}</td><td>${(k.notes||'').substring(0,30)}</td>
        <td><button class="btn btn-sm btn-outline-primary" onclick="ciEditKeyPlayer(${k.id})">编辑</button>
            <button class="btn btn-sm btn-outline-danger" onclick="ciDeleteKeyPlayer(${k.id})">删除</button></td>
    </tr>`).join('') || '<tr><td colspan="10" class="text-center text-muted">暂无数据</td></tr>';
}

function ciEditKeyPlayer(id) {
    ciKpEditId = id || 0;
    const k = id ? ciKpCache.find(x => x.id === id) : null;
    const freqs = CI_META.freq_levels || {};
    const skills = CI_META.skill_levels || {};
    const inf = CI_META.social_influence || {};
    document.getElementById('ciModalTitle').textContent = id ? '编辑核心玩家' : '新增核心玩家';
    document.getElementById('ciModalBody').innerHTML = `
        <div class="row g-3">
            <div class="col-md-6"><label class="form-label">店铺 *</label><select class="form-select" id="k_comp">${ciCompOptions(k?k.competitor_id:'')}</select></div>
            <div class="col-md-6"><label class="form-label">匿名编号</label><input class="form-control" id="k_aid" placeholder="如KP-001" value="${k?(k.anonymous_id||''):''}"></div>
            <div class="col-md-4"><label class="form-label">活跃频率</label><select class="form-select" id="k_freq">
                ${Object.entries(freqs).map(([f,v])=>`<option value="${f}" ${k&&k.active_frequency===f?'selected':''}>${v}</option>`).join('')}
            </select></div>
            <div class="col-md-4"><label class="form-label">常带人数</label><input type="number" class="form-control" id="k_group" value="${k?(k.usual_group_size||0):0}"></div>
            <div class="col-md-4"><label class="form-label">技术水平</label><select class="form-select" id="k_skill">
                ${Object.entries(skills).map(([f,v])=>`<option value="${f}" ${k&&k.skill_level===f?'selected':''}>${v}</option>`).join('')}
            </select></div>
            <div class="col-md-4"><label class="form-label">消费能力</label><input class="form-control" id="k_spend" placeholder="高/中/低" value="${k?(k.spending_power||''):''}"></div>
            <div class="col-md-4"><label class="form-label">社交影响力</label><select class="form-select" id="k_inf">
                ${Object.entries(inf).map(([f,v])=>`<option value="${f}" ${k&&k.social_influence===f?'selected':''}>${v}</option>`).join('')}
            </select></div>
            <div class="col-md-4"><label class="form-label">转化价值</label><input class="form-control" id="k_conv" placeholder="如高/中/低" value="${k?(k.conversion_value||''):''}"></div>
            <div class="col-12"><label class="form-label">备注</label><textarea class="form-control" id="k_notes" rows="2">${k?(k.notes||''):''}</textarea></div>
        </div>
        <div class="text-end mt-3"><button class="btn btn-primary" onclick="ciSaveKeyPlayer()">保存</button></div>`;
    new bootstrap.Modal(document.getElementById('ciModal')).show();
}

async function ciSaveKeyPlayer() {
    const data = {
        competitor_id: parseInt(document.getElementById('k_comp').value),
        anonymous_id: document.getElementById('k_aid').value,
        active_frequency: document.getElementById('k_freq').value,
        usual_group_size: parseInt(document.getElementById('k_group').value)||0,
        skill_level: document.getElementById('k_skill').value,
        spending_power: document.getElementById('k_spend').value,
        social_influence: document.getElementById('k_inf').value,
        conversion_value: document.getElementById('k_conv').value,
        notes: document.getElementById('k_notes').value,
    };
    const url = ciKpEditId ? `/api/ci/key-players/${ciKpEditId}` : '/api/ci/key-players';
    const method = ciKpEditId ? 'PUT' : 'POST';
    const res = await fetch(url, {method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
    if (res.ok) { bootstrap.Modal.getInstance(document.getElementById('ciModal')).hide(); ciKpEditId = 0; ciLoadKeyPlayers(); ciLoadDashboard(); }
    else { const e = await res.json(); alert(e.error || '保存失败'); }
}

async function ciDeleteKeyPlayer(id) {
    if (!confirm('确定删除？')) return;
    await fetch(`/api/ci/key-players/${id}`, {method:'DELETE'});
    ciLoadKeyPlayers();
}

// --- 服务评分 ---
async function ciLoadServiceScores() {
    const filter = document.getElementById('ciScoreFilter').value;
    const url = filter ? `/api/ci/service-scores?competitor_id=${filter}` : '/api/ci/service-scores';
    const res = await fetch(url);
    const data = await res.json();
    ciScoreCache = data;
    const dims = CI_META.score_dimensions || [];
    document.getElementById('ciScoreBody').innerHTML = data.map(s => `<tr>
        <td>${s.competitor_name}</td><td>${s.score_date}</td>
        ${dims.map(([k])=>`<td>${s[k]||'-'}</td>`).join('')}
        <td>${(s.notes||'').substring(0,20)}</td>
        <td><button class="btn btn-sm btn-outline-primary" onclick="ciEditServiceScore(${s.id})">编辑</button>
            <button class="btn btn-sm btn-outline-danger" onclick="ciDeleteScore(${s.id})">删除</button></td>
    </tr>`).join('') || '<tr><td colspan="13" class="text-center text-muted">暂无数据</td></tr>';
}

function ciEditServiceScore(id) {
    ciScoreEditId = id || 0;
    const s = id ? ciScoreCache.find(x => x.id === id) : null;
    const dims = CI_META.score_dimensions || [];
    document.getElementById('ciModalTitle').textContent = id ? '编辑服务评分' : '新增服务评分';
    document.getElementById('ciModalBody').innerHTML = `
        <div class="row g-3">
            <div class="col-md-6"><label class="form-label">店铺 *</label><select class="form-select" id="sc_comp">${ciCompOptions(s?s.competitor_id:'')}</select></div>
            <div class="col-md-6"><label class="form-label">评分日期</label><input type="date" class="form-control" id="sc_date" value="${s?s.score_date:todayStr()}"></div>
            ${dims.map(([k,v])=>`<div class="col-md-3"><label class="form-label">${v} (1-10)</label><input type="number" min="1" max="10" class="form-control" id="sc_${k}" value="${s?(s[k]||5):5}"></div>`).join('')}
            <div class="col-12"><label class="form-label">备注</label><textarea class="form-control" id="sc_notes" rows="2">${s?(s.notes||''):''}</textarea></div>
        </div>
        <div class="text-end mt-3"><button class="btn btn-primary" onclick="ciSaveScore()">保存</button></div>`;
    new bootstrap.Modal(document.getElementById('ciModal')).show();
}

async function ciSaveScore() {
    const dims = CI_META.score_dimensions || [];
    const data = {
        competitor_id: parseInt(document.getElementById('sc_comp').value),
        score_date: document.getElementById('sc_date').value,
        notes: document.getElementById('sc_notes').value,
    };
    dims.forEach(([k]) => { data[k] = parseInt(document.getElementById('sc_'+k).value) || 5; });
    const url = ciScoreEditId ? `/api/ci/service-scores/${ciScoreEditId}` : '/api/ci/service-scores';
    const method = ciScoreEditId ? 'PUT' : 'POST';
    const res = await fetch(url, {method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
    if (res.ok) { bootstrap.Modal.getInstance(document.getElementById('ciModal')).hide(); ciScoreEditId = 0; ciLoadServiceScores(); ciLoadDashboard(); }
    else { const e = await res.json(); alert(e.error || '保存失败'); }
}

async function ciDeleteScore(id) {
    if (!confirm('确定删除？')) return;
    await fetch(`/api/ci/service-scores/${id}`, {method:'DELETE'});
    ciLoadServiceScores();
}

// --- 微信群生态 ---
async function ciLoadCommunity() {
    const filter = document.getElementById('ciCommFilter').value;
    const url = filter ? `/api/ci/community?competitor_id=${filter}` : '/api/ci/community';
    const res = await fetch(url);
    const data = await res.json();
    ciCommCache = data;
    document.getElementById('ciCommBody').innerHTML = data.map(c => `<tr>
        <td>${c.competitor_name}</td><td>${c.record_date}</td><td>${c.group_size||'-'}</td>
        <td>${c.active_members||'-'}</td><td>${c.daily_messages||'-'}</td>
        <td>${c.activity_frequency||'-'}</td><td>${(c.newcomer_mechanism||'-').substring(0,20)}</td>
        <td>${(c.tournament_org||'-').substring(0,20)}</td><td>${c.admin_activity||'-'}</td>
        <td>${(c.group_culture||'-').substring(0,20)}</td>
        <td><button class="btn btn-sm btn-outline-primary" onclick="ciEditCommunity(${c.id})">编辑</button>
            <button class="btn btn-sm btn-outline-danger" onclick="ciDeleteCommunity(${c.id})">删除</button></td>
    </tr>`).join('') || '<tr><td colspan="11" class="text-center text-muted">暂无数据</td></tr>';
}

function ciEditCommunity(id) {
    ciCommEditId = id || 0;
    const c = id ? ciCommCache.find(x => x.id === id) : null;
    document.getElementById('ciModalTitle').textContent = id ? '编辑群生态记录' : '新增群生态记录';
    document.getElementById('ciModalBody').innerHTML = `
        <div class="row g-3">
            <div class="col-md-6"><label class="form-label">店铺 *</label><select class="form-select" id="cm_comp">${ciCompOptions(c?c.competitor_id:'')}</select></div>
            <div class="col-md-6"><label class="form-label">记录日期</label><input type="date" class="form-control" id="cm_date" value="${c?c.record_date:todayStr()}"></div>
            <div class="col-md-3"><label class="form-label">群规模</label><input type="number" class="form-control" id="cm_size" value="${c?(c.group_size||''):''}"></div>
            <div class="col-md-3"><label class="form-label">活跃人数</label><input type="number" class="form-control" id="cm_active" value="${c?(c.active_members||''):''}"></div>
            <div class="col-md-3"><label class="form-label">每日消息</label><input type="number" class="form-control" id="cm_msgs" value="${c?(c.daily_messages||''):''}"></div>
            <div class="col-md-3"><label class="form-label">活动频率</label><input class="form-control" id="cm_freq" placeholder="如每周3次" value="${c?(c.activity_frequency||''):''}"></div>
            <div class="col-md-6"><label class="form-label">新人欢迎机制</label><input class="form-control" id="cm_newcomer" value="${c?(c.newcomer_mechanism||''):''}"></div>
            <div class="col-md-6"><label class="form-label">比赛组织</label><input class="form-control" id="cm_tour" value="${c?(c.tournament_org||''):''}"></div>
            <div class="col-md-6"><label class="form-label">管理员活跃度</label><input class="form-control" id="cm_admin" value="${c?(c.admin_activity||''):''}"></div>
            <div class="col-md-6"><label class="form-label">群文化特点</label><input class="form-control" id="cm_culture" value="${c?(c.group_culture||''):''}"></div>
            <div class="col-12"><label class="form-label">备注</label><textarea class="form-control" id="cm_notes" rows="2">${c?(c.notes||''):''}</textarea></div>
        </div>
        <div class="text-end mt-3"><button class="btn btn-primary" onclick="ciSaveCommunity()">保存</button></div>`;
    new bootstrap.Modal(document.getElementById('ciModal')).show();
}

async function ciSaveCommunity() {
    const data = {
        competitor_id: parseInt(document.getElementById('cm_comp').value),
        record_date: document.getElementById('cm_date').value,
        group_size: parseInt(document.getElementById('cm_size').value)||null,
        active_members: parseInt(document.getElementById('cm_active').value)||null,
        daily_messages: parseInt(document.getElementById('cm_msgs').value)||null,
        activity_frequency: document.getElementById('cm_freq').value,
        newcomer_mechanism: document.getElementById('cm_newcomer').value,
        tournament_org: document.getElementById('cm_tour').value,
        admin_activity: document.getElementById('cm_admin').value,
        group_culture: document.getElementById('cm_culture').value,
        notes: document.getElementById('cm_notes').value,
    };
    const url = ciCommEditId ? `/api/ci/community/${ciCommEditId}` : '/api/ci/community';
    const method = ciCommEditId ? 'PUT' : 'POST';
    const res = await fetch(url, {method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
    if (res.ok) { bootstrap.Modal.getInstance(document.getElementById('ciModal')).hide(); ciCommEditId = 0; ciLoadCommunity(); ciLoadDashboard(); }
    else { const e = await res.json(); alert(e.error || '保存失败'); }
}

async function ciDeleteCommunity(id) {
    if (!confirm('确定删除？')) return;
    await fetch(`/api/ci/community/${id}`, {method:'DELETE'});
    ciLoadCommunity();
}

// --- 营销活动 ---
async function ciLoadMarketing() {
    const filter = document.getElementById('ciMktFilter').value;
    const url = filter ? `/api/ci/marketing?competitor_id=${filter}` : '/api/ci/marketing';
    const res = await fetch(url);
    const data = await res.json();
    ciMktCache = data;
    document.getElementById('ciMktBody').innerHTML = data.map(m => `<tr>
        <td>${m.competitor_name}</td><td>${m.activity_date}</td><td>${m.type_label}</td>
        <td>${(m.content||'').substring(0,30)}</td><td>${m.promotion_channel||'-'}</td>
        <td>${m.estimated_cost||'-'}</td><td>${(m.observed_effect||'-').substring(0,30)}</td>
        <td>${m.worth_learning?'<i class="bi bi-star-fill text-warning"></i>':''}</td>
        <td><button class="btn btn-sm btn-outline-primary" onclick="ciEditMarketing(${m.id})">编辑</button>
            <button class="btn btn-sm btn-outline-danger" onclick="ciDeleteMarketing(${m.id})">删除</button></td>
    </tr>`).join('') || '<tr><td colspan="9" class="text-center text-muted">暂无数据</td></tr>';
}

function ciEditMarketing(id) {
    ciMktEditId = id || 0;
    const m = id ? ciMktCache.find(x => x.id === id) : null;
    const types = CI_META.marketing_types || {};
    document.getElementById('ciModalTitle').textContent = id ? '编辑营销活动' : '新增营销活动';
    document.getElementById('ciModalBody').innerHTML = `
        <div class="row g-3">
            <div class="col-md-4"><label class="form-label">店铺 *</label><select class="form-select" id="m_comp">${ciCompOptions(m?m.competitor_id:'')}</select></div>
            <div class="col-md-4"><label class="form-label">活动日期</label><input type="date" class="form-control" id="m_date" value="${m?m.activity_date:todayStr()}"></div>
            <div class="col-md-4"><label class="form-label">活动类型</label><select class="form-select" id="m_type">
                ${Object.entries(types).map(([k,v])=>`<option value="${k}" ${m&&m.activity_type===k?'selected':''}>${v}</option>`).join('')}
            </select></div>
            <div class="col-12"><label class="form-label">活动内容</label><textarea class="form-control" id="m_content" rows="2">${m?(m.content||''):''}</textarea></div>
            <div class="col-md-6"><label class="form-label">推广方式</label><input class="form-control" id="m_channel" placeholder="如微信群/朋友圈/小红书" value="${m?(m.promotion_channel||''):''}"></div>
            <div class="col-md-6"><label class="form-label">预计成本</label><input type="number" class="form-control" id="m_cost" value="${m?(m.estimated_cost||0):0}"></div>
            <div class="col-12"><label class="form-label">效果观察</label><textarea class="form-control" id="m_effect" rows="2">${m?(m.observed_effect||''):''}</textarea></div>
            <div class="col-md-6"><div class="form-check"><input class="form-check-input" type="checkbox" id="m_learn" ${m&&m.worth_learning?'checked':''}><label class="form-check-label">值得学习</label></div></div>
            <div class="col-12"><label class="form-label">备注</label><textarea class="form-control" id="m_notes" rows="2">${m?(m.notes||''):''}</textarea></div>
        </div>
        <div class="text-end mt-3"><button class="btn btn-primary" onclick="ciSaveMarketing()">保存</button></div>`;
    new bootstrap.Modal(document.getElementById('ciModal')).show();
}

async function ciSaveMarketing() {
    const data = {
        competitor_id: parseInt(document.getElementById('m_comp').value),
        activity_date: document.getElementById('m_date').value,
        activity_type: document.getElementById('m_type').value,
        content: document.getElementById('m_content').value,
        promotion_channel: document.getElementById('m_channel').value,
        estimated_cost: parseFloat(document.getElementById('m_cost').value)||0,
        observed_effect: document.getElementById('m_effect').value,
        worth_learning: document.getElementById('m_learn').checked,
        notes: document.getElementById('m_notes').value,
    };
    const url = ciMktEditId ? `/api/ci/marketing/${ciMktEditId}` : '/api/ci/marketing';
    const method = ciMktEditId ? 'PUT' : 'POST';
    const res = await fetch(url, {method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
    if (res.ok) { bootstrap.Modal.getInstance(document.getElementById('ciModal')).hide(); ciMktEditId = 0; ciLoadMarketing(); ciLoadDashboard(); }
    else { const e = await res.json(); alert(e.error || '保存失败'); }
}

async function ciDeleteMarketing(id) {
    if (!confirm('确定删除？')) return;
    await fetch(`/api/ci/marketing/${id}`, {method:'DELETE'});
    ciLoadMarketing();
}

// --- 分析报告 ---
async function ciLoadAnalysis() {
    try {
        const [scoresRes, swotRes] = await Promise.all([
            fetch('/api/ci/scores'), fetch('/api/ci/swot')
        ]);
        const scores = await scoresRes.json();
        const swot = await swotRes.json();
        const weights = CI_META.score_weights || {};
        const dimLabels = CI_META.score_dim_labels || {};

        // 评分表
        let tableHtml = '<table class="table table-sm table-bordered"><thead><tr><th>店铺</th>';
        Object.entries(weights).forEach(([k,v]) => { tableHtml += `<th>${dimLabels[k]}<br><small>(${v}%)</small></th>`; });
        tableHtml += '<th>总分</th></tr></thead><tbody>';
        scores.forEach(s => {
            tableHtml += `<tr ${s.is_self?'class="table-info"':''}><td><strong>${s.name}</strong></td>`;
            Object.keys(weights).forEach(dim => {
                const val = s.dim_scores[dim] || 0;
                tableHtml += `<td class="text-center">${val > 0 ? val.toFixed(1) : '-'}</td>`;
            });
            tableHtml += `<td class="text-center"><strong>${s.total_score.toFixed(1)}</strong></td></tr>`;
        });
        tableHtml += '</tbody></table>';
        document.getElementById('ciScoreTable').innerHTML = tableHtml;

        // SWOT
        let swotHtml = '<div class="row g-3">';
        Object.entries(swot).forEach(([sid, s]) => {
            const cardColor = scores.find(sc => sc.id == sid)?.is_self ? 'border-primary' : '';
            swotHtml += `<div class="col-md-4"><div class="card ${cardColor} h-100"><div class="card-body">
                <h6 class="card-title">${s.name}</h6>
                <div class="mb-2"><span class="badge bg-success me-1">优势</span>
                    <ul class="small ps-3 mb-1">${s.strengths.map(x=>`<li>${x}</li>`).join('')||'<li class="text-muted">暂无</li>'}</ul></div>
                <div class="mb-2"><span class="badge bg-danger me-1">劣势</span>
                    <ul class="small ps-3 mb-1">${s.weaknesses.map(x=>`<li>${x}</li>`).join('')||'<li class="text-muted">暂无</li>'}</ul></div>
                <div class="mb-2"><span class="badge bg-info me-1">机会</span>
                    <ul class="small ps-3 mb-1">${s.opportunities.map(x=>`<li>${x}</li>`).join('')||'<li class="text-muted">暂无</li>'}</ul></div>
                <div><span class="badge bg-warning me-1">威胁</span>
                    <ul class="small ps-3 mb-1">${s.threats.map(x=>`<li>${x}</li>`).join('')||'<li class="text-muted">暂无</li>'}</ul></div>
            </div></div></div>`;
        });
        swotHtml += '</div>';
        document.getElementById('ciSwotArea').innerHTML = swotHtml;
    } catch (e) { console.error(e); }
}

// --- CSV导出 ---
function ciExportCSV() {
    const modules = ['competitors','pricing','traffic','segments','key_players','service_scores','community','marketing'];
    const module = prompt('导出哪个模块？\ncompetitors/pricing/traffic/segments/key_players/service_scores/community/marketing', 'competitors');
    if (module && modules.includes(module)) {
        window.open(`/api/ci/export/csv?module=${module}`, '_blank');
    }
}

// --- CSV模板下载 ---
function ciDownloadTemplate(module) {
    window.open(`/api/ci/import/template?module=${module}`, '_blank');
}

// --- CSV导入 ---
function ciImportCSV(module) {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.csv,text/csv';
    input.onchange = async () => {
        const file = input.files[0];
        if (!file) return;
        try {
            const text = await file.text();
            const rows = parseCSV(text);
            if (rows.length === 0) { alert('CSV内容为空'); return; }
            if (!confirm(`即将导入 ${rows.length} 条记录到该模块，确认？`)) return;
            const res = await fetch('/api/ci/import/csv', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({module, rows})
            });
            const result = await res.json();
            if (!res.ok) { alert(result.error || '导入失败'); return; }
            let msg = `✅ 成功导入 ${result.imported} 条`;
            if (result.errors && result.errors.length) {
                msg += `\n\n⚠️ ${result.errors.length} 条被跳过：\n` + result.errors.slice(0, 10).join('\n');
            }
            alert(msg);
            // 刷新当前模块
            const reloaders = {
                competitors: () => { ciLoadCompetitors(); ciLoadDashboard(); },
                pricing: ciLoadPricing, traffic: ciLoadTraffic, segments: ciLoadSegments,
                key_players: ciLoadKeyPlayers, service_scores: ciLoadServiceScores,
                community: ciLoadCommunity, marketing: ciLoadMarketing,
            };
            if (reloaders[module]) reloaders[module]();
        } catch (e) {
            alert('导入出错：' + e.message);
        }
    };
    input.click();
}

// 简单CSV解析（支持引号包裹、逗号、换行）
function parseCSV(text) {
    text = text.replace(/^\ufeff/, '');
    const rows = [];
    let row = [], field = '', inQuotes = false;
    for (let i = 0; i < text.length; i++) {
        const ch = text[i];
        if (inQuotes) {
            if (ch === '"') {
                if (text[i+1] === '"') { field += '"'; i++; }
                else inQuotes = false;
            } else field += ch;
        } else if (ch === '"') {
            inQuotes = true;
        } else if (ch === ',') {
            row.push(field); field = '';
        } else if (ch === '\n' || ch === '\r') {
            if (ch === '\r' && text[i+1] === '\n') i++;
            row.push(field); field = '';
            if (row.some(f => f.trim() !== '')) rows.push(row);
            row = [];
        } else field += ch;
    }
    row.push(field);
    if (row.some(f => f.trim() !== '')) rows.push(row);
    if (rows.length < 2) return [];
    // 表头 → 对象数组
    const headers = rows[0].map(h => h.trim());
    return rows.slice(1).map(r => {
        const obj = {};
        headers.forEach((h, idx) => { obj[h] = (r[idx] !== undefined ? r[idx] : '').trim(); });
        return obj;
    });
}

# 流水详情核对信息展示 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让收银流水的详情弹窗准确展示逐人或整桌结账的应收、实收、差额和备注。

**Architecture:** 保持结算数据与营业额逻辑不变。后端详情接口只读取已保存的 `session_players` 和（如存在）`table_settlements`；前端按接口明确返回的 `settlement_mode` 渲染核对区，绝不从玩家行重新推算实收。

**Tech Stack:** Python、Flask、SQLite、原生 JavaScript、Bootstrap、项目自带 `.venv`。

---

## 文件结构

- 修改 `app.py`：扩展 `/api/cashier_history/detail` 的只读响应，返回整桌结算事实和逐人对账字段。
- 修改 `templates/cashier_history.html`：根据结算模式渲染核对区和正确的玩家表列。
- 新增 `tests/test_cashier_history_detail.py`：覆盖逐人差额、逐人无差额、整桌结算和历史兼容。

### Task 1: 为详情接口建立失败测试

**Files:**
- Create: `tests/test_cashier_history_detail.py`
- Reference: `tests/test_table_settlement.py`
- Reference: `app.py:4760-4810`

- [ ] **Step 1: 创建独立测试数据库和登录辅助函数**

复制 `tests/test_table_settlement.py` 的测试环境初始化：在导入 `config` 前设置 `QCOS_SECRET_KEY` 与 `QCOS_ADMIN_PASSWORD`，用临时数据库覆盖 `config.DB_PATH`，调用 `models.init_db()`，再导入 `app as APP` 并创建 `client`。显式插入一个可用机台，避免依赖生产库或默认播种数据。

```python
db = sqlite3.connect(config.DB_PATH)
db.execute("INSERT INTO machines (id, name, type, status) VALUES (1, '测试桌', '8port', 'idle')")
db.commit()
db.close()

def detail(session_id):
    response = client.get(f'/api/cashier_history/detail?session_id={session_id}')
    assert response.status_code == 200, response.data
    body = response.get_json()
    assert body['status'] == 'ok'
    return body
```

- [ ] **Step 2: 写出三个尚未满足的接口断言**

建立一桌逐人结账、其中一人使用 `actual_amount` 小于 `grand_total` 并填写备注；调用详情接口后断言玩家行的 `expected_amount`、`actual_amount`、`difference`、`note` 都存在且金额正确。建立一桌整桌结账后断言响应含 `settlement_mode == 'table'` 与 `table_settlement`，其中包含 `agreed_table_fee`、`product_total`、`expected_amount`、`actual_amount`、`difference`、`note`、`payer_name`。再直接插入一条旧式玩家记录（`actual_amount` 为 `NULL`），断言详情可返回且标记为 `historical_compat`。

```python
assert result['settlement_mode'] == 'player'
assert row['expected_amount'] == row['grand_total']
assert row['actual_amount'] == expected_actual
assert row['difference'] == expected_actual - row['grand_total']
assert row['note'] == '顾客抹零'

assert result['settlement_mode'] == 'table'
assert result['table_settlement']['expected_amount'] == 244.0
assert result['table_settlement']['note'] == '饮料赠送'

assert old_result['players'][0]['historical_compat'] is True
```

- [ ] **Step 3: 运行测试并确认失败原因是详情接口缺少字段**

Run:

```powershell
$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python.exe tests\test_cashier_history_detail.py
```

Expected: 失败，错误为缺少 `settlement_mode`、`table_settlement` 或玩家对账字段，而不是测试环境或数据库初始化失败。

- [ ] **Step 4: 提交测试基线**

```powershell
git add tests/test_cashier_history_detail.py
git commit -m "test: cover cashier detail audit fields"
```

### Task 2: 扩展详情接口的只读数据合同

**Files:**
- Modify: `app.py:4760-4810`
- Test: `tests/test_cashier_history_detail.py`

- [ ] **Step 1: 查询整桌结算记录并确定结算模式**

在读取 `players` 与 `products` 后，使用 `session_id` 查询唯一整桌记录。若存在，转为字典并返回 `settlement_mode: 'table'`；否则返回 `settlement_mode: 'player'`。不得修改任意表，也不得按玩家金额计算整桌实收。

```python
table_settlement = db.execute(
    'SELECT * FROM table_settlements WHERE session_id=? ORDER BY id DESC LIMIT 1',
    [session_id],
).fetchone()
table_settlement = dict(table_settlement) if table_settlement else None
settlement_mode = 'table' if table_settlement else 'player'
```

- [ ] **Step 2: 规范逐人对账字段与历史兼容标记**

为每个玩家补充只读展示字段。新结算记录：应收为 `grand_total`，实收为 `actual_amount`（缺失时退回应收），差额为 `difference`（缺失时为 `0`），备注为空时保留空字符串。旧记录只有在 `actual_amount is None` 时设 `historical_compat=True`；不要写回数据库。

```python
for player in players:
    player['expected_amount'] = float(player.get('grand_total') or 0)
    player['historical_compat'] = player.get('actual_amount') is None
    player['display_actual_amount'] = (
        player['expected_amount'] if player['historical_compat']
        else float(player['actual_amount'] or 0)
    )
    player['display_difference'] = (
        0.0 if player['historical_compat'] else float(player.get('difference') or 0)
    )
    player['display_note'] = '' if player['historical_compat'] else (player.get('note') or '')
```

- [ ] **Step 3: 返回明确的数据合同**

在既有 JSON 返回体中加入以下字段，保留原字段以避免破坏现有界面：

```python
'settlement_mode': settlement_mode,
'table_settlement': table_settlement,
```

- [ ] **Step 4: 运行详情测试并确认通过**

Run:

```powershell
$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python.exe tests\test_cashier_history_detail.py
```

Expected: 所有详情接口测试通过。

- [ ] **Step 5: 提交接口改动**

```powershell
git add app.py tests/test_cashier_history_detail.py
git commit -m "feat: expose audit fields in cashier detail"
```

### Task 3: 渲染可核对的流水详情

**Files:**
- Modify: `templates/cashier_history.html:239-270`
- Test: `tests/test_cashier_history_detail.py`

- [ ] **Step 1: 为金额和文本增加安全显示辅助函数**

在 `openDetail` 内、构造 HTML 前定义展示辅助函数。备注内容必须 HTML 转义，防止录入的备注被当作页面代码执行。

```javascript
const safe = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
}[char]));
const money = (value) => `¥${fmt(value || 0)}`;
const noteText = (value) => value ? safe(value) : '无';
```

- [ ] **Step 2: 渲染整桌结账的顶部核对区**

当 `res.settlement_mode === 'table'` 时，在玩家表前渲染“本桌结算核对”表，字段顺序固定为约定整桌台费、商品合计、应收、实收、差额、支付方式、付款人、备注。金额只使用 `res.table_settlement`。

```javascript
const ts = res.table_settlement;
html += `<h6>本桌结算核对</h6><table class="table table-sm">
  <tr><th>约定整桌台费</th><th>商品合计</th><th>应收</th><th>实收</th><th>差额</th></tr>
  <tr><td>${money(ts.agreed_table_fee)}</td><td>${money(ts.product_total)}</td>
      <td>${money(ts.expected_amount)}</td><td>${money(ts.actual_amount)}</td>
      <td>${money(ts.difference)}</td></tr></table>
  <p class="small"><b>付款人：</b>${safe(ts.payer_name) || '未记录'}　<b>备注：</b>${noteText(ts.note)}</p>`;
```

- [ ] **Step 3: 按模式渲染玩家表，移除误导性的“实收”列**

整桌模式的表头为玩家、开台、结束、实际时长、计算台费、个人商品、结算状态；每行固定显示“由整桌结算”。逐人模式表头为玩家、开台、结束、实际时长、台费、商品、优惠、应收、实收、差额、备注、支付方式；金额使用接口的 `expected_amount`、`display_actual_amount`、`display_difference` 与 `display_note`。旧记录备注列显示“历史未记录”。

- [ ] **Step 4: 手工检查页面结构**

在浏览器登录后打开收银流水任意详情：逐人差额记录应显示每个人的应收/实收/差额/备注；整桌记录应显示顶部核对区，玩家行不显示零元实收。确认商品表和作废/恢复按钮仍可见。

- [ ] **Step 5: 运行回归测试**

Run:

```powershell
$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python.exe tests\test_cashier_history_detail.py
.\.venv\Scripts\python.exe tests\test_table_settlement.py
.\.venv\Scripts\python.exe tests\test_checkout.py
.\.venv\Scripts\python.exe tests\test_member_balance_checkout.py
```

Expected: 详情测试、整桌结账、逐人结账和会员结账均通过。

- [ ] **Step 6: 提交界面改动**

```powershell
git add templates/cashier_history.html tests/test_cashier_history_detail.py
git commit -m "feat: show audit amounts in cashier detail"
```

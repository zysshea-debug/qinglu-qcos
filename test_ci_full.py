import json, os, sys, urllib.request, http.cookiejar

# 集成冒烟脚本：连接运行中的服务器，管理员密码从环境变量读取
ADMIN_PW = os.environ.get('QCOS_TEST_ADMIN_PASSWORD', '')
if not ADMIN_PW:
    print('请设置环境变量 QCOS_TEST_ADMIN_PASSWORD（测试服务器管理员密码）')
    sys.exit(1)

base = 'http://localhost:5000'
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

def req(method, path, data=None):
    url = base + path
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(url, data=body, method=method)
    if body: r.add_header('Content-Type', 'application/json')
    try:
        resp = opener.open(r)
        return resp.status, json.loads(resp.read().decode() or '{}')
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or '{}')

s, d = req('POST', '/api/auth/login', {'username':'admin','password':ADMIN_PW})
print('login:', s)

# 1. meta 是否包含 operating_status
s, d = req('GET', '/api/ci/meta')
print('meta operating_status:', d.get('operating_status'))

# 2. competitors 是否包含新字段
s, d = req('GET', '/api/ci/competitors')
if s == 200 and d:
    print('competitor[0] keys:', [k for k in d[0].keys() if k in ('business_hours','operating_status','contact','notes')])
    print('competitor[0] name:', d[0].get('name'))

# 3. 测试新增竞争店（临时店）
s, d = req('POST', '/api/ci/competitors', {'name':'测试店XYZ','address':'测试路1号','business_hours':'10:00-02:00','operating_status':'preparing','contact':'wx:test','notes':'测试备注'})
print('create competitor:', s, d)
tmp_id = d.get('id')

# 4. 测试PUT更新
s, d = req('PUT', f'/api/ci/competitors/{tmp_id}', {'positioning':'竞技向','operating_status':'active'})
print('update competitor:', s, d)
s, d = req('GET', '/api/ci/competitors')
comp = [c for c in d if c['id'] == tmp_id][0]
print('after update:', comp['positioning'], comp['operating_status'], comp['business_hours'])

# 5. 测试pricing PUT（用现有记录）
s, d = req('GET', '/api/ci/pricing')
if d:
    pid = d[0]['id']
    s, r = req('PUT', f'/api/ci/pricing/{pid}', {'normal_price': '测试价¥25/h'})
    print('update pricing:', s, r)
    s, r = req('GET', '/api/ci/pricing')
    print('pricing[0] normal:', r[0]['normal_price'])
    # 恢复
    req('PUT', f'/api/ci/pricing/{pid}', {'normal_price': ''})

# 6. 测试marketing PUT
s, d = req('GET', '/api/ci/marketing')
if d:
    mid = d[0]['id']
    s, r = req('PUT', f'/api/ci/marketing/{mid}', {'content': '测试编辑内容'})
    print('update marketing:', s, r)
    s, r = req('GET', '/api/ci/marketing')
    print('marketing[0] content:', r[0]['content'])
    req('PUT', f'/api/ci/marketing/{mid}', {'content': ''})

# 7. 测试traffic PUT（新建一条再编辑）
s, d = req('POST', '/api/ci/traffic', {'competitor_id': tmp_id, 'obs_date': '2026-08-06', 'time_slot': 'evening', 'observed_tables': 3, 'active_players': 10, 'is_full': 1, 'activity_level': 'high'})
print('create traffic:', s, d)
s, d = req('GET', '/api/ci/traffic')
tid = None
for t in d:
    if t.get('competitor_id') == tmp_id:
        tid = t['id']
        break
print('found traffic id:', tid)
s, r = req('PUT', f'/api/ci/traffic/{tid}', {'observed_tables': 5, 'is_full': 0, 'is_queuing': 1})
print('update traffic:', s, r)
s, r = req('GET', '/api/ci/traffic')
t2 = [t for t in r if t['id'] == tid][0]
print('after traffic update:', t2['observed_tables'], t2['is_full'], t2['is_queuing'])

# 8. 测试CSV导入（marketing模块，用中文标签表头）
rows = [
    {'店铺名称': '朵拉', '活动日期': '2026-08-01', '活动类型': 'tournament', '活动内容': '导入测试比赛', '推广方式': '微信群', '预计成本': '500', '值得学习0/1': '1'},
]
s, r = req('POST', '/api/ci/import/csv', {'module': 'marketing', 'rows': rows})
print('import csv:', s, r)

# 9. 测试模板下载
s, r = req('GET', '/api/ci/import/template?module=marketing')
print('template:', s, type(r).__name__)

# 10. 删除测试数据
if tmp_id:
    req('DELETE', f'/api/ci/competitors/{tmp_id}')
    print('cleanup competitor:', tmp_id)
# 清理导入的营销记录（内容=导入测试比赛）
s, d = req('GET', '/api/ci/marketing')
for m in d:
    if m.get('content') == '导入测试比赛':
        req('DELETE', f"/api/ci/marketing/{m['id']}")
        print('cleanup marketing:', m['id'])

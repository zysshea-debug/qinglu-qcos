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

# login
s, d = req('POST', '/api/auth/login', {'username':'admin','password':ADMIN_PW})
print('login:', s)
for p in ['/api/ci/meta','/api/ci/competitors','/api/ci/pricing','/api/ci/marketing','/api/ci/traffic','/api/ci/player-segments','/api/ci/key-players','/api/ci/service-scores','/api/ci/community','/api/ci/scores','/api/ci/swot','/api/ci/dashboard']:
    s, d = req('GET', p)
    if isinstance(d, dict):
        keys = list(d.keys())[:5]
        if isinstance(d.get('error'), str):
            print(p, '-> ERROR', d['error'])
        else:
            print(p, '-> OK keys:', keys)
    else:
        print(p, '-> OK len', len(d))

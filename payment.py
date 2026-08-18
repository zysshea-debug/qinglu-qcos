"""青鹭收银 - 支付确认模块

核心目标：扫码收款时，QCOS 必须调用支付通道的「被扫 / 付款码支付」接口，
只有网关实时返回支付成功，才允许结账。彻底杜绝「收银员点一下就记成功」的假确认。

提供方（provider）通过 config.PAYMENT['provider'] 切换：
  - None      : 沿用旧逻辑，仅靠收银员点「确认结账」（不确认到账，仅记账）
  - 'mock'    : 测试用，模拟成功，无需任何凭证
  - 'zhonglun': 四川中仑数科科技有限公司 开放平台（扫码收款/被扫 + 查询 + 异步通知）

说明：中仑开放平台（open.zhonglunnet.com）有应用编码/秘钥、签名、SDK 那一套，
但具体「扫码收款/被扫」的 action 名、字段、验签算法以其「文档中心 > API文档 > 支付」
为准。ZhonglunProvider 已按开放平台通用约定（appkey/secret、JSON POST、签名）搭好
传输骨架，具体 action 名从 config 读取，方便你拿到文档后直接填，无需改代码。
"""
import json
import time
import uuid
import hashlib
import urllib.request
import urllib.error
from datetime import datetime

import config


class PaymentResult:
    """支付结果统一封装。status 取值：
    SUCCESS / FAIL / USERPAYING / ERROR
    """

    def __init__(self, status, out_trade_no=None, transaction_id=None,
                 paid_amount=None, message='', raw=None):
        self.status = status
        self.out_trade_no = out_trade_no
        self.transaction_id = transaction_id
        self.paid_amount = paid_amount
        self.message = message
        self.raw = raw

    def to_dict(self):
        return {
            'status': self.status,
            'out_trade_no': self.out_trade_no,
            'transaction_id': self.transaction_id,
            'paid_amount': self.paid_amount,
            'message': self.message,
        }


def mask_code(code):
    """对付款码做脱敏，仅保留首尾各 4 位。"""
    if not code or len(code) <= 8:
        return '****'
    return code[:4] + '****' + code[-4:]


def gen_out_trade_no(prefix='QL'):
    """生成商户订单号：QL + 时间 + 随机，保证唯一。"""
    return prefix + datetime.now().strftime('%Y%m%d%H%M%S') + uuid.uuid4().hex[:8]


class BaseProvider:
    def micropay(self, auth_code, total_fee, out_trade_no, method, **kwargs):
        raise NotImplementedError

    def query(self, out_trade_no, **kwargs):
        raise NotImplementedError

    def verify_notify(self, payload, signature):
        raise NotImplementedError


class MockProvider(BaseProvider):
    """测试用：直接返回成功，不联网、不需要任何凭证。"""

    def micropay(self, auth_code, total_fee, out_trade_no, method, **kwargs):
        return PaymentResult(
            'SUCCESS',
            out_trade_no=out_trade_no,
            transaction_id='MOCK' + out_trade_no,
            paid_amount=total_fee,
            message='测试模拟支付成功',
        )

    def query(self, out_trade_no, **kwargs):
        return PaymentResult('SUCCESS', out_trade_no=out_trade_no, message='测试模拟查询成功')

    def verify_notify(self, payload, signature):
        return True


class ZhonglunProvider(BaseProvider):
    """中仑数科开放平台 - 扫码收款（被扫）。

    传输约定（来自中仑开放平台文档）：
      - 生产域名：open.zhonglunnet.com
      - 路径格式：open.zhonglunnet.com/{key}/{version}/action
      - 请求：POST application/json，UTF-8
      - 鉴权：appkey（应用编码）+ 应用秘钥 参与签名
    具体「扫码收款/被扫」action 名、字段、签名算法，以中仑开放平台
    「文档中心 > API文档 > 支付」为准，配置在 config.PAYMENT['zhonglun'] 中。
    """

    def __init__(self, cfg):
        self.cfg = cfg or {}
        self.api_base = self.cfg.get('api_base', 'https://open.zhonglunnet.com')
        self.appkey = self.cfg.get('appkey', '')
        self.secret = self.cfg.get('secret', '')
        self.shop_auth = self.cfg.get('shop_auth_code', '')

    # ===== 传输层 =====

    def _sign(self, body_str):
        """TODO: 替换为中仑开放平台实际签名算法（文档「了解中仑开放平台签名」）。
        常见做法为 secret 参与 MD5 / HMAC，下面先用占位实现，拿到文档后改这里即可。"""
        return hashlib.md5((body_str + self.secret).encode('utf-8')).hexdigest()

    def _post(self, action, data):
        url = '{base}/{key}/{version}/{action}'.format(
            base=self.api_base.rstrip('/'),
            key=self.cfg.get('key', 'zl.open.pay.micro'),
            version=self.cfg.get('version', 'v5'),
            action=action,
        )
        body = json.dumps(data, ensure_ascii=False)
        headers = {
            'Content-Type': 'application/json',
            'appkey': self.appkey,
            'sign': self._sign(body),
        }
        req = urllib.request.Request(url, data=body.encode('utf-8'), headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode('utf-8'))

    # ===== 业务层 =====

    def micropay(self, auth_code, total_fee, out_trade_no, method, **kwargs):
        action = self.cfg.get('micropay_action')
        if not action:
            return PaymentResult(
                'ERROR', out_trade_no=out_trade_no,
                message='未配置中仑「扫码收款」action，请在 config.PAYMENT["zhonglun"] 填写 micropay_action',
            )
        data = {
            'auth_code': auth_code,
            'total_fee': int(round(total_fee * 100)),  # 单位：分
            'out_trade_no': out_trade_no,
            'shop_auth_code': self.shop_auth,
            'pay_type': 'wechat' if method == 'scan_wechat' else 'alipay',
            # TODO: 按中仑开放平台「扫码收款」文档补充其余必填字段
        }
        try:
            r = self._post(action, data)
        except urllib.error.URLError as e:
            return PaymentResult('ERROR', out_trade_no=out_trade_no, message='网络错误：' + str(e))
        except Exception as e:  # noqa: BLE001
            return PaymentResult('ERROR', out_trade_no=out_trade_no, message=str(e))
        # TODO: 按中仑实际返回解析 success / trade_state（文档为准）
        if r.get('success') in ('1', 1, True):
            return PaymentResult(
                'SUCCESS', out_trade_no=out_trade_no,
                transaction_id=(r.get('data') or {}).get('transaction_id'),
                paid_amount=total_fee, message='支付成功', raw=r,
            )
        if (r.get('errorCode') in ('USERPAYING',) or r.get('trade_state') == 'USERPAYING'):
            return PaymentResult('USERPAYING', out_trade_no=out_trade_no, message='用户支付中，请稍候')
        return PaymentResult('FAIL', out_trade_no=out_trade_no, message=r.get('errorMsg') or '支付失败', raw=r)

    def query(self, out_trade_no, **kwargs):
        action = self.cfg.get('query_action')
        if not action:
            return PaymentResult('ERROR', out_trade_no=out_trade_no,
                                  message='未配置中仑「订单查询」action')
        try:
            r = self._post(action, {'out_trade_no': out_trade_no, 'shop_auth_code': self.shop_auth})
        except Exception as e:  # noqa: BLE001
            return PaymentResult('ERROR', out_trade_no=out_trade_no, message=str(e))
        # TODO: 按中仑实际返回解析
        if r.get('success') in ('1', 1, True):
            return PaymentResult('SUCCESS', out_trade_no=out_trade_no, message='支付成功', raw=r)
        return PaymentResult('FAIL', out_trade_no=out_trade_no, message=r.get('errorMsg') or '查询失败', raw=r)

    def verify_notify(self, payload, signature):
        """TODO: 按中仑异步通知签名规则验签，防止伪造回调。"""
        return True


_PROVIDERS = {}


def get_provider():
    """按 config.PAYMENT['provider'] 返回 provider 实例（单例）。无配置返回 None。"""
    provider_name = config.PAYMENT.get('provider')
    if not provider_name:
        return None
    if provider_name in _PROVIDERS:
        return _PROVIDERS[provider_name]
    if provider_name == 'mock':
        p = MockProvider()
    elif provider_name == 'zhonglun':
        p = ZhonglunProvider(config.PAYMENT.get('zhonglun', {}))
    else:
        return None
    _PROVIDERS[provider_name] = p
    return p


def micropay_with_poll(provider, auth_code, total_fee, out_trade_no, method, max_poll=5, poll_interval=1.5):
    """发起被扫支付，若返回 USERPAYING 则轮询查询直到成功/失败/超时。"""
    result = provider.micropay(auth_code, total_fee, out_trade_no, method)
    attempts = 0
    while result.status == 'USERPAYING' and attempts < max_poll:
        time.sleep(poll_interval)
        result = provider.query(out_trade_no)
        attempts += 1
    return result

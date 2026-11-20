#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Trae 每日签到领取积分。

流程：Cookie -> 换取 8 小时有效的 Cloud-IDE-JWT -> 查询签到状态 -> 未签到则领取 -> 飞书通知。
所有凭证只从环境变量（GitHub Secrets）读取，不会打印到日志。
"""
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request

HOST = "https://api.trae.cn"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)

# 账号当日已签到时，claim 接口返回的业务码
ALREADY_CLAIMED = 9004


def http_post(path, body, headers):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        HOST + path,
        data=data,
        method="POST",
        headers=dict(
            {
                "User-Agent": UA,
                "Origin": "https://www.trae.cn",
                "Referer": "https://www.trae.cn/",
                "Content-Type": "application/json",
            },
            **headers
        ),
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def mint_token(cookie):
    """用 Cookie 换一个新的 Cloud-IDE-JWT（有效期 8 小时）。"""
    status, text = http_post(
        "/cloudide/api/v3/common/GetUserToken", {}, {"Cookie": cookie}
    )
    if status != 200:
        raise RuntimeError(f"换取 token 失败 HTTP {status}")
    try:
        return json.loads(text)["Result"]["Token"]
    except (KeyError, ValueError):
        raise RuntimeError(f"换取 token 响应异常 HTTP {status}")


def check_status(token):
    status, text = http_post(
        "/trae/api/v2/ug/checkin_credits/status",
        {"req_source": 3},
        {"authorization": "Cloud-IDE-JWT " + token},
    )
    if status != 200:
        raise RuntimeError(f"查询签到状态失败 HTTP {status}")
    return json.loads(text)


def claim_credits(token):
    status, text = http_post(
        "/trae/api/v2/ug/checkin_credits/claim",
        {"req_source": 3},
        {"authorization": "Cloud-IDE-JWT " + token},
    )
    if status != 200:
        raise RuntimeError(f"领取积分失败 HTTP {status}")
    return json.loads(text)


def notify(title, lines):
    """推送飞书群机器人消息，未配置 Webhook 则静默跳过。"""
    webhook = os.environ.get("FEISHU_WEBHOOK", "").strip()
    if not webhook:
        return
    body = {"msg_type": "text", "content": {"text": title + "\n" + "\n".join(lines)}}

    secret = os.environ.get("FEISHU_SECRET", "").strip()
    if secret:
        # 飞书签名校验：以 "{timestamp}\n{secret}" 作为 HMAC-SHA256 的 key
        timestamp = str(int(time.time()))
        string_to_sign = f"{timestamp}\n{secret}"
        sign = base64.b64encode(
            hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
        ).decode()
        body["timestamp"] = timestamp
        body["sign"] = sign

    req = urllib.request.Request(
        webhook,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as exc:  # 通知失败不影响签到结果
        print(f"[warn] 飞书通知发送失败: {exc}")


def describe(info):
    parts = []
    if isinstance(info.get("credits"), int):
        parts.append(f"本次 {info['credits']} 积分")
    if isinstance(info.get("extra_credits"), int) and info["extra_credits"] > 0:
        parts.append(f"额外加成 {info['extra_credits']}")
    return "、".join(parts) or "积分明细未返回"


def main():
    cookie = os.environ.get("TRAE_COOKIE", "").strip()
    if not cookie:
        notify("❌ Trae 签到失败", ["未配置 TRAE_COOKIE Secret"])
        return 1

    # 1. 换 token
    try:
        token = mint_token(cookie)
    except Exception as exc:
        notify(
            "⚠️ Trae 凭证已失效",
            [str(exc), "请重新登录 www.trae.cn，从 DevTools 复制新的 Cookie 更新 TRAE_COOKIE。"],
        )
        return 1

    # 2. 查状态
    try:
        info = check_status(token)
    except Exception as exc:
        notify("❌ Trae 签到失败", [str(exc)])
        return 1

    if not info.get("enable"):
        print("签到活动未开启，跳过")
        notify("ℹ️ Trae 签到", ["当前签到活动未开启，本次未操作。"])
        return 0

    if info.get("checked_in"):
        print("今日已签到")
        notify("ℹ️ Trae 今日已签到", [describe(info)])
        return 0

    # 3. 领取
    try:
        result = claim_credits(token)
    except Exception as exc:
        notify("❌ Trae 签到失败", [str(exc)])
        return 1

    code = result.get("code")
    if code not in (0, ALREADY_CLAIMED):
        notify(
            "❌ Trae 签到失败",
            [f"业务码 {code}", str(result.get("message", ""))[:200]],
        )
        return 1

    # 4. 回查一次状态，拿到真实到账积分
    try:
        info = check_status(token)
    except Exception:
        pass  # 状态回查失败不影响已经领取成功的事实

    print("签到成功")
    notify("✅ Trae 签到成功", [describe(info)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

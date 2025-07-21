"""
euserv 自动续期脚本
功能:
* 使用 TrueCaptcha API 自动识别验证码
* 发送通知到 Telegram
* 增加登录失败重试机制
* 日志信息格式化
"""
import imaplib
import email
from email.header import decode_header
import re
import json
import time
import base64
import requests
from bs4 import BeautifulSoup
from typing import Optional


# 账户信息：用户名和密码
USERNAME = ''
PASSWORD = ''

# TrueCaptcha API 配置
# 申请地址: https://truecaptcha.org/

TRUECAPTCHA_USERID = ''
TRUECAPTCHA_APIKEY = ''


# Gmail 邮箱 配置
MAIL_ADDRESS = ''
APP_PASSWORD = ''
SENDER_FILTER = 'EUserv Support' # 无需修改
SUBJECT_FILTER = 'EUserv - PIN for the Confirmation of a Security Check' # 无需修改
MAX_MAILS = 10  # 无需修改
CODE_PATTER = r"\b\d{6}\b"  # 无需修改


# Telegram Bot 推送配置
TG_BOT_TOKEN = "" # 改为你的Telegram机器人Token
TG_USER_ID = "" # 用户机器人向你发送消息
TG_API_HOST = "https://api.telegram.org"

# 代理设置（如果需要）
PROXIES = {"http": "http://127.0.0.1:10808", "https": "http://127.0.0.1:10808"}

# 最大登录重试次数
LOGIN_MAX_RETRY_COUNT = 5

# 接收 PIN 的等待时间，单位为秒
WAITING_TIME_OF_PIN = 15

# 是否检查验证码解决器的使用情况
CHECK_CAPTCHA_SOLVER_USAGE = True

user_agent = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/95.0.4638.69 Safari/537.36"
)

desp = ""  # 日志信息

def log(info: str):
    # 打印并记录日志信息，附带 emoji 以增加可读性
    emoji_map = {
        "正在续费": "🔄",
        "检测到": "🔍",
        "ServerID": "🔗",
        "无需更新": "✅",
        "续订错误": "⚠️",
        "已成功续订": "🎉",
        "所有工作完成": "🏁",
        "登陆失败": "❗",
        "验证通过": "✔️",
        "验证失败": "❌",
        "API 使用次数": "📊",
        "验证码是": "🔢",
        "登录尝试": "🔑",
        "[Gmail]": "📧",
        "[Captcha Solver]": "🧩",
        "[AutoEUServerless]": "🌐",
    }
    # 对每个关键字进行检查，并在找到时添加 emoji
    for key, emoji in emoji_map.items():
        if key in info:
            info = emoji + " " + info
            break

    print(info)
    global desp
    desp += info + "\n\n"


# 登录重试装饰器
def login_retry(*args, **kwargs):
    def wrapper(func):
        def inner(username, password):
            ret, ret_session = func(username, password)
            max_retry = kwargs.get("max_retry")
            # 默认重试 3 次
            if not max_retry:
                max_retry = 3
            number = 0
            if ret == "-1":
                while number < max_retry:
                    number += 1
                    if number > 1:
                        log("[AutoEUServerless] 登录尝试第 {} 次".format(number))
                    sess_id, session = func(username, password)
                    if sess_id != "-1":
                        return sess_id, session
                    else:
                        if number == max_retry:
                            return sess_id, session
            else:
                return ret, ret_session
        return inner
    return wrapper

# 验证码解决器
def captcha_solver(captcha_image_url: str, session: requests.session) -> dict:
    # TrueCaptcha API 文档: https://apitruecaptcha.org/api
    # 每天免费使用 100 次请求。

    response = session.get(captcha_image_url)
    encoded_string = base64.b64encode(response.content)
    url = "https://api.apitruecaptcha.org/one/gettext"

    data = {
        "userid": TRUECAPTCHA_USERID,
        "apikey": TRUECAPTCHA_APIKEY,
        "case": "mixed",
        "mode": "human",
        "data": str(encoded_string)[2:-1],
    }
    r = requests.post(url=url, json=data)
    j = json.loads(r.text)
    return j

# 处理验证码解决结果
def handle_captcha_solved_result(solved: dict) -> str:
    # 处理验证码解决结果# 
    if "result" in solved:
        solved_text = solved["result"]
        if "RESULT  IS" in solved_text:
            log("[Captcha Solver] 使用的是演示 apikey。")
            # 因为使用了演示 apikey
            text = re.findall(r"RESULT  IS . (.*) .", solved_text)[0]
        else:
            # 使用自己的 apikey
            log("[Captcha Solver] 使用的是您自己的 apikey。")
            text = solved_text
        operators = ["X", "x", "+", "-"]
        if any(x in text for x in operators):
            for operator in operators:
                operator_pos = text.find(operator)
                if operator == "x" or operator == "X":
                    operator = "*"
                if operator_pos != -1:
                    left_part = text[:operator_pos]
                    right_part = text[operator_pos + 1 :]
                    if left_part.isdigit() and right_part.isdigit():
                        return eval(
                            "{left} {operator} {right}".format(
                                left=left_part, operator=operator, right=right_part
                            )
                        )
                    else:
                        # 这些符号("X", "x", "+", "-")不会同时出现，
                        # 它只包含一个算术符号。
                        return text
        else:
            return text
    else:
        print(solved)
        raise KeyError("未找到解析结果。")

# 获取验证码解决器使用情况
def get_captcha_solver_usage() -> dict:
    # 获取验证码解决器的使用情况# 
    url = "https://api.apitruecaptcha.org/one/getusage"

    params = {
        "username": TRUECAPTCHA_USERID,
        "apikey": TRUECAPTCHA_APIKEY,
    }
    r = requests.get(url=url, params=params)
    j = json.loads(r.text)
    return j
 
# 登录函数
@login_retry(max_retry=LOGIN_MAX_RETRY_COUNT)
def login(username: str, password: str) -> (str, requests.session):
    # 登录 EUserv 并获取 session# 
    headers = {"user-agent": user_agent, "origin": "https://www.euserv.com"}
    url = "https://support.euserv.com/index.iphp"
    captcha_image_url = "https://support.euserv.com/securimage_show.php"
    session = requests.Session()

    sess = session.get(url, headers=headers)
    sess_id = re.findall("PHPSESSID=(\\w{10,100});", str(sess.headers))[0]
    session.get("https://support.euserv.com/pic/logo_small.png", headers=headers)

    login_data = {
        "email": username,
        "password": password,
        "form_selected_language": "en",
        "Submit": "Login",
        "subaction": "login",
        "sess_id": sess_id,
    }
    f = session.post(url, headers=headers, data=login_data)
    f.raise_for_status()

    if "Hello" not in f.text and "Confirm or change your customer data here" not in f.text:
        if "To finish the login process please solve the following captcha." not in f.text:
            return "-1", session
        else:
            log("[Captcha Solver] 正在进行验证码识别...")
            solved_result = captcha_solver(captcha_image_url, session)
            captcha_code = handle_captcha_solved_result(solved_result)
            log("[Captcha Solver] 识别的验证码是: {}".format(captcha_code))

            if CHECK_CAPTCHA_SOLVER_USAGE:
                usage = get_captcha_solver_usage()
                log("[Captcha Solver] 当前日期 {0} API 使用次数: {1}".format(
                    usage[0]["date"], usage[0]["count"]
                ))

            f2 = session.post(
                url,
                headers=headers,
                data={
                    "subaction": "login",
                    "sess_id": sess_id,
                    "captcha_code": captcha_code,
                },
            )
            if "To finish the login process please solve the following captcha." not in f2.text:
                log("[Captcha Solver] 验证通过")
                return sess_id, session
            else:
                log("[Captcha Solver] 验证失败")
                return "-1", session
    else:
        return sess_id, session

# 获取服务器列表
def get_servers(sess_id: str, session: requests.session) -> {}:
    # 获取服务器列表# 
    d = {}
    url = "https://support.euserv.com/index.iphp?sess_id=" + sess_id
    headers = {"user-agent": user_agent, "origin": "https://www.euserv.com"}
    f = session.get(url=url, headers=headers)
    f.raise_for_status()
    soup = BeautifulSoup(f.text, "html.parser")
    for tr in soup.select(
        "#kc2_order_customer_orders_tab_content_1 .kc2_order_table.kc2_content_table tr"
    ):
        server_id = tr.select(".td-z1-sp1-kc")
        if not len(server_id) == 1:
            continue
        flag = (
            True
            if tr.select(".td-z1-sp2-kc .kc2_order_action_container")[0]
            .get_text()
            .find("Contract extension possible from")
            == -1
            else False
        )
        d[server_id[0].get_text()] = flag
    return d

# 续期操作
def renew(
    sess_id: str, session: requests.session, password: str, order_id: str
) -> bool:
    # 执行续期操作# 
    url = "https://support.euserv.com/index.iphp"
    headers = {
        "user-agent": user_agent,
        "Host": "support.euserv.com",
        "origin": "https://support.euserv.com",
        "Referer": "https://support.euserv.com/index.iphp",
    }
    data = {
        "Submit": "Extend contract",
        "sess_id": sess_id,
        "ord_no": order_id,
        "subaction": "choose_order",
        "choose_order_subaction": "show_contract_details",
    }
    session.post(url, headers=headers, data=data)

    # 弹出 'Security Check' 窗口，将自动触发 '发送 PIN'。
    session.post(
        url,
        headers=headers,
        data={
            "sess_id": sess_id,
            "subaction": "show_kc2_security_password_dialog",
            "prefix": "kc2_customer_contract_details_extend_contract_",
            "type": "1",
        },
    )

    # 等待邮件解析器解析出 PIN
    time.sleep(WAITING_TIME_OF_PIN)
    # 获取 PIN 码
    pin = get_gmail_pin(
        mail_address=MAIL_ADDRESS,
        app_password=APP_PASSWORD,
        sender_filter=SENDER_FILTER,
        subject_filter=SUBJECT_FILTER,
        max_mails=MAX_MAILS,
        code_pattern=CODE_PATTER,
        timeout=WAITING_TIME_OF_PIN
    )
    
    if pin:
        log(f"[Gmail] PIN: {pin}")
    else:
        raise Exception("无法获取 PIN")
    
    # 使用 PIN 获取 token
    data = {
        "auth": pin,
        "sess_id": sess_id,
        "subaction": "kc2_security_password_get_token",
        "prefix": "kc2_customer_contract_details_extend_contract_",
        "type": 1,
        "ident": f"kc2_customer_contract_details_extend_contract_{order_id}",
    }
    f = session.post(url, headers=headers, data=data)
    f.raise_for_status()
    if not json.loads(f.text)["rs"] == "success":
        return False
    token = json.loads(f.text)["token"]["value"]
    data = {
        "sess_id": sess_id,
        "ord_id": order_id,
        "subaction": "kc2_customer_contract_details_extend_contract_term",
        "token": token,
    }
    session.post(url, headers=headers, data=data)
    time.sleep(5)
    return True

# 检查续期状态
def check(sess_id: str, session: requests.session):
    # 检查续期状态# 
    print("Checking.......")
    d = get_servers(sess_id, session)
    flag = True
    for key, val in d.items():
        if val:
            flag = False
            log("[AutoEUServerless] ServerID: %s 续期失败!" % key)

    if flag:
        log("[AutoEUServerless] 所有工作完成！尽情享受~")

# 发送 Telegram 通知
def telegram():
    message = (
        "<b>AutoEUServerless 日志</b>\n\n" + desp +
        "\n<b>版权声明：</b>\n"
        "本脚本基于 GPL-3.0 许可协议，版权所有。\n\n"
        
        "<b>致谢：</b>\n"
        "特别感谢 <a href='https://github.com/lw9726/eu_ex'>eu_ex</a> 的贡献和启发, 本项目在此基础整理。\n"
        "开发者：<a href='https://github.com/lw9726/eu_ex'>WizisCool</a>\n"
        "<a href='https://www.nodeseek.com/space/8902#/general'>个人Nodeseek主页</a>\n"
        "<a href='https://dooo.ng'>个人小站Dooo.ng</a>\n\n"
        "<b>支持项目：</b>\n"
        "⭐️ 给我们一个 GitHub Star! ⭐️\n"
        "<a href='https://github.com/WizisCool/AutoEUServerless'>访问 GitHub 项目</a>"
    )

    # 请不要删除本段版权声明, 开发不易, 感谢! 感谢!
    # 请勿二次售卖,出售,开源不易,万分感谢!
    data = {
        "chat_id": TG_USER_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true"
    }
    response = requests.post(
        TG_API_HOST + "/bot" + TG_BOT_TOKEN + "/sendMessage", data=data
    )
    if response.status_code != 200:
        print("Telegram Bot 推送失败")
    else:
        print("Telegram Bot 推送成功")


def get_gmail_pin(
    mail_address: str,
    app_password: str,
    sender_filter: str,
    subject_filter: str,
    max_mails: int,
    code_pattern: str,
    timeout: int = 15
) -> Optional[str]:
    """
    从 Gmail 邮箱获取符合条件的邮件并提取 6 位 PIN 码，并标记已读。

    参数:
        mail_address (str): Gmail 邮箱地址
        app_password (str): Gmail 应用专用密码
        sender_filter (str): 发件人过滤条件（如 'EUserv Support'）
        subject_filter (str): 主题过滤条件（如 'EUserv - PIN for the Confirmation of a Security Check'）
        max_mails (int): 最大检查的邮件数量
        code_pattern (str): 用于提取 PIN 码的正则表达式（如 r"\b\d{6}\b"）
        timeout (int): 等待邮件的最大时间（秒），默认 15 秒

    返回:
        str | None: 提取的 6 位 PIN 码，如果未找到则返回 None
    """
    try:
        # 连接到 Gmail IMAP 服务器
        imap_server = "outlook.office365.com"
        imap = imaplib.IMAP4_SSL(imap_server)
        imap.login(mail_address, app_password)

        # 选择收件箱
        imap.select("INBOX")

        start_time = time.time()
        pin = None

        while time.time() - start_time < timeout:
            # 搜索所有未读邮件
            _, message_numbers = imap.search(None, "UNSEEN")

            # 限制检查的邮件数量
            message_numbers = message_numbers[0].split()[:max_mails]
            if not message_numbers:
                time.sleep(2)  # 没有未读邮件，等待后重试
                continue

            for num in message_numbers:
                # 获取邮件内容
                _, msg_data = imap.fetch(num, "(RFC822)")
                email_body = msg_data[0][1]
                msg = email.message_from_bytes(email_body)

                # 获取发件人
                from_header = decode_header(msg.get("From"))[0][0]
                from_str = from_header.decode() if isinstance(from_header, bytes) else from_header
                if sender_filter not in from_str:
                    continue

                # 获取主题
                subject_header = decode_header(msg.get("Subject"))[0][0]
                subject = subject_header.decode() if isinstance(subject_header, bytes) else subject_header
                if subject_filter != subject:
                    continue

                # 获取邮件正文
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            body = part.get_payload(decode=True).decode()
                            match = re.search(code_pattern, body)
                            if match:
                                pin = match.group(0)
                                log(f"[Gmail] 找到 PIN 码: {pin}")
                                # 标记邮件为已读
                                imap.store(num, '+FLAGS', '\Seen')
                                break
                else:
                    body = msg.get_payload(decode=True).decode()
                    match = re.search(code_pattern, body)
                    if match:
                        pin = match.group(0)
                        log(f"[Gmail] 找到 PIN 码: {pin}")
                        # 标记邮件为已读
                        imap.store(num, '+FLAGS', '\Seen')
                        break

                if pin:
                    break

            if pin:
                break
            time.sleep(2)  # 等待后重试

        # 关闭连接
        imap.logout()
        if not pin:
            log(f"[Gmail] 在 {timeout} 秒内未找到符合条件的 PIN 码")
        return pin

    except Exception as e:
        log(f"[Gmail] 获取 PIN 码失败: {str(e)}")
        return None

def main_handler(event, context):
    # 主函数，处理每个账户的续期# 
    if not USERNAME or not PASSWORD:
        log("[AutoEUServerless] 你没有添加任何账户")
        exit(1)
    user_list = USERNAME.strip().split()
    passwd_list = PASSWORD.strip().split()
    if len(user_list) != len(passwd_list):
        log("[AutoEUServerless] 用户名和密码数量不匹配!")
        exit(1)
    for i in range(len(user_list)):
        print("*" * 30)
        log("[AutoEUServerless] 正在续费第 %d 个账号" % (i + 1))
        sessid, s = login(user_list[i], passwd_list[i])
        if sessid == "-1":
            log("[AutoEUServerless] 第 %d 个账号登陆失败，请检查登录信息" % (i + 1))
            continue
        SERVERS = get_servers(sessid, s)
        log("[AutoEUServerless] 检测到第 {} 个账号有 {} 台 VPS，正在尝试续期".format(i + 1, len(SERVERS)))
        for k, v in SERVERS.items():
            if v:
                if not renew(sessid, s, passwd_list[i], k):
                    log("[AutoEUServerless] ServerID: %s 续订错误!" % k)
                else:
                    log("[AutoEUServerless] ServerID: %s 已成功续订!" % k)
            else:
                log("[AutoEUServerless] ServerID: %s 无需更新" % k)
        time.sleep(15)
        check(sessid, s)
        time.sleep(5)

    # 发送 Telegram 通知
    if TG_BOT_TOKEN and TG_USER_ID and TG_API_HOST:
        telegram()

    print("*" * 30)

if __name__ == "__main__":
     main_handler(None, None)

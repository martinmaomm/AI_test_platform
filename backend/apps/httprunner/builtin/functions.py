"""
Built-in functions used in YAML/JSON testcases.
"""

import datetime
import random
import string
import time
import uuid as _uuid

from httprunner.exceptions import ParamsError

try:
    from faker import Faker as _Faker
    _faker_zh = _Faker("zh_CN")
    _faker_en = _Faker()
    _FAKER_AVAILABLE = True
except ImportError:
    _FAKER_AVAILABLE = False


# ==================== 原始内置函数 ====================

def gen_random_string(str_len=10):
    """ generate random string with specified length
    """
    try:
        str_len = int(str_len)
    except (ValueError, TypeError):
        str_len = 10
    return "".join(
        random.choice(string.ascii_letters + string.digits) for _ in range(max(1, str_len))
    )


def get_timestamp(str_len=13):
    """ get timestamp string, length can only between 0 and 16
    """
    try:
        str_len = int(str_len)
    except (ValueError, TypeError):
        str_len = 13
    if 0 < str_len < 17:
        return str(time.time()).replace(".", "")[:str_len]
    raise ParamsError("timestamp length can only between 0 and 16.")


def get_current_date(fmt="%Y-%m-%d"):
    """ get current date, default format is %Y-%m-%d
    """
    return datetime.datetime.now().strftime(fmt)


def sleep(n_secs):
    """ sleep n seconds
    """
    try:
        time.sleep(float(n_secs))
    except (ValueError, TypeError):
        pass


# ==================== 通用辅助函数（无需 Faker）====================

def get_random_string(str_len=10):
    """ generate random alphanumeric string with specified length.
    str_len: 字符串长度，HttpRunner 传入时可能为字符串类型，内部自动转换
    """
    try:
        str_len = int(str_len)
    except (ValueError, TypeError):
        str_len = 10
    return "".join(
        random.choice(string.ascii_letters + string.digits) for _ in range(max(1, str_len))
    )


def get_random_int(min_val=0, max_val=100):
    """ generate a random integer between min_val and max_val (inclusive).
    参数支持字符串类型，内部自动转换为整数
    """
    try:
        min_val = int(min_val)
    except (ValueError, TypeError):
        min_val = 0
    try:
        max_val = int(max_val)
    except (ValueError, TypeError):
        max_val = 100
    if min_val > max_val:
        min_val, max_val = max_val, min_val
    return random.randint(min_val, max_val)


def get_uuid():
    """ generate a UUID4 string
    """
    return str(_uuid.uuid4())


def get_random_phone():
    """ generate a random Chinese mobile phone number
    """
    if _FAKER_AVAILABLE:
        return _faker_zh.phone_number()
    # fallback: generate manually
    prefixes = [
        '130', '131', '132', '133', '134', '135', '136', '137', '138', '139',
        '150', '151', '152', '153', '155', '156', '157', '158', '159',
        '166', '176', '177', '178',
        '180', '181', '182', '183', '184', '185', '186', '187', '188', '189',
    ]
    return random.choice(prefixes) + ''.join(str(random.randint(0, 9)) for _ in range(8))


def get_future_date(days=30):
    """ get a future date string in YYYY-MM-DD format.
    days: 未来天数，支持字符串类型，内部自动转换
    """
    try:
        days = int(days)
    except (ValueError, TypeError):
        days = 30
    future = datetime.datetime.now() + datetime.timedelta(days=days)
    return future.strftime("%Y-%m-%d")


def get_past_date(days=30):
    """ get a past date string in YYYY-MM-DD format.
    days: 过去天数，支持字符串类型，内部自动转换
    """
    try:
        days = int(days)
    except (ValueError, TypeError):
        days = 30
    past = datetime.datetime.now() - datetime.timedelta(days=days)
    return past.strftime("%Y-%m-%d")


# ==================== Faker 驱动的辅助函数 ====================

def get_random_name():
    """ generate a random Chinese name
    """
    if _FAKER_AVAILABLE:
        return _faker_zh.name()
    # fallback: 简单拼接
    surnames = ['王', '李', '张', '刘', '陈', '杨', '赵', '黄', '周', '吴',
                '徐', '孙', '胡', '朱', '高', '林', '何', '郭', '马', '罗']
    given = ['伟', '芳', '娜', '秀英', '敏', '静', '丽', '强', '磊', '军',
             '洋', '勇', '艳', '杰', '涛', '明', '超', '秀兰', '霞', '平']
    return random.choice(surnames) + random.choice(given)


def get_random_id_card():
    """ generate a random Chinese ID card number (18-digit, checksum-valid)
    """
    if _FAKER_AVAILABLE:
        return _faker_zh.ssn()
    # fallback: 简单生成（不保证严格校验）
    area_codes = ['110101', '310101', '440101', '330101', '210101', '500101']
    area = random.choice(area_codes)
    year = random.randint(1970, 2000)
    month = random.randint(1, 12)
    day = random.randint(1, 28)
    seq = random.randint(100, 999)
    base = f"{area}{year:04d}{month:02d}{day:02d}{seq}"
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    check_chars = '10X98765432'
    total = sum(int(b) * w for b, w in zip(base, weights))
    return base + check_chars[total % 11]


def get_random_email():
    """ generate a random email address
    """
    if _FAKER_AVAILABLE:
        return _faker_en.email()
    # fallback
    domains = ['gmail.com', 'qq.com', '163.com', '126.com', 'outlook.com', 'sina.com']
    username = ''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(8))
    return f"{username}@{random.choice(domains)}"


def get_random_ipv4():
    """ generate a random IPv4 address
    """
    if _FAKER_AVAILABLE:
        return _faker_en.ipv4_private()
    return '.'.join(str(random.randint(1, 254)) for _ in range(4))


def get_random_mac_address():
    """ generate a random MAC address
    """
    if _FAKER_AVAILABLE:
        return _faker_en.mac_address()
    return ':'.join(f"{random.randint(0, 255):02x}" for _ in range(6))


def get_random_bank_card():
    """ generate a random bank card number (Luhn-valid)
    """
    if _FAKER_AVAILABLE:
        return _faker_zh.credit_card_number()
    # 手动生成 16 位银行卡号（满足 Luhn 算法）
    prefix = random.choice(['4', '5'])  # Visa / Mastercard 前缀
    partial = [int(prefix)] + [random.randint(0, 9) for _ in range(14)]
    total = 0
    for i, digit in enumerate(reversed(partial)):
        if i % 2 == 0:
            d = digit * 2
            total += d - 9 if d > 9 else d
        else:
            total += digit
    check = (10 - total % 10) % 10
    return ''.join(str(d) for d in partial) + str(check)


def get_random_company():
    """ generate a random Chinese company name
    """
    if _FAKER_AVAILABLE:
        return _faker_zh.company()
    # fallback
    industries = ['科技', '网络', '数据', '信息', '智能', '软件', '电商', '物流']
    suffixes = ['有限公司', '股份有限公司', '集团有限公司', '科技有限公司']
    cities = ['北京', '上海', '广州', '深圳', '杭州', '成都', '武汉']
    return random.choice(cities) + random.choice(industries) + random.choice(suffixes)


def get_random_address():
    """ generate a random Chinese address
    """
    if _FAKER_AVAILABLE:
        return _faker_zh.address()
    # fallback
    provinces = ['北京市', '上海市', '广东省', '浙江省', '江苏省', '四川省']
    cities = ['朝阳区', '浦东新区', '天河区', '西湖区', '鼓楼区', '武侯区']
    streets = ['中山路', '人民路', '解放路', '建设路', '文化路', '科技路']
    return (random.choice(provinces) + random.choice(cities) +
            random.choice(streets) + str(random.randint(1, 999)) + '号')

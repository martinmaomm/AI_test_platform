#!/usr/bin/env python3
"""
ASGI启动脚本
用于启动支持WebSocket的Django服务器
"""

import os
import sys
import django
from django.core.asgi import get_asgi_application

# 添加 apps 目录到 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "apps"))

# 设置Django环境
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aits_backend.settings')
django.setup()

# 导入ASGI应用
from aits_backend.asgi import application

#if __name__ == "__main__":
    #BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # backend 目录绝对路径

    #APPS_DIR = os.path.join(BASE_DIR, "apps")
   # BACKEND_DIR = os.path.join(BASE_DIR, "aits_backend")  # 如果需要
    #import uvicorn
    
   # print("🚀 启动ASGI服务器...")
   # print("📡 WebSocket支持: 已启用")
   # print("🌐 服务器地址: http://127.0.0.1:8000")
   # print("🔌 WebSocket地址: ws://127.0.0.1:8000/ws/")
   # print("=" * 50)
    
    # 启动ASGI服务器
   # uvicorn.run(
   #     "run_asgi:application",
   #     host="127.0.0.1",
   #     port=8000,
   #     log_level="info"
   # )




# ... (上面的代码保持不变) ...

if __name__ == "__main__":
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # backend 目录绝对路径

    APPS_DIR = os.path.join(BASE_DIR, "apps")
    BACKEND_DIR = os.path.join(BASE_DIR, "aits_backend")  # 如果需要
    import uvicorn
    
    # 获取本机IP用于打印提示（这步是可选的，为了方便你看）
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except:
        local_ip = "127.0.0.1"

    print("🚀 启动ASGI服务器...")
    print("📡 WebSocket支持: 已启用")
    print(f"🌐 局域网访问地址: http://{local_ip}:8000") # 这里改成了动态显示你的IP
    print("🔌 WebSocket地址: ws://0.0.0.0:8000/ws/")
    print("=" * 50)
    
    # 启动ASGI服务器
    uvicorn.run(
        "run_asgi:application",
        host="0.0.0.0",    # <---【核心修改】去掉注释，改为 "0.0.0.0"
        port=8000,
        log_level="info"
    )
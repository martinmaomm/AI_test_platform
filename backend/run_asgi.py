#!/usr/bin/env python3
"""
ASGI启动脚本
用于启动支持WebSocket的Django服务器
"""

import os
import sys
import django

# 添加 apps 目录到 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "apps"))

# 设置Django环境
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

# 导入ASGI应用
from config.asgi import application

if __name__ == "__main__":
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
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )

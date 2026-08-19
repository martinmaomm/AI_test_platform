"""
工具模块URL配置
包括文件服务、WebSocket等
"""

from django.urls import path
from .storage import file_storage

app_name = 'util'

urlpatterns = [
    # 文件服务
    path('files/download/', file_storage.FileDownloadView.as_view(), name='file_download'),
    path('files/list/', file_storage.FileListView.as_view(), name='file_list'),
]

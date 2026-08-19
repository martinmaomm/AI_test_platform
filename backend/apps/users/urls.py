from django.urls import path
from . import views

app_name = 'users'

urlpatterns = [
    # 认证相关
    path('register/', views.UserRegistrationView.as_view(), name='register'),
    path('login/', views.UserLoginView.as_view(), name='login'),
    path('logout/', views.UserLogoutView.as_view(), name='logout'),
    path('current-user/', views.current_user, name='current_user'),
    path('change-password/', views.change_password, name='change_password'),
    path('csrf-token/', views.get_csrf_token, name='get_csrf_token'),
    
    # JWT相关
    path('refresh-token/', views.refresh_token, name='refresh_token'),
    path('verify-token/', views.verify_token, name='verify_token'),
    
    # 用户管理
    path('users/', views.UserListView.as_view(), name='user_list'),
    path('manage/profile/', views.UserProfileView.as_view(), name='user_profile'),
    path('manage/preferences/', views.user_preferences, name='user_preferences'),
]

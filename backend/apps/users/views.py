from rest_framework import status, generics, permissions
from rest_framework.decorators import api_view, permission_classes
from common.api import response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError
from django.shortcuts import get_object_or_404
from django.middleware.csrf import get_token

from .models import User, UserProfile
from .serializers import (
    UserSerializer, UserProfileSerializer, 
    UserLoginSerializer, UserRegistrationSerializer
)


class UserRegistrationView(generics.CreateAPIView):
    """用户注册视图"""
    queryset = User.objects.all()
    serializer_class = UserRegistrationSerializer
    permission_classes = [permissions.AllowAny]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            return response(
                kind="created",
                data=UserSerializer(user).data,
                message="注册成功"
            )
        return response(
            kind="validation_error",
            errors=serializer.errors
        )


class UserLoginView(generics.GenericAPIView):
    """用户登录视图 - 纯JWT认证"""
    serializer_class = UserLoginSerializer
    permission_classes = [permissions.AllowAny]
    
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            user = serializer.validated_data['user']

            # 生成JWT token对
            refresh = RefreshToken.for_user(user)
            access_token = refresh.access_token
            
            data = {
                "access": str(access_token),
                "refresh": str(refresh),
                "user": UserSerializer(user).data
            }
            return response(
                kind="success",
                data=data,
                message="登录成功"
            )
        
        return response(
            kind="validation_error",
            errors=serializer.errors,
            message="登录失败"
        )


class UserLogoutView(generics.GenericAPIView):
    """用户登出视图 - 简化版本"""
    permission_classes = [permissions.IsAuthenticated]
    
    def post(self, request):
        # 获取refresh token
        refresh_token = request.data.get('refresh')
        
        if refresh_token:
            try:
                # 尝试将refresh token加入黑名单（如果黑名单功能可用）
                from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
                from rest_framework_simplejwt.tokens import RefreshToken
                
                token = RefreshToken(refresh_token)
                token.blacklist()
            except (TokenError, ImportError, Exception) as e:
                # 如果黑名单功能不可用或token无效，仍然允许登出
                print(f"黑名单功能不可用或token无效: {e}")
                pass
        
        return response(
            kind="success",
            message="登出成功"
        )


class UserProfileView(generics.RetrieveUpdateAPIView):
    """用户资料视图"""
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_object(self):
        return self.request.user


class UserListView(generics.ListAPIView):
    """用户列表视图（仅管理员）"""
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAdminUser]


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def current_user(request):
    """获取当前用户信息"""
    serializer = UserSerializer(request.user)
    return response(
        kind="success",
        data=serializer.data,
        message="获取用户信息成功"
    )


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def change_password(request):
    """修改密码"""
    user = request.user
    old_password = request.data.get('old_password')
    new_password = request.data.get('new_password')
    
    if not user.check_password(old_password):
        return response(
            kind="error",
            message="原密码错误",
            status_code=status.HTTP_400_BAD_REQUEST
        )
    
    user.set_password(new_password)
    user.save()
    
    return response(
        kind="success",
        message="密码修改成功"
    )


@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def get_csrf_token(request):
    """获取CSRF token"""
    return response(
        kind="success",
        data={'csrfToken': get_token(request)},
        message="CSRF token获取成功"
    )


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def refresh_token(request):
    """刷新JWT token"""
    refresh_token = request.data.get('refresh')
    
    if not refresh_token:
        return response(
            kind="validation_error",
            message="缺少refresh token",
            status_code=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        refresh = RefreshToken(refresh_token)
        access_token = refresh.access_token
        
        data = {
            "access": str(access_token),
            "refresh": str(refresh)
        }
        
        return response(
            kind="success",
            data=data,
            message="Token刷新成功"
        )
    except TokenError:
        return response(
            kind="error",
            message="无效的refresh token",
            status_code=status.HTTP_401_UNAUTHORIZED
        )


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def verify_token(request):
    """验证JWT token"""
    token = request.data.get('token')
    
    if not token:
        return response(
            kind="validation_error",
            message="缺少token",
            status_code=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        # 验证token
        from rest_framework_simplejwt.tokens import AccessToken
        access_token = AccessToken(token)
        
        # 获取用户信息
        user_id = access_token['user_id']
        user = User.objects.get(id=user_id)
        
        data = {
            "token": str(access_token),
            "user": UserSerializer(user).data
        }
        
        return response(
            kind="success",
            data=data,
            message="Token验证成功"
        )
    except TokenError:
        return response(
            kind="error",
            message="无效的token",
            status_code=status.HTTP_401_UNAUTHORIZED
        )
    except User.DoesNotExist:
        return response(
            kind="error",
            message="用户不存在",
            status_code=status.HTTP_401_UNAUTHORIZED
        )


@api_view(['GET', 'PUT'])
@permission_classes([permissions.IsAuthenticated])
def user_preferences(request):
    """获取或更新用户偏好设置"""
    from .models import UserPreference
    user = request.user
    
    # 确保用户有偏好设置
    user_preference, created = UserPreference.objects.get_or_create(user=user)
    
    if request.method == 'GET':
        # 获取用户偏好设置
        preferences_data = {
            'selectedProject': user_preference.selected_project,
            'theme': user_preference.theme,
            'language': user_preference.language
        }
        return response(
            kind="success",
            data=preferences_data,
            message="获取用户偏好设置成功"
        )
    
    elif request.method == 'PUT':
        # 更新用户偏好设置
        data = request.data
        
        # 更新选中的项目
        if 'selectedProject' in data:
            user_preference.selected_project = data['selectedProject']
        
        # 更新主题
        if 'theme' in data:
            user_preference.theme = data['theme']
        
        # 更新语言
        if 'language' in data:
            user_preference.language = data['language']
        
        user_preference.save()
        
        # 返回更新后的偏好设置
        preferences_data = {
            'selectedProject': user_preference.selected_project,
            'theme': user_preference.theme,
            'language': user_preference.language
        }
        
        return response(
            kind="success",
            data=preferences_data,
            message="更新用户偏好设置成功"
        )
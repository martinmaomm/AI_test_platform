from rest_framework import serializers
from django.contrib.auth import authenticate
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .login_history import record_successful_login
from .models import LoginRecord, User, UserProfile


class UserProfileSerializer(serializers.ModelSerializer):
    """用户资料序列化器"""
    class Meta:
        model = UserProfile
        fields = ['title', 'department', 'company', 'skills']


class UserSerializer(serializers.ModelSerializer):
    """用户序列化器"""
    profile = UserProfileSerializer(required=False)
    password = serializers.CharField(write_only=True, min_length=8)
    role = serializers.SerializerMethodField()

    def get_role(self, obj):
        return 'admin' if obj.is_staff or obj.is_superuser else 'user'
    
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 
                 'phone', 'avatar', 'bio', 'profile', 'password', 
                 'role', 'is_staff', 'is_superuser',
                 'is_active', 'is_verified', 'created_at', 'updated_at']
        read_only_fields = ['id', 'role', 'is_staff', 'is_superuser', 'is_active', 'is_verified', 'created_at', 'updated_at']
    
    def create(self, validated_data):
        profile_data = validated_data.pop('profile', {})
        user = User.objects.create_user(**validated_data)
        # 创建用户资料
        UserProfile.objects.create(user=user, **profile_data)
        return user

    def update(self, instance, validated_data):
        profile_data = validated_data.pop('profile', None)
        update_fields = list(validated_data)
        if 'password' in validated_data:
            instance.set_password(validated_data.pop('password'))
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        # A concurrent administrator's role/status changes must not be overwritten
        # by the older request.user snapshot used for a personal-profile update.
        if update_fields:
            instance.save(update_fields=update_fields + ['updated_at'])
        if profile_data is not None:
            UserProfile.objects.update_or_create(user=instance, defaults=profile_data)
        return instance


class ManagedUserSerializer(serializers.ModelSerializer):
    """Explicit management fields; never accept arbitrary Django auth flags."""
    role = serializers.ChoiceField(choices=['admin', 'user'], default='user')
    password = serializers.CharField(write_only=True, min_length=8, required=False, trim_whitespace=False)

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'role',
                  'is_staff', 'is_superuser', 'is_active', 'password', 'created_at', 'updated_at']
        read_only_fields = ['id', 'is_staff', 'is_superuser', 'created_at', 'updated_at']

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['role'] = 'admin' if instance.is_staff or instance.is_superuser else 'user'
        return data

    def validate(self, attrs):
        actor = self.context.get('actor', self.context['request'].user)
        # Reject privilege fields rather than silently pretending they were saved.
        if any(key in self.initial_data for key in ('is_superuser', 'is_staff', 'groups', 'user_permissions')):
            raise serializers.ValidationError('请使用角色字段设置权限，不能直接修改系统权限标记。')
        if not self.instance and 'password' not in attrs:
            raise serializers.ValidationError({'password': '新建用户必须设置密码。'})
        if attrs.get('role') == 'admin' and not actor.is_superuser:
            raise serializers.ValidationError({'role': '只有超级管理员可以设置管理员身份。'})
        return attrs

    def create(self, validated_data):
        role = validated_data.pop('role', 'user')
        password = validated_data.pop('password')
        user = User.objects.create_user(password=password, is_staff=role == 'admin', **validated_data)
        UserProfile.objects.create(user=user)
        return user

    def update(self, instance, validated_data):
        role = validated_data.pop('role', None)
        password = validated_data.pop('password', None)
        for key, value in validated_data.items():
            setattr(instance, key, value)
        if role is not None:
            instance.is_staff = role == 'admin'
        if password is not None:
            instance.set_password(password)
        instance.save()
        return instance


class UserProjectAssignmentSerializer(serializers.Serializer):
    project_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), allow_empty=True, max_length=10000,
    )

    def validate_project_ids(self, values):
        from projects.models import Project
        selected = set(values)
        if len(selected) != Project.objects.filter(pk__in=selected).count():
            raise serializers.ValidationError('所选项目不存在或已被删除，请刷新后重试。')
        return sorted(selected)


class UserLoginSerializer(serializers.Serializer):
    """用户登录序列化器"""
    username = serializers.CharField()
    password = serializers.CharField()
    
    def validate(self, attrs):
        username = attrs.get('username')
        password = attrs.get('password')
        
        if username and password:
            user = authenticate(username=username, password=password)
            if not user:
                raise serializers.ValidationError('用户名或密码错误')
            if not user.is_active:
                raise serializers.ValidationError('用户账户已被禁用')
            attrs['user'] = user
        else:
            raise serializers.ValidationError('必须提供用户名和密码')
        
        return attrs


class LoginRecordingTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Preserve SimpleJWT's contract while recording successful token login."""

    def validate(self, attrs):
        data = super().validate(attrs)
        request = self.context.get('request')
        if request is not None:
            record_successful_login(user=self.user, request=request)
        return data


class LoginRecordSerializer(serializers.ModelSerializer):
    """Read-only personal login history representation."""

    class Meta:
        model = LoginRecord
        fields = ['id', 'logged_in_at', 'ip_address', 'user_agent']
        read_only_fields = fields


class UserRegistrationSerializer(serializers.ModelSerializer):
    """用户注册序列化器"""
    password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True)
    
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'password', 'confirm_password', 
                 'first_name', 'last_name']
        read_only_fields = ['id']
    
    def validate(self, attrs):
        if attrs['password'] != attrs['confirm_password']:
            raise serializers.ValidationError('两次输入的密码不一致')
        return attrs
    
    def create(self, validated_data):
        validated_data.pop('confirm_password')
        user = User.objects.create_user(**validated_data)
        # 创建用户资料
        UserProfile.objects.create(user=user)
        return user

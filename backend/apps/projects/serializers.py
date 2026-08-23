from rest_framework import serializers
from .models import Project, ProjectMember, Environment
from .knowledge.models import KnowledgeBaseFile, UploadedFile


class UploadedFileSerializer(serializers.ModelSerializer):
    """上传文件序列化器"""

    # 关联字段序列化（避免N+1查询时用select_related优化）
    uploaded_by_username = serializers.CharField(
        source='uploaded_by.username', read_only=True
    )
    project_name = serializers.CharField(
        source='project.name', read_only=True
    )

    # 衍生/计算字段
    file_url = serializers.SerializerMethodField()
    file_exists = serializers.SerializerMethodField()

    class Meta:
        model = UploadedFile
        fields = [
            'id', 'original_name', 'file', 'file_url', 'file_size',
            'file_type', 'file_hash', 'upload_status',
            'file_exists', 'uploaded_by_username', 'project_name',
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'file', 'file_hash', 'upload_status', 'file_url', 'file_exists',
            'uploaded_by_username', 'project_name',
            'created_at', 'updated_at'
        ]

    def get_file_url(self, obj):
        """返回文件的绝对访问URL"""
        request = self.context.get('request')
        if obj.file and hasattr(obj.file, 'url'):
            return request.build_absolute_uri(obj.file.url) if request else obj.file.url
        return None

    def get_file_exists(self, obj):
        """检查文件是否存在于存储中"""
        return bool(obj.file and obj.file.storage.exists(obj.file.name))



class UploadedFileCreateSerializer(serializers.ModelSerializer):
    """上传文件创建序列化器"""

    class Meta:
        model = UploadedFile
        fields = [
            'original_name', 'file_size', 'file_type'
        ]
        read_only_fields = ['id', 'file', 'file_hash', 'upload_status', 'created_at', 'updated_at']

# 环境序列化器
class EnvironmentSerializer(serializers.ModelSerializer):
    """环境序列化器"""
    category_display = serializers.CharField(source='get_category_display', read_only=True)
    is_api_environment = serializers.BooleanField(read_only=True)
    is_web_environment = serializers.BooleanField(read_only=True)
    is_app_environment = serializers.BooleanField(read_only=True)
    
    # 根据环境类型提供配置示例
    config_example = serializers.SerializerMethodField()
    
    class Meta:
        model = Environment
        fields = [
            'id', 'name', 'description', 'category', 'category_display',
            'config', 'is_active', 'is_api_environment', 'is_web_environment', 'is_app_environment',
            'config_example', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def get_config_example(self, obj):
        """获取配置示例"""
        return obj.get_config_example()


# 环境创建序列化器
class EnvironmentCreateSerializer(serializers.ModelSerializer):
    """环境创建序列化器"""
    class Meta:
        model = Environment
        fields = [
            'name', 'description', 'category', 'config', 'is_active'
        ]
    
    def validate(self, attrs):
        """验证环境配置"""
        category = attrs.get('category')
        config = attrs.get('config', {})
        
        # 创建临时对象进行验证
        temp_env = Environment(category=category, config=config)
        errors = temp_env.validate_config()
        
        if errors:
            raise serializers.ValidationError({
                'config': errors
            })
        
        return attrs


class ProjectMemberSerializer(serializers.ModelSerializer):
    """项目成员序列化器"""
    username = serializers.CharField(source='user.username', read_only=True)
    email = serializers.CharField(source='user.email', read_only=True)
    first_name = serializers.CharField(source='user.first_name', read_only=True)
    last_name = serializers.CharField(source='user.last_name', read_only=True)
    
    class Meta:
        model = ProjectMember
        fields = ['id', 'username', 'email', 'first_name', 'last_name',
                 'role', 'can_edit', 'can_delete', 'can_execute_tests', 
                 'can_view_reports', 'joined_at', 'updated_at']

class KnowledgeBaseFileSerializer(serializers.ModelSerializer):
    """知识库文件序列化器"""

    uploaded_by_username = serializers.CharField(source='uploaded_by.username', read_only=True)
    file_path = serializers.SerializerMethodField()

    class Meta:
        model = KnowledgeBaseFile
        fields = [
            'id',
            'file_name',        # 来自 property
            'file_size',        # 来自 property
            'file_size_mb',     # 来自 property
            'file_type',        # 来自 property
            'file_path',        # 单独方法获取路径
            'status',
            'parsed_content',
            'error_message',
            'metadata',
            'uploaded_by_username',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id', 'file_name', 'file_size', 'file_size_mb', 'file_type',
            'file_path', 'parsed_content', 'error_message',
            'created_at', 'updated_at'
        ]

    def get_file_path(self, obj):
        """从关联的UploadedFile获取文件路径"""
        if obj.uploaded_file and obj.uploaded_file.file:
            return obj.uploaded_file.file.url
        return None



class ProjectSerializer(serializers.ModelSerializer):
    """项目序列化器"""
    created_by_username = serializers.CharField(source='created_by.username', read_only=True)
    owner_username = serializers.CharField(source='owner.username', read_only=True)
    project_type_display = serializers.CharField(source='get_project_type_display', read_only=True)
    
    class Meta:
        model = Project
        fields = [
            'id', 'name', 'description', 'project_type', 'project_type_display',
            'created_by_username', 'owner_username',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class ProjectDetailSerializer(serializers.ModelSerializer):
    """项目详情序列化器"""
    environments = EnvironmentSerializer(many=True, read_only=True)
    members = ProjectMemberSerializer(many=True, read_only=True)
    knowledge_files = KnowledgeBaseFileSerializer(many=True, read_only=True)
    created_by_username = serializers.CharField(source='created_by.username', read_only=True)
    owner_username = serializers.CharField(source='owner.username', read_only=True)
    project_type_display = serializers.CharField(source='get_project_type_display', read_only=True)

    class Meta:
        model = Project
        fields = [
            'id', 'name', 'description', 'project_type', 'project_type_display',
            'environments', 'members', 'knowledge_files',
            'created_by_username', 'owner_username', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class ProjectUpdateSerializer(serializers.ModelSerializer):
    """项目编辑序列化器，只允许修改名称和描述。"""

    class Meta:
        model = Project
        fields = ['name', 'description']

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('项目名称不能为空')
        return value

    def validate_description(self, value):
        return value.strip()


class ProjectCreateSerializer(serializers.ModelSerializer):
    """项目创建序列化器"""
    project_type = serializers.ChoiceField(
        choices=Project.PROJECT_TYPE_CHOICES,
        default='api',
        required=False
    )

    class Meta:
        model = Project
        fields = ['name', 'description', 'project_type']
    
    def create(self, validated_data):
        project = super().create(validated_data)
        
        # 创建者自动成为项目所有者
        ProjectMember.objects.create(
            project=project,
            user=self.context['request'].user,
            role='owner',
            can_edit=True,
            can_delete=True,
            can_execute_tests=True,
            can_view_reports=True
        )
        
        return project


class ProjectMemberCreateSerializer(serializers.ModelSerializer):
    """项目成员创建序列化器"""
    class Meta:
        model = ProjectMember
        fields = ['user', 'role', 'can_edit', 'can_delete', 
                 'can_execute_tests', 'can_view_reports']
    
    def validate(self, attrs):
        project = self.context.get('project')
        user = attrs.get('user')
        
        # 检查用户是否已经是项目成员
        if ProjectMember.objects.filter(project=project, user=user).exists():
            raise serializers.ValidationError('该用户已经是项目成员')
        
        return attrs




class KnowledgeBaseFileCreateSerializer(serializers.ModelSerializer):
    """知识库文件创建序列化器"""
    class Meta:
        model = KnowledgeBaseFile
        fields = ['project', 'uploaded_file']
    
    def create(self, validated_data):
        validated_data['uploaded_by'] = self.context['request'].user
        return super().create(validated_data)

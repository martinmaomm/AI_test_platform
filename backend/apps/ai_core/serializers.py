"""
AI核心模块的序列化器
"""
from rest_framework import serializers
from .models import LLMConfiguration, LLMUsageLog, RAGConfiguration


class LLMConfigurationSerializer(serializers.ModelSerializer):
    """LLM配置序列化器"""
    
    provider_display = serializers.CharField(source='get_provider_display', read_only=True)
    model_type_display = serializers.CharField(source='get_model_type_display', read_only=True)
    
    class Meta:
        model = LLMConfiguration
        fields = [
            'id', 'model_type', 'model_type_display', 'provider', 'provider_name', 'provider_display', 'is_active',
            'api_key', 'base_url', 'model_name', 'extra_config', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    
    def validate(self, attrs):
        """验证配置数据"""
        provider = attrs.get('provider')
        api_key = attrs.get('api_key', '')
        base_url = attrs.get('base_url', '')
        model_name = attrs.get('model_name', '')
        
        # 验证必填字段
        if provider in ['openai', 'deepseek']:
            if not api_key:
                raise serializers.ValidationError({
                    'api_key': f'{provider}需要提供API密钥'
                })
            if not base_url:
                raise serializers.ValidationError({
                    'base_url': f'{provider}需要提供基础URL'
                })
        
        if provider == 'ollama':
            if not base_url:
                raise serializers.ValidationError({
                    'base_url': 'Ollama需要提供基础URL'
                })
        
        if not model_name:
            raise serializers.ValidationError({
                'model_name': '必须指定模型名称'
            })
        
        return attrs
    
    def create(self, validated_data):
        """创建LLM配置"""
        # 自动设置创建者
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)


class LLMConfigurationCreateSerializer(LLMConfigurationSerializer):
    """创建LLM配置的序列化器"""
    
    class Meta(LLMConfigurationSerializer.Meta):
        fields = LLMConfigurationSerializer.Meta.fields
        extra_kwargs = {
            'api_key': {'write_only': True},  # API密钥只写不读
        }


class LLMConfigurationListSerializer(serializers.ModelSerializer):
    """LLM配置列表序列化器（隐藏敏感信息）"""
    
    provider_display = serializers.CharField(source='get_provider_display', read_only=True)
    model_type_display = serializers.CharField(source='get_model_type_display', read_only=True)
    api_key_masked = serializers.SerializerMethodField()
    
    class Meta:
        model = LLMConfiguration
        fields = [
            'id', 'model_type', 'model_type_display', 'provider', 'provider_name', 'provider_display', 'is_active',
            'api_key_masked', 'base_url', 'model_name', 'created_at', 'updated_at'
        ]
    
    def get_api_key_masked(self, obj):
        """获取脱敏的API密钥"""
        if not obj.api_key:
            return ''
        
        key = obj.api_key
        if len(key) <= 8:
            return '*' * len(key)
        
        return key[:4] + '*' * (len(key) - 8) + key[-4:]
    


class LLMUsageLogSerializer(serializers.ModelSerializer):
    """LLM使用日志序列化器"""
    
    configuration_display = serializers.CharField(source='configuration.model_name', read_only=True)
    provider = serializers.CharField(source='configuration.provider', read_only=True)
    
    class Meta:
        model = LLMUsageLog
        fields = [
            'id', 'configuration_display', 'provider', 'prompt_tokens', 'completion_tokens',
            'total_tokens', 'response_time', 'success', 'error_message',
            'task_type', 'task_id', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']




class LLMTestConnectionSerializer(serializers.Serializer):
    """测试LLM连接的序列化器"""
    
    config_id = serializers.IntegerField()


# ============ RAG配置序列化器 ============

class RAGConfigurationSerializer(serializers.ModelSerializer):
    """RAG配置序列化器"""
    
    vector_db_type_display = serializers.CharField(source='get_vector_db_type_display', read_only=True)
    
    class Meta:
        model = RAGConfiguration
        fields = [
            'id', 'name', 'is_active', 'is_default', 'vector_db_type', 'vector_db_type_display',
            'embedding_model', 'chunk_size', 'chunk_overlap',
            'milvus_host', 'milvus_port', 'milvus_collection_name', 'milvus_dim',
            'extra_config', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def validate_chunk_size(self, value):
        """验证分块大小"""
        if not 100 <= value <= 10000:
            raise serializers.ValidationError("分块大小必须在100到10000之间")
        return value
    
    def validate_chunk_overlap(self, value):
        """验证分块重叠大小"""
        if not 0 <= value <= 5000:
            raise serializers.ValidationError("分块重叠大小必须在0到5000之间")
        return value
    
    def validate_milvus_port(self, value):
        """验证Milvus端口"""
        if not 1 <= value <= 65535:
            raise serializers.ValidationError("端口号必须在1到65535之间")
        return value
    
    def validate_milvus_dim(self, value):
        """验证Milvus向量维度"""
        if not 1 <= value <= 10000:
            raise serializers.ValidationError("向量维度必须在1到10000之间")
        return value
    
    def validate(self, attrs):
        """验证配置数据"""
        vector_db_type = attrs.get('vector_db_type')
        
        # 验证Milvus相关字段
        if vector_db_type == 'milvus':
            milvus_host = attrs.get('milvus_host', '')
            milvus_port = attrs.get('milvus_port')
            milvus_collection_name = attrs.get('milvus_collection_name', '')
            milvus_dim = attrs.get('milvus_dim')
            
            if not milvus_host:
                raise serializers.ValidationError({
                    'milvus_host': 'Milvus主机地址不能为空'
                })
            
            if not milvus_collection_name:
                raise serializers.ValidationError({
                    'milvus_collection_name': 'Milvus集合名称不能为空'
                })
        
        return attrs
    
    def create(self, validated_data):
        """创建RAG配置"""
        # 自动设置创建者
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)


class RAGConfigurationCreateSerializer(RAGConfigurationSerializer):
    """创建RAG配置的序列化器"""
    
    class Meta(RAGConfigurationSerializer.Meta):
        fields = RAGConfigurationSerializer.Meta.fields


class RAGConfigurationListSerializer(serializers.ModelSerializer):
    """RAG配置列表序列化器"""
    
    vector_db_type_display = serializers.CharField(source='get_vector_db_type_display', read_only=True)
    
    class Meta:
        model = RAGConfiguration
        fields = [
            'id', 'name', 'is_active', 'is_default', 'vector_db_type', 'vector_db_type_display',
            'embedding_model', 'chunk_size', 'chunk_overlap',
            'milvus_host', 'milvus_port', 'milvus_collection_name', 'milvus_dim',
            'created_at', 'updated_at'
        ]


class RAGTestConnectionSerializer(serializers.Serializer):
    """测试RAG连接的序列化器"""
    
    config_id = serializers.IntegerField(required=True)


# ============ 视觉模型配置序列化器 ============

class VisionTestConnectionSerializer(serializers.Serializer):
    """测试视觉模型连接的序列化器"""
    
    config_id = serializers.IntegerField(required=True)


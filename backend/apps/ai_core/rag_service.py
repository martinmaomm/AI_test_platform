"""
RAG (Retrieval-Augmented Generation) 管理器
使用LangChain 1.0+ 实现RAG功能，支持多种向量数据库
调试阶段使用Chroma，生产阶段使用Milvus

功能包括：
- 向量数据库操作（文档摄入、搜索）

已升级到 LangChain 1.0+，使用新的导入路径和API
"""
import os
import logging
import hashlib
from typing import Dict, Any, Optional
from django.conf import settings
from django.utils import timezone

# LangChain 1.0+ 标准导入
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_milvus import Milvus
from langchain_chroma import Chroma
from langchain_core.documents import Document

logger = logging.getLogger(__name__)


class VectorDBConfig:
    """向量数据库配置"""
    
    def __init__(self):
        self.debug_mode = True  # 默认使用Chroma
        self.chunk_size = 500  # 减小分块大小
        self.chunk_overlap = 100  # 减小重叠大小
        
        # 嵌入模型配置（必须从数据库获取，不设置默认值）
        self.embedding_model = None
        
        # Chroma配置（调试模式）
        self.chroma_persist_directory = os.path.join(settings.BASE_DIR, 'chroma_db')
        
        # Milvus配置（生产模式）
        self.milvus_host = 'localhost'
        self.milvus_port = 19530
        self.milvus_collection_name = 'knowledge_base'
        self.milvus_dim = 1024  # 默认维度，会根据嵌入模型自动更新


class RAGManager:
    """RAG管理器 - 提供向量数据库操作和知识库搜索功能"""
    
    def __init__(self):
        self.config = VectorDBConfig()
        self.embedding_model = None
        self.vector_store = None
        self.text_splitter = None
        self._initialized = False
        
        # 从数据库加载配置（必须通过前端配置）
        self._load_config_from_db()
        
        # 初始化组件
        self._initialize_components()
    
    def _load_config_from_db(self):
        """从数据库加载RAG配置"""
        from .models import RAGConfiguration
        
        # 优先获取默认配置，否则获取任何启用的配置
        config = (RAGConfiguration.objects.filter(is_default=True, is_active=True).first() or 
                  RAGConfiguration.objects.filter(is_active=True).first())
        
        if config:
            logger.info(f"从数据库加载RAG配置: {config.name}")
            self._update_config_from_db(config)
            return
        
        # 检查是否有任何配置（包括禁用的）
        any_config = RAGConfiguration.objects.first()
        error_msg = (
            "数据库中存在RAG配置，但没有启用的配置。"
            "请在前端RAG配置页面启用一个配置，或创建一个新的RAG配置。"
            if any_config else
            "数据库中没有任何RAG配置。"
            "请在前端RAG配置页面创建并配置RAG，然后再使用RAG功能。"
        )
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    def _get_embedding_dimension(self, model_name: str) -> int:
        """根据嵌入模型名称获取向量维度（动态获取）"""
        # 优先使用硬编码映射（快速）
        model_dimensions = {
            'BAAI/bge-large-zh-v1.5': 1024,
            'BAAI/bge-m3': 1024,
            'BAAI/bge-small-zh-v1.5': 512,
        }
        
        if model_name in model_dimensions:
            return model_dimensions[model_name]
        
        # 如果不在映射中，尝试动态获取（需要先初始化模型）
        # 注意：这个方法在模型初始化前调用时可能无法工作
        # 因此保留默认值作为后备
        logger.warning(f"嵌入模型 {model_name} 不在已知维度映射中，使用默认值1024")
        return 1024  # 默认值
    
    def _update_config_from_db(self, config: 'RAGConfiguration'):
        """从数据库配置对象更新配置"""
        # 配置字段映射：数据库字段 -> config属性
        # 注意：chroma_persist_directory 使用默认值，不从数据库读取
        field_mapping = {
            'chunk_size': 'chunk_size',
            'chunk_overlap': 'chunk_overlap',
            'milvus_host': 'milvus_host',
            'milvus_port': 'milvus_port',
            'milvus_collection_name': 'milvus_collection_name',
            'embedding_model': 'embedding_model',
        }
        
        # 批量更新配置
        for db_field, config_attr in field_mapping.items():
            value = getattr(config, db_field, None)
            if value:
                setattr(self.config, config_attr, value)
        
        # 验证嵌入模型必须从数据库获取
        if not self.config.embedding_model:
            error_msg = "嵌入模型配置缺失。请在前端RAG配置页面设置嵌入模型。"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        # 向量数据库类型
        if config.vector_db_type:
            self.config.debug_mode = (config.vector_db_type.lower() == 'chroma')
        
        # 注意：向量维度将在初始化嵌入模型时动态获取，这里不设置
        # 使用预估维度（用于Milvus配置，实际维度会在模型初始化后更新）
        estimated_dim = self._get_embedding_dimension(self.config.embedding_model)
        self.config.milvus_dim = estimated_dim
        
        logger.info(f"RAG配置更新完成: 向量数据库={config.vector_db_type}, 嵌入模型={self.config.embedding_model}, 预估维度={estimated_dim}")
    
    def _ensure_initialized(self):
        """确保RAG管理器已初始化"""
        if not self._initialized:
            raise RuntimeError("RAG管理器尚未初始化")
    
    def _initialize_components(self):
        """初始化RAG组件"""
        # 初始化文本分割器（LangChain 1.0+ 简化参数）
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.config.chunk_size,
            chunk_overlap=self.config.chunk_overlap,
            separators=["\n\n", "\n", "。", "！", "？", "；", " ", ""]  # 支持中文标点符号
        )
        
        # 初始化嵌入模型（固定使用BGE-M3）
        self._initialize_embedding_model()
        
        # 初始化向量数据库
        self._initialize_vector_store()
        
        self._initialized = True
        logger.info("RAG管理器初始化成功")
    
    def _initialize_embedding_model(self):
        """初始化嵌入模型（从数据库配置读取）"""
        if not self.config.embedding_model:
            error_msg = "嵌入模型配置缺失。请在前端RAG配置页面设置嵌入模型。"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        model_name = self.config.embedding_model
        try:
            # 检测GPU支持
            try:
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
                if device == "cuda":
                    logger.info(f"检测到GPU，将使用CUDA加速嵌入模型")
            except ImportError:
                device = "cpu"
                logger.info("未安装PyTorch，使用CPU模式")
            
            # 配置嵌入模型，优化批量处理性能
            self.embedding_model = HuggingFaceEmbeddings(
                model_name=model_name,
                encode_kwargs={
                    "normalize_embeddings": True,
                    "batch_size": 32  # 批量处理大小，提高向量生成效率
                },
                model_kwargs={"device": device} if device == "cuda" else {}
            )
            
            # 动态获取实际维度（更准确）
            test_text = "test"
            test_embedding = self.embedding_model.embed_query(test_text)
            actual_dimension = len(test_embedding)
            
            # 更新配置中的维度
            self.config.milvus_dim = actual_dimension
            
            logger.info(f"使用嵌入模型: {model_name}, 维度: {actual_dimension} (动态获取), 设备: {device}")
        except Exception as e:
            logger.error(f"嵌入模型加载失败: {model_name}, 错误: {e}", exc_info=True)
            raise RuntimeError(f"嵌入模型初始化失败 ({model_name}): {e}") from e
    
    def _initialize_vector_store(self):
        """初始化向量数据库"""
        if self.config.debug_mode:
            self._initialize_chroma()
        else:
            try:
                self._initialize_milvus()
            except Exception as e:
                logger.warning(f"Milvus初始化失败，回退到Chroma: {e}")
                self.config.debug_mode = True
                self._initialize_chroma()
    
    def _initialize_chroma(self):
        """初始化Chroma向量数据库（多进程安全）"""
        import chromadb
        from chromadb.config import Settings
        
        os.makedirs(self.config.chroma_persist_directory, exist_ok=True)
        
        # 获取当前嵌入模型的维度
        expected_dim = self.config.milvus_dim
        collection_name = "knowledge_base"
        
        # 使用统一的Chroma设置，确保多进程环境中的一致性
        # 这是关键：所有进程必须使用完全相同的设置
        chroma_settings = Settings(
            anonymized_telemetry=False,
            allow_reset=True
        )
        
        # 直接创建LangChain的Chroma实例，不要先创建临时客户端
        # 这样可以避免设置冲突
        try:
            # 尝试直接创建，如果LangChain支持client_settings则使用
            try:
                self.vector_store = Chroma(
                    persist_directory=self.config.chroma_persist_directory,
                    embedding_function=self.embedding_model,
                    collection_name=collection_name,
                    client_settings=chroma_settings
                )
            except TypeError:
                # 如果LangChain版本不支持client_settings参数，使用默认方式
                # 注意：这可能导致设置冲突，但至少可以工作
                logger.warning("当前LangChain版本不支持client_settings参数，使用默认设置")
                self.vector_store = Chroma(
                    persist_directory=self.config.chroma_persist_directory,
                    embedding_function=self.embedding_model,
                    collection_name=collection_name
                )
            
            # 初始化后检查集合维度（通过vector_store访问）
            try:
                # 尝试获取集合信息来检查维度
                if hasattr(self.vector_store, '_collection'):
                    collection = self.vector_store._collection
                    if collection.count() > 0:
                        # 获取一个样本来检查维度
                        sample = collection.peek(limit=1)
                        if sample.get('embeddings') and len(sample['embeddings']) > 0:
                            existing_dim = len(sample['embeddings'][0])
                            if existing_dim != expected_dim:
                                logger.warning(
                                    f"Chroma集合维度不匹配: 现有维度={existing_dim}, 期望维度={expected_dim}。"
                                    f"将删除旧集合并创建新集合。"
                                )
                                # 删除集合并重新创建
                                if hasattr(self.vector_store, '_client'):
                                    self.vector_store._client.delete_collection(collection_name)
                                    logger.info(f"已删除维度不匹配的Chroma集合: {collection_name}")
                                    # 重新创建vector_store
                                    try:
                                        self.vector_store = Chroma(
                                            persist_directory=self.config.chroma_persist_directory,
                                            embedding_function=self.embedding_model,
                                            collection_name=collection_name,
                                            client_settings=chroma_settings
                                        )
                                    except TypeError:
                                        self.vector_store = Chroma(
                                            persist_directory=self.config.chroma_persist_directory,
                                            embedding_function=self.embedding_model,
                                            collection_name=collection_name
                                        )
            except Exception as e:
                # 如果无法检查维度，记录警告但继续
                logger.debug(f"无法检查Chroma集合维度: {e}。继续使用现有集合。")
            
            logger.info(f"Chroma向量数据库初始化成功: {self.config.chroma_persist_directory}, 维度={expected_dim}")
        except ValueError as e:
            # 处理Chroma客户端冲突错误
            if "already exists" in str(e) and "different settings" in str(e):
                error_msg = (
                    f"Chroma客户端设置冲突: {e}\n"
                    f"这通常发生在多进程环境中，当不同进程使用不同设置创建客户端时。\n"
                    f"解决方案：\n"
                    f"1. 重启所有Celery worker进程（推荐）\n"
                    f"2. 或者删除chroma_db目录并重新初始化"
                )
                logger.error(error_msg)
                raise RuntimeError(error_msg) from e
            else:
                raise
    
    def _initialize_milvus(self):
        """初始化Milvus向量数据库"""
        self.vector_store = Milvus(
            embedding_function=self.embedding_model,
            collection_name=self.config.milvus_collection_name,
            connection_args={
                "host": self.config.milvus_host,
                "port": self.config.milvus_port
            },
            index_params={
                "metric_type": "COSINE",
                "index_type": "IVF_FLAT",
                "params": {"nlist": 128}
            },
            search_params={
                "metric_type": "COSINE",
                "params": {"nprobe": 10}
            }
        )
        logger.info(f"Milvus向量数据库初始化成功: {self.config.milvus_host}:{self.config.milvus_port}")
    
    def _persist_vector_store(self):
        """持久化向量数据库（Chroma需要）"""
        # 在LangChain 1.0+中，Chroma会自动持久化到persist_directory
        # 对于Chroma，add_documents后会自动持久化，不需要显式调用
        # 显式调用persist()在某些版本中可能已废弃，但为了兼容性保留
        if self.config.debug_mode:
            # Chroma会自动持久化，不需要额外操作
            pass
        else:
            # Milvus不需要持久化操作
            pass
    
    # ============ 底层向量数据库操作 ============
    
    def ingest_document(self, content: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        将文档内容摄入向量数据库
        
        Args:
            content: 文档内容
            metadata: 文档元数据
            
        Returns:
            摄入结果
        """
        self._ensure_initialized()
        
        try:
            doc_id = self._generate_doc_id(content, metadata)
            
            # 使用 split_documents 直接创建 Document 对象（LangChain 1.0+ 推荐方式）
            temp_doc = Document(page_content=content, metadata=metadata)
            documents = self.text_splitter.split_documents([temp_doc])
            
            # 为每个分块添加额外元数据
            for i, doc in enumerate(documents):
                doc.metadata.update({
                    'doc_id': doc_id,  # 文档ID，用于删除、重建和聚合
                    'chunk_id': i,
                    'total_chunks': len(documents),
                    'chunk_size': len(doc.page_content),
                    'ingestion_time': timezone.now().isoformat()
                })
            
            logger.info(f"文档分割完成: {len(documents)}个块, 总长度: {len(content)}字符")
            
            # 添加到向量数据库并持久化
            self.vector_store.add_documents(documents)
            self._persist_vector_store()
            
            logger.info(f"文档摄入成功: {doc_id}, 块数: {len(documents)}")
            return {
                'success': True,
                'doc_id': doc_id,
                'chunks_count': len(documents),
                'total_size': len(content),
                'metadata': metadata,
                'message': '文档摄入成功'
            }
        except Exception as e:
            logger.error(f'文档摄入失败: {e}', exc_info=True)
            return {
                'success': False,
                'error': f'文档摄入失败: {str(e)}'
            }
    
    def search_documents(self, query: str, top_k: int = 5, 
                        filter_metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        搜索相关文档
        
        Args:
            query: 搜索查询
            top_k: 返回结果数量
            filter_metadata: 元数据过滤条件
            
        Returns:
            搜索结果
        """
        self._ensure_initialized()
        
        try:
            # 使用 similarity_search_with_score 获取相似度分数
            # 注意：不同向量数据库的分数含义可能不同
            # Chroma: 返回距离（越小越相似），Milvus: 返回相似度（越大越相似）
            try:
                results_with_scores = self.vector_store.similarity_search_with_score(
                    query, k=top_k, filter=filter_metadata
                )
                
                # 格式化结果（包含分数）
                formatted_results = []
                for doc, score in results_with_scores:
                    # 对于Chroma，分数是距离（越小越好），转换为相似度（0-1范围）
                    # 对于Milvus，分数已经是相似度
                    if self.config.debug_mode:
                        # Chroma返回的是距离，转换为相似度（使用简单的归一化）
                        # 注意：这是一个简化的转换，实际可能需要根据具体距离范围调整
                        similarity = 1.0 / (1.0 + score) if score > 0 else 1.0
                    else:
                        # Milvus返回的已经是相似度
                        similarity = float(score)
                    
                    formatted_results.append({
                        'content': doc.page_content,
                        'metadata': doc.metadata,
                        'score': similarity,
                        'distance': float(score) if self.config.debug_mode else None
                    })
            except AttributeError:
                # 如果向量数据库不支持 similarity_search_with_score，回退到普通搜索
                logger.warning("向量数据库不支持带分数的搜索，使用普通搜索")
                results = self.vector_store.similarity_search(
                    query, k=top_k, filter=filter_metadata
                )
                formatted_results = [{
                    'content': doc.page_content,
                    'metadata': doc.metadata,
                    'score': None
                } for doc in results]
            
            logger.info(f"文档搜索成功: {query}, 结果数: {len(formatted_results)}")
            return {
                'success': True,
                'query': query,
                'results': formatted_results,
                'total_found': len(formatted_results),
                'message': f'找到{len(formatted_results)}个相关文档'
            }
        except Exception as e:
            logger.error(f'文档搜索失败: {e}', exc_info=True)
            return {
                'success': False,
                'error': f'文档搜索失败: {str(e)}'
            }
    
    def delete_documents_by_doc_id(self, doc_id: str) -> Dict[str, Any]:
        """
        根据doc_id删除文档的所有chunk
        
        Args:
            doc_id: 文档ID
            
        Returns:
            删除结果
        """
        self._ensure_initialized()
        
        try:
            if self.config.debug_mode:
                # Chroma删除方式
                return self._delete_chroma_documents(doc_id)
            else:
                # Milvus删除方式
                return self._delete_milvus_documents(doc_id)
        except Exception as e:
            logger.error(f'删除文档失败: doc_id={doc_id}, 错误: {e}', exc_info=True)
            return {
                'success': False,
                'error': f'删除文档失败: {str(e)}'
            }
    
    def _delete_chroma_documents(self, doc_id: str) -> Dict[str, Any]:
        """从Chroma删除文档"""
        try:
            # Chroma需要通过查询找到所有匹配的文档ID，然后删除
            # 先搜索所有匹配doc_id的文档
            if hasattr(self.vector_store, '_collection'):
                collection = self.vector_store._collection
                
                # 查询所有匹配doc_id的文档
                # 注意：Chroma的where参数用于过滤metadata
                results = collection.get(
                    where={"doc_id": doc_id}
                )
                
                if results and results.get('ids'):
                    ids_to_delete = results['ids']
                    # 删除这些文档
                    collection.delete(ids=ids_to_delete)
                    logger.info(f"已从Chroma删除文档: doc_id={doc_id}, 删除chunk数: {len(ids_to_delete)}")
                    return {
                        'success': True,
                        'doc_id': doc_id,
                        'deleted_count': len(ids_to_delete),
                        'message': f'成功删除{len(ids_to_delete)}个chunk'
                    }
                else:
                    logger.warning(f"未找到匹配的文档: doc_id={doc_id}")
                    return {
                        'success': True,
                        'doc_id': doc_id,
                        'deleted_count': 0,
                        'message': '未找到匹配的文档'
                    }
            else:
                return {
                    'success': False,
                    'error': '无法访问Chroma集合'
                }
        except Exception as e:
            logger.error(f'Chroma删除文档失败: {e}', exc_info=True)
            raise
    
    def _delete_milvus_documents(self, doc_id: str) -> Dict[str, Any]:
        """从Milvus删除文档"""
        try:
            # Milvus支持通过filter删除
            if hasattr(self.vector_store, 'delete'):
                # Milvus的delete方法支持filter参数
                self.vector_store.delete(filter={"doc_id": doc_id})
                logger.info(f"已从Milvus删除文档: doc_id={doc_id}")
                return {
                    'success': True,
                    'doc_id': doc_id,
                    'message': '成功删除文档'
                }
            else:
                return {
                    'success': False,
                    'error': 'Milvus不支持删除操作'
                }
        except Exception as e:
            logger.error(f'Milvus删除文档失败: {e}', exc_info=True)
            raise
    
    # ============ 辅助方法 ============
    
    def _generate_doc_id(self, content: str, metadata: Dict[str, Any]) -> str:
        """生成文档ID"""
        # 使用文件名和内容哈希生成唯一ID
        file_name = metadata.get('file_name', 'unknown')
        content_hash = hashlib.md5(content.encode()).hexdigest()[:8]
        return f"{file_name}_{content_hash}"
    
    def _reset_components(self):
        """重置所有组件"""
        self._initialized = False
        self.embedding_model = None
        self.vector_store = None
        self.text_splitter = None
    
    def switch_to_config(self, config_id: int) -> bool:
        """切换到指定的配置"""
        from .models import RAGConfiguration
        
        config = RAGConfiguration.objects.filter(id=config_id, is_active=True).first()
        if not config:
            logger.error(f"配置 {config_id} 不存在或未激活")
            return False
        
        try:
            logger.info(f"切换到RAG配置: {config.name}")
            self._update_config_from_db(config)
            self._reset_components()
            self._initialize_components()
            logger.info("RAG配置切换完成")
            return True
        except Exception as e:
            logger.error(f"切换RAG配置失败: {e}", exc_info=True)
            return False
    
    def reload_config(self):
        """重新从数据库加载配置并重新初始化（用于配置更新后）"""
        logger.info("重新从数据库加载RAG配置")
        try:
            self._load_config_from_db()
            self._reset_components()
            self._initialize_components()
            logger.info("RAG配置重新加载完成")
        except Exception as e:
            logger.error(f"重新加载RAG配置失败: {e}", exc_info=True)
            raise
    
    def test_connection(self) -> Dict[str, Any]:
        """测试RAG连接"""
        self._ensure_initialized()
        
        try:
            # 统一的连接测试：测试嵌入模型
            test_text = "测试连接"
            test_embedding = self.embedding_model.embed_query(test_text)
            
            vector_db_name = 'Chroma' if self.config.debug_mode else 'Milvus'
            result = {
                'success': True,
                'message': f'{vector_db_name}连接测试成功',
                'vector_db_type': 'chroma' if self.config.debug_mode else 'milvus',
                'embedding_model': self.config.embedding_model,
                'test_embedding_dim': len(test_embedding)
            }
            
            if not self.config.debug_mode:
                result.update({
                    'milvus_host': self.config.milvus_host,
                    'milvus_port': self.config.milvus_port
                })
            
            return result
        except Exception as e:
            logger.error(f"RAG连接测试失败: {e}", exc_info=True)
            return {
                'success': False,
                'error': f'连接测试失败: {str(e)}'
            }


# ============ 工厂函数 ============

def get_rag_manager() -> RAGManager:
    """
    获取RAG管理器实例
    
    注意：在多进程环境（如Celery worker）中，每次调用都会创建新实例。
    这避免了单例模式在多进程环境中的冲突问题。
    
    虽然每次创建实例会有一定的开销（加载嵌入模型），但在多进程环境中
    这是必要的，因为：
    1. 每个进程需要独立的向量数据库连接
    2. 避免Chroma客户端设置冲突
    3. 确保进程间的数据隔离
    """
    return RAGManager()

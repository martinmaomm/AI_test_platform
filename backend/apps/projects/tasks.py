"""
项目模块的Celery异步任务

使用LangChain 1.0+标准流程处理知识库文件：
1. Document Loader: 使用LangChain标准document_loaders解析文件
2. Text Splitter: 分割文档为chunks
3. Embedding: 生成向量嵌入
4. Vector Store: 存储到向量数据库（支持语义搜索）
"""
import logging
import os
import tempfile
from typing import Dict, Any, Optional, List
from celery import shared_task
from django.utils import timezone
from django.core.files.storage import default_storage
from langchain_community.document_loaders import (
    TextLoader, PyPDFLoader, Docx2txtLoader, UnstructuredExcelLoader,
    JSONLoader, UnstructuredXMLLoader
)
from langchain_core.documents import Document
from .knowledge.models import KnowledgeBaseFile

# 串联触发 WebUI 三维资产提取
from web_testing.tasks import extract_pom_from_doc_task

logger = logging.getLogger(__name__)


def _merge_documents_content(documents: List[Document], file_type: str, loader_name: str) -> Dict[str, Any]:
    """
    合并文档内容并构建返回结果
    
    Args:
        documents: 文档列表
        file_type: 文件类型
        loader_name: loader名称
    
    Returns:
        包含content和metadata的字典
    """
    if not documents:
        return {
            "content": "",
            "metadata": {
                "file_type": file_type,
                "parsed_with": loader_name,
                "document_count": 0
            }
        }
    
    # 合并所有Document的page_content和metadata
    content_parts = []
    merged_metadata = {"parsed_with": loader_name}
    
    for doc in documents:
        content_parts.append(doc.page_content)
        if doc.metadata:
            merged_metadata.update(doc.metadata)
    
    content = "\n\n".join(content_parts)
    
    # 构建返回结果
    result_metadata = {
        "file_type": file_type,
        "document_count": len(documents),
        **merged_metadata
    }
    
    # 添加统计信息
    if content:
        result_metadata.update({
            "line_count": len(content.splitlines()),
            "word_count": len(content.split()),
            "char_count": len(content)
        })
    
    return {
        "content": content,
        "metadata": result_metadata
    }


def _update_file_status(knowledge_file, status: str, error_message: str = ""):
    """更新知识库文件状态"""
    if knowledge_file:
        try:
            knowledge_file.status = status
            knowledge_file.error_message = error_message
            knowledge_file.save()
        except Exception as e:
            logger.warning(f"更新文件状态失败: {e}")


def _parse_file_content(file_path: str, file_type: str, file_name: str) -> Optional[Dict[str, Any]]:
    """
    使用LangChain标准document_loaders解析文件内容
    
    Args:
        file_path: 文件路径（Django storage路径）
        file_type: 文件类型
        file_name: 文件名
    
    Returns:
        解析结果字典，包含content和metadata，如果解析失败返回None
    """
    try:
        if not default_storage.exists(file_path):
            logger.error(f"文件不存在: {file_path}")
            return None
        
        # 获取文件的本地路径（LangChain loaders需要文件路径）
        # 对于Django storage，需要先下载到临时位置或使用文件对象
        local_file_path = None
        is_temp_file = False
        
        if hasattr(default_storage, 'path'):
            # 本地文件系统存储，直接使用路径
            local_file_path = default_storage.path(file_path)
        else:
            # 非本地存储（如S3），需要下载到临时文件
            is_temp_file = True
            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_type}") as tmp_file:
                with default_storage.open(file_path, 'rb') as storage_file:
                    tmp_file.write(storage_file.read())
                local_file_path = tmp_file.name
        
        # 根据文件类型使用LangChain标准document_loaders
        try:
            if file_type in ['txt', 'md']:
                # 文本文件 - 使用TextLoader
                loader = TextLoader(local_file_path, encoding='utf-8')
                documents = loader.load()
                result = _merge_documents_content(documents, file_type, "TextLoader")
                result["metadata"]["encoding"] = "utf-8"
                return result
            
            elif file_type == 'pdf':
                # PDF文件 - 使用PyPDFLoader
                loader = PyPDFLoader(local_file_path)
                documents = loader.load()
                result = _merge_documents_content(documents, file_type, "PyPDFLoader")
                result["metadata"]["page_count"] = len(documents)
                return result
            
            elif file_type == 'docx':
                # Word文档 - 使用Docx2txtLoader
                loader = Docx2txtLoader(local_file_path)
                documents = loader.load()
                return _merge_documents_content(documents, file_type, "Docx2txtLoader")
            
            elif file_type == 'doc':
                # 旧的.doc格式，提示用户转换
                file_size = default_storage.size(file_path)
                return {
                    "content": f"文件 {file_name} 解析成功\n\n文件类型: Word文档(.doc格式)\n文件大小: {file_size} 字节\n\n注意: .doc文件是旧的二进制格式，当前系统无法直接解析其内容。\n建议将文件转换为.docx格式以获得完整的文档内容解析和RAG功能。",
                    "metadata": {
                        "file_size": file_size,
                        "file_type": "Word文档(.doc格式)",
                        "parsed_with": "info_only"
                    }
                }
            
            elif file_type in ['xlsx', 'xls']:
                # Excel文件 - 使用UnstructuredExcelLoader
                loader = UnstructuredExcelLoader(local_file_path, mode="elements")
                documents = loader.load()
                result = _merge_documents_content(documents, file_type, "UnstructuredExcelLoader")
                return result
            
            elif file_type == 'json':
                # JSON文件 - 使用JSONLoader
                loader = JSONLoader(
                    file_path=local_file_path,
                    jq_schema='.',  # 提取所有内容
                    text_content=False  # 如果为True，会将JSON作为文本处理
                )
                documents = loader.load()
                return _merge_documents_content(documents, file_type, "JSONLoader")
            
            elif file_type in ['yaml', 'yml']:
                # YAML文件 - 使用TextLoader作为文本处理
                loader = TextLoader(local_file_path, encoding='utf-8')
                documents = loader.load()
                result = _merge_documents_content(documents, file_type, "TextLoader")
                result["metadata"]["encoding"] = "utf-8"
                return result
            
            elif file_type == 'xml':
                # XML文件 - 使用UnstructuredXMLLoader
                loader = UnstructuredXMLLoader(local_file_path)
                documents = loader.load()
                return _merge_documents_content(documents, file_type, "UnstructuredXMLLoader")
            
            else:
                # 其他文件类型 - 使用TextLoader作为通用文本处理
                loader = TextLoader(local_file_path, encoding='utf-8')
                documents = loader.load()
                result = _merge_documents_content(documents, file_type, "TextLoader")
                result["metadata"]["encoding"] = "utf-8"
                return result
        
        finally:
            # 清理临时文件（如果是非本地存储）
            if is_temp_file and local_file_path:
                try:
                    os.unlink(local_file_path)
                except Exception as e:
                    logger.warning(f"清理临时文件失败: {e}")
    
    except ImportError as e:
        error_msg = f"缺少必要的LangChain document loader依赖: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return {
            "content": f"文件 {file_name} 解析失败: {error_msg}",
            "metadata": {
                "file_type": file_type,
                "error": error_msg,
                "parsed_with": "error"
            }
        }
    except Exception as e:
        logger.error(f"解析文件 {file_name} 失败: {str(e)}", exc_info=True)
        return {
            "content": f"文件 {file_name} 解析失败: {str(e)}",
            "metadata": {
                "file_type": file_type,
                "error": str(e),
                "parsed_with": "error"
            }
        }


def _perform_rag_ingestion(knowledge_file, content: str) -> Dict[str, Any]:
    """
    执行RAG入库操作（使用LangChain 1.0+标准流程）
    
    RAG入库流程：
    1. Document Loader: 创建Document对象
    2. Text Splitter: 分割文档为chunks
    3. Embedding: 生成向量嵌入
    4. Vector Store: 存储到向量数据库
    
    Args:
        knowledge_file: 知识库文件对象
        content: 解析后的内容
    
    Returns:
        RAG入库结果
    """
    try:
        # 检查内容长度
        if len(content.strip()) < 10:
            return {
                'success': False,
                'error': '文件内容过短，无法进行有效向量化',
                'metadata': {}
            }
        
        # ========== Step 1: 导入RAG管理器（包含Embedding和Vector Store） ==========
        from ai_core.rag_service import get_rag_manager
        
        rag_manager = get_rag_manager()
        
        # ========== Step 2: Document Loader - 创建Document对象 ==========
        # 构建文档元数据
        document_metadata = {
            'file_name': knowledge_file.file_name,
            'file_type': knowledge_file.file_type,
            'file_id': knowledge_file.id,
            'project_id': knowledge_file.project.id,
            'uploaded_by': knowledge_file.uploaded_file.uploaded_by.username if knowledge_file.uploaded_file.uploaded_by else 'unknown',
            'upload_time': knowledge_file.uploaded_file.created_at.isoformat(),
            'file_size': knowledge_file.uploaded_file.file.size if knowledge_file.uploaded_file.file else 0,
            'content_length': len(content),
            'word_count': len(content.split()),
            'ingestion_time': timezone.now().isoformat(),
            'rag_system': 'langchain',
            'vector_db_type': 'Chroma' if rag_manager.config.debug_mode else 'Milvus'
        }
        
        # 创建Document对象（LangChain 1.0+标准方式）
        document = Document(
            page_content=content,
            metadata=document_metadata
        )
        
        logger.info(f"Document创建完成: {knowledge_file.file_name}, 内容长度: {len(content)}字符")
        
        # ========== Step 3: Text Splitter - 分割文档为chunks ==========
        # 使用RAG管理器中的text_splitter
        chunks = rag_manager.text_splitter.split_documents([document])
        
        logger.info(f"文档分割完成: {len(chunks)}个chunks")
        
        # ========== Step 4: 为每个chunk添加doc_id等元数据 ==========
        doc_id = rag_manager._generate_doc_id(content, document_metadata)
        for i, chunk in enumerate(chunks):
            chunk.metadata.update({
                'doc_id': doc_id,  # 文档ID，用于删除、重建和聚合
                'chunk_id': i,
                'total_chunks': len(chunks),
                'chunk_size': len(chunk.page_content),
                'ingestion_time': timezone.now().isoformat()
            })
        
        # ========== Step 5: Embedding + Vector Store - 生成向量并存储 ==========
        # 使用vector_store.add_documents()会自动调用embedding模型生成向量
        # LangChain会自动批量处理向量生成（通过embedding模型的batch_size配置）
        import time
        embedding_start_time = time.time()
        
        logger.info(f"开始生成向量并存储: {len(chunks)}个chunks")
        
        # 批量添加文档，LangChain会自动批量处理向量生成
        # 注意：add_documents内部会调用embedding模型的embed_documents方法进行批量处理
        rag_manager.vector_store.add_documents(chunks)
        
        embedding_duration = time.time() - embedding_start_time
        avg_time_per_chunk = embedding_duration / len(chunks) if len(chunks) > 0 else 0
        logger.info(
            f"向量生成和存储完成，耗时: {embedding_duration:.2f}秒, "
            f"平均每个chunk: {avg_time_per_chunk:.2f}秒, "
            f"处理速度: {len(chunks)/embedding_duration:.2f} chunks/秒"
        )
        
        # 持久化向量数据库（Chroma会自动持久化，这里只是确保）
        rag_manager._persist_vector_store()
        
        logger.info(f"向量存储完成: doc_id={doc_id}, chunks数={len(chunks)}")
        
        # ========== 构建返回结果 ==========
        result_metadata = {
            **document_metadata,
            'rag_doc_id': doc_id,
            'chunks_count': len(chunks),
            'total_size': len(content),
            'vector_dimension': rag_manager.config.milvus_dim,
            'chunk_size': rag_manager.config.chunk_size,
            'chunk_overlap': rag_manager.config.chunk_overlap
        }
        
        logger.info(f"RAG入库完成: {knowledge_file.file_name}, 块数: {len(chunks)}")
        
        return {
            'success': True,
            'metadata': result_metadata,
            'message': 'RAG入库成功',
            'rag_result': {
                'success': True,
                'doc_id': doc_id,
                'chunks_count': len(chunks),
                'total_size': len(content),
                'message': '文档摄入成功'
            }
        }
    
    except Exception as e:
        error_msg = f'RAG入库失败: {str(e)}'
        logger.error(error_msg, exc_info=True)
        
        return {
            'success': False,
            'error': error_msg
        }


def _build_result(status: str, message: str, start_time=None, end_time=None) -> Dict[str, Any]:
    """构建统一返回结果"""
    # 处理时间参数，如果为None则使用当前时间
    if start_time is None:
        start_time = timezone.now()
    if end_time is None:
        end_time = timezone.now()
    
    # 计算持续时间
    try:
        duration_ms = int((end_time - start_time).total_seconds() * 1000)
    except (TypeError, AttributeError):
        duration_ms = 0
    
    return {
        "status": status,  # 任务状态：running/completed/failed
        "message": message,
        "timestamps": {
            "start": start_time.isoformat() if start_time else None,
            "end": end_time.isoformat() if end_time else None,
            "duration_ms": duration_ms
        }
    }


@shared_task(bind=True)
def process_knowledge_base_file_async(self, knowledge_file_id: int) -> Dict[str, Any]:
    """
    异步处理知识库文件（遵循LangChain 1.0+语义搜索最佳实践）
    
    处理流程：
    1. Document Loader: 解析文件内容
    2. Text Splitter: 分割文档为chunks
    3. Embedding: 生成向量嵌入
    4. Vector Store: 存储到向量数据库（支持语义搜索）
    
    Args:
        knowledge_file_id: 知识库文件ID
        
    Returns:
        处理结果字典
    """
    start_time = timezone.now()
    knowledge_file = None
    
    try:
        # ========== Step 1: 任务初始化 ==========
        self.update_state(
            state="PROGRESS", 
            meta={
                "message": "任务开始",
                "start_time": start_time.isoformat(),
                "stage": "initialization"
            }
        )

        # ========== Step 2: 文件校验（Document Loader准备） ==========
        try:
            knowledge_file = KnowledgeBaseFile.objects.select_related(
                'uploaded_file', 'project'
            ).get(id=knowledge_file_id)
        except KnowledgeBaseFile.DoesNotExist:
            logger.error(f"知识库文件不存在: {knowledge_file_id}")
            return _build_result("failed", "文件不存在", start_time, timezone.now())

        uploaded_file = knowledge_file.uploaded_file
        if not uploaded_file:
            _update_file_status(knowledge_file, KnowledgeBaseFile.RAGIngestionStatus.FAILED, "缺少上传文件")
            logger.error(f"知识库文件 {knowledge_file_id} 缺少上传文件")
            return _build_result("failed", "缺少上传文件", start_time, timezone.now())

        # ========== Step 3: Document Loader - 解析文件内容 ==========
        self.update_state(
            state="PROGRESS", 
            meta={
                "message": "正在解析文件内容",
                "stage": "document_loading",
                "file_name": knowledge_file.file_name
            }
        )
        
        _update_file_status(knowledge_file, KnowledgeBaseFile.RAGIngestionStatus.RUNNING, "")

        parse_result = _parse_file_content(
            uploaded_file.file.name, 
            uploaded_file.file_type, 
            uploaded_file.original_name
        )
        
        if not parse_result or not parse_result.get("content"):
            error_msg = "文件解析失败：无法提取有效内容"
            _update_file_status(knowledge_file, KnowledgeBaseFile.RAGIngestionStatus.FAILED, error_msg)
            logger.error(f"文件解析失败: {knowledge_file.file_name}")
            return _build_result("failed", error_msg, start_time, timezone.now())

        content = parse_result["content"]
        
        # 验证内容质量
        if len(content.strip()) < 10:
            error_msg = "文件内容过短，无法进行有效向量化"
            _update_file_status(knowledge_file, KnowledgeBaseFile.RAGIngestionStatus.FAILED, error_msg)
            logger.warning(f"文件内容过短: {knowledge_file.file_name}, 长度: {len(content)}")
            return _build_result("failed", error_msg, start_time, timezone.now())
        
        knowledge_file.parsed_content = content
        knowledge_file.save()
        
        logger.info(f"文件解析完成: {knowledge_file.file_name}, 内容长度: {len(content)}字符")

        # ========== Step 4: RAG入库（Text Splitter + Embedding + Vector Store） ==========
        self.update_state(
            state="PROGRESS", 
            meta={
                "message": "正在执行RAG入库",
                "stage": "rag_ingestion",
                "file_name": knowledge_file.file_name
            }
        )
        
        rag_result = _perform_rag_ingestion(knowledge_file, content)

        if rag_result.get("success"):
            # 更新文件状态和元数据
            knowledge_file.metadata = rag_result.get("metadata", {})
            _update_file_status(knowledge_file, KnowledgeBaseFile.RAGIngestionStatus.COMPLETED, "")
            
            chunks_count = rag_result.get("rag_result", {}).get("chunks_count", 0)
            logger.info(
                f"知识库文件处理完成: {knowledge_file.file_name}, "
                f"chunks数: {chunks_count}, "
                f"耗时: {(timezone.now() - start_time).total_seconds():.2f}秒"
            )

            logger.info(f"文件 {knowledge_file.file_name} 的 RAG 向量入库已完成。")

            # ================= 新增：串联触发三维资产提取 =================
             #try:
                # logger.info("正在自动触发 WebUI 资产（模块-页面-元素）提取任务...")
                 #extract_pom_from_doc_task.delay(
                  #   project_id=knowledge_file.project.id,
                 #    file_id=knowledge_file.id
               #  )
            # except Exception as e:
             #    logger.error(f"触发 WebUI 资产提取任务失败: {str(e)}")
            # =========================================================

            return _build_result(
                "completed", 
                f"知识库文件处理完成（已生成{chunks_count}个可搜索chunks）", 
                start_time, 
                timezone.now()
            )
        else:
            # RAG入库失败，更新文件状态
            error_msg = rag_result.get("error", "RAG入库失败")
            _update_file_status(knowledge_file, KnowledgeBaseFile.RAGIngestionStatus.FAILED, error_msg)
            logger.error(f"RAG入库失败: {knowledge_file.file_name}, 错误: {error_msg}")
            return _build_result("failed", error_msg, start_time, timezone.now())

    except ValueError as e:
        # 处理RAG配置错误
        error_msg = f"RAG配置错误: {str(e)}"
        logger.error(error_msg, exc_info=True)
        if knowledge_file:
            _update_file_status(knowledge_file, KnowledgeBaseFile.RAGIngestionStatus.FAILED, error_msg)
        return _build_result("failed", error_msg, start_time, timezone.now())
        
    except Exception as e:
        # 处理其他异常
        error_msg = f"任务异常: {str(e)}"
        logger.error(error_msg, exc_info=True)
        if knowledge_file:
            _update_file_status(knowledge_file, KnowledgeBaseFile.RAGIngestionStatus.FAILED, error_msg)
        return _build_result("failed", error_msg, start_time, timezone.now())


@shared_task
def reprocess_knowledge_file_async(knowledge_file_id: int) -> Dict[str, Any]:
    """
    重新处理知识库文件
    
    Args:
        knowledge_file_id: KnowledgeBaseFile的ID
    
    Returns:
        处理结果
    """
    try:
        # 重置文件状态为等待入库
        knowledge_file = KnowledgeBaseFile.objects.get(id=knowledge_file_id)
        knowledge_file.status = KnowledgeBaseFile.RAGIngestionStatus.PENDING
        knowledge_file.error_message = ''
        knowledge_file.save()
        
        # 启动异步处理任务
        task = process_knowledge_base_file_async.delay(knowledge_file_id)
        
        return {
            'success': True,
            'message': '文件已加入重新处理队列',
            'task_id': str(task.id)
        }
    
    except Exception as e:
        error_msg = f'重新处理文件失败: {str(e)}'
        logger.error(error_msg, exc_info=True)
        
        return {
            'success': False,
            'error': error_msg
        }


@shared_task(bind=True)
def delete_knowledge_file_async(self, knowledge_file_id: int) -> Dict[str, Any]:
    """
    异步删除知识库文件
    
    删除流程：
    1. 从向量数据库删除文档
    2. 删除物理文件
    3. 删除uploaded_file记录
    4. 删除知识库文件记录
    
    Args:
        knowledge_file_id: 知识库文件ID
        
    Returns:
        删除结果字典
    """
    start_time = timezone.now()
    knowledge_file = None
    
    try:
        # ========== Step 1: 任务初始化 ==========
        self.update_state(
            state="PROGRESS",
            meta={
                "message": "开始删除文件",
                "start_time": start_time.isoformat(),
                "stage": "initialization"
            }
        )
        
        # ========== Step 2: 获取文件信息 ==========
        try:
            knowledge_file = KnowledgeBaseFile.objects.select_related(
                'uploaded_file', 'project'
            ).get(id=knowledge_file_id)
        except KnowledgeBaseFile.DoesNotExist:
            logger.error(f"知识库文件不存在: {knowledge_file_id}")
            return _build_result("failed", "文件不存在", start_time, timezone.now())
        
        file_name = knowledge_file.file_name
        uploaded_file = knowledge_file.uploaded_file
        file_status = knowledge_file.status
        
        # ========== Step 3: 从向量数据库删除文档（仅当文件已成功入库时） ==========
        # 只有当status为COMPLETED时，文件才成功入库到向量数据库，需要删除
        if file_status == KnowledgeBaseFile.RAGIngestionStatus.COMPLETED:
            self.update_state(
                state="PROGRESS",
                meta={
                    "message": "正在从向量数据库删除文档",
                    "stage": "vector_db_deletion",
                    "file_name": file_name
                }
            )
            
            try:
                from ai_core.rag_service import get_rag_manager
                rag_manager = get_rag_manager()
                
                # 尝试从metadata中获取rag_doc_id
                metadata = knowledge_file.metadata or {}
                rag_doc_id = metadata.get('rag_doc_id')
                
                if rag_doc_id:
                    delete_result = rag_manager.delete_documents_by_doc_id(rag_doc_id)
                    if delete_result.get('success'):
                        deleted_count = delete_result.get('deleted_count', 0)
                        logger.info(f"已从向量数据库删除文档: rag_doc_id={rag_doc_id}, 删除chunk数: {deleted_count}")
                    else:
                        logger.warning(f"从向量数据库删除文档失败: rag_doc_id={rag_doc_id}, 错误: {delete_result.get('error')}")
                else:
                    # 如果没有rag_doc_id，尝试重新生成doc_id并删除
                    if uploaded_file and knowledge_file.parsed_content:
                        try:
                            import hashlib
                            content_hash = hashlib.md5(knowledge_file.parsed_content.encode()).hexdigest()[:8]
                            generated_doc_id = f"{file_name}_{content_hash}"
                            
                            delete_result = rag_manager.delete_documents_by_doc_id(generated_doc_id)
                            if delete_result.get('success'):
                                deleted_count = delete_result.get('deleted_count', 0)
                                logger.info(f"已从向量数据库删除文档: doc_id={generated_doc_id}, 删除chunk数: {deleted_count}")
                            else:
                                logger.warning(f"使用生成的doc_id删除文档失败: {delete_result.get('error')}")
                        except Exception as e:
                            logger.warning(f"使用生成的doc_id删除文档失败: {e}")
                    else:
                        logger.warning(f"知识库文件 {knowledge_file_id} 没有rag_doc_id，且无法生成doc_id，跳过向量数据库删除")
            except Exception as e:
                # RAG删除失败不应该阻止文件删除
                logger.warning(f"删除向量数据库文档时出错: {e}")
        else:
            # 文件未成功入库，跳过向量数据库删除
            logger.info(f"知识库文件 {knowledge_file_id} 状态为 {file_status}，未成功入库，跳过向量数据库删除")
        
        # ========== Step 4: 删除物理文件 ==========
        self.update_state(
            state="PROGRESS",
            meta={
                "message": "正在删除物理文件",
                "stage": "file_deletion",
                "file_name": file_name
            }
        )
        
        if uploaded_file:
            if uploaded_file.file:
                try:
                    file_path = uploaded_file.file.name
                    if default_storage.exists(file_path):
                        default_storage.delete(file_path)
                        logger.info(f"已删除物理文件: {file_path}")
                    else:
                        logger.warning(f"物理文件不存在: {file_path}")
                except Exception as e:
                    logger.warning(f"删除物理文件失败: {uploaded_file.file.name if uploaded_file.file else 'Unknown'}, 错误: {e}")
            
            # ========== Step 5: 删除uploaded_file记录 ==========
            uploaded_file_id = uploaded_file.id
            uploaded_file.delete()
            logger.info(f"已删除uploaded_file记录: ID={uploaded_file_id}")
        
        # ========== Step 6: 删除知识库文件记录 ==========
        self.update_state(
            state="PROGRESS",
            meta={
                "message": "正在删除数据库记录",
                "stage": "db_deletion",
                "file_name": file_name
            }
        )
        
        knowledge_file.delete()
        logger.info(f"已删除知识库文件: ID={knowledge_file_id}")
        
        end_time = timezone.now()
        duration = (end_time - start_time).total_seconds()
        logger.info(f"知识库文件删除完成: {file_name}, 耗时: {duration:.2f}秒")
        
        return _build_result(
            "completed",
            f"文件删除成功: {file_name}",
            start_time,
            end_time
        )
    
    except Exception as e:
        error_msg = f"删除知识库文件失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return _build_result("failed", error_msg, start_time, timezone.now())

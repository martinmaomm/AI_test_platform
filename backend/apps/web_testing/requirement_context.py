"""Build safe, deterministic context for WebUI requirement generation."""

from collections import Counter, defaultdict
import re

from ai_core.models import LLMConfiguration, ModelType, RAGConfiguration
from projects.knowledge.models import KnowledgeBaseFile

from .generation_security import redact_metadata, redact_text
from .models import WebPage, WebUITestModule


CONTEXT_VERSION = 'webui-requirement-v2.0'
MAX_KNOWLEDGE_SOURCES = 8
MAX_KNOWLEDGE_SNIPPET_LENGTH = 1200
MAX_KNOWLEDGE_CANDIDATES = 50


def _normalize_business_rules(value):
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, dict):
        return [f'{key}: {item}' for key, item in value.items()]
    if value:
        return [str(value).strip()]
    return []


def _module_scope(project_id, selected_module):
    modules = list(
        WebUITestModule.objects.filter(project_id=project_id)
        .order_by('order', 'id')
        .values('id', 'name', 'parent_id')
    )
    children = defaultdict(list)
    by_id = {}
    for module in modules:
        by_id[module['id']] = module
        children[module['parent_id']].append(module['id'])

    scoped_ids = []
    pending = [selected_module.id]
    seen = set()
    while pending:
        module_id = pending.pop(0)
        if module_id in seen:
            continue
        seen.add(module_id)
        scoped_ids.append(module_id)
        pending.extend(children.get(module_id, []))

    path = []
    cursor = by_id.get(selected_module.id)
    path_seen = set()
    while cursor and cursor['id'] not in path_seen:
        path_seen.add(cursor['id'])
        path.append(cursor['name'])
        cursor = by_id.get(cursor['parent_id'])
    path.reverse()
    return scoped_ids, path


def _metadata_matches_module(metadata, module_ids):
    if not isinstance(metadata, dict):
        return False
    raw_values = []
    for key in ('module_id', 'module_ids', 'webui_module_id', 'webui_module_ids'):
        value = metadata.get(key)
        if isinstance(value, (list, tuple, set)):
            raw_values.extend(value)
        elif value not in (None, ''):
            raw_values.append(value)
    normalized = set()
    for value in raw_values:
        try:
            normalized.add(int(value))
        except (TypeError, ValueError):
            continue
    return bool(normalized.intersection(module_ids))


def build_requirement_generation_context(*, project_id, module_id, user):
    """Return asset readiness without loading embeddings or calling an LLM."""

    selected_module = WebUITestModule.objects.get(pk=module_id, project_id=project_id)
    module_ids, module_path = _module_scope(project_id, selected_module)
    business_rules = _normalize_business_rules(selected_module.business_rules)

    pages = list(
        WebPage.objects.filter(project_id=project_id, module_id__in=module_ids)
        .order_by('name')
        .prefetch_related('elements')
    )
    page_items = []
    total_elements = 0
    for page in pages:
        elements = list(page.elements.all())
        total_elements += len(elements)
        page_items.append({
            'id': page.id,
            'name': page.name,
            'url_path': page.url_path or '/',
            'page_class_name': page.page_class_name or '',
            'module_id': page.module_id,
            'element_count': len(elements),
            'elements': [
                {
                    'id': element.id,
                    'name': element.name,
                    'action_type': element.action_type or '',
                }
                for element in elements
            ],
        })

    knowledge_files = list(
        KnowledgeBaseFile.objects.filter(project_id=project_id)
        .select_related('uploaded_file')
        .only(
            'id', 'status', 'metadata', 'created_at',
            'uploaded_file__original_name',
        )
        .order_by('-created_at')
    )
    knowledge_status_counts = Counter(item.status for item in knowledge_files)
    completed_files = [
        item for item in knowledge_files
        if item.status == KnowledgeBaseFile.RAGIngestionStatus.COMPLETED
    ]
    module_matched_files = [
        item for item in completed_files
        if _metadata_matches_module(item.metadata, set(module_ids))
    ]
    module_matched_file_ids = {item.id for item in module_matched_files}

    models = list(
        LLMConfiguration.objects.filter(
            created_by=user,
            model_type=ModelType.LLM,
            is_active=True,
        ).order_by('id')
    )
    rag_config = RAGConfiguration.objects.filter(
        created_by=user,
        is_active=True,
    ).order_by('-is_default', '-created_at').first()

    blockers = []
    warnings = []
    if not models:
        blockers.append('当前账号没有可用的 LLM 模型，请先配置并启用模型。')
    if not selected_module.description:
        warnings.append('当前模块尚未填写模块描述。')
    if not business_rules:
        warnings.append('当前模块尚未维护业务规则。')
    if not pages:
        warnings.append('当前模块及其子模块尚未维护页面资产。')
    elif total_elements == 0:
        warnings.append('当前模块已有页面，但尚未维护页面元素。')
    if not rag_config:
        warnings.append('当前账号没有启用的 RAG 配置，本次只能使用模块和页面资产。')
    elif not completed_files:
        warnings.append('当前项目没有已完成入库的知识文件。')
    elif not module_matched_files:
        warnings.append('知识库已有资料，但没有标记为当前模块的资料；生成时将使用项目级检索。')

    if blockers:
        readiness_status = 'blocked'
        readiness_label = '无法生成'
    elif warnings:
        readiness_status = 'sparse'
        readiness_label = '资料较少'
    else:
        readiness_status = 'ready'
        readiness_label = '准备完成'

    return {
        'module': {
            'id': selected_module.id,
            'name': selected_module.name,
            'path': module_path,
            'description': selected_module.description or '',
            'business_rules': business_rules,
            'business_rule_count': len(business_rules),
            'included_module_ids': module_ids,
        },
        'assets': {
            'page_count': len(pages),
            'element_count': total_elements,
            'pages': page_items,
        },
        'knowledge': {
            'configured': bool(rag_config),
            'config_name': rag_config.name if rag_config else '',
            'embedding_model': rag_config.embedding_model if rag_config else '',
            'total_files': len(knowledge_files),
            'completed_files': len(completed_files),
            'module_matched_files': len(module_matched_files),
            'status_counts': dict(knowledge_status_counts),
            'files': [
                {
                    'id': item.id,
                    'name': item.file_name,
                    'status': item.status,
                    'module_matched': item.id in module_matched_file_ids,
                }
                for item in completed_files[:8]
            ],
        },
        'models': [
            {
                'id': model.id,
                'provider': model.provider,
                'model_name': model.model_name,
                'is_active': model.is_active,
            }
            for model in models
        ],
        'default_model_id': models[0].id if models else None,
        'readiness': {
            'status': readiness_status,
            'label': readiness_label,
            'can_generate': not blockers,
            'blockers': blockers,
            'warnings': warnings,
        },
    }


def _query_terms(value):
    return {
        term.casefold()
        for term in re.findall(r'[\w\u4e00-\u9fff]{2,}', str(value or ''))
        if term.strip()
    }


def build_requirement_generation_prompt_context(*, project_id, module_id, user, request_text=''):
    """Add bounded knowledge snippets for one task without exposing them to the preflight API."""

    context = build_requirement_generation_context(
        project_id=project_id,
        module_id=module_id,
        user=user,
    )
    module_ids = set(context['module']['included_module_ids'])
    terms = _query_terms(request_text)
    terms.update(_query_terms(' '.join(context['module']['path'])))
    terms.update(_query_terms(context['module']['description']))

    completed_files = list(
        KnowledgeBaseFile.objects.filter(
            project_id=project_id,
            status=KnowledgeBaseFile.RAGIngestionStatus.COMPLETED,
        )
        .select_related('uploaded_file')
        .only(
            'id', 'metadata', 'parsed_content', 'updated_at',
            'uploaded_file__original_name',
        )
        .order_by('-updated_at')
        [:MAX_KNOWLEDGE_CANDIDATES]
    )

    ranked = []
    for item in completed_files:
        module_matched = _metadata_matches_module(item.metadata, module_ids)
        searchable = f'{item.file_name}\n{item.parsed_content or ""}'.casefold()
        keyword_score = sum(1 for term in terms if term and term in searchable)
        ranked.append((1 if module_matched else 0, keyword_score, item))
    ranked.sort(key=lambda entry: (entry[0], entry[1], entry[2].updated_at), reverse=True)

    matched_sources = []
    snippets = []
    for module_matched, keyword_score, item in ranked[:MAX_KNOWLEDGE_SOURCES]:
        source = {
            'id': item.id,
            'name': item.file_name,
            'module_matched': bool(module_matched),
            'relevance_score': keyword_score,
        }
        matched_sources.append(source)
        content = redact_text(item.parsed_content or '').strip()
        if content:
            snippets.append({
                **source,
                'content': content[:MAX_KNOWLEDGE_SNIPPET_LENGTH],
            })

    context['knowledge']['matched_sources'] = matched_sources
    context['knowledge']['snippets'] = snippets
    context['context_version'] = CONTEXT_VERSION
    return redact_metadata(context)


def build_requirement_generation_snapshot(prompt_context, *, generation_config, model_config):
    """Persist a reproducible safe snapshot while excluding knowledge contents and secrets."""

    knowledge = dict(prompt_context.get('knowledge') or {})
    knowledge.pop('snippets', None)
    snapshot = {
        'context_version': prompt_context.get('context_version', CONTEXT_VERSION),
        'module': prompt_context.get('module') or {},
        'assets': prompt_context.get('assets') or {},
        'knowledge': knowledge,
        'model': {
            'id': model_config.id,
            'provider': model_config.provider,
            'model_name': model_config.model_name,
        },
        'generation_config': generation_config,
        'readiness': prompt_context.get('readiness') or {},
    }
    return redact_metadata(snapshot)

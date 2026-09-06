from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from langchain_core.documents import Document

from project_knowledge.indexing import ProjectKnowledgeIndex


class RetrievalContextTests(SimpleTestCase):
    def test_vector_hits_leave_room_for_same_section_neighbours(self):
        config = SimpleNamespace(vector_db_type='chroma')
        index = ProjectKnowledgeIndex(config, embeddings=object(), vector_store=object())
        revision = SimpleNamespace(document=SimpleNamespace(name='规则', id=1))
        chunks = [SimpleNamespace(id=str(n), revision=revision, revision_id='revision',
                                  section_key='section' if n < 3 else 'other', heading='',
                                  content=f'规则{n}', ordinal=n, location={}) for n in range(5)]
        hits = [(Document(page_content=chunk.content, metadata={'chunk_id': chunk.id}), score)
                for chunk, score in [(chunks[1], 0.1), (chunks[3], 0.2), (chunks[4], 0.3)]]
        with patch('project_knowledge.indexing._current_indexed_chunks', return_value=chunks), patch.object(index, '_vector_search', return_value=hits):
            result = index.search(1, ['revision'], '问题', top_k=3)
        self.assertEqual([item['id'] for item in result], ['1', '0', '2'])

    def test_neighbours_never_cross_document_section_boundaries(self):
        config = SimpleNamespace(vector_db_type='chroma')
        index = ProjectKnowledgeIndex(config, embeddings=object(), vector_store=object())
        revision = SimpleNamespace(document=SimpleNamespace(name='规则', id=1))
        chunks = [SimpleNamespace(id=str(n), revision=revision, revision_id='revision',
                                  section_key=f'section-{n}', heading='', content=f'规则{n}',
                                  ordinal=n, location={}) for n in range(3)]
        hits = [(Document(page_content=chunks[1].content, metadata={'chunk_id': '1'}), 0.1)]
        with patch('project_knowledge.indexing._current_indexed_chunks', return_value=chunks), patch.object(index, '_vector_search', return_value=hits):
            result = index.search(1, ['revision'], '问题', top_k=3)
        self.assertEqual([item['id'] for item in result], ['1'])

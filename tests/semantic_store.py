"""시멘틱 메모리 시험용 보조 Store. 키 없이 돌도록 임베딩은 문자열만으로 정하는 가짜다."""
from langgraph.store.memory import InMemoryStore

FAKE_INDEX_CONFIG = {"dims": 3}


def fake_embed(texts):
    return [[1.0, float(sum(map(ord, text)) % 13), float(len(text) % 7)] for text in texts]


def indexed_store() -> InMemoryStore:
    """SemanticRuleStore가 요구하는 시멘틱 인덱스(EC-07)를 가진 InMemoryStore."""
    return InMemoryStore(index={**FAKE_INDEX_CONFIG, "embed": fake_embed, "fields": ["instruction"]})

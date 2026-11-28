"""F4 evidence-backed remediation recommendations (for F7 report assembly)."""
from .agent.contracts import (
    ApprovedKnowledge,
    ContractRecommendationOption,
    CorrelationCandidateInput,
    CorrelationContext,
    ErrorEnvelope,
    EvidenceCheckDetail,
    EvidenceLink,
    EvidenceRef,
    F3CorrelationInput,
    IncidentWindow,
    MemorySearchResult,
    MissingEvidenceInput,
    ProposedOption,
    RankedOptionDetail,
    RecommendationResult,
    RecommendationState,
    ValidationRecord,
    build_correlation_context,
)
from .agent.evidence import CatalogEvidenceResolver, EvidenceResolver, F1ReportEvidenceResolver
from .agent.generator import generate_recommendation_options
from .agent.llm import LLMClient, LLMProviderError, ScriptedLLM
from .agent.memory import MemoryLookup, f5_memory_lookup, search_approved_incidents
from .agent.pipeline import (
    failed_contract_result,
    recommend,
    recommend_with_state,
    render_markdown,
    to_contract_result,
)
from .agent.ranker import rank_recommendation
from .agent.validator import validate_recommendation_evidence

__all__ = [
    "ApprovedKnowledge",
    "CatalogEvidenceResolver",
    "ContractRecommendationOption",
    "CorrelationCandidateInput",
    "CorrelationContext",
    "ErrorEnvelope",
    "EvidenceCheckDetail",
    "EvidenceLink",
    "EvidenceRef",
    "EvidenceResolver",
    "F1ReportEvidenceResolver",
    "f5_memory_lookup",
    "F3CorrelationInput",
    "IncidentWindow",
    "LLMClient",
    "LLMProviderError",
    "MemoryLookup",
    "MemorySearchResult",
    "MissingEvidenceInput",
    "ProposedOption",
    "RankedOptionDetail",
    "RecommendationResult",
    "RecommendationState",
    "ScriptedLLM",
    "ValidationRecord",
    "build_correlation_context",
    "failed_contract_result",
    "generate_recommendation_options",
    "rank_recommendation",
    "recommend",
    "recommend_with_state",
    "render_markdown",
    "search_approved_incidents",
    "to_contract_result",
    "validate_recommendation_evidence",
]

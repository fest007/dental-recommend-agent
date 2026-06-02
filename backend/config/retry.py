"""
Centralized retry configuration (tech_design.md section 10.2).

All LLM and external service calls should use these settings for consistent
retry behavior across the application.
"""

RETRY_CONFIG = {
    "llm_enrichment": {
        "max_retries": 3,
        "backoff": [2, 5, 10],  # seconds
        "timeout": 30,
        "batch_retry": True,  # retry entire batch on failure
    },
    "llm_ranking": {
        "max_retries": 2,
        "backoff": [1, 3],
        "timeout": 15,
        "fallback": "rule_based_ranking",
    },
    "llm_agent": {
        "max_retries": 2,
        "backoff": [1, 3],
        "timeout": 20,
        "fallback": "error_message",
    },
    "vector_db": {
        "max_retries": 2,
        "backoff": [1, 3],
        "fallback": "skip_vector_recall",
    },
    "knowledge_graph_llm": {
        "max_retries": 2,
        "backoff": [3, 8],
        "timeout": 60,
        "fallback": "skip_kg_construction",
    },
}


def get_retry_config(service: str) -> dict:
    """Get retry configuration for a specific service.

    Parameters
    ----------
    service : str
        Service name: llm_enrichment, llm_ranking, llm_agent, vector_db, knowledge_graph_llm

    Returns
    -------
    dict
        Retry config with keys: max_retries, backoff, timeout, fallback (optional)
    """
    return RETRY_CONFIG.get(service, {
        "max_retries": 2,
        "backoff": [1, 3],
        "timeout": 30,
    })

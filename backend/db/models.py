from sqlalchemy import Column, Integer, String, Float, DateTime, Date, JSON, ForeignKey, func
from sqlalchemy.orm import relationship
from db.database import Base


class ProductRaw(Base):
    __tablename__ = "products_raw"

    id = Column(Integer, primary_key=True, autoincrement=True)
    row_num = Column(Integer, nullable=False, default=0)
    sku = Column(String(50), nullable=False, index=True)
    product_name = Column(String(500), nullable=False)
    old_sku = Column(String(50), nullable=True)
    status = Column(String(50), nullable=False, default="active")
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    enriched = relationship("ProductEnriched", back_populates="raw_product", uselist=False)


class ProductEnriched(Base):
    __tablename__ = "products_enriched"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey("products_raw.id"), nullable=False, index=True)
    sku = Column(String(50), nullable=False, index=True)
    name = Column(String(500), nullable=False)
    brand = Column(String(100), nullable=False, default="")
    category_l1 = Column(String(50), nullable=False, default="")
    category_l2 = Column(String(50), nullable=False, default="")
    product_type = Column(String(20), nullable=False, default="equipment")
    usage_scenario = Column(String(200), nullable=False, default="")
    keywords = Column(JSON, nullable=False, default=list)
    consumables = Column(JSON, nullable=False, default=list)
    related_accessories = Column(JSON, nullable=False, default=list)
    typical_purchase_cycle_days = Column(Integer, nullable=True)
    unit_hint = Column(String(20), nullable=False, default="台")
    embedding_vector_id = Column(String(100), nullable=False, default="")
    enriched_at = Column(DateTime, nullable=False, server_default=func.now())
    enrichment_confidence = Column(Float, nullable=False, default=0.0)
    llm_model = Column(String(50), nullable=False, default="")

    raw_product = relationship("ProductRaw", back_populates="enriched")


class ProductRelation(Base):
    __tablename__ = "product_relations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_sku = Column(String(50), nullable=False, index=True)
    target_sku = Column(String(50), nullable=False, index=True)
    relation_type = Column(String(20), nullable=False, default="related")
    weight = Column(Float, nullable=False, default=1.0)
    description = Column(String(200), nullable=False, default="")
    source = Column(String(20), nullable=False, default="llm")
    created_at = Column(DateTime, nullable=False, server_default=func.now())


class UserPurchase(Base):
    __tablename__ = "user_purchases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(50), nullable=False, index=True)
    sku = Column(String(50), nullable=False, index=True)
    product_name = Column(String(500), nullable=False)
    quantity = Column(Integer, nullable=False, default=1)
    purchase_date = Column(Date, nullable=False)
    original_sku = Column(String(50), nullable=False, default="")
    import_batch = Column(String(64), nullable=False, default="", index=True)
    imported_at = Column(DateTime, nullable=False, server_default=func.now())


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(50), nullable=False, unique=True, index=True)
    profile_json = Column(JSON, nullable=False, default=dict)
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class Recommendation(Base):
    __tablename__ = "recommendations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(50), nullable=False, index=True)
    recommended_sku = Column(String(50), nullable=False, index=True)
    rank = Column(Integer, nullable=False, default=0)
    reason = Column(String(500), nullable=False, default="")
    confidence = Column(Float, nullable=False, default=0.0)
    source = Column(String(20), nullable=False, default="llm")
    status = Column(String(20), nullable=False, default="pending")
    feedback_at = Column(DateTime, nullable=True)
    generated_at = Column(DateTime, nullable=False, server_default=func.now())

    feedbacks = relationship("FeedbackLog", back_populates="recommendation")


class FeedbackLog(Base):
    __tablename__ = "feedback_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    recommendation_id = Column(Integer, ForeignKey("recommendations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(50), nullable=False, index=True)
    action = Column(String(20), nullable=False, default="viewed")
    feedback_note = Column(String(500), nullable=False, default="")
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    recommendation = relationship("Recommendation", back_populates="feedbacks")


class SkuMapping(Base):
    __tablename__ = "sku_mapping"

    id = Column(Integer, primary_key=True, autoincrement=True)
    new_sku = Column(String(50), nullable=False, index=True)
    old_sku = Column(String(50), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())


class OptimizationLog(Base):
    __tablename__ = "optimization_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    optimization_type = Column(String(50), nullable=False, default="weekly")
    source_stats = Column(JSON, nullable=False, default=dict)
    failed_analysis = Column(JSON, nullable=False, default=list)
    prompt_adjustments = Column(JSON, nullable=False, default=dict)
    summary = Column(String(1000), nullable=False, default="")
    created_at = Column(DateTime, nullable=False, server_default=func.now())


class LlmConfig(Base):
    __tablename__ = "llm_config"

    id = Column(Integer, primary_key=True, default=1)
    base_url = Column(String(500), nullable=False, default="https://api.openai.com/v1")
    api_key = Column(String(200), nullable=False, default="")
    ranking_model = Column(String(100), nullable=False, default="gpt-4o")
    enrichment_model = Column(String(100), nullable=False, default="gpt-4o-mini")
    embedding_model = Column(String(100), nullable=False, default="text-embedding-3-small")
    temperature = Column(Float, nullable=False, default=0.7)
    max_tokens = Column(Integer, nullable=False, default=4096)
    timeout = Column(Integer, nullable=False, default=30)
    available_models = Column(JSON, nullable=False, default=list)
    models_updated_at = Column(DateTime, nullable=True)
    connection_status = Column(String(20), nullable=False, default="untested")
    last_test_at = Column(DateTime, nullable=True)
    langsmith_api_key = Column(String(200), nullable=False, default="")
    langsmith_project = Column(String(100), nullable=False, default="dental-recommend-agent")
    langsmith_enabled = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class BatchCheckpoint(Base):
    """Stores batch processing checkpoints for断点恢复 (§10.4)."""
    __tablename__ = "batch_checkpoints"

    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_type = Column(String(50), nullable=False, index=True)
    batch_id = Column(String(100), nullable=False, index=True)
    last_completed_idx = Column(Integer, nullable=False, default=0)
    total_items = Column(Integer, nullable=False, default=0)
    completed_items = Column(Integer, nullable=False, default=0)
    failed_items = Column(Integer, nullable=False, default=0)
    metadata_json = Column(JSON, nullable=False, default=dict)
    status = Column(String(20), nullable=False, default="running")
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

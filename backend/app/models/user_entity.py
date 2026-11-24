import uuid
from datetime import datetime
from sqlalchemy import DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class UserEntity(Base):
    """
    Lightweight identity & profile knowledge-graph record for a user.
    Enables instant (0ms) multi-hop entity resolution (e.g. 'father_name', 'spouse_name', 'dob', 'pan_number').
    """
    __tablename__ = "user_entities"
    __table_args__ = (
        UniqueConstraint("user_id", "entity_key", name="uq_user_entity_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_value: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_file_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL"), nullable=True
    )
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    user = relationship("User", back_populates="entities")
    source_file = relationship("FileDocument")

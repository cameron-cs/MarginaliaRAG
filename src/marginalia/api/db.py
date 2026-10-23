"""Database models and session handling (SQLAlchemy 2, async).

Types are portable (Uuid, JSON, timezone-aware DateTime), so unit tests run on SQLite while
production uses Postgres. The schema is managed by Alembic (migrations/).

Books live in two tables. `library_books` mirrors the library's profile.json files, one row per
distinct PDF. `user_books` gives a user access to one, with the user's own prompts. Two users
uploading the same file share the library book (processed once) but not each other's prompts or
questions.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'users'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default='user')          # user | admin
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RefreshToken(Base):
    """One issued refresh token. Rotation links tokens into a family; reusing a rotated token
    revokes the whole family (the token was stolen, or replayed)."""
    __tablename__ = 'refresh_tokens'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)     # the JWT's jti
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    family: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LibraryBook(Base):
    __tablename__ = 'library_books'
    book_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(500))
    author: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(16))         # the library profile's status
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    chapters: Mapped[int] = mapped_column(Integer, default=0)
    cover: Mapped[str | None] = mapped_column(String(40), nullable=True)          # file in the book's folder
    cover_source: Mapped[str | None] = mapped_column(String(16), nullable=True)   # pdf | openlibrary | generated
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class UserBook(Base):
    __tablename__ = 'user_books'
    __table_args__ = (UniqueConstraint('user_id', 'book_id'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    book_id: Mapped[str] = mapped_column(ForeignKey('library_books.book_id', ondelete='CASCADE'), index=True)
    filename: Mapped[str] = mapped_column(String(300))
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # pending (not checked yet) | approved | generated (written for the user) | rejected
    # | error (could not be checked; saving the prompts again retries)
    prompt_status: Mapped[str] = mapped_column(String(16), default='pending')
    prompt_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    book: Mapped[LibraryBook] = relationship(lazy='joined')


class Question(Base):
    __tablename__ = 'questions'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    user_book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('user_books.id', ondelete='CASCADE'), index=True)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default='queued')   # queued | running | done | failed | refused
    # Who settled it: agent (answered or failed) | cache (an earlier answer) | guard (refused)
    answered_by: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    details: Mapped[dict] = mapped_column(JSON, default=dict)          # tool calls, grounding verdict, timings
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def user_book_status(ub):
    """What a user sees: their prompts and the shared library book together.
    queued: waiting for the next batch; checking: a batch is working on it."""
    if ub.prompt_status == 'rejected' or ub.book.status == 'rejected':
        return 'rejected'
    if ub.book.status == 'failed' or ub.prompt_status == 'error':
        return 'failed'
    if ub.book.status == 'ready' and ub.prompt_status in ('approved', 'generated'):
        return 'ready'
    if ub.book.status == 'processing':
        return 'checking'
    return 'queued'


class Database:
    def __init__(self, url, **engine_kwargs):
        if url.startswith('sqlite'):
            from sqlalchemy.pool import StaticPool
            engine_kwargs.setdefault('poolclass', StaticPool)
            engine_kwargs.setdefault('connect_args', {'check_same_thread': False})
        else:
            engine_kwargs.setdefault('pool_pre_ping', True)
        self.engine = create_async_engine(url, **engine_kwargs)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)

    async def create_all(self):
        """For tests and throwaway databases; real deployments run `alembic upgrade head`."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def dispose(self):
        await self.engine.dispose()

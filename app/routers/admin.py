"""
Admin endpoints — protected by is_admin flag.
Provides: dashboard stats, user list, user management, DB health.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.book_entry import BookEntry
from app.models.dna_snapshot import DNASnapshot
from app.models.refresh_token import RefreshToken
from app.models.user import User

router = APIRouter(prefix="/admin", tags=["admin"])

async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user

class DashboardStats(BaseModel):
    total_users: int
    total_entries: int
    total_echoes: int
    total_dna_generated: int
    users_last_7d: int
    entries_last_7d: int
    db_size_mb: float
    expired_refresh_tokens: int


class AdminUser(BaseModel):
    id: str
    username: str
    email: str
    display_name: str | None
    personality_type: str | None
    is_admin: bool
    book_count: int
    created_at: datetime
    last_active: datetime | None


class AdminUserDetail(AdminUser):
    is_public: bool
    dna_dirty: bool
    share_token: str | None
    entries: list[dict]


@router.get("/dashboard", response_model=DashboardStats)
async def dashboard(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    total_users = (await db.execute(select(func.count(User.id)))).scalar_one()
    total_entries = (await db.execute(select(func.count(BookEntry.id)))).scalar_one()
    total_echoes = (await db.execute(
        select(func.count(BookEntry.id)).where(BookEntry.public_echo.isnot(None))
    )).scalar_one()
    total_dna = (await db.execute(select(func.count(DNASnapshot.id)))).scalar_one()
    users_7d = (await db.execute(
        select(func.count(User.id)).where(User.created_at >= week_ago)
    )).scalar_one()
    entries_7d = (await db.execute(
        select(func.count(BookEntry.id)).where(BookEntry.created_at >= week_ago)
    )).scalar_one()

    # DB size
    try:
        size_result = await db.execute(text(
            "SELECT pg_database_size(current_database()) / 1048576.0"
        ))
        db_size = round(size_result.scalar_one(), 2)
    except Exception:
        db_size = 0.0

    # Expired refresh tokens (cleanup candidates)
    expired = (await db.execute(
        select(func.count(RefreshToken.id)).where(
            (RefreshToken.expires_at < now) | (RefreshToken.is_revoked == True)
        )
    )).scalar_one()

    return DashboardStats(
        total_users=total_users,
        total_entries=total_entries,
        total_echoes=total_echoes,
        total_dna_generated=total_dna,
        users_last_7d=users_7d,
        entries_last_7d=entries_7d,
        db_size_mb=db_size,
        expired_refresh_tokens=expired,
    )


@router.get("/users", response_model=list[AdminUser])
async def list_users(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    result = await db.execute(
        select(User).order_by(User.created_at.desc())
    )
    users = result.scalars().all()

    out = []
    for u in users:
        count_result = await db.execute(
            select(func.count(BookEntry.id)).where(BookEntry.user_id == u.id)
        )
        book_count = count_result.scalar_one()

        out.append(AdminUser(
            id=str(u.id),
            username=u.username,
            email=u.email,
            display_name=u.display_name,
            personality_type=u.personality_type,
            is_admin=u.is_admin,
            book_count=book_count,
            created_at=u.created_at,
            last_active=u.updated_at,
        ))
    return out


@router.get("/users/{user_id}", response_model=AdminUserDetail)
async def get_user_detail(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    entries_result = await db.execute(
        select(BookEntry)
        .options(selectinload(BookEntry.emotions))
        .where(BookEntry.user_id == user.id)
        .order_by(BookEntry.created_at.desc())
    )
    entries = entries_result.scalars().all()

    return AdminUserDetail(
        id=str(user.id),
        username=user.username,
        email=user.email,
        display_name=user.display_name,
        personality_type=user.personality_type,
        is_admin=user.is_admin,
        is_public=user.is_public,
        dna_dirty=user.dna_dirty,
        share_token=user.share_token,
        book_count=len(entries),
        created_at=user.created_at,
        last_active=user.updated_at,
        entries=[
            {
                "id": str(e.id),
                "title": e.title,
                "author": e.author,
                "intensity": e.intensity,
                "emotions": [em.emotion_id for em in e.emotions],
                "public_echo": e.public_echo,
                "created_at": e.created_at.isoformat(),
            }
            for e in entries
        ],
    )


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.is_admin:
        raise HTTPException(status_code=400, detail="Cannot delete admin user")

    await db.delete(user)
    await db.flush()
    return {"message": f"User {user.username} deleted"}


@router.post("/cleanup-tokens")
async def cleanup_tokens(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Delete expired and revoked refresh tokens."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        delete(RefreshToken).where(
            (RefreshToken.expires_at < now) | (RefreshToken.is_revoked == True)
        )
    )
    count = result.rowcount
    await db.flush()
    return {"deleted": count}


@router.get("/db-health")
async def db_health(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Check database health and connection pool."""
    try:
        await db.execute(text("SELECT 1"))
        size_result = await db.execute(text(
            "SELECT pg_database_size(current_database()) / 1048576.0 AS size_mb"
        ))
        size_mb = round(size_result.scalar_one(), 2)

        tables_result = await db.execute(text("""
            SELECT relname AS table, n_live_tup AS rows
            FROM pg_stat_user_tables
            ORDER BY n_live_tup DESC
        """))
        tables = [{"table": r[0], "rows": r[1]} for r in tables_result.fetchall()]

        return {
            "status": "healthy",
            "db_size_mb": size_mb,
            "tables": tables,
        }
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}
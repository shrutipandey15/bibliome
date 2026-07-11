from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.user import User
from app.schemas.echo import BlockRequest
from app.services.handle_service import resolve_handle
from app.services.social_service import block_user, mute_user, unblock_user, unmute_user

router = APIRouter(prefix="/social", tags=["social"])


async def _target(db: AsyncSession, handle: str, current_user: User) -> User:
    target, _ = await resolve_handle(db, handle)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such handle")
    if target.id == current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot do that to yourself")
    return target


@router.post("/blocks", status_code=status.HTTP_204_NO_CONTENT)
async def block(data: BlockRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Block a handle — bidirectional, cross-surface, silent to the blocked party."""
    target = await _target(db, data.handle, current_user)
    await block_user(db, current_user.id, target.id)


@router.delete("/blocks/{handle}", status_code=status.HTTP_204_NO_CONTENT)
async def unblock(handle: str, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    target, _ = await resolve_handle(db, handle)
    if target is not None:
        await unblock_user(db, current_user.id, target.id)


@router.post("/mutes", status_code=status.HTTP_204_NO_CONTENT)
async def mute(data: BlockRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Mute a handle — one-way hide from your feed."""
    target = await _target(db, data.handle, current_user)
    await mute_user(db, current_user.id, target.id)


@router.delete("/mutes/{handle}", status_code=status.HTTP_204_NO_CONTENT)
async def unmute(handle: str, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    target, _ = await resolve_handle(db, handle)
    if target is not None:
        await unmute_user(db, current_user.id, target.id)

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.user import User
from app.schemas.profile import (
    CollectionCreate,
    CollectionItemAdd,
    CollectionReorder,
    CollectionResponse,
    CollectionUpdate,
    ProfileUpdate,
)
from app.services.collection_service import (
    CollectionError,
    add_item,
    create_collection,
    delete_collection,
    get_owned_collection,
    remove_item,
    reorder_items,
    update_collection,
)
from app.services.handle_service import resolve_handle
from app.services.profile_service import compose_profile

router = APIRouter(tags=["profile"])

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _sanitize_bio(text: str | None) -> str | None:
    if text is None:
        return None
    return _CONTROL_RE.sub("", text).strip() or None


# ── Profile ──

@router.get("/me/profile")
async def get_my_profile(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The self-view: full profile, editable client-side."""
    return await compose_profile(db, current_user.id, current_user)


@router.patch("/me/profile")
async def update_my_profile(
    data: ProfileUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    update = data.model_dump(exclude_unset=True)
    if "bio" in update:
        update["bio"] = _sanitize_bio(update["bio"])
    for field, value in update.items():
        setattr(current_user, field, value)
    await db.flush()
    return await compose_profile(db, current_user.id, current_user)


@router.get("/profile/{handle}")
async def get_profile(
    handle: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Another reader's profile — visibility + block enforced server-side.
    Blocked or unknown → 404; private-to-stranger → minimal card."""
    owner, canonical = await resolve_handle(db, handle)
    if owner is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such profile")
    profile = await compose_profile(db, current_user.id, owner)
    if profile is None:  # blocked → appears not to exist
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such profile")
    profile["canonical_handle"] = canonical  # supports "previously known as" redirect
    return profile


# ── Collections ──

@router.post("/collections", response_model=CollectionResponse, status_code=status.HTTP_201_CREATED)
async def post_collection(
    data: CollectionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        c = await create_collection(db, current_user.id, data.title, data.description, data.visibility)
    except CollectionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return CollectionResponse(id=c.id, title=c.title, description=c.description, visibility=c.visibility, position=c.position)


async def _owned_or_404(db: AsyncSession, collection_id: uuid.UUID, user_id: uuid.UUID):
    c = await get_owned_collection(db, collection_id, user_id)
    if c is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found")
    return c


@router.patch("/collections/{collection_id}", response_model=CollectionResponse)
async def patch_collection(
    collection_id: uuid.UUID,
    data: CollectionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    c = await _owned_or_404(db, collection_id, current_user.id)
    try:
        await update_collection(db, c, **data.model_dump(exclude_unset=True))
    except CollectionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return CollectionResponse(id=c.id, title=c.title, description=c.description, visibility=c.visibility, position=c.position)


@router.delete("/collections/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_collection_endpoint(
    collection_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    c = await _owned_or_404(db, collection_id, current_user.id)
    await delete_collection(db, c)


@router.post("/collections/{collection_id}/items", status_code=status.HTTP_204_NO_CONTENT)
async def add_collection_item(
    collection_id: uuid.UUID,
    data: CollectionItemAdd,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    c = await _owned_or_404(db, collection_id, current_user.id)
    try:
        await add_item(db, c, data.entry_id, current_user.id)
    except CollectionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.delete("/collections/{collection_id}/items/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_collection_item(
    collection_id: uuid.UUID,
    entry_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    c = await _owned_or_404(db, collection_id, current_user.id)
    await remove_item(db, c, entry_id)


@router.patch("/collections/{collection_id}/reorder", status_code=status.HTTP_204_NO_CONTENT)
async def reorder_collection(
    collection_id: uuid.UUID,
    data: CollectionReorder,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    c = await _owned_or_404(db, collection_id, current_user.id)
    await reorder_items(db, c, data.entry_ids)

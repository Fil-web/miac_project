from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload
from starlette.responses import RedirectResponse

from app.audit import add_audit_event
from app.auth import get_current_user, require_role, validate_csrf
from app.db import get_db
from app.models import Category, Part, PartMovement, Reserve, Supplier, User
from app.templating import flash, render


router = APIRouter(prefix="/parts", tags=["Комплектующие"])
PAGE_SIZE = 50


def parse_reserve_filter(value: str | None) -> int | None:
    """Разбирает необязательный резерв и допускает пустое значение формы."""

    if value is None or not value.strip():
        return None
    try:
        reserve_id = int(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Некорректный резерв в фильтре") from exc
    if reserve_id <= 0:
        raise HTTPException(status_code=400, detail="Некорректный резерв в фильтре")
    return reserve_id


def form_choices(db: Session) -> dict:
    """Загружает активные справочники для формы карточки."""

    return {
        "reserves": db.scalars(select(Reserve).where(Reserve.is_active.is_(True)).order_by(Reserve.name)).all(),
        "categories": db.scalars(
            select(Category).where(Category.is_active.is_(True)).order_by(Category.name)
        ).all(),
        "suppliers": db.scalars(
            select(Supplier).where(Supplier.is_active.is_(True)).order_by(Supplier.name)
        ).all(),
    }


def get_part_or_404(
    db: Session,
    part_id: int,
    *,
    for_update: bool = False,
    include_deleted: bool = False,
) -> Part:
    """Возвращает карточку или единообразную ошибку 404."""

    statement = select(Part).where(Part.id == part_id)
    if not include_deleted:
        statement = statement.where(Part.is_deleted.is_(False))
    if for_update:
        statement = statement.with_for_update()
    part = db.scalar(statement)
    if not part:
        raise HTTPException(status_code=404, detail="Комплектующее не найдено")
    return part


@router.get("", name="parts_list")
def parts_list(
    request: Request,
    reserve_id: str | None = Query(default=None, max_length=30),
    part_status: str | None = Query(default=None, alias="status"),
    q: str = Query(default="", max_length=150),
    page: int = Query(default=1, ge=1),
    deleted: bool = Query(default=False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Выводит карточки с поиском и фильтрами по резерву и статусу."""

    if deleted and user.role.name != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Недостаточно прав")

    selected_reserve_id = parse_reserve_filter(reserve_id)
    statement = select(Part).options(
        joinedload(Part.reserve),
        joinedload(Part.category),
        joinedload(Part.current_workstation),
        joinedload(Part.deleted_by_user),
    )
    count_statement = select(func.count()).select_from(Part)
    filters = [Part.is_deleted.is_(deleted)]
    if selected_reserve_id:
        filters.append(Part.reserve_id == selected_reserve_id)
    if part_status in {"in_stock", "installed", "written_off"}:
        filters.append(Part.status == part_status)
    if q.strip():
        pattern = f"%{q.strip()}%"
        filters.append(
            or_(
                Part.name.ilike(pattern),
                Part.part_type.ilike(pattern),
                Part.serial_number.ilike(pattern),
                Part.inventory_number.ilike(pattern),
            )
        )

    if filters:
        statement = statement.where(*filters)
        count_statement = count_statement.where(*filters)
    total = db.scalar(count_statement) or 0
    parts = db.scalars(
        statement.order_by(Part.created_at.desc()).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
    ).all()
    reserves = db.scalars(select(Reserve).order_by(Reserve.name)).all()
    return render(
        request,
        "parts/list.html",
        user=user,
        page_title="Комплектующие",
        parts=parts,
        reserves=reserves,
        selected_reserve=selected_reserve_id,
        selected_status=part_status,
        q=q,
        page=page,
        pages=max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
        total=total,
        deleted=deleted,
    )


@router.get("/new", name="part_new")
def part_new(
    request: Request,
    user: User = Depends(require_role("admin", "storekeeper")),
    db: Session = Depends(get_db),
):
    """Показывает форму постановки комплектующего на учёт."""

    return render(
        request,
        "parts/form.html",
        user=user,
        page_title="Новое комплектующее",
        part=None,
        **form_choices(db),
    )


@router.post("/new", name="part_create")
def part_create(
    request: Request,
    name: Annotated[str, Form(min_length=1, max_length=255)],
    part_type: Annotated[str, Form(min_length=1, max_length=150)],
    serial_number: Annotated[str, Form(max_length=150)],
    received_at: Annotated[date, Form()],
    reserve_id: Annotated[int, Form()],
    category_id: Annotated[int, Form()],
    supplier_id: Annotated[int | None, Form()] = None,
    notes: Annotated[str, Form(max_length=4000)] = "",
    inventory_number: Annotated[str, Form(max_length=150)] = "",
    without_inventory_number: Annotated[bool, Form()] = False,
    _: None = Depends(validate_csrf),
    user: User = Depends(require_role("admin", "storekeeper")),
    db: Session = Depends(get_db),
):
    """Создаёт карточку и движение «Поступление» в одной транзакции."""

    cleaned_inventory_number = None if without_inventory_number else inventory_number.strip() or None
    part = Part(
        name=name.strip(),
        part_type=part_type.strip(),
        serial_number=serial_number.strip() or None,
        inventory_number=cleaned_inventory_number,
        received_at=received_at,
        reserve_id=reserve_id,
        category_id=category_id,
        supplier_id=supplier_id,
        status="in_stock",
        notes=notes.strip() or None,
        created_by=user.id,
    )
    if not without_inventory_number and not cleaned_inventory_number:
        return render(
            request,
            "parts/form.html",
            user=user,
            page_title="Новое комплектующее",
            part=part,
            without_inventory_number=False,
            error="Укажите инвентарный номер или отметьте «Без инвентарного номера».",
            status_code=400,
            **form_choices(db),
        )
    try:
        db.add(part)
        db.flush()
        db.add(
            PartMovement(
                part_id=part.id,
                movement_type="receipt",
                to_reserve_id=part.reserve_id,
                reason="Первичная постановка комплектующего на учёт",
                performed_by=user.id,
            )
        )
        add_audit_event(
            db,
            request,
            "PART_CREATED",
            "part",
            part.id,
            user=user,
            details={
                "reserve_id": reserve_id,
                "serial_number": part.serial_number,
                "inventory_number": part.inventory_number,
            },
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        part.id = None
        return render(
            request,
            "parts/form.html",
            user=user,
            page_title="Новое комплектующее",
            part=part,
            without_inventory_number=without_inventory_number,
            error="Не удалось сохранить карточку. Проверьте уникальность инвентарного и серийного номеров.",
            status_code=400,
            **form_choices(db),
        )
    flash(request, "Комплектующее поставлено на учёт.")
    return RedirectResponse(f"/parts/{part.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{part_id}", name="part_detail")
def part_detail(
    part_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Показывает карточку и полную историю движения."""

    part = db.scalar(
        select(Part)
        .options(
            joinedload(Part.reserve),
            joinedload(Part.category),
            joinedload(Part.supplier),
            joinedload(Part.current_workstation),
            joinedload(Part.deleted_by_user),
        )
        .where(Part.id == part_id)
    )
    if not part:
        raise HTTPException(status_code=404, detail="Комплектующее не найдено")
    if part.is_deleted and user.role.name != "admin":
        raise HTTPException(status_code=404, detail="Комплектующее не найдено")
    movements = db.scalars(
        select(PartMovement)
        .options(
            joinedload(PartMovement.from_reserve),
            joinedload(PartMovement.to_reserve),
            joinedload(PartMovement.from_workstation),
            joinedload(PartMovement.to_workstation),
            joinedload(PartMovement.performer),
        )
        .where(PartMovement.part_id == part.id)
        .order_by(PartMovement.performed_at.desc())
    ).all()
    reserves = db.scalars(select(Reserve).where(Reserve.is_active.is_(True)).order_by(Reserve.name)).all()
    return render(
        request,
        "parts/detail.html",
        user=user,
        page_title=f"Комплектующее № {part.id}",
        part=part,
        movements=movements,
        reserves=reserves,
    )


@router.get("/{part_id}/edit", name="part_edit")
def part_edit(
    part_id: int,
    request: Request,
    user: User = Depends(require_role("admin", "storekeeper")),
    db: Session = Depends(get_db),
):
    """Показывает форму редактирования описательных полей карточки."""

    part = get_part_or_404(db, part_id)
    return render(
        request,
        "parts/form.html",
        user=user,
        page_title="Редактирование комплектующего",
        part=part,
        **form_choices(db),
    )


@router.post("/{part_id}/edit", name="part_update")
def part_update(
    part_id: int,
    request: Request,
    name: Annotated[str, Form(min_length=1, max_length=255)],
    part_type: Annotated[str, Form(min_length=1, max_length=150)],
    serial_number: Annotated[str, Form(max_length=150)],
    received_at: Annotated[date, Form()],
    category_id: Annotated[int, Form()],
    supplier_id: Annotated[int | None, Form()] = None,
    notes: Annotated[str, Form(max_length=4000)] = "",
    inventory_number: Annotated[str, Form(max_length=150)] = "",
    without_inventory_number: Annotated[bool, Form()] = False,
    _: None = Depends(validate_csrf),
    user: User = Depends(require_role("admin", "storekeeper")),
    db: Session = Depends(get_db),
):
    """Обновляет реквизиты без скрытого изменения резерва или статуса."""

    part = get_part_or_404(db, part_id, for_update=True)
    before = {
        "name": part.name,
        "serial_number": part.serial_number,
        "inventory_number": part.inventory_number,
        "category_id": part.category_id,
    }
    cleaned_inventory_number = None if without_inventory_number else inventory_number.strip() or None
    part.name = name.strip()
    part.part_type = part_type.strip()
    part.serial_number = serial_number.strip() or None
    part.inventory_number = cleaned_inventory_number
    part.received_at = received_at
    part.category_id = category_id
    part.supplier_id = supplier_id
    part.notes = notes.strip() or None
    part.updated_at = datetime.now(timezone.utc)
    if not without_inventory_number and not cleaned_inventory_number:
        return render(
            request,
            "parts/form.html",
            user=user,
            page_title="Редактирование комплектующего",
            part=part,
            without_inventory_number=False,
            error="Укажите инвентарный номер или отметьте «Без инвентарного номера».",
            status_code=400,
            **form_choices(db),
        )
    add_audit_event(
        db,
        request,
        "PART_UPDATED",
        "part",
        part.id,
        user=user,
        details={
            "before": before,
            "after": {
                "name": part.name,
                "serial_number": part.serial_number,
                "inventory_number": part.inventory_number,
                "category_id": category_id,
            },
        },
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render(
            request,
            "parts/form.html",
            user=user,
            page_title="Редактирование комплектующего",
            part=part,
            without_inventory_number=without_inventory_number,
            error="Инвентарный или серийный номер уже используется либо справочник недоступен.",
            status_code=400,
            **form_choices(db),
        )
    flash(request, "Карточка обновлена.")
    return RedirectResponse(f"/parts/{part.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{part_id}/transfer-reserve", name="part_transfer_reserve")
def part_transfer_reserve(
    part_id: int,
    request: Request,
    reserve_id: Annotated[int, Form()],
    _: None = Depends(validate_csrf),
    user: User = Depends(require_role("admin", "storekeeper")),
    db: Session = Depends(get_db),
):
    """Переносит принадлежность между резервами с автоматической записью истории."""

    part = get_part_or_404(db, part_id, for_update=True)
    target = db.scalar(select(Reserve).where(Reserve.id == reserve_id, Reserve.is_active.is_(True)))
    if not target:
        raise HTTPException(status_code=400, detail="Целевой резерв недоступен")
    if part.status == "written_off":
        raise HTTPException(status_code=400, detail="Списанное комплектующее нельзя перемещать")
    if part.reserve_id == reserve_id:
        raise HTTPException(status_code=400, detail="Выберите другой резерв")

    old_reserve_id = part.reserve_id
    part.reserve_id = reserve_id
    part.updated_at = datetime.now(timezone.utc)
    db.add(
        PartMovement(
            part_id=part.id,
            movement_type="reserve_transfer",
            from_reserve_id=old_reserve_id,
            to_reserve_id=reserve_id,
            from_workstation_id=part.current_workstation_id,
            to_workstation_id=part.current_workstation_id,
            reason=f"Перемещение в резерв «{target.name}»",
            performed_by=user.id,
        )
    )
    add_audit_event(
        db,
        request,
        "PART_RESERVE_TRANSFERRED",
        "part",
        part.id,
        user=user,
        details={"from_reserve_id": old_reserve_id, "to_reserve_id": reserve_id},
    )
    db.commit()
    flash(request, "Комплектующее перемещено между резервами.")
    return RedirectResponse(f"/parts/{part.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{part_id}/write-off", name="part_write_off")
def part_write_off(
    part_id: int,
    request: Request,
    reason: Annotated[str, Form(min_length=3, max_length=2000)],
    _: None = Depends(validate_csrf),
    user: User = Depends(require_role("admin", "storekeeper")),
    db: Session = Depends(get_db),
):
    """Списывает комплектующее и сохраняет прежнее место в движении."""

    part = get_part_or_404(db, part_id, for_update=True)
    if part.status == "written_off":
        raise HTTPException(status_code=400, detail="Комплектующее уже списано")
    old_workstation_id = part.current_workstation_id
    part.status = "written_off"
    part.current_workstation_id = None
    part.updated_at = datetime.now(timezone.utc)
    db.add(
        PartMovement(
            part_id=part.id,
            movement_type="write_off",
            from_reserve_id=part.reserve_id,
            from_workstation_id=old_workstation_id,
            reason=reason.strip(),
            performed_by=user.id,
        )
    )
    add_audit_event(
        db,
        request,
        "PART_WRITTEN_OFF",
        "part",
        part.id,
        user=user,
        details={"reserve_id": part.reserve_id, "from_workstation_id": old_workstation_id},
    )
    db.commit()
    flash(request, "Комплектующее списано.", "warning")
    return RedirectResponse(f"/parts/{part.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{part_id}/delete", name="part_delete")
def part_delete(
    part_id: int,
    request: Request,
    reason: Annotated[str, Form(min_length=3, max_length=1000)],
    _: None = Depends(validate_csrf),
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Мягко удаляет карточку независимо от наличия истории движений."""

    part = get_part_or_404(db, part_id, for_update=True)
    part.is_deleted = True
    part.deleted_at = datetime.now(timezone.utc)
    part.deleted_by = user.id
    part.deletion_reason = reason.strip()
    part.updated_at = part.deleted_at
    add_audit_event(
        db,
        request,
        "PART_DELETED",
        "part",
        part.id,
        user=user,
        details={"name": part.name, "reason": part.deletion_reason},
    )
    db.commit()
    flash(request, "Комплектующее удалено. Карточку можно восстановить в разделе «Удалённые».", "warning")
    return RedirectResponse("/parts", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{part_id}/restore", name="part_restore")
def part_restore(
    part_id: int,
    request: Request,
    _: None = Depends(validate_csrf),
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Восстанавливает мягко удалённую карточку со всей её историей."""

    part = get_part_or_404(db, part_id, for_update=True, include_deleted=True)
    if not part.is_deleted:
        flash(request, "Комплектующее не удалено.", "warning")
        return RedirectResponse(f"/parts/{part.id}", status_code=status.HTTP_303_SEE_OTHER)

    part.is_deleted = False
    part.deleted_at = None
    part.deleted_by = None
    part.deletion_reason = None
    part.updated_at = datetime.now(timezone.utc)
    add_audit_event(
        db,
        request,
        "PART_RESTORED",
        "part",
        part.id,
        user=user,
        details={"name": part.name},
    )
    db.commit()
    flash(request, "Комплектующее восстановлено вместе с историей движения.")
    return RedirectResponse("/parts?deleted=true", status_code=status.HTTP_303_SEE_OTHER)

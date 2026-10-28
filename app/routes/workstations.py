from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload
from starlette.responses import RedirectResponse

from app.audit import add_audit_event
from app.auth import get_current_user, require_role, validate_csrf
from app.db import get_db
from app.models import Employee, Part, PartMovement, User, Workstation
from app.templating import flash, render


router = APIRouter(prefix="/workstations", tags=["АРМ"])

MAIN_COMPONENT_GROUPS = (
    ("Материнская плата + процессор", ("материн", "процессор"), "bi-motherboard"),
    ("Оперативная память", ("оператив",), "bi-memory"),
    ("Блок питания", ("питан",), "bi-lightning-charge"),
)

# Полный набор нужен именно для оценки работоспособности: объединённая карточка
# «Материнская плата + процессор» считается заполненной только при наличии обоих узлов.
REQUIRED_COMPONENTS = (
    ("Материнская плата", "материн"),
    ("Процессор", "процессор"),
    ("Оперативная память", "оператив"),
    ("Блок питания", "питан"),
)


def group_main_components(parts: list[Part]) -> list[dict[str, object]]:
    """Группирует основные компоненты для краткого состава АРМ."""

    return [
        {
            "label": label,
            "icon": icon,
            "parts": [
                part
                for part in parts
                if any(keyword in part.category.name.casefold() for keyword in category_keywords)
            ],
        }
        for label, category_keywords, icon in MAIN_COMPONENT_GROUPS
    ]


def missing_required_components(parts: list[Part]) -> list[str]:
    """Возвращает обязательные узлы, которых нет в действующем составе АРМ."""

    category_names = [part.category.name.casefold() for part in parts if part.category]
    return [
        label
        for label, keyword in REQUIRED_COMPONENTS
        if not any(keyword in category_name for category_name in category_names)
    ]


def get_workstation_or_404(
    db: Session,
    workstation_id: int,
    *,
    for_update: bool = False,
    include_deleted: bool = False,
) -> Workstation:
    """Загружает АРМ или возвращает ошибку 404."""

    statement = select(Workstation).where(Workstation.id == workstation_id)
    if not include_deleted:
        statement = statement.where(Workstation.is_deleted.is_(False))
    if for_update:
        statement = statement.with_for_update()
    workstation = db.scalar(statement)
    if not workstation:
        raise HTTPException(status_code=404, detail="АРМ не найдено")
    return workstation


@router.get("", name="workstations_list")
def workstations_list(
    request: Request,
    q: str = Query(default="", max_length=150),
    deleted: bool = Query(default=False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Показывает справочник АРМ с поиском по кабинету и названию."""

    if deleted and user.role.name != "admin":
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    statement = (
        select(Workstation)
        .options(
            joinedload(Workstation.responsible),
            joinedload(Workstation.deleted_by_user),
            selectinload(Workstation.installed_parts).joinedload(Part.category),
        )
        .where(Workstation.is_deleted.is_(deleted))
    )
    if q.strip():
        pattern = f"%{q.strip()}%"
        statement = statement.where(
            or_(Workstation.name.ilike(pattern), Workstation.cabinet.ilike(pattern))
        )
    workstations = db.scalars(statement.order_by(Workstation.name)).all()
    workstation_rows = []
    for workstation in workstations:
        installed_parts = [
            part
            for part in workstation.installed_parts
            if part.status == "installed" and not part.is_deleted
        ]
        missing_components = missing_required_components(installed_parts)
        workstation_rows.append(
            {
                "workstation": workstation,
                "missing_components": missing_components,
                "is_operational": workstation.is_active and not missing_components and not workstation.is_deleted,
            }
        )
    return render(
        request,
        "workstations/list.html",
        user=user,
        page_title="АРМ",
        workstation_rows=workstation_rows,
        q=q,
        deleted=deleted,
    )


@router.get("/new", name="workstation_new")
def workstation_new(
    request: Request,
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Показывает форму нового АРМ."""

    employees = db.scalars(select(Employee).where(Employee.is_active.is_(True)).order_by(Employee.full_name)).all()
    return render(
        request,
        "workstations/form.html",
        user=user,
        page_title="Новое АРМ",
        workstation=None,
        employees=employees,
    )


@router.post("/new", name="workstation_create")
def workstation_create(
    request: Request,
    name: Annotated[str, Form(min_length=1, max_length=255)],
    cabinet: Annotated[str, Form(min_length=1, max_length=100)],
    responsible_id: Annotated[int | None, Form()] = None,
    description: Annotated[str, Form(max_length=4000)] = "",
    _: None = Depends(validate_csrf),
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Создаёт АРМ и событие аудита."""

    workstation = Workstation(
        name=name.strip(),
        cabinet=cabinet.strip(),
        responsible_id=responsible_id,
        description=description.strip() or None,
    )
    try:
        db.add(workstation)
        db.flush()
        add_audit_event(
            db,
            request,
            "WORKSTATION_CREATED",
            "workstation",
            workstation.id,
            user=user,
            details={"name": workstation.name},
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        employees = db.scalars(
            select(Employee).where(Employee.is_active.is_(True)).order_by(Employee.full_name)
        ).all()
        return render(
            request,
            "workstations/form.html",
            user=user,
            page_title="Новое АРМ",
            workstation=workstation,
            employees=employees,
            error="Не удалось создать АРМ. Проверьте выбранного ответственного сотрудника.",
            status_code=400,
        )
    flash(request, "АРМ создано.")
    return RedirectResponse(f"/workstations/{workstation.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{workstation_id}", name="workstation_detail")
def workstation_detail(
    workstation_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Показывает карточку АРМ, состав и связанную историю."""

    workstation = db.scalar(
        select(Workstation)
        .options(joinedload(Workstation.responsible), joinedload(Workstation.deleted_by_user))
        .where(Workstation.id == workstation_id)
    )
    if not workstation or (workstation.is_deleted and user.role.name != "admin"):
        raise HTTPException(status_code=404, detail="АРМ не найдено")
    installed_parts = db.scalars(
        select(Part)
        .options(joinedload(Part.reserve), joinedload(Part.category))
        .where(Part.current_workstation_id == workstation.id, Part.is_deleted.is_(False))
        .order_by(Part.name)
    ).all()
    available_parts = []
    other_workstations = []
    if not workstation.is_deleted:
        available_parts = db.scalars(
            select(Part)
            .options(joinedload(Part.reserve))
            .where(Part.status == "in_stock", Part.is_deleted.is_(False))
            .order_by(Part.name)
            .limit(200)
        ).all()
        other_workstations = db.scalars(
            select(Workstation)
            .where(
                Workstation.id != workstation.id,
                Workstation.is_active.is_(True),
                Workstation.is_deleted.is_(False),
            )
            .order_by(Workstation.name)
        ).all()
    movements = db.scalars(
        select(PartMovement)
        .join(Part)
        .options(joinedload(PartMovement.part), joinedload(PartMovement.performer))
        .where(
            Part.is_deleted.is_(False),
            or_(
                PartMovement.from_workstation_id == workstation.id,
                PartMovement.to_workstation_id == workstation.id,
            )
        )
        .order_by(PartMovement.performed_at.desc())
        .limit(100)
    ).all()
    return render(
        request,
        "workstations/detail.html",
        user=user,
        page_title=workstation.name,
        workstation=workstation,
        installed_parts=installed_parts,
        available_parts=available_parts,
        other_workstations=other_workstations,
        movements=movements,
        main_components=group_main_components(installed_parts),
        missing_components=missing_required_components(installed_parts),
    )


@router.get("/{workstation_id}/edit", name="workstation_edit")
def workstation_edit(
    workstation_id: int,
    request: Request,
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Показывает форму изменения АРМ."""

    workstation = get_workstation_or_404(db, workstation_id)
    employees = db.scalars(select(Employee).where(Employee.is_active.is_(True)).order_by(Employee.full_name)).all()
    return render(
        request,
        "workstations/form.html",
        user=user,
        page_title="Редактирование АРМ",
        workstation=workstation,
        employees=employees,
    )


@router.post("/{workstation_id}/edit", name="workstation_update")
def workstation_update(
    workstation_id: int,
    request: Request,
    name: Annotated[str, Form(min_length=1, max_length=255)],
    cabinet: Annotated[str, Form(min_length=1, max_length=100)],
    responsible_id: Annotated[int | None, Form()] = None,
    description: Annotated[str, Form(max_length=4000)] = "",
    _: None = Depends(validate_csrf),
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Обновляет карточку АРМ с фиксацией старых реквизитов."""

    workstation = get_workstation_or_404(db, workstation_id, for_update=True)
    before = {
        "name": workstation.name,
        "cabinet": workstation.cabinet,
        "responsible_id": workstation.responsible_id,
    }
    workstation.name = name.strip()
    workstation.cabinet = cabinet.strip()
    workstation.responsible_id = responsible_id
    workstation.description = description.strip() or None
    workstation.updated_at = datetime.now(timezone.utc)
    add_audit_event(
        db,
        request,
        "WORKSTATION_UPDATED",
        "workstation",
        workstation.id,
        user=user,
        details={
            "before": before,
            "after": {
                "name": workstation.name,
                "cabinet": workstation.cabinet,
                "responsible_id": responsible_id,
            },
        },
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        employees = db.scalars(
            select(Employee).where(Employee.is_active.is_(True)).order_by(Employee.full_name)
        ).all()
        return render(
            request,
            "workstations/form.html",
            user=user,
            page_title="Редактирование АРМ",
            workstation=workstation,
            employees=employees,
            error="Не удалось обновить АРМ. Проверьте выбранного ответственного сотрудника.",
            status_code=400,
        )
    flash(request, "Карточка АРМ обновлена.")
    return RedirectResponse(f"/workstations/{workstation.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{workstation_id}/delete", name="workstation_delete")
def workstation_delete(
    workstation_id: int,
    request: Request,
    reason: Annotated[str, Form(min_length=3, max_length=1000)],
    _: None = Depends(validate_csrf),
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Мягко удаляет пустое АРМ, сохраняя карточку и историю операций."""

    workstation = get_workstation_or_404(db, workstation_id, for_update=True)
    installed_count = db.scalar(
        select(Part.id)
        .where(
            Part.current_workstation_id == workstation.id,
            Part.status == "installed",
            Part.is_deleted.is_(False),
        )
        .limit(1)
    )
    if installed_count is not None:
        flash(request, "Перед удалением АРМ верните все комплектующие на склад или переместите их.", "danger")
        return RedirectResponse(f"/workstations/{workstation.id}", status_code=status.HTTP_303_SEE_OTHER)

    workstation.is_deleted = True
    workstation.is_active = False
    workstation.deleted_at = datetime.now(timezone.utc)
    workstation.deleted_by = user.id
    workstation.deletion_reason = reason.strip()
    workstation.updated_at = workstation.deleted_at
    add_audit_event(
        db,
        request,
        "WORKSTATION_DELETED",
        "workstation",
        workstation.id,
        user=user,
        details={"name": workstation.name, "reason": workstation.deletion_reason},
    )
    db.commit()
    flash(request, "АРМ удалено из рабочего списка. История сохранена.", "warning")
    return RedirectResponse("/workstations", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{workstation_id}/restore", name="workstation_restore")
def workstation_restore(
    workstation_id: int,
    request: Request,
    _: None = Depends(validate_csrf),
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Восстанавливает мягко удалённое АРМ."""

    workstation = get_workstation_or_404(
        db,
        workstation_id,
        for_update=True,
        include_deleted=True,
    )
    if not workstation.is_deleted:
        flash(request, "АРМ уже находится в рабочем списке.", "warning")
        return RedirectResponse(f"/workstations/{workstation.id}", status_code=status.HTTP_303_SEE_OTHER)

    workstation.is_deleted = False
    workstation.is_active = True
    workstation.deleted_at = None
    workstation.deleted_by = None
    workstation.deletion_reason = None
    workstation.updated_at = datetime.now(timezone.utc)
    add_audit_event(
        db,
        request,
        "WORKSTATION_RESTORED",
        "workstation",
        workstation.id,
        user=user,
        details={"name": workstation.name},
    )
    db.commit()
    flash(request, "АРМ восстановлено.")
    return RedirectResponse(f"/workstations/{workstation.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{workstation_id}/install", name="workstation_install_part")
def install_part(
    workstation_id: int,
    request: Request,
    part_id: Annotated[int, Form()],
    _: None = Depends(validate_csrf),
    user: User = Depends(require_role("admin", "storekeeper")),
    db: Session = Depends(get_db),
):
    """Устанавливает складское комплектующее в АРМ атомарной операцией."""

    workstation = get_workstation_or_404(db, workstation_id)
    if not workstation.is_active:
        raise HTTPException(status_code=400, detail="Нельзя устанавливать в неактивное АРМ")
    part = db.scalar(
        select(Part).where(Part.id == part_id, Part.is_deleted.is_(False)).with_for_update()
    )
    if not part:
        raise HTTPException(status_code=404, detail="Комплектующее не найдено")
    if part.status != "in_stock":
        raise HTTPException(status_code=409, detail="Комплектующее уже установлено или списано")

    part.status = "installed"
    part.current_workstation_id = workstation.id
    part.updated_at = datetime.now(timezone.utc)
    db.add(
        PartMovement(
            part_id=part.id,
            movement_type="installation",
            from_reserve_id=part.reserve_id,
            to_reserve_id=part.reserve_id,
            to_workstation_id=workstation.id,
            reason=f"Установка в АРМ «{workstation.name}»",
            performed_by=user.id,
        )
    )
    add_audit_event(
        db,
        request,
        "PART_INSTALLED",
        "part",
        part.id,
        user=user,
        details={"workstation_id": workstation.id, "reserve_id": part.reserve_id},
    )
    db.commit()
    flash(request, "Комплектующее установлено в АРМ.")
    return RedirectResponse(f"/workstations/{workstation.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{workstation_id}/remove/{part_id}", name="workstation_remove_part")
def remove_part(
    workstation_id: int,
    part_id: int,
    request: Request,
    _: None = Depends(validate_csrf),
    user: User = Depends(require_role("admin", "storekeeper")),
    db: Session = Depends(get_db),
):
    """Возвращает установленное комплектующее на склад своего резерва."""

    workstation = get_workstation_or_404(db, workstation_id)
    part = db.scalar(
        select(Part).where(Part.id == part_id, Part.is_deleted.is_(False)).with_for_update()
    )
    if not part or part.status != "installed" or part.current_workstation_id != workstation_id:
        raise HTTPException(status_code=409, detail="Комплектующее не установлено в указанном АРМ")

    part.status = "in_stock"
    part.current_workstation_id = None
    part.updated_at = datetime.now(timezone.utc)
    db.add(
        PartMovement(
            part_id=part.id,
            movement_type="removal",
            from_reserve_id=part.reserve_id,
            to_reserve_id=part.reserve_id,
            from_workstation_id=workstation_id,
            reason=f"Возврат из АРМ «{workstation.name}» на склад",
            performed_by=user.id,
        )
    )
    add_audit_event(
        db,
        request,
        "PART_RETURNED_TO_STOCK",
        "part",
        part.id,
        user=user,
        details={"from_workstation_id": workstation_id, "reserve_id": part.reserve_id},
    )
    db.commit()
    flash(request, "Комплектующее возвращено на склад.")
    return RedirectResponse(f"/workstations/{workstation_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{workstation_id}/move/{part_id}", name="workstation_move_part")
def move_part(
    workstation_id: int,
    part_id: int,
    request: Request,
    target_workstation_id: Annotated[int, Form()],
    _: None = Depends(validate_csrf),
    user: User = Depends(require_role("admin", "storekeeper")),
    db: Session = Depends(get_db),
):
    """Перемещает комплектующее между двумя АРМ одной записью истории."""

    source = get_workstation_or_404(db, workstation_id)
    target = get_workstation_or_404(db, target_workstation_id)
    if source.id == target.id or not target.is_active:
        raise HTTPException(status_code=400, detail="Выберите другое действующее АРМ")
    part = db.scalar(
        select(Part).where(Part.id == part_id, Part.is_deleted.is_(False)).with_for_update()
    )
    if not part or part.status != "installed" or part.current_workstation_id != source.id:
        raise HTTPException(status_code=409, detail="Комплектующее уже перемещено другим пользователем")

    part.current_workstation_id = target.id
    part.updated_at = datetime.now(timezone.utc)
    db.add(
        PartMovement(
            part_id=part.id,
            movement_type="installation",
            from_reserve_id=part.reserve_id,
            to_reserve_id=part.reserve_id,
            from_workstation_id=source.id,
            to_workstation_id=target.id,
            reason=f"Перемещение из АРМ «{source.name}» в АРМ «{target.name}»",
            performed_by=user.id,
        )
    )
    add_audit_event(
        db,
        request,
        "PART_MOVED_BETWEEN_WORKSTATIONS",
        "part",
        part.id,
        user=user,
        details={"from_workstation_id": source.id, "to_workstation_id": target.id},
    )
    db.commit()
    flash(request, f"Комплектующее перемещено в АРМ «{target.name}».")
    return RedirectResponse(f"/workstations/{source.id}", status_code=status.HTTP_303_SEE_OTHER)

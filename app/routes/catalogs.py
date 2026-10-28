from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.responses import RedirectResponse

from app.audit import add_audit_event
from app.auth import get_current_user, require_role, validate_csrf
from app.db import get_db
from app.models import Category, Employee, Part, PartMovement, Reserve, Supplier, User, Workstation
from app.templating import flash, render


router = APIRouter(prefix="/catalogs", tags=["Справочники"])

CATALOG_MODELS = {
    "reserve": Reserve,
    "category": Category,
    "employee": Employee,
    "supplier": Supplier,
}

CATALOG_SECTIONS = {
    "reserves": {"entity": "reserve", "model": Reserve, "order": Reserve.name},
    "categories": {"entity": "category", "model": Category, "order": Category.name},
    "employees": {"entity": "employee", "model": Employee, "order": Employee.full_name},
    "suppliers": {"entity": "supplier", "model": Supplier, "order": Supplier.name},
}

ENTITY_SECTIONS = {config["entity"]: section for section, config in CATALOG_SECTIONS.items()}

CATALOG_REFERENCES = {
    "reserve": (Part.reserve_id, PartMovement.from_reserve_id, PartMovement.to_reserve_id),
    "category": (Part.category_id,),
    "employee": (Workstation.responsible_id, User.employee_id),
    "supplier": (Part.supplier_id,),
}


def catalog_url(entity: str) -> str:
    """Возвращает адрес вкладки для выбранного типа справочника."""

    return f"/catalogs?section={ENTITY_SECTIONS[entity]}"


@router.get("", name="catalogs_index")
def catalogs_index(
    request: Request,
    section: str = Query(default="reserves"),
    edit_id: int | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Показывает справочники, используемые карточками и АРМ."""

    if section not in CATALOG_SECTIONS:
        section = "reserves"
    config = CATALOG_SECTIONS[section]
    edit_item = None
    if edit_id is not None:
        if user.role.name != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Недостаточно прав")
        edit_item = db.scalar(select(config["model"]).where(config["model"].id == edit_id))
        if not edit_item:
            raise HTTPException(status_code=404, detail="Элемент справочника не найден")
    return render(
        request,
        "catalogs/index.html",
        user=user,
        page_title="Справочники",
        section=section,
        entity=config["entity"],
        items=db.scalars(select(config["model"]).order_by(config["order"])).all(),
        edit_item=edit_item,
    )


def commit_catalog_entry(
    db: Session,
    request: Request,
    user: User,
    entry,
    action: str,
    entity: str,
    details: dict,
) -> bool:
    """Сохраняет элемент справочника и аудит, обрабатывая уникальность."""

    try:
        db.add(entry)
        db.flush()
        add_audit_event(db, request, action, entity, entry.id, user=user, details=details)
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


@router.post("/reserves", name="reserve_create")
def reserve_create(
    request: Request,
    code: Annotated[str, Form(min_length=1, max_length=50)],
    name: Annotated[str, Form(min_length=1, max_length=255)],
    description: Annotated[str, Form(max_length=2000)] = "",
    _: None = Depends(validate_csrf),
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Добавляет источник поступления."""

    entry = Reserve(code=code.strip().upper(), name=name.strip(), description=description.strip() or None)
    if commit_catalog_entry(
        db, request, user, entry, "RESERVE_CREATED", "reserve", {"code": entry.code, "name": entry.name}
    ):
        flash(request, "Резерв добавлен.")
    else:
        flash(request, "Резерв с таким кодом уже существует.", "danger")
    return RedirectResponse("/catalogs?section=reserves", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/categories", name="category_create")
def category_create(
    request: Request,
    name: Annotated[str, Form(min_length=1, max_length=255)],
    description: Annotated[str, Form(max_length=2000)] = "",
    _: None = Depends(validate_csrf),
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Добавляет категорию комплектующих."""

    entry = Category(name=name.strip(), description=description.strip() or None)
    if commit_catalog_entry(
        db, request, user, entry, "CATEGORY_CREATED", "category", {"name": entry.name}
    ):
        flash(request, "Категория добавлена.")
    else:
        flash(request, "Категория с таким названием уже существует.", "danger")
    return RedirectResponse("/catalogs?section=categories", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/employees", name="employee_create")
def employee_create(
    request: Request,
    full_name: Annotated[str, Form(min_length=3, max_length=255)],
    department: Annotated[str, Form(max_length=255)] = "",
    position: Annotated[str, Form(max_length=255)] = "",
    phone: Annotated[str, Form(max_length=50)] = "",
    _: None = Depends(validate_csrf),
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Добавляет сотрудника в справочник ответственных лиц."""

    entry = Employee(
        full_name=full_name.strip(),
        department=department.strip() or None,
        position=position.strip() or None,
        phone=phone.strip() or None,
    )
    commit_catalog_entry(
        db, request, user, entry, "EMPLOYEE_CREATED", "employee", {"full_name": entry.full_name}
    )
    flash(request, "Сотрудник добавлен.")
    return RedirectResponse("/catalogs?section=employees", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/suppliers", name="supplier_create")
def supplier_create(
    request: Request,
    name: Annotated[str, Form(min_length=1, max_length=255)],
    inn: Annotated[str, Form(max_length=12)] = "",
    contact_info: Annotated[str, Form(max_length=2000)] = "",
    _: None = Depends(validate_csrf),
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Добавляет поставщика."""

    cleaned_inn = inn.strip() or None
    if cleaned_inn and (not cleaned_inn.isdigit() or len(cleaned_inn) not in {10, 12}):
        flash(request, "ИНН должен содержать 10 или 12 цифр.", "danger")
        return RedirectResponse("/catalogs?section=suppliers", status_code=status.HTTP_303_SEE_OTHER)
    entry = Supplier(name=name.strip(), inn=cleaned_inn, contact_info=contact_info.strip() or None)
    if commit_catalog_entry(
        db, request, user, entry, "SUPPLIER_CREATED", "supplier", {"name": entry.name, "inn": cleaned_inn}
    ):
        flash(request, "Поставщик добавлен.")
    else:
        flash(request, "Поставщик с таким ИНН уже существует.", "danger")
    return RedirectResponse("/catalogs?section=suppliers", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{entity}/{entity_id}/toggle", name="catalog_toggle")
def catalog_toggle(
    entity: str,
    entity_id: int,
    request: Request,
    _: None = Depends(validate_csrf),
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Архивирует или восстанавливает элемент вместо физического удаления."""

    model = CATALOG_MODELS.get(entity)
    if not model:
        raise HTTPException(status_code=404, detail="Справочник не найден")
    entry = db.scalar(select(model).where(model.id == entity_id).with_for_update())
    if not entry:
        raise HTTPException(status_code=404, detail="Элемент справочника не найден")
    entry.is_active = not entry.is_active
    add_audit_event(
        db,
        request,
        "CATALOG_ENTRY_TOGGLED",
        entity,
        entry.id,
        user=user,
        details={"is_active": entry.is_active},
    )
    db.commit()
    flash(request, "Состояние элемента справочника изменено.")
    return RedirectResponse(catalog_url(entity), status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{entity}/{entity_id}/edit", name="catalog_edit")
def catalog_edit(
    entity: str,
    entity_id: int,
    request: Request,
    name: Annotated[str, Form(max_length=255)] = "",
    description: Annotated[str, Form(max_length=2000)] = "",
    full_name: Annotated[str, Form(max_length=255)] = "",
    department: Annotated[str, Form(max_length=255)] = "",
    position: Annotated[str, Form(max_length=255)] = "",
    phone: Annotated[str, Form(max_length=50)] = "",
    inn: Annotated[str, Form(max_length=12)] = "",
    contact_info: Annotated[str, Form(max_length=2000)] = "",
    _: None = Depends(validate_csrf),
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Изменяет реквизиты справочника, сохраняя код резерва неизменным."""

    model = CATALOG_MODELS.get(entity)
    if not model:
        raise HTTPException(status_code=404, detail="Справочник не найден")
    entry = db.scalar(select(model).where(model.id == entity_id).with_for_update())
    if not entry:
        raise HTTPException(status_code=404, detail="Элемент справочника не найден")

    before: dict[str, str | None] = {}
    after: dict[str, str | None] = {}
    if entity in {"reserve", "category"}:
        cleaned_name = name.strip()
        if not cleaned_name:
            flash(request, "Наименование не может быть пустым.", "danger")
            return RedirectResponse(f"{catalog_url(entity)}&edit_id={entry.id}", status_code=303)
        before = {"name": entry.name, "description": entry.description}
        entry.name = cleaned_name
        entry.description = description.strip() or None
        after = {"name": entry.name, "description": entry.description}
    elif entity == "employee":
        cleaned_full_name = full_name.strip()
        if len(cleaned_full_name) < 3:
            flash(request, "ФИО должно содержать не менее трёх символов.", "danger")
            return RedirectResponse(f"{catalog_url(entity)}&edit_id={entry.id}", status_code=303)
        before = {
            "full_name": entry.full_name,
            "department": entry.department,
            "position": entry.position,
            "phone": entry.phone,
        }
        entry.full_name = cleaned_full_name
        entry.department = department.strip() or None
        entry.position = position.strip() or None
        entry.phone = phone.strip() or None
        after = {
            "full_name": entry.full_name,
            "department": entry.department,
            "position": entry.position,
            "phone": entry.phone,
        }
        for account in db.scalars(select(User).where(User.employee_id == entry.id)).all():
            account.full_name = entry.full_name
    else:
        cleaned_name = name.strip()
        cleaned_inn = inn.strip() or None
        if not cleaned_name:
            flash(request, "Наименование не может быть пустым.", "danger")
            return RedirectResponse(f"{catalog_url(entity)}&edit_id={entry.id}", status_code=303)
        if cleaned_inn and (not cleaned_inn.isdigit() or len(cleaned_inn) not in {10, 12}):
            flash(request, "ИНН должен содержать 10 или 12 цифр.", "danger")
            return RedirectResponse(f"{catalog_url(entity)}&edit_id={entry.id}", status_code=303)
        before = {"name": entry.name, "inn": entry.inn, "contact_info": entry.contact_info}
        entry.name = cleaned_name
        entry.inn = cleaned_inn
        entry.contact_info = contact_info.strip() or None
        after = {"name": entry.name, "inn": entry.inn, "contact_info": entry.contact_info}

    add_audit_event(
        db,
        request,
        "CATALOG_ENTRY_UPDATED",
        entity,
        entry.id,
        user=user,
        details={"before": before, "after": after},
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        flash(request, "Не удалось сохранить изменения: такое значение уже используется.", "danger")
        return RedirectResponse(f"{catalog_url(entity)}&edit_id={entry.id}", status_code=303)

    flash(request, "Изменения сохранены.")
    return RedirectResponse(catalog_url(entity), status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{entity}/{entity_id}/delete", name="catalog_delete")
def catalog_delete(
    entity: str,
    entity_id: int,
    request: Request,
    _: None = Depends(validate_csrf),
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Удаляет только неиспользуемый элемент справочника с обязательным аудитом."""

    model = CATALOG_MODELS.get(entity)
    if not model:
        raise HTTPException(status_code=404, detail="Справочник не найден")
    entry = db.scalar(select(model).where(model.id == entity_id).with_for_update())
    if not entry:
        raise HTTPException(status_code=404, detail="Элемент справочника не найден")

    usage_count = sum(
        db.scalar(select(func.count()).select_from(column.class_).where(column == entry.id)) or 0
        for column in CATALOG_REFERENCES[entity]
    )
    if usage_count:
        flash(
            request,
            f"Удаление невозможно: элемент используется в связанных записях ({usage_count}). Перенесите его в архив.",
            "danger",
        )
        return RedirectResponse(catalog_url(entity), status_code=status.HTTP_303_SEE_OTHER)

    display_name = getattr(entry, "name", None) or getattr(entry, "full_name", None) or str(entry.id)
    add_audit_event(
        db,
        request,
        "CATALOG_ENTRY_DELETED",
        entity,
        entry.id,
        user=user,
        details={"name": display_name},
    )
    db.delete(entry)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        flash(request, "Удаление невозможно: элемент связан с другими данными. Используйте архив.", "danger")
        return RedirectResponse(catalog_url(entity), status_code=status.HTTP_303_SEE_OTHER)

    flash(request, "Элемент справочника удалён.", "warning")
    return RedirectResponse(catalog_url(entity), status_code=status.HTTP_303_SEE_OTHER)

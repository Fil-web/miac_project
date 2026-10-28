from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session, joinedload, selectinload
from starlette.responses import Response

from app.auth import get_current_user
from app.db import get_db
from app.models import Employee, Part, Reserve, Role, ROLE_LABELS, User, Workstation
from app.report_exports import build_pdf_response, build_xlsx_response
from app.routes.workstations import missing_required_components
from app.templating import render


router = APIRouter(prefix="/reports", tags=["Отчёты"])
ExportFormat = Literal["pdf", "xlsx"]


def export_report(
    file_format: ExportFormat,
    title: str,
    filename_prefix: str,
    headers: tuple[str, ...],
    rows: list[tuple[object, ...]],
) -> Response:
    """Выбирает локальный формат выгрузки с единым именованием файла."""

    filename = f"{filename_prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    if file_format == "pdf":
        return build_pdf_response(title, filename, headers, rows)
    return build_xlsx_response(title, filename, headers, rows)


def load_stock_rows(db: Session) -> list[dict[str, object]]:
    """Загружает агрегированные остатки по каждому резерву."""

    records = db.execute(
        select(
            Reserve.id,
            Reserve.code,
            Reserve.name,
            func.sum(case((Part.status == "in_stock", 1), else_=0)).label("in_stock"),
            func.sum(case((Part.status == "installed", 1), else_=0)).label("installed"),
            func.sum(case((Part.status == "written_off", 1), else_=0)).label("written_off"),
            func.count(Part.id).label("total"),
        )
        .outerjoin(Part, and_(Part.reserve_id == Reserve.id, Part.is_deleted.is_(False)))
        .group_by(Reserve.id, Reserve.code, Reserve.name)
        .order_by(Reserve.name)
    ).all()
    return [
        {
            "id": row.id,
            "code": row.code,
            "name": row.name,
            "in_stock": int(row.in_stock or 0),
            "installed": int(row.installed or 0),
            "written_off": int(row.written_off or 0),
            "total": int(row.total or 0),
        }
        for row in records
    ]


def load_workstation_report(
    db: Session,
    workstation_id: int,
) -> tuple[Workstation, list[Part]]:
    """Возвращает выбранное АРМ и его действующий состав."""

    workstation = db.scalar(
        select(Workstation)
        .options(joinedload(Workstation.responsible))
        .where(Workstation.id == workstation_id, Workstation.is_deleted.is_(False))
    )
    if not workstation:
        raise HTTPException(status_code=404, detail="АРМ не найдено")
    parts = db.scalars(
        select(Part)
        .options(joinedload(Part.category), joinedload(Part.reserve))
        .where(
            Part.current_workstation_id == workstation.id,
            Part.status == "installed",
            Part.is_deleted.is_(False),
        )
        .order_by(Part.category_id, Part.name)
    ).all()
    return workstation, parts


def load_employee_rows(db: Session) -> list[dict[str, object]]:
    """Формирует список сотрудников вместе с состоянием учётной записи."""

    records = db.execute(
        select(Employee, User, Role)
        .outerjoin(User, User.employee_id == Employee.id)
        .outerjoin(Role, Role.id == User.role_id)
        .order_by(Employee.full_name)
    ).all()
    rows = []
    for employee, account, role in records:
        if not account:
            account_state = "Не создана"
        elif account.is_deleted:
            account_state = "Удалена"
        elif account.is_active:
            account_state = "Активна"
        else:
            account_state = "Заблокирована"
        rows.append(
            {
                "employee": employee,
                "username": account.username if account else None,
                "role": ROLE_LABELS.get(role.name, role.name) if role else None,
                "account_state": account_state,
                "employee_state": "Действует" if employee.is_active else "В архиве",
            }
        )
    return rows


def load_workstation_rows(db: Session) -> list[dict[str, object]]:
    """Формирует сводный список АРМ и число установленных комплектующих."""

    parts_count = (
        select(func.count(Part.id))
        .where(
            Part.current_workstation_id == Workstation.id,
            Part.status == "installed",
            Part.is_deleted.is_(False),
        )
        .correlate(Workstation)
        .scalar_subquery()
    )
    records = db.execute(
        select(Workstation, parts_count.label("parts_count"))
        .options(
            joinedload(Workstation.responsible),
            selectinload(Workstation.installed_parts).joinedload(Part.category),
        )
        .where(Workstation.is_deleted.is_(False))
        .order_by(Workstation.name)
    ).all()
    return [
        {
            "workstation": workstation,
            "parts_count": int(count or 0),
            "state": (
                "Действует"
                if workstation.is_active
                and not missing_required_components(
                    [
                        part
                        for part in workstation.installed_parts
                        if part.status == "installed" and not part.is_deleted
                    ]
                )
                else "Не действует"
            ),
        }
        for workstation, count in records
    ]


@router.get("", name="reports_index")
def reports_index(request: Request, user: User = Depends(get_current_user)):
    """Показывает доступные отчёты."""

    return render(request, "reports/index.html", user=user, page_title="Отчёты")


@router.get("/stocks", name="report_stocks")
def report_stocks(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Формирует остатки и распределение комплектующих по резервам."""

    return render(
        request,
        "reports/stocks.html",
        user=user,
        page_title="Остатки по резервам",
        rows=load_stock_rows(db),
    )


@router.get("/stocks/export/{file_format}", name="report_stocks_export")
def report_stocks_export(
    file_format: ExportFormat,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Выгружает остатки по резервам в PDF или XLSX."""

    rows = load_stock_rows(db)
    return export_report(
        file_format,
        "Остатки по резервам",
        "ostatki_po_rezervam",
        ("Код", "Резерв", "На складе", "Установлено", "Списано", "Всего"),
        [(row["code"], row["name"], row["in_stock"], row["installed"], row["written_off"], row["total"]) for row in rows],
    )


@router.get("/workstation", name="report_workstation")
def report_workstation(
    request: Request,
    workstation_id: int | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Формирует перечень комплектующих по выбранному АРМ."""

    workstations = db.scalars(
        select(Workstation)
        .where(Workstation.is_deleted.is_(False))
        .order_by(Workstation.name)
    ).all()
    workstation = None
    parts: list[Part] = []
    if workstation_id:
        workstation, parts = load_workstation_report(db, workstation_id)
    return render(
        request,
        "reports/workstation.html",
        user=user,
        page_title="Комплектующие по АРМ",
        workstations=workstations,
        workstation=workstation,
        parts=parts,
        selected_workstation=workstation_id,
    )


@router.get("/workstation/export/{file_format}", name="report_workstation_export")
def report_workstation_export(
    file_format: ExportFormat,
    workstation_id: int = Query(),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Выгружает состав выбранного АРМ в PDF или XLSX."""

    workstation, parts = load_workstation_report(db, workstation_id)
    return export_report(
        file_format,
        f"Комплектующие по АРМ «{workstation.name}»",
        f"sostav_arm_{workstation.id}",
        ("Категория", "Наименование", "Тип / модель", "Инвентарный номер", "Серийный номер", "Резерв"),
        [
            (
                part.category.name,
                part.name,
                part.part_type,
                part.inventory_number or "Не учитывается",
                part.serial_number,
                part.reserve.name,
            )
            for part in parts
        ],
    )


@router.get("/employees", name="report_employees")
def report_employees(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Показывает сводный список сотрудников."""

    return render(
        request,
        "reports/employees.html",
        user=user,
        page_title="Список сотрудников",
        rows=load_employee_rows(db),
    )


@router.get("/employees/export/{file_format}", name="report_employees_export")
def report_employees_export(
    file_format: ExportFormat,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Выгружает список сотрудников в PDF или XLSX."""

    rows = load_employee_rows(db)
    return export_report(
        file_format,
        "Список сотрудников",
        "spisok_sotrudnikov",
        ("ФИО", "Подразделение", "Должность", "Телефон", "Логин", "Роль", "Учётная запись", "Сотрудник"),
        [
            (
                row["employee"].full_name,
                row["employee"].department,
                row["employee"].position,
                row["employee"].phone,
                row["username"],
                row["role"],
                row["account_state"],
                row["employee_state"],
            )
            for row in rows
        ],
    )


@router.get("/workstations", name="report_workstations")
def report_workstations(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Показывает сводный список всех АРМ."""

    return render(
        request,
        "reports/workstations.html",
        user=user,
        page_title="Список АРМ",
        rows=load_workstation_rows(db),
    )


@router.get("/workstations/export/{file_format}", name="report_workstations_export")
def report_workstations_export(
    file_format: ExportFormat,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Выгружает список АРМ в PDF или XLSX."""

    rows = load_workstation_rows(db)
    return export_report(
        file_format,
        "Список АРМ",
        "spisok_arm",
        ("Наименование", "Кабинет", "Ответственный", "Подразделение", "Комплектующих", "Состояние"),
        [
            (
                row["workstation"].name,
                row["workstation"].cabinet,
                row["workstation"].responsible.full_name if row["workstation"].responsible else None,
                row["workstation"].responsible.department if row["workstation"].responsible else None,
                row["parts_count"],
                row["state"],
            )
            for row in rows
        ],
    )

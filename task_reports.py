"""Department task reports: minimal editable OOXML, no external runtime dependency."""
import json
import re
from datetime import date, datetime
from io import BytesIO
from typing import Optional
from zipfile import ZipFile, ZIP_DEFLATED
from xml.sax.saxutils import escape
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session, selectinload
import models
from database import get_db
from tasks import display_name, DEPARTMENTS, EVENTS

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def paragraph(value='', bold=False):
    # XML 1.0 rejects control characters even when escaped.
    value = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value))
    runs = []
    for i, line in enumerate(value.split('\n')):
        if i: runs.append('<w:r><w:br/></w:r>')
        runs.append('<w:r>' + ('<w:rPr><w:b/></w:rPr>' if bold else '') + '<w:t xml:space="preserve">' + escape(line) + '</w:t></w:r>')
    return '<w:p>' + ''.join(runs) + '</w:p>'


def completion_dates(db, tasks):
    ids = [task.id for task in tasks if task.completed]
    if not ids: return {}
    rows = db.query(models.TaskHistory).filter(models.TaskHistory.task_id.in_(ids)).order_by(models.TaskHistory.id).all()
    completed = {}
    for row in rows:
        before = json.loads(row.before_json) if row.before_json else {}
        after = json.loads(row.after_json)
        if after.get('completed') and not before.get('completed'):
            completed[row.task_id] = row.created_at
        elif not after.get('completed'):
            completed.pop(row.task_id, None)
    return completed


def local_datetime(value):
    # App deadline dates are interpreted in the organization's Europe/Moscow day.
    from datetime import timezone, timedelta
    return value.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=3)))


def task_description(task, finished):
    lines = [task.title, task.description or 'Без описания', 'Дедлайн: ' + (date.fromisoformat(task.deadline).strftime('%d.%m.%Y') if task.deadline else 'Без срока')]
    if task.completed:
        if finished:
            actual = local_datetime(finished)
            lines.append('Выполнена: ' + actual.strftime('%d.%m.%Y %H:%M'))
            late = actual.date().isoformat() > task.deadline if task.deadline else False
            lines.append(('Просрочен дедлайн: да' if late else 'Просрочен дедлайн: нет') if task.deadline else 'Просрочка: без дедлайна')
        else:
            lines.extend(['Выполнена: дата неизвестна', 'Просрочка: неизвестно' if task.deadline else 'Просрочка: без дедлайна'])
    else:
        lines.append('Не выполнена')
        lines.append('Просрочен дедлайн: ' + ('да' if task.deadline < local_datetime(datetime.utcnow()).date().isoformat() else 'нет') if task.deadline else 'Просрочка: без дедлайна')
    return '\n'.join(lines)


def build_department_report(db, card, department, event, tasks):
    people = db.query(models.User).join(models.UserDepartment).filter(models.UserDepartment.department_id == department.id).all()
    people.sort(key=lambda user: display_name(user).casefold())
    warned = {uid for uid, in db.query(models.MemberWarning.user_id).filter(models.MemberWarning.removed_at.is_(None)).distinct()}
    finished = completion_dates(db, tasks)
    body = paragraph('Отчёт', True) + paragraph('Отдел: ' + department.name, True) + paragraph('Карточка отдела: ' + card.title, True) + paragraph('Мероприятие: ' + (event.title if event else 'Общие таски отдела'), True)
    body += paragraph('1. Краткое описание того, что сделано', True) + paragraph() + paragraph('2. Какие ошибки были допущены', True) + paragraph() + paragraph('3. Список тасок по людям в отделе', True)
    rows = [['Человек в отделе', 'Таски: описание, дедлайн, выполнение и просрочка', 'Есть ли предупреждение', 'Итог']]
    for person in people:
        assigned = [task for task in tasks if any(link.user_id == person.id for link in task.assignee_links)]
        rows.append([display_name(person), '\n\n'.join(task_description(task, finished.get(task.id)) for task in assigned) or 'Нет тасок в выбранной карточке', 'Да' if person.id in warned else 'Нет', ''])
    borders = ''.join(f'<w:{side} w:val="single" w:sz="4" w:color="999999"/>' for side in ['top','left','bottom','right','insideH','insideV'])
    body += '<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/><w:tblBorders>'+borders+'</w:tblBorders></w:tblPr><w:tblGrid><w:gridCol w:w="2100"/><w:gridCol w:w="6000"/><w:gridCol w:w="1900"/><w:gridCol w:w="1500"/></w:tblGrid>'
    for index, row in enumerate(rows):
        body += '<w:tr>' + ('<w:trPr><w:tblHeader/></w:trPr>' if index == 0 else '')
        body += ''.join('<w:tc>'+paragraph(cell,index == 0)+'</w:tc>' for cell in row) + '</w:tr>'
    body += '</w:tbl><w:sectPr><w:pgSz w:w="16838" w:h="11906" w:orient="landscape"/><w:pgMar w:top="720" w:right="720" w:bottom="720" w:left="720"/></w:sectPr>'
    document = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="'+W+'"><w:body>'+body+'</w:body></w:document>'
    output=BytesIO()
    with ZipFile(output,'w',ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml','<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr('_rels/.rels','<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr('word/document.xml',document)
    return output.getvalue()


def create_report_router(*, get_current_user, has_access_to_card, can_view_warnings):
    router=APIRouter(prefix='/api/cards',tags=['Task reports'])

    @router.get('/{card_id}/task-report')
    def report(card_id: int, department_id: int, event_card_id: Optional[int] = None,
               user=Depends(get_current_user), db: Session=Depends(get_db)):
        if not can_view_warnings(user.position_ref):
            raise HTTPException(403,'Нет доступа к отчёту с предупреждениями')
        card=db.get(models.Card,card_id)
        if not card or not has_access_to_card(user,card):
            raise HTTPException(404,'Карточка не найдена или недоступна')
        if card.category.name != DEPARTMENTS:
            raise HTTPException(422,'Отчёт доступен только в карточке отдела')
        department=db.get(models.Department,department_id)
        if not department:raise HTTPException(404,'Отдел не найден')
        event=db.get(models.Card,event_card_id) if event_card_id is not None else None
        if event_card_id is not None and (not event or not has_access_to_card(user,event)):
            raise HTTPException(404,'Мероприятие не найдено или недоступно')
        if event and event.category.name != EVENTS:raise HTTPException(422,'Выберите мероприятие')
        tasks=db.query(models.Task).options(selectinload(models.Task.assignee_links),selectinload(models.Task.department_links).selectinload(models.TaskDepartment.card),selectinload(models.Task.event_card)).filter(models.Task.department_links.any(models.TaskDepartment.card_id==card_id),models.Task.event_card_id==event_card_id).order_by(models.Task.id).all()
        tasks=[task for task in tasks if all(has_access_to_card(user,link.card) for link in task.department_links) and (task.event_card is None or has_access_to_card(user,task.event_card))]
        filename='Отчёт - '+department.name+' - '+(event.title if event else 'Общие таски')+'.docx'
        return Response(build_department_report(db,card,department,event,tasks),media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',headers={'Content-Disposition':"attachment; filename=report.docx; filename*=UTF-8''"+quote(filename,safe=''),'Cache-Control':'no-store'})

    return router

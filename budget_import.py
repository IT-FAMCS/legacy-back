"""Bounded public Google Sheets preview and atomic, durable import."""
import csv
import hashlib
import html
import io
import json
import re
import secrets
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit, quote
from urllib.request import build_opener, HTTPRedirectHandler, Request
from urllib.error import URLError
from typing import Literal, Optional
from fastapi import Depends, HTTPException
from pydantic import Field, field_validator
from sqlalchemy.orm import Session
from database import get_db
from tasks import begin_task_write
from budget import Input, money, now
from budget_custody import validate_treasurer
import models

LIMIT = 4_000_000
MAX_ROWS = 5000
MONTHS = ['январ','феврал','март','апрел','май','июн','июл','август','сентябр','октябр','ноябр','декабр']


def spreadsheet_id(url):
    parsed=urlsplit(url)
    match=re.fullmatch(r'/spreadsheets/d/([A-Za-z0-9_-]{10,150})(?:/.*)?',parsed.path)
    if parsed.scheme != 'https' or parsed.netloc != 'docs.google.com' or not match:
        raise ValueError('Укажите HTTPS-ссылку Google Sheets')
    return match[1]


class PreviewInput(Input):
    url: str = Field(max_length=1000)
    account: Literal['cash','card']
    treasurer_id: Optional[int] = Field(default=None, gt=0)
    fallback_month: Optional[str] = Field(default=None, pattern=r'^20[0-9]{2}-(0[1-9]|1[0-2])$')

    @field_validator('url')
    @classmethod
    def check_url(cls, value):
        spreadsheet_id(value)
        return value


class GoogleRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed=urlsplit(newurl)
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None,443) or not (parsed.hostname == 'docs.google.com' or (parsed.hostname or '').endswith('.googleusercontent.com')):
            raise ValueError('Недопустимое перенаправление Google Sheets')
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def read_google(url):
    try:
        with build_opener(GoogleRedirects()).open(Request(url,headers={'User-Agent':'Legacy Budget Import'}),timeout=15) as response:
            data=response.read(LIMIT+1)
            if len(data)>LIMIT: raise ValueError('Лист слишком большой')
            text=data.decode('utf-8-sig')
            if '<html' in text[:300].lower() and '/edit' not in url:
                raise ValueError('Таблица недоступна. Откройте доступ по ссылке для чтения')
            return text
    except (URLError, TimeoutError, UnicodeError) as exc:
        raise ValueError('Не удалось прочитать публичную Google-таблицу') from exc


def fetch_spreadsheet(url):
    sid=spreadsheet_id(url)
    page=read_google(f'https://docs.google.com/spreadsheets/d/{sid}/edit')
    titles=[html.unescape(title) for title in re.findall(r'class="[^"<>]*docs-sheet-tab-caption[^"<>]*">([^<]+)</div>',page)]
    if not titles or len(titles)>24: raise ValueError('Не удалось определить листы таблицы (до 24 листов)')
    # The initial data contains stable sheet IDs and titles in escaped JSON.
    decoded=page.replace('\\\"','"')
    sheets=[]
    for title in dict.fromkeys(titles):
        pattern=r'"([0-9]+)",\[\{"1":\[\[0,0,"'+re.escape(title)+'"'
        match=re.search(pattern,decoded)
        if not match: raise ValueError('Не удалось определить ID листа '+title)
        gid=match[1]
        content=read_google(f'https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid={gid}')
        sheets.append(dict(title=title,gid=gid,csv=content))
    return sid,sheets


def month_date(title, fallback):
    text=title.lower().replace('ё','е')
    for month, name in enumerate(MONTHS,1):
        if name in text:
            year=re.search(r'(?<![0-9])(20[0-9]{2}|[0-9]{2})(?![0-9])',text)
            if year:return datetime(2000+int(year[1]) if len(year[1])==2 else int(year[1]),month,1)
    if fallback:return datetime.strptime(fallback,'%Y-%m')
    return None


def amount(value):
    value=re.sub(r'[ \u00a0\u202f]','',value).replace(',','.')
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,2})?',value):raise ValueError('Некорректная сумма: '+value)
    number=Decimal(value)
    if number>Decimal('9999999999.99'):raise ValueError('Сумма превышает допустимую')
    return number


def parse_book(sid,sheets,account,fallback,treasurer_id):
    rows=[]; skipped=[]
    for sheet in sheets:
        if sheet['title'].strip().lower() == 'main':
            skipped.append(sheet['title']);continue
        data=list(csv.reader(io.StringIO(sheet['csv'])))
        header=None
        for index,cells in enumerate(data[:50]):
            names=[cell.strip().lower().lstrip('\ufeff') for cell in cells]
            income=next((i for i,n in enumerate(names) if n=='доход'),None)
            expense=next((i for i,n in enumerate(names) if n=='расход'),None)
            description=next((i for i,n in enumerate(names) if n.startswith('статья')),None)
            if income is not None and expense is not None and description is not None:
                header=(index,description,income,expense,next((i for i,n in enumerate(names) if n=='дата'),None));break
        if header is None:skipped.append(sheet['title']);continue
        start,desc,inc,exp,datecol=header
        default=month_date(sheet['title'],fallback)
        for index,cells in enumerate(data[start+1:],start+2):
            def cell(i):return cells[i].strip() if i is not None and i<len(cells) else ''
            description=cell(desc)
            if not description:
                if any(cell(c) and amount(cell(c)) != 0 for c in (inc,exp)):
                    raise ValueError(f"Лист {sheet['title']}, строка {index}: отсутствует описание")
                continue
            if re.match(r'^(итог[ои]?|всего|остаток|сальдо|сумма)(?:[\s:.,]|$)',description,re.I):continue
            if not cell(inc) and not cell(exp):continue
            if cell(inc).lower()=='доход' and cell(exp).lower()=='расход':continue
            if len(description)>2000:raise ValueError('Слишком длинное описание')
            explicit=cell(datecol)
            stamp=None
            if explicit:
                for fmt in ('%d.%m.%Y','%Y-%m-%d','%d/%m/%Y'):
                    try:stamp=datetime.strptime(explicit,fmt);break
                    except ValueError:pass
                if stamp is None:raise ValueError('Некорректная дата: '+explicit)
            elif default:stamp=default
            else:raise ValueError('Для листа '+sheet['title']+' укажите месяц без даты')
            if cell(inc) and cell(exp) and amount(cell(inc)) != 0 and amount(cell(exp)) != 0:
                raise ValueError(f"Лист {sheet['title']}, строка {index}: одновременно доход и расход")
            for direction,column in [('income',inc),('expense',exp)]:
                if not cell(column):continue
                number=amount(cell(column))
                if number==0:continue
                key=hashlib.sha256(f"{sid}:{sheet['gid']}:{index}".encode()).hexdigest()
                record=dict(source_key=key,sheet=sheet['title'],source_row=index,direction=direction,account=account,
                            treasurer_id=treasurer_id,description=description,amount=money(number),operation_at=(stamp-timedelta(hours=3)).isoformat(),approximate_date=not bool(explicit))
                record['fingerprint']=hashlib.sha256(json.dumps({k:record[k] for k in ('direction','description','amount','operation_at')},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
                rows.append(record)
                if len(rows)>MAX_ROWS:raise ValueError('Слишком много операций (максимум 5000)')
    if not rows:raise ValueError('Не найдено операций со столбцами Статья, Доход, Расход')
    return rows,skipped


def source_status(db,row):
    previous=db.get(models.BudgetImportSource,row['source_key'])
    return 'new' if previous is None else ('skipped' if previous.fingerprint==row['fingerprint'] else 'conflict')


def install_import_routes(router,get_current_user):
    def admin(user=Depends(get_current_user)):
        if user.position_ref.name.strip().lower() not in ('admin','админ'):raise HTTPException(403,'Импорт доступен только администратору')
        return user

    @router.post('/api/budget/imports/preview')
    def preview(data:PreviewInput,user=Depends(admin),db:Session=Depends(get_db)):
        validate_treasurer(db,data.treasurer_id)
        try:
            sid,sheets=fetch_spreadsheet(data.url)
            rows,skipped=parse_book(sid,sheets,data.account,data.fallback_month,data.treasurer_id)
        except (ValueError,csv.Error,InvalidOperation) as exc:raise HTTPException(422,str(exc))
        for row in rows:row['status']=source_status(db,row)
        result=dict(id=secrets.token_hex(24),rows=rows,skipped_sheets=skipped,
                    new_count=sum(r['status']=='new' for r in rows),skipped_count=sum(r['status']=='skipped' for r in rows),conflict_count=sum(r['status']=='conflict' for r in rows))
        for direction in ('income','expense'):
            result[direction+'_total']=money(sum((Decimal(r['amount']) for r in rows if r['direction']==direction and r['status']=='new'),Decimal(0)))
        db.add(models.BudgetImportBatch(id=result['id'],created_by=user.id,payload=json.dumps(result,ensure_ascii=False),expires_at=now()+timedelta(minutes=30)))
        db.commit()
        return result

    @router.post('/api/budget/imports/{bid}/commit')
    def commit(bid:str,user=Depends(admin),db:Session=Depends(get_db)):
        begin_task_write(db)
        batch=db.get(models.BudgetImportBatch,bid)
        if batch is None or batch.created_by!=user.id:raise HTTPException(404,'Предпросмотр не найден')
        if batch.result:return json.loads(batch.result)
        if batch.expires_at<now():raise HTTPException(410,'Предпросмотр устарел. Прочитайте таблицу заново')
        payload=json.loads(batch.payload)
        if any(source_status(db,row)=='conflict' for row in payload['rows']):raise HTTPException(409,'Ранее импортированная строка изменилась. Исправьте операцию вручную')
        count=0
        for row in payload['rows']:
            if source_status(db,row)!='new':continue
            validate_treasurer(db,row['treasurer_id'])
            entry=models.BudgetEntry(**{k:row[k] for k in ('direction','account','description','amount','treasurer_id')},operation_at=datetime.fromisoformat(row['operation_at']),created_by=user.id,updated_by=user.id)
            db.add(entry);db.flush()
            db.add(models.BudgetImportSource(source_key=row['source_key'],fingerprint=row['fingerprint'],entry_id=entry.id));db.flush()
            count+=1
        result=dict(created_count=count)
        batch.result=json.dumps(result);db.commit()
        return result

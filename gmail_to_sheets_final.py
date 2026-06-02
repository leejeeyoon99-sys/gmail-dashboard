#!/usr/bin/env python3
"""Gmail 받은편지함 → Google Sheets 자동 정리"""
import subprocess, json, sys, re, os
from datetime import datetime, timedelta, timezone, date
from collections import Counter
from pathlib import Path

# Google API
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# ── 설정 ────────────────────────────────────────────────────────────────
BASE_DIR       = Path(__file__).parent
CLIENT_SECRET  = BASE_DIR / "client_secret_.json"
TOKEN_FILE     = BASE_DIR / "token.json"
GWS_EXE        = Path(r"C:\Users\User\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\npm\node_modules\@googleworkspace\cli\bin\gws.exe")

SCOPES   = ["https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/gmail.readonly"]
DAYS     = 7
TAB_NAME = "메일로그"

# ── Google 인증 ─────────────────────────────────────────────────────────
def get_credentials():
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    return creds

# ── gws CLI 호출 ────────────────────────────────────────────────────────
def gws(*args):
    cmd = [str(GWS_EXE)] + list(args)
    r = subprocess.run(cmd, capture_output=True)
    raw = r.stdout.decode("utf-8", errors="replace").strip()
    try:
        return json.loads(raw)
    except Exception:
        return {}

# ── 분류 헬퍼 ──────────────────────────────────────────────────────────
def classify_type(text):
    t = text.lower()
    if re.search(r"환불|refund|취소|cancel", t):            return "환불"
    if re.search(r"주문|order|결제|payment|invoice", t):    return "주문"
    if re.search(r"문의|inquiry|question|help|support", t): return "문의"
    if re.search(r"협업|collaborat|partner|제안|meeting", t):return "협업"
    if re.search(r"보안|security|verify|인증|login|sudo", t):return "보안알림"
    if re.search(r"welcome|가입|시작|getting started", t):  return "서비스가입"
    return "기타"

def classify_urgency(text):
    t = text.lower()
    if re.search(r"긴급|urgent|asap|즉시|sudo|verify|인증", t): return "상"
    if re.search(r"중요|important|deadline|마감|today", t):      return "중"
    return "하"

def classify_sentiment(text):
    t = text.lower()
    if re.search(r"오류|error|fail|실패|거절|denied|취소|cancel|경고|warning", t): return "부정"
    if re.search(r"환영|감사|축하|welcome|congrat|success|완료|승인|approved", t): return "긍정"
    return "중립"

def calc_score(urgency, sentiment, labels, mail_type):
    s = 5
    if urgency == "상":   s += 3
    elif urgency == "중": s += 1
    if sentiment == "부정": s += 2
    if "UNREAD" in labels: s += 1
    if mail_type in ("문의", "협업", "환불"): s += 1
    if mail_type == "서비스가입": s -= 2
    return max(1, min(10, s))

def parse_date(raw):
    try:
        raw = re.sub(r"\s*\(.*?\)", "", raw).strip()
        for fmt in ["%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                    "%d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M %z"]:
            try:
                dt = datetime.strptime(raw, fmt)
                return dt.astimezone(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
            except: pass
    except: pass
    return raw[:16] if len(raw) >= 16 else raw

# ── 메인 ────────────────────────────────────────────────────────────────
def main():
    since = (date.today() - timedelta(days=DAYS)).strftime("%Y/%m/%d")

    # 1. Gmail 데이터 수집 (gws CLI)
    print(f"[1/5] 최근 {DAYS}일({since} 이후) 받은편지함 수집 중...")
    inbox = gws("gmail", "+triage", "--query", f"in:inbox after:{since}", "--max", "200", "--format", "json")
    msgs = inbox.get("messages", [])
    print(f"    → {len(msgs)}개 메시지 발견")

    print("[2/5] 발신함 조회 중...")
    sent_data = gws("gmail", "+triage", "--query", f"in:sent after:{since}", "--max", "200", "--format", "json")
    sent_ids = {m["id"] for m in sent_data.get("messages", [])}

    print("[3/5] 각 스레드 분류 중...")
    rows = []
    for msg in msgs:
        tid = msg["id"]
        detail = gws("gmail", "users", "threads", "get", "--params",
                     f'{{"userId":"me","id":"{tid}","format":"metadata","metadataHeaders":["From","Subject","Date"]}}')
        messages = detail.get("messages", [])
        if not messages:
            continue
        first   = messages[0]
        hdrs    = {h["name"]: h["value"] for h in first.get("payload", {}).get("headers", [])}
        labels  = first.get("labelIds", [])
        snippet = first.get("snippet", "")[:80]
        sender  = hdrs.get("From", msg.get("from", ""))
        subject = hdrs.get("Subject", msg.get("subject", ""))
        date_str = parse_date(hdrs.get("Date", msg.get("date", "")))

        combined  = f"{sender} {subject} {snippet}"
        mail_type = classify_type(combined)
        urgency   = classify_urgency(f"{subject} {snippet}")
        sentiment = classify_sentiment(snippet)
        score     = calc_score(urgency, sentiment, labels, mail_type)
        reply     = "회신완료" if tid in sent_ids else "미회신"
        url       = f"https://mail.google.com/mail/u/0/#inbox/{tid}"

        rows.append([date_str, sender, subject, snippet, mail_type, urgency, sentiment, score, reply, url])
        print(f"    ✓ [{date_str}] {subject[:45]}")

    rows.sort(key=lambda r: r[0], reverse=True)

    # 4. Google Sheets API로 직접 작성
    print("[4/5] Google Sheets 생성 및 데이터 입력 중...")
    creds = get_credentials()
    svc = build("sheets", "v4", credentials=creds)
    ss = svc.spreadsheets()

    created = ss.create(body={
        "properties": {"title": "Gmail 메일 로그"},
        "sheets": [{"properties": {"title": TAB_NAME}}]
    }).execute()
    sid   = created["spreadsheetId"]
    sh_id = created["sheets"][0]["properties"]["sheetId"]
    print(f"    → https://docs.google.com/spreadsheets/d/{sid}")

    header = ["수신일시","발신자","제목","내용 요약","유형","긴급도","감정","중요도","미회신 여부","Gmail 링크"]
    ss.values().update(
        spreadsheetId=sid,
        range=f"{TAB_NAME}!A1",
        valueInputOption="USER_ENTERED",
        body={"values": [header] + rows}
    ).execute()

    # 5. 서식 적용
    print("[5/5] 서식 적용 중...")
    nr = len(rows)
    requests = [
        {"repeatCell": {
            "range": {"sheetId": sh_id, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": {
                "backgroundColor": {"red": 0.2, "green": 0.44, "blue": 0.76},
                "textFormat": {"bold": True, "foregroundColor": {"red":1,"green":1,"blue":1}},
                "horizontalAlignment": "CENTER"
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)"
        }},
        {"updateSheetProperties": {
            "properties": {"sheetId": sh_id, "gridProperties": {"frozenRowCount":1, "frozenColumnCount":1}},
            "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount"
        }},
        {"setBasicFilter": {"filter": {"range": {
            "sheetId": sh_id, "startRowIndex": 0, "endRowIndex": nr+1,
            "startColumnIndex": 0, "endColumnIndex": 10
        }}}},
        {"addConditionalFormatRule": {"rule": {
            "ranges": [{"sheetId": sh_id, "startRowIndex":1, "endRowIndex":nr+1, "startColumnIndex":0, "endColumnIndex":10}],
            "booleanRule": {"condition": {"type":"TEXT_EQ","values":[{"userEnteredValue":"상"}]},
                            "format": {"backgroundColor": {"red":1.0,"green":0.8,"blue":0.8}}}
        }, "index": 0}},
        {"addConditionalFormatRule": {"rule": {
            "ranges": [{"sheetId": sh_id, "startRowIndex":1, "endRowIndex":nr+1, "startColumnIndex":0, "endColumnIndex":10}],
            "booleanRule": {"condition": {"type":"TEXT_EQ","values":[{"userEnteredValue":"미회신"}]},
                            "format": {"backgroundColor": {"red":1.0,"green":0.98,"blue":0.8}}}
        }, "index": 1}},
        {"addConditionalFormatRule": {"rule": {
            "ranges": [{"sheetId": sh_id, "startRowIndex":1, "endRowIndex":nr+1, "startColumnIndex":7, "endColumnIndex":8}],
            "booleanRule": {"condition": {"type":"NUMBER_GREATER_THAN_EQ","values":[{"userEnteredValue":"8"}]},
                            "format": {"textFormat": {"bold": True}}}
        }, "index": 2}},
        {"autoResizeDimensions": {"dimensions": {
            "sheetId": sh_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 10
        }}}
    ]
    ss.batchUpdate(spreadsheetId=sid, body={"requests": requests}).execute()

    # 완료 보고
    print(f"\n{'='*55}")
    print("[완료 보고]")
    print(f"{'='*55}")
    print(f"시트 URL : https://docs.google.com/spreadsheets/d/{sid}")
    print(f"총 스레드 : {len(rows)}건")
    type_cnt = Counter(r[4] for r in rows)
    print("\n[유형별 건수]")
    for k, v in type_cnt.most_common():
        print(f"  {k}: {v}건")
    uc = sum(1 for r in rows if r[8] == "미회신")
    hc = sum(1 for r in rows if isinstance(r[7], int) and r[7] >= 8)
    print(f"\n[미회신 건수]  {uc}건")
    print(f"[중요도 8이상] {hc}건")
    print(f"{'='*55}")

if __name__ == "__main__":
    main()

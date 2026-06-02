function parseCookies(req) {
  return Object.fromEntries(
    (req.headers.cookie || '').split(';').map(c => c.trim().split('=').map(decodeURIComponent))
  );
}

function classifyType(text) {
  const t = text.toLowerCase();
  if (/환불|refund|취소|cancel/.test(t))              return '환불';
  if (/주문|order|결제|payment|invoice/.test(t))       return '주문';
  if (/문의|inquiry|question|help|support/.test(t))    return '문의';
  if (/협업|collaborat|partner|제안|meeting/.test(t))  return '협업';
  if (/보안|security|verify|인증|login|sudo/.test(t))  return '보안알림';
  if (/welcome|가입|시작|getting started/.test(t))     return '서비스가입';
  return '기타';
}
function classifyUrgency(text) {
  const t = text.toLowerCase();
  if (/긴급|urgent|asap|즉시|sudo|verify|인증/.test(t)) return '상';
  if (/중요|important|deadline|마감|today/.test(t))     return '중';
  return '하';
}
function classifySentiment(text) {
  const t = text.toLowerCase();
  if (/오류|error|fail|실패|거절|denied|취소|cancel|경고|warning/.test(t)) return '부정';
  if (/환영|감사|축하|welcome|congrat|success|완료|승인|approved/.test(t))  return '긍정';
  return '중립';
}
function calcScore(urg, sent, labels, type) {
  let s = 5;
  if (urg === '상') s += 3; else if (urg === '중') s += 1;
  if (sent === '부정') s += 2;
  if (labels.includes('UNREAD')) s += 1;
  if (['문의','협업','환불'].includes(type)) s += 1;
  if (type === '서비스가입') s -= 2;
  return Math.max(1, Math.min(10, s));
}

async function getAccessToken(cookies) {
  // refresh_token으로 새 access_token 발급
  if (!cookies.refresh_token) return cookies.access_token;
  const r = await fetch('https://oauth2.googleapis.com/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      client_id: process.env.GOOGLE_CLIENT_ID,
      client_secret: process.env.GOOGLE_CLIENT_SECRET,
      refresh_token: cookies.refresh_token,
      grant_type: 'refresh_token',
    }),
  });
  const data = await r.json();
  return data.access_token || cookies.access_token;
}

export default async function handler(req, res) {
  const cookies = parseCookies(req);
  if (!cookies.access_token && !cookies.refresh_token) {
    return res.status(401).json({ error: 'unauthorized' });
  }

  const token = await getAccessToken(cookies);
  const headers = { Authorization: `Bearer ${token}` };
  const since = Math.floor((Date.now() - 7 * 86400000) / 1000);

  // 받은편지함 스레드 목록
  const listRes = await fetch(
    `https://gmail.googleapis.com/gmail/v1/users/me/threads?q=in:inbox after:${since}&maxResults=50`,
    { headers }
  );
  const listData = await listRes.json();
  const threads = listData.threads || [];

  // 발신함 ID 목록
  const sentRes = await fetch(
    `https://gmail.googleapis.com/gmail/v1/threads?q=in:sent after:${since}&maxResults=100`,
    { headers }
  );
  const sentData = await sentRes.json();
  const sentIds = new Set((sentData.threads || []).map(t => t.id));

  // 각 스레드 상세 조회
  const rows = await Promise.all(threads.map(async t => {
    const r = await fetch(
      `https://gmail.googleapis.com/gmail/v1/users/me/threads/${t.id}?format=metadata&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date`,
      { headers }
    );
    const d = await r.json();
    const msg = d.messages?.[0];
    if (!msg) return null;

    const hdrs = Object.fromEntries((msg.payload?.headers || []).map(h => [h.name, h.value]));
    const labels = msg.labelIds || [];
    const snippet = (msg.snippet || '').substring(0, 80);
    const from = hdrs.From || '';
    const subject = hdrs.Subject || '';
    const date = hdrs.Date || '';

    const combined = `${from} ${subject} ${snippet}`;
    const type = classifyType(combined);
    const urgency = classifyUrgency(`${subject} ${snippet}`);
    const sentiment = classifySentiment(snippet);
    const score = calcScore(urgency, sentiment, labels, type);
    const reply = sentIds.has(t.id) ? '회신완료' : '미회신';

    // 날짜 파싱
    let dateStr = date;
    try {
      const dt = new Date(date);
      dateStr = dt.toLocaleString('ko-KR', { timeZone: 'Asia/Seoul', year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' });
    } catch {}

    return {
      date: dateStr, from, subject, summary: snippet,
      type, urgency, sentiment, score, reply,
      link: `https://mail.google.com/mail/u/0/#inbox/${t.id}`
    };
  }));

  const filtered = rows.filter(Boolean).sort((a, b) => b.date.localeCompare(a.date));
  res.json({ mails: filtered });
}

export default async function handler(req, res) {
  const { code } = req.query;
  if (!code) return res.status(400).send('No code');

  const baseUrl = process.env.BASE_URL || `https://${process.env.VERCEL_URL}`;

  // 코드 → 토큰 교환
  const tokenRes = await fetch('https://oauth2.googleapis.com/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      code,
      client_id: process.env.GOOGLE_CLIENT_ID,
      client_secret: process.env.GOOGLE_CLIENT_SECRET,
      redirect_uri: `${baseUrl}/api/callback`,
      grant_type: 'authorization_code',
    }),
  });

  const token = await tokenRes.json();
  if (!token.access_token) return res.status(400).json(token);

  // 쿠키에 저장 (1시간)
  res.setHeader('Set-Cookie', [
    `access_token=${token.access_token}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=3600`,
    `refresh_token=${token.refresh_token || ''}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=2592000`,
  ]);

  res.redirect('/gmail_dashboard.html');
}

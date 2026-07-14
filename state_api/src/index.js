const encoder = new TextEncoder();
const USERNAME_RE = /^[A-Za-z][A-Za-z0-9_]{3,31}$/;
const DEFAULT_ORIGIN = 'https://slavagostev2-betboom-monitor.pages.dev';
const RAW_BASE = 'https://raw.githubusercontent.com/slavagostev2-dot/betboom-wheel-monitor/main/';

function cors(request, env) {
  const allowed = String(env.APP_ORIGIN || DEFAULT_ORIGIN).replace(/\/$/, '');
  const origin = request.headers.get('Origin') || '';
  return {
    'Access-Control-Allow-Origin': origin === allowed ? origin : allowed,
    'Access-Control-Allow-Methods': 'GET,POST,PUT,OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type,X-Telegram-Init-Data,Authorization',
    'Access-Control-Max-Age': '86400',
    Vary: 'Origin',
  };
}

function json(request, env, value, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: {
      ...cors(request, env),
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff',
    },
  });
}

function failure(request, env, status, message) {
  return json(request, env, { ok: false, error: message }, status);
}

async function bodyJson(request) {
  try {
    const value = await request.json();
    return value && typeof value === 'object' ? value : {};
  } catch {
    return {};
  }
}

function nowIso() {
  return new Date().toISOString();
}

function parseStored(value, fallback = {}) {
  try {
    return JSON.parse(String(value));
  } catch {
    return fallback;
  }
}

function safeObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
}

function hex(bytes) {
  return [...new Uint8Array(bytes)].map(value => value.toString(16).padStart(2, '0')).join('');
}

function constantTimeEqual(left, right) {
  const a = encoder.encode(String(left || ''));
  const b = encoder.encode(String(right || ''));
  if (a.length !== b.length) return false;
  let result = 0;
  for (let index = 0; index < a.length; index += 1) result |= a[index] ^ b[index];
  return result === 0;
}

async function hmac(keyBytes, value) {
  const key = await crypto.subtle.importKey(
    'raw',
    keyBytes,
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  return crypto.subtle.sign('HMAC', key, encoder.encode(value));
}

async function validateInitData(initData, env) {
  const token = String(env.BOT_TOKEN || '');
  if (!token || !initData) throw new Error('Telegram authentication is unavailable');
  const params = new URLSearchParams(initData);
  const receivedHash = params.get('hash') || '';
  if (!receivedHash) throw new Error('Telegram hash is missing');
  params.delete('hash');
  const dataCheckString = [...params.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, value]) => `${key}=${value}`)
    .join('\n');
  const secret = await hmac(encoder.encode('WebAppData'), token);
  const expectedHash = hex(await hmac(new Uint8Array(secret), dataCheckString));
  if (!constantTimeEqual(expectedHash, receivedHash)) throw new Error('Telegram signature is invalid');
  const authDate = Number(params.get('auth_date') || 0);
  const current = Math.floor(Date.now() / 1000);
  const maxAge = Math.max(60, Number(env.TMA_MAX_AGE_SECONDS || 86400));
  if (!authDate || authDate > current + 60 || current - authDate > maxAge) {
    throw new Error('Telegram authentication data is expired');
  }
  const user = parseStored(params.get('user'), null);
  if (!user || typeof user !== 'object' || !user.id) throw new Error('Telegram user is missing');
  return user;
}

async function miniAppUser(request, env) {
  return validateInitData(request.headers.get('X-Telegram-Init-Data') || '', env);
}

function internalAuthorized(request, env) {
  const header = request.headers.get('Authorization') || '';
  const supplied = header.startsWith('Bearer ') ? header.slice(7) : '';
  const expected = String(env.STATE_API_TOKEN || env.BOT_TOKEN || '');
  return Boolean(expected && supplied && constantTimeEqual(supplied, expected));
}

async function upsertUser(env, user) {
  const id = String(user.id);
  const current = nowIso();
  await env.DB.prepare(`
    INSERT INTO users
      (id,chat_id,username,first_name,last_name,photo_url,first_seen_at,last_seen_at,blocked)
    VALUES (?,?,?,?,?,?,?,?,0)
    ON CONFLICT(id) DO UPDATE SET
      chat_id=excluded.chat_id,
      username=excluded.username,
      first_name=excluded.first_name,
      last_name=excluded.last_name,
      photo_url=excluded.photo_url,
      last_seen_at=excluded.last_seen_at
  `).bind(
    id,
    id,
    String(user.username || ''),
    String(user.first_name || ''),
    String(user.last_name || ''),
    String(user.photo_url || ''),
    current,
    current,
  ).run();
  await env.DB.prepare(`
    INSERT INTO roles (user_id,role) VALUES (?,'user')
    ON CONFLICT(user_id) DO NOTHING
  `).bind(id).run();
  return id;
}

async function readUserState(env, userId) {
  const [participation, hidden, settings] = await Promise.all([
    env.DB.prepare(`
      SELECT wheel_key,joined_at,active FROM wheel_participation WHERE user_id=?
    `).bind(userId).all(),
    env.DB.prepare(`
      SELECT wheel_key FROM hidden_wheels
      WHERE user_id=? AND active=1 AND (expires_at IS NULL OR expires_at>?)
    `).bind(userId, nowIso()).all(),
    env.DB.prepare(`
      SELECT setting_key,setting_value FROM user_settings WHERE user_id=?
    `).bind(userId).all(),
  ]);
  const joined = [];
  const participationHistory = [];
  for (const row of participation.results || []) {
    participationHistory.push(String(row.wheel_key));
    if (Number(row.active)) joined.push(String(row.wheel_key));
  }
  const resultSettings = {};
  for (const row of settings.results || []) {
    resultSettings[String(row.setting_key)] = parseStored(row.setting_value, row.setting_value);
  }
  return {
    joined,
    participationHistory,
    hiddenWheels: (hidden.results || []).map(row => String(row.wheel_key)),
    settings: resultSettings,
  };
}

async function session(request, env) {
  const user = await miniAppUser(request, env);
  const userId = await upsertUser(env, user);
  return json(request, env, { ok: true, user, state: await readUserState(env, userId) });
}

async function participation(request, env) {
  const user = await miniAppUser(request, env);
  const userId = await upsertUser(env, user);
  const body = await bodyJson(request);
  const wheelKey = String(body.wheel_key || '').trim().toLowerCase();
  if (!wheelKey || wheelKey.length > 160) return failure(request, env, 400, 'Invalid wheel key');
  const joined = body.joined !== false;
  const joinedAt = nowIso();
  const record = { joined_at: joinedAt };
  await env.DB.prepare(`
    INSERT INTO wheel_participation (user_id,wheel_key,joined_at,active,data_json)
    VALUES (?,?,?,?,?)
    ON CONFLICT(user_id,wheel_key) DO UPDATE SET
      active=excluded.active,
      joined_at=CASE WHEN excluded.active=1 THEN excluded.joined_at ELSE wheel_participation.joined_at END,
      data_json=CASE WHEN excluded.active=1 THEN excluded.data_json ELSE wheel_participation.data_json END
  `).bind(userId, wheelKey, joinedAt, joined ? 1 : 0, JSON.stringify(record)).run();
  return json(request, env, { ok: true, wheel_key: wheelKey, joined });
}

async function hiddenWheel(request, env) {
  const user = await miniAppUser(request, env);
  const userId = await upsertUser(env, user);
  const body = await bodyJson(request);
  const wheelKey = String(body.wheel_key || '').trim().toLowerCase();
  if (!wheelKey || wheelKey.length > 160) return failure(request, env, 400, 'Invalid wheel key');
  const hidden = body.hidden !== false;
  const hiddenAt = nowIso();
  const expiresAt = new Date(Date.now() + 30 * 86400 * 1000).toISOString();
  const record = { hidden_at: hiddenAt, expires_at: expiresAt };
  await env.DB.prepare(`
    INSERT INTO hidden_wheels (user_id,wheel_key,hidden_at,expires_at,active,data_json)
    VALUES (?,?,?,?,?,?)
    ON CONFLICT(user_id,wheel_key) DO UPDATE SET
      hidden_at=excluded.hidden_at,
      expires_at=excluded.expires_at,
      active=excluded.active,
      data_json=excluded.data_json
  `).bind(userId, wheelKey, hiddenAt, expiresAt, hidden ? 1 : 0, JSON.stringify(record)).run();
  return json(request, env, { ok: true, wheel_key: wheelKey, hidden });
}

async function userSettings(request, env) {
  const user = await miniAppUser(request, env);
  const userId = await upsertUser(env, user);
  const body = await bodyJson(request);
  const allowed = new Set(['autoRefresh', 'haptics', 'lightTheme', 'themeVersion']);
  const statements = [];
  for (const [key, value] of Object.entries(safeObject(body.settings))) {
    if (!allowed.has(key)) continue;
    statements.push(env.DB.prepare(`
      INSERT INTO user_settings (user_id,setting_key,setting_value) VALUES (?,?,?)
      ON CONFLICT(user_id,setting_key) DO UPDATE SET setting_value=excluded.setting_value
    `).bind(userId, key, JSON.stringify(value)));
  }
  if (statements.length) await env.DB.batch(statements);
  return json(request, env, { ok: true });
}

async function knownSource(source) {
  try {
    const responses = await Promise.all([
      fetch(`${RAW_BASE}public_sources.txt?t=${Date.now()}`, { cf: { cacheTtl: 60 } }),
      fetch(`${RAW_BASE}source_catalog.txt?t=${Date.now()}`, { cf: { cacheTtl: 60 } }),
    ]);
    const text = (await Promise.all(responses.map(item => item.ok ? item.text() : ''))).join('\n');
    return text.split(/\r?\n/).some(line => (
      line.split('#')[0].trim().replace(/^@/, '').toLowerCase() === source.toLowerCase()
    ));
  } catch {
    return false;
  }
}

function escapeHtml(value) {
  return String(value || '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;',
  }[character]));
}

async function inspectSource(source) {
  try {
    const response = await fetch(`https://telegram.me/${encodeURIComponent(source)}`, {
      headers: { 'User-Agent': 'Mozilla/5.0 (compatible; BBVG/1.0)' },
      redirect: 'follow',
    });
    const text = await response.text();
    const messages = (text.match(/tgme_widget_message/g) || []).length;
    const titleMatch = text.match(/tgme_channel_info_header_title[^>]*>([\s\S]*?)<\/div>/i);
    const title = titleMatch
      ? titleMatch[1].replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()
      : '';
    const wheelLinks = [...new Set(
      (text.match(/https?:\/\/(?:www\.)?betboom\.ru\/[^\s"'<>]+/gi) || []).slice(0, 10),
    )];
    const isPublic = response.ok && (messages > 0 || text.includes('tgme_channel_info'));
    return {
      public: isPublic,
      http_status: response.status,
      messages,
      title,
      wheel_links: wheelLinks,
      detail: isPublic
        ? 'публичный источник доступен'
        : `источник недоступен: HTTP ${response.status}`,
    };
  } catch (error) {
    return {
      public: false,
      http_status: null,
      messages: 0,
      title: '',
      wheel_links: [],
      detail: String(error),
    };
  }
}

async function notifyModerators(env, record) {
  const token = String(env.BOT_TOKEN || '');
  if (!token) return;
  const moderators = await env.DB.prepare(`
    SELECT u.chat_id,COALESCE(p.enabled,1) AS enabled
    FROM roles r
    JOIN users u ON u.id=r.user_id
    LEFT JOIN notification_preferences p
      ON p.user_id=u.id AND p.preference_key='admin_requests'
    WHERE r.role IN ('owner','admin') AND u.blocked=0
  `).all();
  const check = parseStored(record.check_json, {});
  const text = [
    '📨 <b>Запрос пользователя на добавление источника</b>',
    '',
    `Канал: <b>@${escapeHtml(record.source)}</b>`,
    `Название: ${escapeHtml(check.title || 'не найдено')}`,
    `Пользователь: ${escapeHtml(record.requester_name || 'неизвестно')}`,
    `Telegram ID: <code>${escapeHtml(record.requester_id)}</code>`,
    '',
    '<b>Автоматическая проверка</b>',
    `Публичный источник: ${check.public ? 'да' : 'нет'}`,
    `Доступных сообщений: ${Number(check.messages || 0)}`,
    `Найдено ссылок на колёса: ${Array.isArray(check.wheel_links) ? check.wheel_links.length : 0}`,
    `Результат: ${escapeHtml(check.detail || 'нет данных')}`,
  ].join('\n');
  const replyMarkup = {
    inline_keyboard: [
      [{ text: 'Открыть канал', url: `https://telegram.me/${record.source}` }],
      [
        { text: '⚡ В основные', callback_data: `sr:fast:${record.id}` },
        { text: '🌙 В ночное наблюдение', callback_data: `sr:nightly:${record.id}` },
      ],
      [{ text: 'Отклонить', callback_data: `sr:reject:${record.id}` }],
    ],
  };
  await Promise.all((moderators.results || []).filter(row => Number(row.enabled)).map(async row => {
    try {
      await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          chat_id: row.chat_id,
          text,
          parse_mode: 'HTML',
          disable_web_page_preview: true,
          reply_markup: replyMarkup,
        }),
      });
    } catch (error) {
      console.warn('Moderator notification failed', error);
    }
  }));
}

async function sourceRequest(request, env) {
  const user = await miniAppUser(request, env);
  const userId = await upsertUser(env, user);
  const body = await bodyJson(request);
  const source = String(body.source || '').trim().replace(/^@/, '').toLowerCase();
  if (!USERNAME_RE.test(source)) return failure(request, env, 400, 'Введите корректный username канала');
  if (await knownSource(source)) return failure(request, env, 409, 'Этот источник уже проверяется');
  const duplicate = await env.DB.prepare(`
    SELECT id FROM source_requests
    WHERE requester_id=? AND lower(source)=? AND status='pending' LIMIT 1
  `).bind(userId, source).first();
  if (duplicate) {
    return json(request, env, {
      ok: true,
      duplicate: true,
      request_id: duplicate.id,
      status: 'pending',
    });
  }
  const check = await inspectSource(source);
  if (!check.public) {
    return failure(request, env, 422, 'Публичный источник не найден или временно недоступен');
  }
  const id = crypto.randomUUID().replace(/-/g, '').slice(0, 12);
  const requesterName = [user.first_name, user.last_name].filter(Boolean).join(' ') || 'Пользователь';
  const record = {
    id,
    source,
    status: 'pending',
    created_at: nowIso(),
    requester_id: userId,
    requester_chat_id: userId,
    requester_name: user.username ? `${requesterName} (@${user.username})` : requesterName,
    requester_username: String(user.username || ''),
    check_json: JSON.stringify(check),
  };
  await env.DB.prepare(`
    INSERT INTO source_requests
      (id,source,status,created_at,requester_id,requester_chat_id,
       requester_name,requester_username,check_json)
    VALUES (?,?,?,?,?,?,?,?,?)
  `).bind(
    record.id,
    record.source,
    record.status,
    record.created_at,
    record.requester_id,
    record.requester_chat_id,
    record.requester_name,
    record.requester_username,
    record.check_json,
  ).run();
  await notifyModerators(env, record);
  return json(request, env, { ok: true, request_id: id, status: 'pending' }, 201);
}

async function accessFromDb(env) {
  const [usersResult, rolesResult, prefsResult, participationResult, hiddenResult, settingsResult] = await Promise.all([
    env.DB.prepare('SELECT * FROM users ORDER BY first_seen_at').all(),
    env.DB.prepare('SELECT user_id,role FROM roles').all(),
    env.DB.prepare('SELECT user_id,preference_key,enabled FROM notification_preferences').all(),
    env.DB.prepare('SELECT user_id,wheel_key,joined_at,active,data_json FROM wheel_participation').all(),
    env.DB.prepare('SELECT user_id,wheel_key,hidden_at,expires_at,active,data_json FROM hidden_wheels').all(),
    env.DB.prepare('SELECT setting_key,setting_value FROM system_settings').all(),
  ]);
  const roles = Object.fromEntries((rolesResult.results || []).map(row => [String(row.user_id), String(row.role)]));
  const systemSettings = Object.fromEntries(
    (settingsResult.results || []).map(row => [String(row.setting_key), parseStored(row.setting_value, row.setting_value)]),
  );
  const users = {};
  for (const row of usersResult.results || []) {
    users[row.id] = {
      id: String(row.id),
      chat_id: String(row.chat_id),
      username: String(row.username || ''),
      first_name: String(row.first_name || ''),
      last_name: String(row.last_name || ''),
      photo_url: String(row.photo_url || ''),
      first_seen_at: String(row.first_seen_at),
      last_seen_at: String(row.last_seen_at),
      notifications_enabled: false,
      notification_preferences: {},
      participating_wheels: {},
      hidden_wheels: {},
    };
  }
  for (const row of prefsResult.results || []) {
    if (!users[row.user_id]) continue;
    users[row.user_id].notification_preferences[row.preference_key] = Boolean(row.enabled);
    if (row.preference_key === 'wheels') users[row.user_id].notifications_enabled = Boolean(row.enabled);
  }
  for (const row of participationResult.results || []) {
    if (!users[row.user_id] || !Number(row.active)) continue;
    const value = safeObject(parseStored(row.data_json, {}));
    users[row.user_id].participating_wheels[row.wheel_key] = {
      ...value,
      joined_at: value.joined_at || row.joined_at,
    };
  }
  for (const row of hiddenResult.results || []) {
    if (!users[row.user_id] || !Number(row.active)) continue;
    const value = safeObject(parseStored(row.data_json, {}));
    users[row.user_id].hidden_wheels[row.wheel_key] = {
      ...value,
      hidden_at: value.hidden_at || row.hidden_at,
      expires_at: value.expires_at || row.expires_at,
    };
  }
  const ownerId = Object.keys(roles).find(id => roles[id] === 'owner') || '';
  const admins = Object.keys(roles).filter(id => roles[id] === 'admin').sort();
  const recipients = Object.values(users)
    .filter(record => record.notifications_enabled)
    .map(record => record.chat_id)
    .sort();
  return {
    version: 3,
    owner_id: ownerId,
    admins,
    blocked_users: (usersResult.results || []).filter(row => Number(row.blocked)).map(row => String(row.id)),
    notification_recipients: recipients,
    settings: {
      public_panel: systemSettings.public_panel !== false,
      notifications: systemSettings.notifications !== false,
      monitor_interval_minutes: Number(systemSettings.monitor_interval_minutes || 5),
    },
    users,
  };
}

async function upsertAccess(env, access) {
  const users = safeObject(access.users);
  const ownerId = String(access.owner_id || '');
  const admins = new Set((Array.isArray(access.admins) ? access.admins : []).map(String));
  const blocked = new Set((Array.isArray(access.blocked_users) ? access.blocked_users : []).map(String));
  const current = nowIso();
  const statements = [env.DB.prepare("UPDATE roles SET role='user' WHERE role IN ('owner','admin')")];
  for (const [idRaw, recordRaw] of Object.entries(users)) {
    const id = String(idRaw);
    const record = safeObject(recordRaw);
    statements.push(env.DB.prepare(`
      INSERT INTO users
        (id,chat_id,username,first_name,last_name,photo_url,first_seen_at,last_seen_at,blocked)
      VALUES (?,?,?,?,?,?,?,?,?)
      ON CONFLICT(id) DO UPDATE SET
        chat_id=excluded.chat_id,
        username=excluded.username,
        first_name=excluded.first_name,
        last_name=excluded.last_name,
        photo_url=excluded.photo_url,
        last_seen_at=excluded.last_seen_at,
        blocked=excluded.blocked
    `).bind(
      id,
      String(record.chat_id || id),
      String(record.username || ''),
      String(record.first_name || ''),
      String(record.last_name || ''),
      String(record.photo_url || ''),
      String(record.first_seen_at || current),
      String(record.last_seen_at || current),
      blocked.has(id) ? 1 : 0,
    ));
    const role = id === ownerId ? 'owner' : (admins.has(id) ? 'admin' : 'user');
    statements.push(env.DB.prepare(`
      INSERT INTO roles (user_id,role) VALUES (?,?)
      ON CONFLICT(user_id) DO UPDATE SET role=excluded.role
    `).bind(id, role));
    statements.push(env.DB.prepare('DELETE FROM notification_preferences WHERE user_id=?').bind(id));
    statements.push(env.DB.prepare('DELETE FROM wheel_participation WHERE user_id=?').bind(id));
    statements.push(env.DB.prepare('DELETE FROM hidden_wheels WHERE user_id=?').bind(id));
    const preferences = Object.keys(safeObject(record.notification_preferences)).length
      ? safeObject(record.notification_preferences)
      : { wheels: record.notifications_enabled !== false };
    for (const [key, enabled] of Object.entries(preferences)) {
      statements.push(env.DB.prepare(`
        INSERT INTO notification_preferences (user_id,preference_key,enabled) VALUES (?,?,?)
      `).bind(id, key, enabled ? 1 : 0));
    }
    for (const [key, raw] of Object.entries(safeObject(record.participating_wheels))) {
      const value = safeObject(raw);
      const joinedAt = String(value.joined_at || current);
      statements.push(env.DB.prepare(`
        INSERT INTO wheel_participation (user_id,wheel_key,joined_at,active,data_json)
        VALUES (?,?,?,1,?)
      `).bind(id, String(key).toLowerCase(), joinedAt, JSON.stringify(value)));
    }
    for (const [key, raw] of Object.entries(safeObject(record.hidden_wheels))) {
      const value = safeObject(raw);
      const hiddenAt = String(value.hidden_at || current);
      statements.push(env.DB.prepare(`
        INSERT INTO hidden_wheels (user_id,wheel_key,hidden_at,expires_at,active,data_json)
        VALUES (?,?,?,?,1,?)
      `).bind(
        id,
        String(key).toLowerCase(),
        hiddenAt,
        value.expires_at || null,
        JSON.stringify(value),
      ));
    }
  }
  for (const [key, value] of Object.entries(safeObject(access.settings))) {
    statements.push(env.DB.prepare(`
      INSERT INTO system_settings (setting_key,setting_value) VALUES (?,?)
      ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value
    `).bind(key, JSON.stringify(value)));
  }
  if (statements.length) await env.DB.batch(statements);
}

async function sourceRequestsFromDb(env) {
  const result = await env.DB.prepare('SELECT * FROM source_requests ORDER BY created_at').all();
  const requests = {};
  for (const row of result.results || []) {
    requests[row.id] = {
      id: row.id,
      source: row.source,
      status: row.status,
      created_at: row.created_at,
      requester_id: row.requester_id,
      requester_chat_id: row.requester_chat_id,
      requester_name: row.requester_name,
      requester_username: row.requester_username,
      check: safeObject(parseStored(row.check_json, {})),
      destination: row.destination || '',
      decision_text: row.decision_text || '',
      decided_at: row.decided_at || null,
      decided_by: row.decided_by || null,
    };
  }
  return { version: 1, requests };
}

async function upsertSourceRequests(env, payload) {
  const statements = [];
  for (const [idRaw, raw] of Object.entries(safeObject(payload.requests))) {
    const value = safeObject(raw);
    const id = String(value.id || idRaw);
    const requesterId = String(value.requester_id || 'unknown');
    const createdAt = String(value.created_at || nowIso());
    statements.push(env.DB.prepare(`
      INSERT INTO users
        (id,chat_id,username,first_name,last_name,photo_url,first_seen_at,last_seen_at,blocked)
      VALUES (?,?,?,?,?,?,?,?,0) ON CONFLICT(id) DO NOTHING
    `).bind(
      requesterId,
      String(value.requester_chat_id || requesterId),
      String(value.requester_username || ''),
      '', '', '', createdAt, createdAt,
    ));
    statements.push(env.DB.prepare(`
      INSERT INTO roles (user_id,role) VALUES (?,'user') ON CONFLICT(user_id) DO NOTHING
    `).bind(requesterId));
    statements.push(env.DB.prepare(`
      INSERT INTO source_requests
        (id,source,status,created_at,requester_id,requester_chat_id,requester_name,
         requester_username,check_json,destination,decision_text,decided_at,decided_by)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(id) DO UPDATE SET
        source=excluded.source,
        status=excluded.status,
        requester_id=excluded.requester_id,
        requester_chat_id=excluded.requester_chat_id,
        requester_name=excluded.requester_name,
        requester_username=excluded.requester_username,
        check_json=excluded.check_json,
        destination=excluded.destination,
        decision_text=excluded.decision_text,
        decided_at=excluded.decided_at,
        decided_by=excluded.decided_by
    `).bind(
      id,
      String(value.source || ''),
      String(value.status || 'pending'),
      createdAt,
      requesterId,
      String(value.requester_chat_id || requesterId),
      String(value.requester_name || ''),
      String(value.requester_username || ''),
      JSON.stringify(safeObject(value.check)),
      String(value.destination || ''),
      String(value.decision_text || ''),
      value.decided_at || null,
      value.decided_by || null,
    ));
  }
  if (statements.length) await env.DB.batch(statements);
}

async function publicSystem(request, env) {
  try {
    const response = await fetch(`${RAW_BASE}system_check_state.json?t=${Date.now()}`, { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return json(request, env, await response.json());
  } catch {
    return json(request, env, { status: 'delayed', checked_at: null }, 503);
  }
}

export default {
  async fetch(request, env) {
    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: cors(request, env) });
    }
    const url = new URL(request.url);
    try {
      if (url.pathname === '/health' && request.method === 'GET') {
        return json(request, env, { ok: true, service: 'bbvg-private-state' });
      }
      if (url.pathname === '/v1/public/system' && request.method === 'GET') return publicSystem(request, env);
      if (url.pathname === '/v1/session' && request.method === 'POST') return session(request, env);
      if (url.pathname === '/v1/me/participation' && request.method === 'PUT') return participation(request, env);
      if (url.pathname === '/v1/me/hidden' && request.method === 'PUT') return hiddenWheel(request, env);
      if (url.pathname === '/v1/me/settings' && request.method === 'PUT') return userSettings(request, env);
      if (url.pathname === '/v1/source-requests' && request.method === 'POST') return sourceRequest(request, env);
      if (url.pathname.startsWith('/v1/admin/')) {
        if (!internalAuthorized(request, env)) return failure(request, env, 401, 'Unauthorized');
        if (url.pathname === '/v1/admin/access' && request.method === 'GET') {
          return json(request, env, await accessFromDb(env));
        }
        if (url.pathname === '/v1/admin/access' && request.method === 'PUT') {
          await upsertAccess(env, await bodyJson(request));
          return json(request, env, { ok: true });
        }
        if (url.pathname === '/v1/admin/source-requests' && request.method === 'GET') {
          return json(request, env, await sourceRequestsFromDb(env));
        }
        if (url.pathname === '/v1/admin/source-requests' && request.method === 'PUT') {
          await upsertSourceRequests(env, await bodyJson(request));
          return json(request, env, { ok: true });
        }
      }
      return failure(request, env, 404, 'Not found');
    } catch (error) {
      console.error(error);
      const message = error instanceof Error ? error.message : 'Unexpected error';
      return failure(request, env, message.includes('Telegram') ? 401 : 500, message);
    }
  },
};
